from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_admin_helper_scripts_are_cache_busted_and_feedback_loads_before_identity():
    source = (ROOT / "dashboard" / "admin_app.py").read_text()

    assert 'admin_invite_feedback.js?v={version}' in source
    assert 'admin_identity.js?v={version}' in source
    assert source.index('admin_invite_feedback.js?v={version}') < source.index(
        'admin_identity.js?v={version}'
    )
    assert '_ADMIN_ASSET_VERSION = ' in source


def test_invite_feedback_is_local_visible_and_resets_invitee_fields():
    source = (ROOT / "dashboard" / "static" / "admin_invite_feedback.js").read_text()

    assert "status.id = 'inviteStatus'" in source
    assert "status.setAttribute('aria-live', 'polite')" in source
    assert "button.textContent = 'Sending…'" in source
    assert "setInviteStatus('Sending invitation…')" in source
    assert "inviteButton.textContent = 'Send another invitation'" in source
    for field_id in (
        "inviteGivenName",
        "inviteFamilyName",
        "inviteCallsign",
        "inviteEmail",
    ):
        assert field_id in source


def test_personalised_invite_handler_also_clears_fields_after_success():
    source = (ROOT / "dashboard" / "static" / "admin_identity.js").read_text()

    success = source.index("showMessage(`Invitation sent to ${payload.email}.`, 'success');")
    for field_id in (
        "inviteGivenName",
        "inviteFamilyName",
        "inviteCallsign",
        "inviteEmail",
    ):
        clear = source.index(f"$('${field_id}').value = '';", success - 500)
        assert clear < success
