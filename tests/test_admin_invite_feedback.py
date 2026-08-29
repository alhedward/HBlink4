from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_admin_helper_scripts_are_cache_busted_and_feedback_loads_before_identity():
    source = (ROOT / "dashboard" / "admin_app.py").read_text()

    assert 'admin_invite_feedback.js?v={version}' in source
    assert 'admin_identity.js?v={version}' in source
    assert 'admin_profile_compact.js?v={version}' in source
    assert source.index('admin_invite_feedback.js?v={version}') < source.index(
        'admin_identity.js?v={version}'
    )
    assert source.index('admin_identity.js?v={version}') < source.index(
        'admin_profile_compact.js?v={version}'
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


def test_personalised_invite_handler_clears_fields_before_success_message():
    source = (ROOT / "dashboard" / "static" / "admin_identity.js").read_text()

    success = source.index("showMessage(`Invitation sent to ${payload.email}.`, 'success');")
    for field_id in (
        "inviteGivenName",
        "inviteFamilyName",
        "inviteCallsign",
        "inviteEmail",
    ):
        clear = source.index(f"$('{field_id}').value = '';", success - 500)
        assert clear < success


def test_completed_profile_collapses_to_change_button():
    source = (ROOT / "dashboard" / "static" / "admin_profile_compact.js").read_text()

    assert "Change administrator profile" in source
    assert "function profileComplete()" in source
    assert "compact.classList.remove('hidden')" in source
    assert "details.classList.add('hidden')" in source
    assert "$('profileHint').textContent === 'Profile complete.'" in source


def test_configured_mfa_collapses_to_change_button_and_counts_passkeys():
    mfa_source = (ROOT / "dashboard" / "static" / "admin_mfa.js").read_text()
    webauthn_source = (ROOT / "dashboard" / "static" / "admin_webauthn.js").read_text()

    assert "Change MFA" in mfa_source
    assert "state.totpEnabled" in mfa_source
    assert "state.passkeyCount" in mfa_source
    assert "MFA configured:" in mfa_source
    assert "state.passkeyCount = credentials.length" in webauthn_source
    assert "window.updateAdminSecurityPanel" in webauthn_source
