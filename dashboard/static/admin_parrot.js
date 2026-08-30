(() => {
    'use strict';

    const PANEL_ID = 'parrotVoiceTelemetryPanel';
    let status = null;
    let busy = false;

    async function request(url, options = {}) {
        const response = await fetch(url, { credentials: 'same-origin', cache: 'no-store', ...options });
        let body = {};
        try { body = await response.json(); } catch (_) {}
        if (!response.ok) {
            const error = new Error(body.detail || `Request failed (${response.status})`);
            error.status = response.status;
            throw error;
        }
        return body;
    }

    function panel() {
        return document.getElementById(PANEL_ID);
    }

    function setPanelMessage(text, kind = '') {
        const root = panel();
        if (!root) return;
        const message = root.querySelector('[data-parrot-message]');
        message.textContent = text;
        message.style.color = kind === 'error' ? '#fca5a5' : (kind === 'success' ? '#86efac' : '#cbd5e1');
    }

    function setButtonsDisabled(disabled) {
        const root = panel();
        if (!root) return;
        root.querySelectorAll('button').forEach(button => { button.disabled = disabled; });
    }

    function renderStatus() {
        const root = panel();
        if (!root || !status) return;
        const badge = root.querySelector('[data-parrot-state]');
        const details = root.querySelector('[data-parrot-details]');
        badge.textContent = status.voice_telemetry_enabled ? 'Enabled' : 'Disabled';
        badge.style.fontWeight = '700';
        details.textContent = `TG${status.talkgroup} · source ${status.voice_telemetry_source_id} · ${status.voice_telemetry_pause_seconds}s post-echo pause`;
        root.querySelector('[data-enable]').disabled = busy || status.voice_telemetry_enabled;
        root.querySelector('[data-disable]').disabled = busy || !status.voice_telemetry_enabled;
    }

    function ensurePanel() {
        const editor = document.getElementById('editorPanel');
        if (!editor || panel()) return;

        const root = document.createElement('section');
        root.id = PANEL_ID;
        root.className = 'card';
        root.innerHTML = `
            <strong>Local parrot voice telemetry</strong>
            <p class="subtle">Controls the spoken BER / RSSI / timeslot report after the TG9990 echo. The ordinary parrot echo is independent of this setting.</p>
            <p><strong>Status:</strong> <span data-parrot-state>Loading…</span></p>
            <p class="subtle" data-parrot-details></p>
            <div class="actions">
                <button type="button" data-enable>Enable &amp; restart</button>
                <button type="button" class="danger" data-disable>Disable &amp; restart</button>
            </div>
            <p class="subtle" data-parrot-message style="margin-bottom:0;margin-top:12px;">A confirmed full-config backup is required before this switch can be changed.</p>
        `;

        const patterns = document.getElementById('patterns');
        editor.insertBefore(root, patterns || editor.firstChild);
        root.querySelector('[data-enable]').addEventListener('click', () => applySetting(true));
        root.querySelector('[data-disable]').addEventListener('click', () => applySetting(false));
    }

    async function loadStatus() {
        ensurePanel();
        const root = panel();
        const editor = document.getElementById('editorPanel');
        if (!root || !editor || editor.classList.contains('hidden')) return;

        try {
            status = await request('/api/admin/local-services/parrot/voice-telemetry');
            renderStatus();
            setPanelMessage('Changing this setting writes only the voice telemetry flag and requires an HBlink4 restart.');
        } catch (error) {
            if (error.status === 401) return;
            setPanelMessage(error.message, 'error');
        }
    }

    async function applySetting(enabled) {
        if (busy || !status) return;
        busy = true;
        setButtonsDisabled(true);
        try {
            const admin = await request('/api/admin/status');
            if (!admin.authenticated) throw new Error('Administrator login required');
            if (!admin.config_backup_ready) throw new Error('Download and confirm the current full configuration backup first');
            if (!admin.csrf_token) throw new Error('Administrator CSRF token is unavailable');

            setPanelMessage(`${enabled ? 'Enabling' : 'Disabling'} voice telemetry…`);
            const saved = await request('/api/admin/local-services/parrot/voice-telemetry', {
                method: 'PUT',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRF-Token': admin.csrf_token
                },
                body: JSON.stringify({ revision: status.revision, enabled })
            });
            status = saved.configuration;
            renderStatus();

            if (!admin.restart_enabled) {
                setPanelMessage('Setting saved. HBlink4 restart is required but restart is disabled in dashboard configuration.', 'success');
                return;
            }

            setPanelMessage('Setting saved. Restarting HBlink4…');
            const restarted = await request('/api/admin/restart', {
                method: 'POST',
                headers: { 'X-CSRF-Token': admin.csrf_token }
            });
            setPanelMessage(`Voice telemetry ${enabled ? 'enabled' : 'disabled'}; HBlink4 is ${restarted.status}.`, 'success');
            await loadStatus();
        } catch (error) {
            setPanelMessage(error.message, 'error');
            if (error.status === 409) await loadStatus();
        } finally {
            busy = false;
            renderStatus();
        }
    }

    function watchEditor() {
        ensurePanel();
        const editor = document.getElementById('editorPanel');
        if (!editor) return;
        const observer = new MutationObserver(() => {
            if (!editor.classList.contains('hidden')) loadStatus();
        });
        observer.observe(editor, { attributes: true, attributeFilter: ['class'] });
        if (!editor.classList.contains('hidden')) loadStatus();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', watchEditor, { once: true });
    } else {
        watchEditor();
    }
})();
