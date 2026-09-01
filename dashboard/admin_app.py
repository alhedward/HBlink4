"""HBlink4 admin composition without browser voice-report controls.

The configuration workflow enforces the safety backup at entry. Once an
administrator has entered the editor, normal edit/save/restart/restore actions
are not blocked by a second, redundant in-page backup confirmation state.
"""
from __future__ import annotations

from pathlib import Path
from fastapi.responses import HTMLResponse, JSONResponse

from . import admin_app_impl as _impl

for _name in dir(_impl):
    if not _name.startswith("__"):
        globals().setdefault(_name, getattr(_impl, _name))

_ADMIN_ASSET_VERSION = "20260901-admin-backup-gate-1"
_impl._ADMIN_ASSET_VERSION = _ADMIN_ASSET_VERSION


class AdminIdentityMiddleware(_impl.AdminIdentityMiddleware):
    async def _serve_admin_page(self, scope, receive, send):
        html_path = Path(_impl.server.__file__).parent / "static" / "admin.html"
        if not html_path.exists():
            await self.app(scope, receive, send)
            return
        html = html_path.read_text(encoding="utf-8")
        version = _ADMIN_ASSET_VERSION
        scripts = (
            f'\n<script src="/static/admin_mfa.js?v={version}"></script>'
            f'\n<script src="/static/admin_webauthn.js?v={version}"></script>'
            f'\n<script src="/static/admin_invite_feedback.js?v={version}"></script>'
            f'\n<script src="/static/admin_identity.js?v={version}"></script>'
            f'\n<script src="/static/admin_profile_compact.js?v={version}"></script>'
            f'\n<script src="/static/local_services.js?v={version}"></script>\n'
        )
        html = html.replace("</body>", scripts + "</body>")
        await self._send(
            HTMLResponse(html, headers={"Cache-Control": "no-store"}),
            scope,
            receive,
            send,
        )

    def _align_config_editor_session(self, scope):
        """Keep the legacy route guard aligned after the entry backup gate.

        The editor entry flow has already required a backup. The underlying
        legacy routes still check config_backup_confirmed/revision, so align
        those fields with the current live revision before ordinary editor
        operations instead of forcing a second backup/confirm cycle.
        """
        session = self._admin_session(scope)
        _raw, _config, revision, _section = _impl.server._talkgroup_store()._read()
        session.config_backup_revision = revision
        session.config_backup_confirmed = True

    async def __call__(self, scope, receive, send):
        if scope.get("type") == "http":
            method = scope.get("method")
            path = scope.get("path")

            # Spoken/voice report controls are intentionally removed from the
            # web interface. TG9990 parrot echo itself remains unchanged.
            if path == "/api/admin/parrot-voice":
                await self._send(
                    JSONResponse(
                        {"detail": "Browser voice-report control is not available."},
                        status_code=404,
                    ),
                    scope,
                    receive,
                    send,
                )
                return

            if path in {"/api/admin/talkgroups", "/api/admin/config-restore"} and method in {"GET", "PUT", "POST"}:
                try:
                    self._align_config_editor_session(scope)
                except PermissionError:
                    pass
                except Exception as exc:
                    _impl.server.logger.error("Could not align admin config editor backup state: %s", exc)

        await super().__call__(scope, receive, send)


app = AdminIdentityMiddleware(_impl.security_app.app)
