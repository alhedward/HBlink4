"""Administrative helpers for the HBlink4 dashboard.

The dashboard admin surface intentionally exposes only repeater talkgroup ACLs.
Passphrases and the rest of config/config.json never leave the server.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import os
import secrets
import tempfile
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional, Sequence, Tuple


PASSWORD_SCHEME = "pbkdf2_sha256"
PASSWORD_ITERATIONS = 390_000
MAX_TALKGROUP_ID = 0xFFFFFF


class AdminConfigError(RuntimeError):
    """Raised when dashboard admin configuration is invalid."""


class TalkgroupConfigError(RuntimeError):
    """Raised when the HBlink4 talkgroup configuration cannot be edited."""


class StaleConfigError(TalkgroupConfigError):
    """Raised when config/config.json changed after the editor loaded it."""


class RestartError(RuntimeError):
    """Raised when an HBlink4 restart cannot be completed or verified."""


def hash_password(password: str, iterations: int = PASSWORD_ITERATIONS) -> str:
    """Return a PBKDF2-SHA256 password hash suitable for dashboard config."""
    if not isinstance(password, str) or not password:
        raise ValueError("Password must not be empty")
    if iterations < 100_000:
        raise ValueError("PBKDF2 iteration count is too low")

    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return "$".join(
        [
            PASSWORD_SCHEME,
            str(iterations),
            base64.urlsafe_b64encode(salt).decode("ascii").rstrip("="),
            base64.urlsafe_b64encode(digest).decode("ascii").rstrip("="),
        ]
    )


def _b64decode_unpadded(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def verify_password(password: str, encoded_hash: str) -> bool:
    """Verify a password against a hash produced by :func:`hash_password`."""
    try:
        scheme, iterations_text, salt_text, digest_text = encoded_hash.split("$", 3)
        if scheme != PASSWORD_SCHEME:
            return False
        iterations = int(iterations_text)
        if iterations < 100_000:
            return False
        salt = _b64decode_unpadded(salt_text)
        expected = _b64decode_unpadded(digest_text)
        actual = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), salt, iterations
        )
        return hmac.compare_digest(actual, expected)
    except (AttributeError, TypeError, ValueError):
        return False


@dataclass
class AdminSession:
    username: str
    csrf_token: str
    expires_at: float


class AdminSessionManager:
    """Small in-memory session store for the single-process dashboard."""

    def __init__(self, timeout_minutes: int = 60):
        timeout_minutes = int(timeout_minutes)
        if timeout_minutes < 5 or timeout_minutes > 24 * 60:
            raise AdminConfigError("admin.session_timeout_minutes must be 5..1440")
        self.timeout_seconds = timeout_minutes * 60
        self._sessions: Dict[str, AdminSession] = {}

    def create(self, username: str) -> Tuple[str, AdminSession]:
        self._purge_expired()
        token = secrets.token_urlsafe(32)
        session = AdminSession(
            username=username,
            csrf_token=secrets.token_urlsafe(24),
            expires_at=time.time() + self.timeout_seconds,
        )
        self._sessions[token] = session
        return token, session

    def get(self, token: Optional[str]) -> Optional[AdminSession]:
        if not token:
            return None
        session = self._sessions.get(token)
        if session is None:
            return None
        if session.expires_at <= time.time():
            self._sessions.pop(token, None)
            return None
        return session

    def destroy(self, token: Optional[str]) -> None:
        if token:
            self._sessions.pop(token, None)

    def _purge_expired(self) -> None:
        now = time.time()
        expired = [token for token, session in self._sessions.items() if session.expires_at <= now]
        for token in expired:
            self._sessions.pop(token, None)


class LoginRateLimiter:
    """Per-client login failure limiter kept in memory with dashboard state."""

    def __init__(self, max_failures: int = 5, window_seconds: int = 300):
        self.max_failures = max_failures
        self.window_seconds = window_seconds
        self._failures: Dict[str, Deque[float]] = defaultdict(deque)

    def retry_after(self, client_key: str) -> int:
        failures = self._failures[client_key]
        now = time.time()
        cutoff = now - self.window_seconds
        while failures and failures[0] <= cutoff:
            failures.popleft()
        if len(failures) < self.max_failures:
            return 0
        return max(1, int(self.window_seconds - (now - failures[0])))

    def record_failure(self, client_key: str) -> None:
        failures = self._failures[client_key]
        failures.append(time.time())
        self.retry_after(client_key)

    def record_success(self, client_key: str) -> None:
        self._failures.pop(client_key, None)


def _normalize_talkgroups(value: Any, field_name: str) -> Optional[List[int]]:
    """Validate a talkgroup ACL while preserving null/list semantics."""
    if value is None:
        return None
    if not isinstance(value, list):
        raise TalkgroupConfigError(f"{field_name} must be null or a list")

    result: List[int] = []
    seen = set()
    for raw_tg in value:
        if isinstance(raw_tg, bool) or not isinstance(raw_tg, int):
            raise TalkgroupConfigError(f"{field_name} contains a non-integer talkgroup")
        if raw_tg < 1 or raw_tg > MAX_TALKGROUP_ID:
            raise TalkgroupConfigError(
                f"{field_name} talkgroup {raw_tg} must be between 1 and {MAX_TALKGROUP_ID}"
            )
        if raw_tg not in seen:
            seen.add(raw_tg)
            result.append(raw_tg)
    return result


def _revision(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


class TalkgroupConfigStore:
    """Read/write only the repeater talkgroup ACL portion of HBlink4 config."""

    def __init__(self, config_path: Path, backup_on_save: bool = True):
        self.config_path = Path(config_path)
        self.backup_on_save = bool(backup_on_save)

    def _read(self) -> Tuple[bytes, dict, str, str]:
        try:
            raw = self.config_path.read_bytes()
        except FileNotFoundError as exc:
            raise TalkgroupConfigError(f"HBlink4 config not found: {self.config_path}") from exc
        except OSError as exc:
            raise TalkgroupConfigError(f"Cannot read HBlink4 config: {exc}") from exc

        try:
            config = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise TalkgroupConfigError(f"HBlink4 config is not valid JSON: {exc}") from exc
        if not isinstance(config, dict):
            raise TalkgroupConfigError("HBlink4 config root must be a JSON object")

        if "repeater_configurations" in config:
            section_key = "repeater_configurations"
        elif "repeaters" in config:
            section_key = "repeaters"
        else:
            raise TalkgroupConfigError("HBlink4 config has no repeater_configurations section")

        section = config.get(section_key)
        if not isinstance(section, dict):
            raise TalkgroupConfigError(f"{section_key} must be a JSON object")
        patterns = section.get("patterns", [])
        if not isinstance(patterns, list):
            raise TalkgroupConfigError(f"{section_key}.patterns must be a list")

        return raw, config, _revision(raw), section_key

    @staticmethod
    def _slot_payload(config: dict) -> dict:
        return {
            "slot1_talkgroups": _normalize_talkgroups(
                config.get("slot1_talkgroups"), "slot1_talkgroups"
            ),
            "slot2_talkgroups": _normalize_talkgroups(
                config.get("slot2_talkgroups"), "slot2_talkgroups"
            ),
        }

    def load_for_editor(self) -> dict:
        raw, config, revision, section_key = self._read()
        del raw
        section = config[section_key]
        result_patterns = []
        for index, pattern in enumerate(section.get("patterns", [])):
            if not isinstance(pattern, dict):
                raise TalkgroupConfigError(f"Pattern {index} must be a JSON object")
            pattern_config = pattern.get("config")
            if not isinstance(pattern_config, dict):
                raise TalkgroupConfigError(f"Pattern {index}.config must be a JSON object")

            result_patterns.append(
                {
                    "index": index,
                    "name": str(pattern.get("name", f"Pattern {index + 1}")),
                    "description": str(pattern.get("description", "")),
                    "match": pattern.get("match", {}),
                    "trust": bool(pattern_config.get("trust", False)),
                    **self._slot_payload(pattern_config),
                }
            )

        default_payload = None
        if "default" in section:
            default_config = section["default"]
            if not isinstance(default_config, dict):
                raise TalkgroupConfigError(f"{section_key}.default must be a JSON object")
            default_payload = self._slot_payload(default_config)

        return {
            "revision": revision,
            "config_file": self.config_path.name,
            "patterns": result_patterns,
            "default": default_payload,
        }

    def save_talkgroups(
        self,
        expected_revision: str,
        pattern_updates: Sequence[dict],
        default_update: Optional[dict],
    ) -> dict:
        raw, config, current_revision, section_key = self._read()
        if not isinstance(expected_revision, str) or not expected_revision:
            raise TalkgroupConfigError("Missing configuration revision")
        if not hmac.compare_digest(current_revision, expected_revision):
            raise StaleConfigError(
                "HBlink4 config changed after this editor page loaded; reload before saving"
            )
        if not isinstance(pattern_updates, list):
            raise TalkgroupConfigError("patterns must be a list")

        section = config[section_key]
        patterns = section.get("patterns", [])
        seen_indices = set()

        for update in pattern_updates:
            if not isinstance(update, dict):
                raise TalkgroupConfigError("Each pattern update must be an object")
            index = update.get("index")
            if isinstance(index, bool) or not isinstance(index, int):
                raise TalkgroupConfigError("Pattern index must be an integer")
            if index < 0 or index >= len(patterns):
                raise TalkgroupConfigError(f"Pattern index {index} is out of range")
            if index in seen_indices:
                raise TalkgroupConfigError(f"Pattern index {index} was submitted more than once")
            seen_indices.add(index)

            pattern = patterns[index]
            if not isinstance(pattern, dict) or not isinstance(pattern.get("config"), dict):
                raise TalkgroupConfigError(f"Pattern {index} has an invalid config object")

            pattern_config = pattern["config"]
            if "slot1_talkgroups" not in update or "slot2_talkgroups" not in update:
                raise TalkgroupConfigError(f"Pattern {index} must include both slot talkgroup lists")
            pattern_config["slot1_talkgroups"] = _normalize_talkgroups(
                update["slot1_talkgroups"], f"patterns[{index}].slot1_talkgroups"
            )
            pattern_config["slot2_talkgroups"] = _normalize_talkgroups(
                update["slot2_talkgroups"], f"patterns[{index}].slot2_talkgroups"
            )

        if default_update is not None:
            if "default" not in section:
                raise TalkgroupConfigError("This HBlink4 config has no default repeater configuration")
            if not isinstance(default_update, dict):
                raise TalkgroupConfigError("default must be an object or null")
            if "slot1_talkgroups" not in default_update or "slot2_talkgroups" not in default_update:
                raise TalkgroupConfigError("Default configuration must include both slot talkgroup lists")
            default_config = section["default"]
            if not isinstance(default_config, dict):
                raise TalkgroupConfigError("Default repeater configuration is invalid")
            default_config["slot1_talkgroups"] = _normalize_talkgroups(
                default_update["slot1_talkgroups"], "default.slot1_talkgroups"
            )
            default_config["slot2_talkgroups"] = _normalize_talkgroups(
                default_update["slot2_talkgroups"], "default.slot2_talkgroups"
            )

        # Only the two talkgroup fields above are mutable through this API. Their
        # types/ranges have already been validated, while all other HBlink4 config
        # objects are carried through unchanged at the JSON value level.

        rendered = (json.dumps(config, indent=4) + "\n").encode("utf-8")
        self._atomic_replace(raw, rendered)
        return self.load_for_editor()

    def _atomic_replace(self, old_raw: bytes, new_raw: bytes) -> None:
        try:
            stat_result = self.config_path.stat()
            if self.backup_on_save:
                backup_path = self.config_path.with_suffix(self.config_path.suffix + ".bak")
                self._atomic_write(backup_path, old_raw, stat_result.st_mode & 0o777)
            self._atomic_write(self.config_path, new_raw, stat_result.st_mode & 0o777)
        except OSError as exc:
            raise TalkgroupConfigError(f"Could not save HBlink4 config: {exc}") from exc

    @staticmethod
    def _atomic_write(path: Path, content: bytes, mode: int) -> None:
        parent = path.parent
        fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=parent)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temp_name, mode)
            os.replace(temp_name, path)
        finally:
            try:
                os.unlink(temp_name)
            except FileNotFoundError:
                pass


class RestartController:
    """Run a fixed, locally configured HBlink4 restart command and verify it."""

    def __init__(self, settings: dict):
        settings = settings or {}
        self.enabled = bool(settings.get("enabled", False))
        self.command = self._validate_command(settings.get("command"), "admin.restart.command")
        self.status_command = self._validate_command(
            settings.get("status_command"), "admin.restart.status_command", allow_empty=True
        )
        self.timeout_seconds = float(settings.get("timeout_seconds", 15))
        self.verify_attempts = int(settings.get("verify_attempts", 6))
        self.verify_delay_seconds = float(settings.get("verify_delay_seconds", 0.5))
        if self.timeout_seconds <= 0 or self.timeout_seconds > 120:
            raise AdminConfigError("admin.restart.timeout_seconds must be > 0 and <= 120")
        if self.verify_attempts < 1 or self.verify_attempts > 60:
            raise AdminConfigError("admin.restart.verify_attempts must be 1..60")
        if self.verify_delay_seconds < 0 or self.verify_delay_seconds > 10:
            raise AdminConfigError("admin.restart.verify_delay_seconds must be 0..10")

    @staticmethod
    def _validate_command(
        value: Any, field_name: str, allow_empty: bool = False
    ) -> Optional[Tuple[str, ...]]:
        if value in (None, []):
            if allow_empty:
                return None
            return None
        if not isinstance(value, list) or not value:
            raise AdminConfigError(f"{field_name} must be a non-empty JSON array")
        if not all(isinstance(part, str) and part for part in value):
            raise AdminConfigError(f"{field_name} must contain only non-empty strings")
        return tuple(value)

    async def restart(self) -> dict:
        if not self.enabled:
            raise RestartError("HBlink4 restart is disabled in dashboard configuration")
        if not self.command:
            raise RestartError("No HBlink4 restart command is configured")

        return_code, stdout, stderr = await self._run_command(self.command)
        if return_code != 0:
            detail = (stderr or stdout or "restart command returned a non-zero status").strip()
            raise RestartError(detail[-800:])

        if self.status_command:
            last_detail = ""
            for attempt in range(self.verify_attempts):
                if attempt:
                    await asyncio.sleep(self.verify_delay_seconds)
                status_code, status_stdout, status_stderr = await self._run_command(
                    self.status_command
                )
                status_text = (status_stdout or status_stderr).strip()
                last_detail = status_text or f"status command returned {status_code}"
                if status_code == 0 and status_text.lower() == "active":
                    return {"ok": True, "status": "active"}
            raise RestartError(f"Restart command completed but HBlink4 is not active: {last_detail}")

        return {"ok": True, "status": "restart command completed"}

    async def _run_command(self, command: Sequence[str]) -> Tuple[int, str, str]:
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                process.communicate(), timeout=self.timeout_seconds
            )
        except FileNotFoundError as exc:
            raise RestartError(f"Restart helper executable was not found: {command[0]}") from exc
        except asyncio.TimeoutError as exc:
            try:
                process.kill()
                await process.wait()
            except Exception:
                pass
            raise RestartError("HBlink4 restart/status command timed out") from exc

        return (
            process.returncode,
            stdout_bytes.decode("utf-8", errors="replace")[-2000:],
            stderr_bytes.decode("utf-8", errors="replace")[-2000:],
        )
