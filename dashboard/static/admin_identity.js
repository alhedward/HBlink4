(() => {
    'use strict';

    state.adminProfile = null;

    function buildProfilePanel() {
        if ($('adminProfilePanel')) return;
        const panel = document.createElement('section');
        panel.id = 'adminProfilePanel';
        panel.className = 'card hidden';
        panel.innerHTML = `
            <strong>Your administrator profile</strong>
            <p class="subtle" style="margin-bottom: 10px;">
                Used for administrator identification and to sign invitation emails.
                Missing profile details can be completed here as administrative cleanup.
            </p>
            <div class="slots">
                <div>
                    <label for="profileGivenName">First name</label>
                    <input id="profileGivenName" autocomplete="given-name">
                </div>
                <div>
                    <label for="profileFamilyName">Last name</label>
                    <input id="profileFamilyName" autocomplete="family-name">
                </div>
                <div>
                    <label for="profileCallsign">Callsign</label>
                    <input id="profileCallsign" autocomplete="off" spellcheck="false" placeholder="VK2ALE">
                </div>
                <div>
                    <label for="profileEmail">Email</label>
                    <input id="profileEmail" type="email" readonly>
                </div>
            </div>
            <div class="actions" style="margin-top: 14px;">
                <button id="profileSaveBtn" type="button">Save administrator profile</button>
                <span id="profileHint" class="subtle"></span>
            </div>`;
        $('adminUsersPanel').insertAdjacentElement('beforebegin', panel);
        $('profileCallsign').addEventListener('input', () => {
            $('profileCallsign').value = $('profileCallsign').value.toUpperCase();
        });
    }

    function enhanceInviteForm() {
        if ($('inviteGivenName')) return;
        const email = $('inviteEmail');
        if (!email) return;
        const holder = email.parentElement;
        holder.querySelector('label[for="inviteEmail"]').textContent = 'Invitee email';

        const firstLabel = document.createElement('label');
        firstLabel.htmlFor = 'inviteGivenName';
        firstLabel.textContent = 'Invitee first name';
        const first = document.createElement('input');
        first.id = 'inviteGivenName';
        first.autocomplete = 'off';

        const lastLabel = document.createElement('label');
        lastLabel.htmlFor = 'inviteFamilyName';
        lastLabel.textContent = 'Invitee last name';
        const last = document.createElement('input');
        last.id = 'inviteFamilyName';
        last.autocomplete = 'off';

        const callLabel = document.createElement('label');
        callLabel.htmlFor = 'inviteCallsign';
        callLabel.textContent = 'Invitee callsign';
        const callsign = document.createElement('input');
        callsign.id = 'inviteCallsign';
        callsign.autocomplete = 'off';
        callsign.spellcheck = false;
        callsign.placeholder = 'VK2XYZ';
        callsign.addEventListener('input', () => {
            callsign.value = callsign.value.toUpperCase();
        });

        holder.insertBefore(firstLabel, holder.firstChild);
        holder.insertBefore(first, firstLabel.nextSibling);
        holder.insertBefore(lastLabel, first.nextSibling);
        holder.insertBefore(last, lastLabel.nextSibling);
        holder.insertBefore(callLabel, last.nextSibling);
        holder.insertBefore(callsign, callLabel.nextSibling);
    }

    function fillProfile(profile) {
        state.adminProfile = profile || null;
        $('profileGivenName').value = profile?.given_name || '';
        $('profileFamilyName').value = profile?.family_name || '';
        $('profileCallsign').value = profile?.callsign || '';
        $('profileEmail').value = profile?.email || '';
        const complete = !!(
            profile?.given_name && profile?.family_name && profile?.callsign && profile?.email
        );
        $('profileHint').textContent = complete
            ? 'Profile complete.'
            : 'Complete the missing fields before inviting another administrator.';
        return complete;
    }

    async function refreshProfile() {
        const panel = $('adminProfilePanel');
        if (!panel) return false;
        try {
            const result = await api('/api/admin/profile');
            panel.classList.remove('hidden');
            $('inviteBtn').classList.remove('hidden');
            fillProfile(result.profile || {});
            return !!result.complete;
        } catch (error) {
            state.adminProfile = null;
            if (error.status === 401 || error.status === 409) {
                panel.classList.add('hidden');
                if (error.status === 409) $('inviteBtn').classList.add('hidden');
                return false;
            }
            panel.classList.remove('hidden');
            $('profileHint').textContent = `Profile unavailable: ${error.message}`;
            return false;
        }
    }

    async function saveProfile() {
        const result = await api('/api/admin/profile', {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': state.csrf },
            body: JSON.stringify({
                given_name: $('profileGivenName').value.trim(),
                family_name: $('profileFamilyName').value.trim(),
                callsign: $('profileCallsign').value.trim()
            })
        });
        fillProfile(result.profile || {});
        showMessage('Administrator profile saved.', 'success');
        return result;
    }

    function enhancedUserLabel(user, currentUsername) {
        const name = [user.given_name, user.family_name].filter(Boolean).join(' ');
        const call = user.callsign ? ` (${user.callsign})` : '';
        const email = user.email || user.username;
        const you = user.username === currentUsername ? ' (you)' : '';
        const identity = name ? `${name}${call} · ${email}` : `${email}${call}`;
        return `${identity}${you} — ${user.status || 'UNKNOWN'}`;
    }

    // Replace the original renderer so stored Cognito profile fields are visible.
    renderAdminUsers = function(users, currentUsername) {
        const root = $('adminUsersList');
        root.replaceChildren();
        if (!users.length) {
            const note = document.createElement('div');
            note.className = 'subtle';
            note.textContent = 'No Cognito administrators found.';
            root.appendChild(note);
            return;
        }
        for (const user of users) {
            const row = document.createElement('div');
            row.className = 'actions';
            row.style.justifyContent = 'space-between';
            row.style.padding = '9px 0';
            row.style.borderTop = '1px solid #334155';
            const label = document.createElement('div');
            label.textContent = enhancedUserLabel(user, currentUsername);
            const button = document.createElement('button');
            button.type = 'button';
            button.className = 'secondary';
            const pending = user.status === 'FORCE_CHANGE_PASSWORD';
            button.textContent = pending ? 'Resend invite' : 'Send password reset';
            button.addEventListener('click', () => withBusy(async () => {
                const action = pending ? 'resend-invite' : 'reset-password';
                await api(`/api/admin/users/${encodeURIComponent(user.username)}/${action}`, {
                    method: 'POST',
                    headers: { 'X-CSRF-Token': state.csrf }
                });
                showMessage(pending ? 'Invitation resent.' : 'Password reset code sent.', 'success');
                await loadAdminUsers();
            }));
            row.append(label, button);
            root.appendChild(row);
        }
    };

    buildProfilePanel();
    enhanceInviteForm();

    $('profileSaveBtn').addEventListener('click', () => withBusy(saveProfile));

    // The legacy page has an email-only invitation listener. Capture the click
    // first so personalised profile fields and the SES HTML path are authoritative.
    document.addEventListener('click', event => {
        const button = event.target.closest && event.target.closest('#inviteBtn');
        if (!button) return;
        event.preventDefault();
        event.stopImmediatePropagation();
        withBusy(async () => {
            const complete = await refreshProfile();
            if (!complete) {
                $('adminProfilePanel').classList.remove('hidden');
                $('profileGivenName').focus();
                throw new Error('Complete your administrator profile before sending invitations');
            }
            const payload = {
                given_name: $('inviteGivenName').value.trim(),
                family_name: $('inviteFamilyName').value.trim(),
                callsign: $('inviteCallsign').value.trim(),
                email: $('inviteEmail').value.trim()
            };
            if (!payload.given_name || !payload.family_name || !payload.callsign || !payload.email) {
                throw new Error('Enter the invitee first name, last name, callsign and email');
            }
            await api('/api/admin/users/invite', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': state.csrf },
                body: JSON.stringify(payload)
            });
            $('inviteGivenName').value = '';
            $('inviteFamilyName').value = '';
            $('inviteCallsign').value = '';
            $('inviteEmail').value = '';
            showMessage(`Invitation sent to ${payload.email}.`, 'success');
            await loadAdminUsers();
        });
    }, true);

    // This runs after the TOTP and WebAuthn wrappers, preserving their refreshes.
    const previousRefreshStatus = refreshStatus;
    refreshStatus = async function() {
        const result = await previousRefreshStatus();
        await refreshProfile();
        return result;
    };

    refreshProfile().catch(() => {});
})();
