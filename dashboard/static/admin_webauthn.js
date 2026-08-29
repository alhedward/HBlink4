(() => {
    'use strict';

    const supported = !!(window.PublicKeyCredential && navigator.credentials);

    function b64urlToBytes(value) {
        const normalized = String(value).replace(/-/g, '+').replace(/_/g, '/');
        const padded = normalized + '='.repeat((4 - normalized.length % 4) % 4);
        const binary = atob(padded);
        const bytes = new Uint8Array(binary.length);
        for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
        return bytes;
    }

    function bytesToB64url(value) {
        if (value === null || value === undefined) return null;
        const bytes = new Uint8Array(value);
        let binary = '';
        for (const byte of bytes) binary += String.fromCharCode(byte);
        return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/g, '');
    }

    function requestOptions(raw) {
        const options = JSON.parse(JSON.stringify(raw || {}));
        if (options.challenge) options.challenge = b64urlToBytes(options.challenge);
        if (Array.isArray(options.allowCredentials)) {
            options.allowCredentials = options.allowCredentials.map(item => ({
                ...item,
                id: b64urlToBytes(item.id)
            }));
        }
        return options;
    }

    function creationOptions(raw) {
        const options = JSON.parse(JSON.stringify(raw || {}));
        if (options.challenge) options.challenge = b64urlToBytes(options.challenge);
        if (options.user && options.user.id) options.user.id = b64urlToBytes(options.user.id);
        if (Array.isArray(options.excludeCredentials)) {
            options.excludeCredentials = options.excludeCredentials.map(item => ({
                ...item,
                id: b64urlToBytes(item.id)
            }));
        }
        return options;
    }

    function credentialJSON(credential) {
        if (credential && typeof credential.toJSON === 'function') return credential.toJSON();
        if (!credential) return null;
        const response = credential.response || {};
        const result = {
            id: credential.id,
            rawId: bytesToB64url(credential.rawId),
            type: credential.type,
            authenticatorAttachment: credential.authenticatorAttachment || undefined,
            clientExtensionResults: credential.getClientExtensionResults
                ? credential.getClientExtensionResults()
                : {},
            response: {
                clientDataJSON: bytesToB64url(response.clientDataJSON)
            }
        };
        if (response.attestationObject) {
            result.response.attestationObject = bytesToB64url(response.attestationObject);
            if (typeof response.getTransports === 'function') {
                result.response.transports = response.getTransports();
            }
        }
        if (response.authenticatorData) {
            result.response.authenticatorData = bytesToB64url(response.authenticatorData);
            result.response.signature = bytesToB64url(response.signature);
            result.response.userHandle = response.userHandle
                ? bytesToB64url(response.userHandle)
                : null;
        }
        return result;
    }

    function buildLoginButton() {
        if ($('passkeyLoginBtn')) return;
        const actions = $('loginBtn').parentElement;
        const button = document.createElement('button');
        button.id = 'passkeyLoginBtn';
        button.type = 'button';
        button.className = 'secondary';
        button.textContent = 'Sign in with security key / passkey';
        if (!supported) {
            button.disabled = true;
            button.title = 'This browser does not support WebAuthn';
        }
        actions.appendChild(button);
    }

    function buildSecurityPanel() {
        const parent = $('mfaSecurityPanel');
        if (!parent || $('passkeySecurityBox')) return;
        const box = document.createElement('div');
        box.id = 'passkeySecurityBox';
        box.style.marginTop = '18px';
        box.style.borderTop = '1px solid #334155';
        box.style.paddingTop = '14px';
        box.innerHTML = `
            <strong>Security keys &amp; passkeys (FIDO2/WebAuthn)</strong>
            <p class="subtle" style="margin-bottom: 10px;">
                Optional. Register a YubiKey, another FIDO2 security key, or a platform passkey.
                User verification is required, so a successful passkey sign-in can satisfy the
                Cognito MFA requirement without sending the private key to HBlink4.
            </p>
            <div id="passkeyStatus" class="subtle"></div>
            <div class="actions" style="margin-top: 12px;">
                <button id="passkeyRegisterBtn" type="button">Register security key / passkey</button>
            </div>
            <div id="passkeyList" style="margin-top: 12px;"></div>`;
        parent.appendChild(box);
        if (!supported) {
            $('passkeyRegisterBtn').disabled = true;
            $('passkeyStatus').textContent = 'WebAuthn is not supported by this browser.';
        }
    }

    async function signInWithPasskey() {
        if (!supported) throw new Error('This browser does not support WebAuthn');
        const username = $('username').value.trim();
        if (!username) throw new Error('Enter your administrator username or email first');
        const start = await api('/api/admin/webauthn/login/start', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ username })
        });
        showMessage('Use your security key or passkey to continue…', 'info');
        let credential;
        try {
            credential = await navigator.credentials.get({ publicKey: requestOptions(start.public_key) });
        } catch (error) {
            if (error && error.name === 'NotAllowedError') {
                throw new Error('Security-key / passkey sign-in was cancelled or timed out');
            }
            throw error;
        }
        const result = await api('/api/admin/webauthn/login/complete', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                challenge_token: start.challenge_token,
                credential: credentialJSON(credential)
            })
        });
        state.csrf = result.csrf_token;
        clearMessage();
        await refreshStatus();
    }

    function renderCredentials(credentials) {
        const root = $('passkeyList');
        if (!root) return;
        root.replaceChildren();
        if (!credentials.length) {
            const note = document.createElement('div');
            note.className = 'subtle';
            note.textContent = 'No security keys or passkeys are registered for this account.';
            root.appendChild(note);
            return;
        }
        for (const item of credentials) {
            const row = document.createElement('div');
            row.className = 'actions';
            row.style.justifyContent = 'space-between';
            row.style.padding = '8px 0';
            row.style.borderTop = '1px solid #334155';
            const label = document.createElement('div');
            const attachment = item.attachment ? ` · ${item.attachment}` : '';
            label.textContent = `${item.name || 'Security key / passkey'}${attachment}`;
            const remove = document.createElement('button');
            remove.type = 'button';
            remove.className = 'danger';
            remove.textContent = 'Remove';
            remove.addEventListener('click', () => {
                if (!window.confirm('Remove this security key / passkey from your administrator account?')) return;
                withBusy(async () => {
                    await api('/api/admin/webauthn/credentials/delete', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': state.csrf },
                        body: JSON.stringify({ credential_id: item.credential_id })
                    });
                    showMessage('Security key / passkey removed.', 'success');
                    await refreshPasskeys();
                });
            });
            row.append(label, remove);
            root.appendChild(row);
        }
    }

    async function refreshPasskeys() {
        const box = $('passkeySecurityBox');
        if (!box) return;
        if (!supported) {
            box.classList.remove('hidden');
            return;
        }
        try {
            const result = await api('/api/admin/webauthn/credentials');
            box.classList.remove('hidden');
            const credentials = result.credentials || [];
            $('passkeyStatus').textContent = credentials.length
                ? `${credentials.length} security key / passkey credential${credentials.length === 1 ? '' : 's'} registered.`
                : 'No security key / passkey is registered yet.';
            renderCredentials(credentials);
        } catch (error) {
            if (error.status === 401 || error.status === 409) {
                box.classList.add('hidden');
                return;
            }
            box.classList.remove('hidden');
            $('passkeyStatus').textContent = `Security-key status unavailable: ${error.message}`;
        }
    }

    async function registerPasskey() {
        if (!supported) throw new Error('This browser does not support WebAuthn');
        const start = await api('/api/admin/webauthn/register/start', {
            method: 'POST',
            headers: { 'X-CSRF-Token': state.csrf }
        });
        showMessage('Touch or unlock the security key / passkey you want to register…', 'info');
        let credential;
        try {
            credential = await navigator.credentials.create({ publicKey: creationOptions(start.public_key) });
        } catch (error) {
            if (error && error.name === 'NotAllowedError') {
                throw new Error('Security-key / passkey registration was cancelled or timed out');
            }
            throw error;
        }
        await api('/api/admin/webauthn/register/complete', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': state.csrf },
            body: JSON.stringify({ credential: credentialJSON(credential) })
        });
        showMessage('Security key / passkey registered successfully.', 'success');
        await refreshPasskeys();
    }

    buildLoginButton();
    buildSecurityPanel();

    $('passkeyLoginBtn').addEventListener('click', () => withBusy(signInWithPasskey));
    $('passkeyRegisterBtn').addEventListener('click', () => withBusy(registerPasskey));

    // admin_mfa.js already wraps refreshStatus. Wrap the current function again
    // so passkey controls follow whichever Cognito/local session is active.
    const previousRefreshStatus = refreshStatus;
    refreshStatus = async function() {
        const result = await previousRefreshStatus();
        await refreshPasskeys();
        return result;
    };

    refreshPasskeys().catch(() => {});
})();
