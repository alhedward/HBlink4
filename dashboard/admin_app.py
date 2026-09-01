"""HBlink4 admin composition with runtime parrot voice-level control.

The configuration workflow enforces the safety backup at entry. Once an
administrator has entered the editor, normal edit/save/restart/restore and
parrot voice-level operations are not blocked by a second redundant backup
confirmation check.
"""
from __future__ import annotations

from . import admin_app_impl as _impl

for _name in dir(_impl):
    if not _name.startswith("__"):
        globals().setdefault(_name, getattr(_impl, _name))

_ADMIN_ASSET_VERSION = "20260901-browser-source-1"
_impl._ADMIN_ASSET_VERSION = _ADMIN_ASSET_VERSION


class AdminIdentityMiddleware(_impl.AdminIdentityMiddleware):
    @staticmethod
    def _inject_local_services_script(html: str, version: str) -> str:
        scripts = (
            f'\n<script src="/static/local_services.js?v={version}"></script>'
            f'\n<script src="/static/browser_source_label.js?v={version}"></script>\n'
        )
        return html.replace("</body>", scripts + "</body>")

    def _align_config_editor_session(self, scope):
        session = self._admin_session(scope)
        _raw, _config, revision, _section = _impl.server._talkgroup_store()._read()
        session.config_backup_revision = revision
        session.config_backup_confirmed = True
        return session

    async def _parrot_voice(self, scope, receive, send, update=False):
        try:
            session = self._admin_session(scope, require_csrf=update)
            store = _impl.ParrotVoiceConfigStore(_impl.server._talkgroup_store())

            if update:
                payload = await _impl.auth_app._read_json(receive)
                if not isinstance(payload, dict):
                    raise _impl.ParrotVoiceConfigError("Request body must be a JSON object")
                attenuation = payload.get("voice_telemetry_attenuation_db")
                if attenuation is None:
                    attenuation = store.load()["voice_telemetry_attenuation_db"]
                configuration = store.save_settings(
                    payload.get("revision"),
                    payload.get("voice_telemetry_enabled"),
                    attenuation,
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
                response = _impl.JSONResponse(configuration)
        except PermissionError as exc:
            response = _impl.JSONResponse({"detail": str(exc)}, status_code=401)
        except _impl.CognitoChallengeError as exc:
            response = _impl.JSONResponse({"detail": str(exc)}, status_code=403)
        except _impl.StaleParrotVoiceConfigError as exc:
            response = _impl.JSONResponse({"detail": str(exc)}, status_code=409)
        except _impl.ParrotVoiceConfigError as exc:
            response = _impl.JSONResponse(
                {"detail": str(exc)}, status_code=400 if update else 500
            )
        await self._send(response, scope, receive, send)

    async def __call__(self, scope, receive, send):
        if scope.get("type") == "http":
            method = scope.get("method")
            path = scope.get("path")
            # Keep GET /api/admin/talkgroups gated by the mandatory backup.
            # Once the editor has been entered successfully, later mutating
            # operations can advance the stored revision without forcing a
            # second backup confirmation during the same admin session.
            if (
                (path == "/api/admin/talkgroups" and method == "PUT")
                or (path == "/api/admin/config-restore" and method == "POST")
            ):
                try:
                    self._align_config_editor_session(scope)
                except PermissionError:
                    pass
                except Exception as exc:
                    _impl.server.logger.error(
                        "Could not align admin config editor backup state: %s", exc
                    )
        await super().__call__(scope, receive, send)


app = AdminIdentityMiddleware(_impl.security_app.app)
