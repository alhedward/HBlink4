"""Server-backed live activity state for local DMR services.

This module gives the dashboard a second, independent observation path for the
TG9990 parrot.  WebSocket delivery remains the primary real-time mechanism, but
we retain the most recent sanitized lifecycle event in memory and expose it via
a small read-only API.  The browser can therefore recover from a missed or
transient WebSocket event without touching HBlink routing or packet data.
"""

from __future__ import annotations

import json
from time import time
from typing import Any, Dict, Optional

from fastapi import Query


_PHASES = {
    "parrot_recording_started": "recording",
    "parrot_recording_complete": "preparing",
    "parrot_playback_started": "playback",
    "parrot_playback_complete": "complete",
    "parrot_recording_discarded": "cancelled",
    "parrot_playback_cancelled": "cancelled",
}
_STREAM_PHASES = {
    "stream_start": "recording",
    "stream_update": "recording",
    "stream_end": "preparing",
}
_FINAL_TTL_SECONDS = 8.0

_activity_by_talkgroup: Dict[int, Dict[str, Any]] = {}
_installed = False


def _integer(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    return None


def _safe_activity_data(data: Dict[str, Any]) -> Dict[str, Any]:
    """Return only operational metadata safe for the public dashboard."""
    result: Dict[str, Any] = {}
    for key in (
        "repeater_id",
        "slot",
        "src_id",
        "rf_src",
        "talkgroup",
        "dst_id",
        "stream_id",
        "packet_count",
        "packets",
        "duration",
        "reason",
        "connection_type",
        "is_assumed",
    ):
        if key in data:
            result[key] = data[key]

    quality = data.get("rf_quality")
    if isinstance(quality, dict):
        result["rf_quality"] = {
            key: value
            for key, value in quality.items()
            if key in {
                "ber_average_percent",
                "ber_peak_percent",
                "ber_samples",
                "rssi_average_dbm",
                "rssi_min_dbm",
                "rssi_max_dbm",
                "rssi_samples",
            }
            and isinstance(value, (int, float))
            and not isinstance(value, bool)
        }

    if "src_id" not in result and _integer(result.get("rf_src")) is not None:
        result["src_id"] = result["rf_src"]
    if "packet_count" not in result and _integer(result.get("packets")) is not None:
        result["packet_count"] = result["packets"]
    return result


def _record(event: Dict[str, Any]) -> None:
    event_type = event.get("type")
    raw_data = event.get("data")
    if not isinstance(event_type, str) or not isinstance(raw_data, dict):
        return

    phase = _PHASES.get(event_type)
    talkgroup = _integer(raw_data.get("talkgroup"))

    if phase is None and event_type in _STREAM_PHASES:
        # Ordinary stream events are a fallback for a missed parrot-specific
        # lifecycle event.  Only RX streams from local HBP endpoints qualify;
        # assumed TX streams and external trunks must never masquerade as the
        # local echo service.
        connection_type = raw_data.get("connection_type") or "repeater"
        if connection_type not in {"repeater", "hotspot", "unknown"}:
            return
        if raw_data.get("is_assumed") is True:
            return
        talkgroup = _integer(raw_data.get("dst_id"))
        phase = _STREAM_PHASES[event_type]

    if phase is None or talkgroup is None:
        return

    event_timestamp = event.get("timestamp")
    if not isinstance(event_timestamp, (int, float)) or isinstance(event_timestamp, bool):
        event_timestamp = time()

    _activity_by_talkgroup[talkgroup] = {
        "phase": phase,
        "timestamp": float(event_timestamp),
        "data": _safe_activity_data(raw_data),
    }


def get_activity(talkgroup: int) -> Optional[Dict[str, Any]]:
    activity = _activity_by_talkgroup.get(talkgroup)
    if not activity:
        return None
    if activity["phase"] in {"complete", "cancelled"}:
        if time() - activity["timestamp"] > _FINAL_TTL_SECONDS:
            _activity_by_talkgroup.pop(talkgroup, None)
            return None
    return {
        "phase": activity["phase"],
        "timestamp": activity["timestamp"],
        "data": dict(activity["data"]),
    }


class ParrotActivityScriptMiddleware:
    """Inject the small polling fallback into HTML dashboard responses."""

    def __init__(self, app, asset_version: str):
        self.app = app
        self.script = (
            f'\n<script src="/static/parrot_activity_poll.js?v={asset_version}"></script>\n'
        ).encode("utf-8")

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        start_message = None
        body_parts = []

        async def capture(message):
            nonlocal start_message
            if message["type"] == "http.response.start":
                start_message = message
                return
            if message["type"] != "http.response.body":
                await send(message)
                return
            body_parts.append(message.get("body", b""))
            if message.get("more_body", False):
                return

            if start_message is None:
                await send(message)
                return

            headers = list(start_message.get("headers", []))
            content_type = next(
                (
                    value.decode("latin-1").lower()
                    for key, value in headers
                    if key.lower() == b"content-type"
                ),
                "",
            )
            body = b"".join(body_parts)
            if "text/html" in content_type and b"</body>" in body:
                body = body.replace(b"</body>", self.script + b"</body>", 1)
                headers = [
                    (key, value)
                    for key, value in headers
                    if key.lower() != b"content-length"
                ]
                headers.append((b"content-length", str(len(body)).encode("ascii")))
                start_message = {**start_message, "headers": headers}

            await send(start_message)
            await send({"type": "http.response.body", "body": body, "more_body": False})

        await self.app(scope, receive, capture)


def install(admin_app_module):
    """Install activity recording/API and return the wrapped composed app."""
    global _installed

    server = admin_app_module.server
    asset_version = "20260830-parrot-live-3"
    dashboard_version = "1.3.1"

    # Keep release/version reporting authoritative in the deployed composed app.
    admin_app_module._DASHBOARD_VERSION = dashboard_version
    admin_app_module._ADMIN_ASSET_VERSION = asset_version
    server.DASHBOARD_VERSION = dashboard_version
    server.app.version = dashboard_version

    if not _installed:
        original_handle_event = server.EventReceiver.handle_event

        async def handle_event_with_parrot_state(self, event):
            _record(event)
            return await original_handle_event(self, event)

        server.EventReceiver.handle_event = handle_event_with_parrot_state

        async def local_service_activity(
            talkgroup: int = Query(default=9990, ge=1, le=0xFFFFFF),
        ):
            return {"talkgroup": talkgroup, "activity": get_activity(talkgroup)}

        server.app.add_api_route(
            "/api/local-services/activity",
            local_service_activity,
            methods=["GET"],
            include_in_schema=False,
        )
        _installed = True

    return ParrotActivityScriptMiddleware(admin_app_module.app, asset_version)
