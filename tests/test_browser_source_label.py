from pathlib import Path

from dashboard import admin_app


def test_dashboard_injects_browser_source_label_script():
    html = admin_app.AdminIdentityMiddleware._inject_local_services_script(
        "<html><body></body></html>", "test-version"
    )
    assert "/static/local_services.js?v=test-version" in html
    assert "/static/browser_source_label.js?v=test-version" in html


def test_browser_source_label_targets_legacy_and_current_helper_ids():
    script = (
        Path(__file__).resolve().parents[1]
        / "dashboard"
        / "static"
        / "browser_source_label.js"
    ).read_text(encoding="utf-8")
    assert "16777001" in script
    assert "50525419" in script
    assert "SGARSWEB" in script
    assert "SGARS Browser DMR Local Peer" in script
    assert "cells[2].textContent" in script
    assert "radioId" in script
    assert "callsign" in script
