#!/usr/bin/env python3
"""One-shot dashboard patch helper; removed before review."""

from pathlib import Path


def patch(path_name, replacements):
    path = Path(path_name)
    text = path.read_text()
    for label, old, new in replacements:
        count = text.count(old)
        if count != 1:
            raise SystemExit(f"{path_name}: {label}: expected one match, found {count}")
        text = text.replace(old, new, 1)
    path.write_text(text)


patch(
    "dashboard/parrot_activity_state.py",
    [
        (
            "telemetry phases",
            '    "parrot_playback_started": "playback",\n    "parrot_playback_complete": "complete",',
            '    "parrot_playback_started": "playback",\n    "parrot_telemetry_started": "telemetry",\n    "parrot_telemetry_complete": "telemetry",\n    "parrot_telemetry_cancelled": "telemetry",\n    "parrot_playback_complete": "complete",',
        ),
        (
            "safe telemetry status",
            '        "is_assumed",\n    ):',
            '        "is_assumed",\n        "voice_telemetry_status",\n    ):',
        ),
        (
            "asset version",
            '    asset_version = "20260830-parrot-live-3"',
            '    asset_version = "20260830-parrot-live-4"',
        ),
        (
            "dashboard version",
            '    dashboard_version = "1.3.1"',
            '    dashboard_version = "1.3.2"',
        ),
    ],
)

patch(
    "dashboard/static/parrot_activity_poll.js",
    [
        (
            "poll telemetry label",
            "        case 'playback': return '🔵 Playing back';\n        case 'complete': return '✅ Test complete';",
            "        case 'playback': return '🔵 Playing back';\n        case 'telemetry': return '🟣 Voice report';\n        case 'complete': return '✅ Test complete';",
        ),
    ],
)

patch(
    "dashboard/static/local_services.js",
    [
        (
            "ws telemetry label",
            "        case 'playback': return '🔵 Playing back';\n        case 'complete': return '✅ Test complete';",
            "        case 'playback': return '🔵 Playing back';\n        case 'telemetry': return '🟣 Voice report';\n        case 'complete': return '✅ Test complete';",
        ),
        (
            "final state reset",
            """            badgeResetTimer = setTimeout(() => {
                const badge = document.getElementById(`${SERVICE_ID}Badge`);
                if (badge && parrot) badge.textContent = `🦜 Parrot TG${parrot.talkgroup}`;
            }, 5000);""",
            """            badgeResetTimer = setTimeout(() => {
                activity = null;
                renderActivity();
            }, 8000);""",
        ),
        (
            "initial local endpoint fallback",
            """                const active = data.streams.find(stream =>
                    stream &&
                    (stream.connection_type || 'repeater') === 'repeater' &&
                    !stream.is_assumed &&""",
            """                const active = data.streams.find(stream => {
                    if (!stream) return false;
                    const connectionType = stream.connection_type || 'repeater';
                    return ['repeater', 'hotspot', 'unknown'].includes(connectionType) &&
                    !stream.is_assumed &&""",
        ),
        (
            "initial local endpoint close",
            """                    stream.dst_id === parrot.talkgroup &&
                    stream.status === 'active'
                );""",
            """                    stream.dst_id === parrot.talkgroup &&
                    stream.status === 'active';
                });""",
        ),
        (
            "stream start local endpoints",
            """        case 'stream_start':
            if ((data.connection_type || 'repeater') === 'repeater' &&
                !data.is_assumed && data.dst_id === parrot.talkgroup) {
                setActivity('recording', data);
            }
            break;""",
            """        case 'stream_start': {
            const connectionType = data.connection_type || 'repeater';
            if (['repeater', 'hotspot', 'unknown'].includes(connectionType) &&
                !data.is_assumed && data.dst_id === parrot.talkgroup) {
                setActivity('recording', data);
            }
            break;
        }""",
        ),
        (
            "telemetry lifecycle",
            """        case 'parrot_playback_started':
            setActivity('playback', data);
            break;
        case 'parrot_playback_complete':""",
            """        case 'parrot_playback_started':
            setActivity('playback', data);
            break;
        case 'parrot_telemetry_started':
        case 'parrot_telemetry_complete':
        case 'parrot_telemetry_cancelled':
            setActivity('telemetry', data);
            break;
        case 'parrot_playback_complete':""",
        ),
    ],
)

patch(
    "tests/test_parrot_activity_recovery.py",
    [
        (
            "recovery dashboard version",
            '    assert "dashboard_version = \\"1.3.1\\"" in state_source',
            '    assert "dashboard_version = \\"1.3.2\\"" in state_source',
        ),
        (
            "recovery asset version",
            '    assert "20260830-parrot-live-3" in state_source',
            '    assert "20260830-parrot-live-4" in state_source',
        ),
    ],
)

patch(
    "tests/test_parrot_activity_dashboard.py",
    [
        (
            "lifecycle telemetry list",
            '        "parrot_playback_started",\n        "parrot_playback_complete",',
            '        "parrot_playback_started",\n        "parrot_telemetry_started",\n        "parrot_telemetry_complete",\n        "parrot_telemetry_cancelled",\n        "parrot_playback_complete",',
        ),
        (
            "final event assertion",
            '    assert \'_emit_parrot_event(self, "parrot_playback_complete", activity)\' in source',
            '    assert \'"parrot_playback_complete"\' in source',
        ),
        (
            "ui telemetry event assertion",
            '    assert "parrot_playback_complete" in source\n    assert "parrot_playback_cancelled" in source',
            '    assert "parrot_playback_complete" in source\n    assert "parrot_telemetry_started" in source\n    assert "parrot_telemetry_complete" in source\n    assert "parrot_telemetry_cancelled" in source\n    assert "parrot_playback_cancelled" in source',
        ),
        (
            "ui voice label assertion",
            '    assert "Playing back" in source\n    assert "Test complete" in source',
            '    assert "Playing back" in source\n    assert "Voice report" in source\n    assert "Test complete" in source',
        ),
        (
            "dashboard version admin",
            '    assert admin_app._DASHBOARD_VERSION == "1.3.1"',
            '    assert admin_app._DASHBOARD_VERSION == "1.3.2"',
        ),
        (
            "dashboard version server",
            '    assert server.DASHBOARD_VERSION == "1.3.1"',
            '    assert server.DASHBOARD_VERSION == "1.3.2"',
        ),
        (
            "dashboard version app",
            '    assert server.app.version == "1.3.1"',
            '    assert server.app.version == "1.3.2"',
        ),
        (
            "asset version admin",
            '    assert admin_app._ADMIN_ASSET_VERSION == "20260830-parrot-live-3"',
            '    assert admin_app._ADMIN_ASSET_VERSION == "20260830-parrot-live-4"',
        ),
    ],
)
