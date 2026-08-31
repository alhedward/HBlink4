"""HBlink4 admin composition with runtime parrot voice-level control."""
from __future__ import annotations

from . import admin_app_impl as _impl

for _name in dir(_impl):
    if not _name.startswith("__"):
        globals().setdefault(_name, getattr(_impl, _name))

_ADMIN_ASSET_VERSION = "20260831-parrot-level-1"
_impl._ADMIN_ASSET_VERSION = _ADMIN_ASSET_VERSION


class AdminIdentityMiddleware(_impl.AdminIdentityMiddleware):
    async def _parrot_voice(self, scope, receive, send, update=False):
        try:
            session = self._admin_session(scope, require_csrf=update)
            self._require_backup(session)
            store = _impl.ParrotVoiceConfigStore(_impl.server._talkgroup_store())

            if update:
                payload = await _impl.auth_app._read_json(receive)
                if not isinstance(payload, dict):
                    raise _impl.ParrotVoiceConfigError("Request body must be a JSON object")
                configuration = store.save_settings(
                    payload.get("revision"),
                    payload.get("voice_telemetry_enabled"),
                    payload.get("voice_telemetry_attenuation_db"),
                )
                _impl.server.logger.warning(
                    "Dashboard admin %s set parrot voice telemetry=%s attenuation=-%.1f dB",
                    session.username,
                    configuration["voice_telemetry_enabled"],
                    configuration["voice_telemetry_attenuation_db"],
                )
                response = _impl.JSONResponse(
                    {
                        "ok": True,
                        "restart_required": True,
                        "configuration": configuration,
                    }
                )
            else:
                configuration = store.load()
                if configuration["revision"] != session.config_backup_revision:
                    session.config_backup_revision = None
                    session.config_backup_confirmed = False
                    raise _impl.BackupRequiredError(
                        "HBlink4 config changed after the backup download; download a fresh backup before editing"
                    )
                response = _impl.JSONResponse(configuration)
        except PermissionError as exc:
            response = _impl.JSONResponse({"detail": str(exc)}, status_code=401)
        except _impl.CognitoChallengeError as exc:
            response = _impl.JSONResponse({"detail": str(exc)}, status_code=403)
        except _impl.BackupRequiredError as exc:
            response = _impl.JSONResponse({"detail": str(exc)}, status_code=428)
        except _impl.StaleParrotVoiceConfigError as exc:
            response = _impl.JSONResponse({"detail": str(exc)}, status_code=409)
        except _impl.ParrotVoiceConfigError as exc:
            response = _impl.JSONResponse(
                {"detail": str(exc)}, status_code=400 if update else 500
            )
        await self._send(response, scope, receive, send)
