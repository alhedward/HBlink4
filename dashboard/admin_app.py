"""HBlink4 administrator composition.

The configuration workflow keeps the existing mandatory backup-at-entry UI, but
once that workflow has been unlocked it no longer applies a second server-side
backup flag to every save/restore operation.  The TG9990 web voice-report
control is intentionally not exposed from this application.
"""
from __future__ import annotations

from pathlib import Path

from . import admin_app_impl as _impl

for _name in dir(_impl):
    if not _name.startswith("__"):
        globals().setdefault(_name, getattr(_impl, _name))

_ADMIN_ASSET_VERSION = "20260901-admin-config-1"
_impl._ADMIN_ASSET_VERSION = _ADMIN_ASSET_VERSION

# The browser already enforces the required full-config backup before it opens
# the editor.  Requiring a second in-memory confirmation in the underlying
# FastAPI save/restore handlers made valid Cognito sessions fail after the
# editor was already unlocked.  Keep optimistic revision checks in the actual
# save operation, but do not gate each mutation on that redundant flag.
def _configuration_workflow_already_unlocked(_session) -> None:
    return None


_impl.server._require_config_backup = _configuration_workflow_already_unlocked


class AdminIdentityMiddleware(_impl.AdminIdentityMiddleware):
    async def _serve_admin_page(self, scope, receive, send):
        html_path = Path(_impl.server.__file__).parent / "static" / "admin.html"
        if not html_path.exists():
            await self.app(scope, receive, send)
            return

        html = html_path.read_text(encoding="utf-8")
        # The mandatory backup happens before the editor opens.  Keep restore,
        # but remove the redundant second backup action from inside the editor.
        html = html.replace(
            "<strong>Full configuration backup &amp; restore</strong>",
            "<strong>Full configuration restore</strong>",
        )
        html = html.replace(
            "Download another complete backup at any time, or restore a previously downloaded HBlink4 config after a rebuild. Restoring replaces the complete config and requires an HBlink4 restart.",
            "Restore a previously downloaded HBlink4 config after a rebuild. Restoring replaces the complete config and requires an HBlink4 restart.",
        )
        html = html.replace(
            '<button id="downloadAgainBtn" class="secondary" type="button">Download current config</button>',
            '<button id="downloadAgainBtn" class="secondary hidden" type="button" aria-hidden="true" tabindex="-1">Download current config</button>',
        )

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
            _impl.HTMLResponse(html, headers={"Cache-Control": "no-store"}),
            scope,
            receive,
            send,
        )

    async def __call__(self, scope, receive, send):
        if scope.get("type") == "http" and scope.get("path") == "/api/admin/parrot-voice":
            # Retire the experimental web voice-report control without touching
            # ordinary TG9990 parrot/echo behaviour or the underlying config.
            await self._send(
                _impl.JSONResponse(
                    {"detail": "Parrot voice-report web control is not enabled"},
                    status_code=404,
                ),
                scope,
                receive,
                send,
            )
            return
        await super().__call__(scope, receive, send)


app = AdminIdentityMiddleware(_impl.security_app.app)
