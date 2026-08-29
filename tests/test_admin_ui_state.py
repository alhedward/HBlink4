from pathlib import Path


def test_authenticated_refresh_hides_auth_only_panels():
    html = (Path(__file__).parents[1] / "dashboard" / "static" / "admin.html").read_text()

    authenticated_start = html.index("        state.csrf = status.csrf_token;")
    backup_gate = html.index("        if (!status.config_backup_ready) {", authenticated_start)
    authenticated_block = html[authenticated_start:backup_gate]

    assert "$('newPasswordPanel').classList.add('hidden');" in authenticated_block
    assert "$('resetPanel').classList.add('hidden');" in authenticated_block
