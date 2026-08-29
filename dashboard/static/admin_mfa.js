(() => {
    'use strict';

    function buildMfaLoginPanel() {
        if ($('mfaLoginPanel')) return;
        const panel = document.createElement('section');
        panel.id = 'mfaLoginPanel';
        panel.className = 'card login-card hidden';
        panel.innerHTML = `
            <h2>Authenticator verification</h2>
            <p class="subtle">Enter the 6-digit code from your authenticator app.</p>
            <form id="mfaLoginForm">
                <label for="mfaLoginCode">Authenticator code</label>
                <input id="mfaLoginCode" inputmode="numeric" autocomplete="one-time-code"
                       pattern="[0-9]{6}" maxlength="6" required>
                <div class="actions" style="margin-top: 16px;">
                    <button id="mfaLoginBtn" type="submit">Verify &amp; log in</button>
                    <button id="mfaLoginCancelBtn" class="secondary" type="button">Back</button>
                </div>
            </form>`;
        $('newPasswordPanel').insertAdjacentElement('afterend', panel);
    }

    function buildMfaSecurityPanel() {
        if ($('mfaSecurityPanel')) return;
        const panel = document.createElement('section');
        panel.id = 'mfaSecurityPanel';
        panel.className = 'card hidden';
        panel.innerHTML = `
            <strong>Account security</strong>
            <p class="subtle" style="margin-bottom: 10px;">
                Authenticator-app MFA is optional. Once enabled for your Cognito account,
                you will enter a 6-digit code after your password on future logins.
            </p>
            <div id="mfaSecurityStatus" class="subtle"></div>
            <div class="actions" style="margin-top: 12px;">
                <button id="mfaSetupBtn" type="button">Set up authenticator MFA</button>
                <button id="mfaDisableBtn" class="danger hidden" type="button">Disable authenticator MFA</button>
            </div>
            <div id="mfaSetupBox" class="hidden" style="margin-top: 16px; border-top: 1px solid #334155; padding-top: 14px;">
                <p style="margin-top: 0;">
                    Add a new account in your authenticator app, then enter this secret manually.
                    The secret is shown only during setup.
                </p>
                <label for="mfaSecret">Authenticator secret</label>
                <input id="mfaSecret" readonly autocomplete="off" spellcheck="false"
                       style="font-family: ui-monospace, SFMono-Regular, Menlo, monospace;">
                <details style="margin-top: 10px;">
                    <summary class="subtle">Show otpauth URI</summary>
                    <div id="mfaOtpUri" class="match" style="margin-top: 8px; overflow-wrap: anywhere;"></div>
                </details>
                <label for="mfaVerifyCode">Enter the current 6-digit code</label>
                <input id="mfaVerifyCode" inputmode="numeric" autocomplete="one-time-code"
                       pattern="[0-9]{6}" maxlength="6">
                <div class="actions" style="margin-top: 12px;">
                    <button id="mfaVerifyBtn" type="button">Verify and enable MFA</button>
                    <button id="mfaSetupCancelBtn" class="secondary" type="button">Cancel</button>
                </div>
            </div>`;
        $('backupPanel').insertAdjacentElement('afterend', panel);
    }

    function showMfaLogin(challengeToken, message = 'Enter your authenticator code to continue.') {
        state.mfaChallengeToken = challengeToken;
        $('loginPanel').classList.add('hidden');
        $('newPasswordPanel').classList.add('hidden');
        $('resetPanel').classList.add('hidden');
        $('mfaLoginPanel').classList.remove('hidden');
        $('mfaLoginCode').value = '';
        showMessage(message, 'info');
        setTimeout(() => $('mfaLoginCode').focus(), 0);
    }

    function hideMfaLogin() {
        state.mfaChallengeToken = null;
        $('mfaLoginPanel').classList.add('hidden');
        $('mfaLoginCode').value = '';
    }

    async function refreshMfaSecurity() {
        const panel = $('mfaSecurityPanel');
        if (!panel) return;
        try {
            const result = await api('/api/admin/mfa/status');
            panel.classList.remove('hidden');
            $('mfaSecurityStatus').textContent = result.enabled
                ? 'Authenticator-app MFA is enabled for this administrator.'
                : 'Authenticator-app MFA is not enabled for this administrator.';
            $('mfaSetupBtn').classList.toggle('hidden', !!result.enabled);
            $('mfaDisableBtn').classList.toggle('hidden', !result.enabled);
            if (result.enabled) {
                $('mfaSetupBox').classList.add('hidden');
                $('mfaSecret').value = '';
                $('mfaOtpUri').textContent = '';
                $('mfaVerifyCode').value = '';
            }
        } catch (error) {
            if (error.status === 401 || error.status === 409) {
                panel.classList.add('hidden');
                return;
            }
            panel.classList.remove('hidden');
            $('mfaSecurityStatus').textContent = `MFA status unavailable: ${error.message}`;
        }
    }

    buildMfaLoginPanel();
    buildMfaSecurityPanel();
    state.mfaChallengeToken = null;

    // Replace the original login submit path in capture phase so a Cognito MFA
    // challenge is handled before the legacy bubble listener sees the response.
    $('loginForm').addEventListener('submit', event => {
        event.preventDefault();
        event.stopImmediatePropagation();
        withBusy(async () => {
            const result = await api('/api/admin/login', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ username: $('username').value, password: $('password').value })
            });
            $('password').value = '';
            if (result.challenge === 'NEW_PASSWORD_REQUIRED') {
                state.challengeToken = result.challenge_token;
                hideMfaLogin();
                $('loginPanel').classList.add('hidden');
                $('newPasswordPanel').classList.remove('hidden');
                showMessage('Your invitation is valid. Choose a permanent password to continue.', 'info');
                return;
            }
            if (result.challenge === 'SOFTWARE_TOKEN_MFA') {
                showMfaLogin(result.challenge_token);
                return;
            }
            state.csrf = result.csrf_token;
            hideMfaLogin();
            clearMessage();
            await refreshStatus();
            await refreshMfaSecurity();
        });
    }, true);

    // Do the same for the first-login password form. Normally a newly invited
    // user has not enrolled MFA yet, but handling the transition makes the flow
    // safe for accounts that later return to a Cognito password challenge.
    $('newPasswordForm').addEventListener('submit', event => {
        event.preventDefault();
        event.stopImmediatePropagation();
        withBusy(async () => {
            if ($('newPassword').value !== $('newPasswordConfirm').value) {
                throw new Error('The new passwords do not match');
            }
            const result = await api('/api/admin/new-password', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    challenge_token: state.challengeToken,
                    new_password: $('newPassword').value
                })
            });
            $('newPassword').value = '';
            $('newPasswordConfirm').value = '';
            if (result.challenge === 'SOFTWARE_TOKEN_MFA') {
                state.challengeToken = null;
                showMfaLogin(result.challenge_token);
                return;
            }
            state.challengeToken = null;
            state.csrf = result.csrf_token;
            $('newPasswordPanel').classList.add('hidden');
            clearMessage();
            await refreshStatus();
            await refreshMfaSecurity();
        });
    }, true);

    $('mfaLoginForm').addEventListener('submit', event => {
        event.preventDefault();
        withBusy(async () => {
            const result = await api('/api/admin/mfa/challenge', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    challenge_token: state.mfaChallengeToken,
                    code: $('mfaLoginCode').value.trim()
                })
            });
            state.csrf = result.csrf_token;
            hideMfaLogin();
            clearMessage();
            await refreshStatus();
            await refreshMfaSecurity();
        });
    });

    $('mfaLoginCancelBtn').addEventListener('click', () => {
        hideMfaLogin();
        $('loginPanel').classList.remove('hidden');
        clearMessage();
    });

    $('mfaSetupBtn').addEventListener('click', () => withBusy(async () => {
        const result = await api('/api/admin/mfa/setup/start', {
            method: 'POST',
            headers: { 'X-CSRF-Token': state.csrf }
        });
        $('mfaSecret').value = result.secret || '';
        $('mfaOtpUri').textContent = result.otpauth_uri || '';
        $('mfaVerifyCode').value = '';
        $('mfaSetupBox').classList.remove('hidden');
        showMessage('Authenticator setup started. Verify one 6-digit code before MFA is enabled.', 'info');
    }));

    $('mfaVerifyBtn').addEventListener('click', () => withBusy(async () => {
        const code = $('mfaVerifyCode').value.trim();
        if (!/^\d{6}$/.test(code)) throw new Error('Enter the current 6-digit authenticator code');
        await api('/api/admin/mfa/setup/verify', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': state.csrf },
            body: JSON.stringify({ code })
        });
        $('mfaSecret').value = '';
        $('mfaOtpUri').textContent = '';
        $('mfaVerifyCode').value = '';
        $('mfaSetupBox').classList.add('hidden');
        showMessage('Authenticator-app MFA is now enabled for your account.', 'success');
        await refreshMfaSecurity();
    }));

    $('mfaSetupCancelBtn').addEventListener('click', () => {
        $('mfaSecret').value = '';
        $('mfaOtpUri').textContent = '';
        $('mfaVerifyCode').value = '';
        $('mfaSetupBox').classList.add('hidden');
        clearMessage();
    });

    $('mfaDisableBtn').addEventListener('click', () => {
        if (!window.confirm('Disable authenticator-app MFA for your Cognito administrator account?')) return;
        withBusy(async () => {
            await api('/api/admin/mfa/disable', {
                method: 'POST',
                headers: { 'X-CSRF-Token': state.csrf }
            });
            showMessage('Authenticator-app MFA has been disabled for your account.', 'success');
            await refreshMfaSecurity();
        });
    });

    // Existing code calls refreshStatus from login/logout/expiry paths. Wrap it
    // so the security card follows the current session without modifying the
    // large legacy admin page script.
    const originalRefreshStatus = refreshStatus;
    refreshStatus = async function() {
        const result = await originalRefreshStatus();
        await refreshMfaSecurity();
        return result;
    };

    refreshMfaSecurity().catch(() => {});
})();
