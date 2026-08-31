(() => {
    'use strict';

    const CARD_ID = 'parrotVoicePanel';
    let current = null;
    let loading = false;

    function installStyles() {
        if (document.getElementById('parrotVoiceStyles')) return;
        const style = document.createElement('style');
        style.id = 'parrotVoiceStyles';
        style.textContent = `
            .parrot-voice-row { display:flex; gap:14px; align-items:center; justify-content:space-between; flex-wrap:wrap; }
            .parrot-voice-switch { display:inline-flex; gap:10px; align-items:center; margin:0; cursor:pointer; font-weight:650; }
            .parrot-voice-switch input { width:20px; height:20px; margin:0; accent-color:#0284c7; }
            .parrot-voice-level { display:flex; gap:10px; align-items:center; flex-wrap:wrap; margin-top:14px; }
            .parrot-voice-level input { width:90px; }
            .parrot-voice-status { margin-top:10px; color:#94a3b8; }
        `;
        document.head.appendChild(style);
    }

    function ensureCard() {
        const editor = document.getElementById('editorPanel');
        const patterns = document.getElementById('patterns');
        if (!editor || !patterns) return null;
        let card = document.getElementById(CARD_ID);
        if (card) return card;

        installStyles();
        card = document.createElement('section');
        card.id = CARD_ID;
        card.className = 'card';
        card.innerHTML = `
            <div class="parrot-voice-row">
                <div>
                    <strong>Parrot voice telemetry</strong>
                    <p class="subtle" style="margin:6px 0 0;">
                        Spoken BER, RSSI and timeslot report after the TG9990 echo. The report now uses PCM gain followed by live OpenDMR AMBE encoding.
                    </p>
                </div>
                <label class="parrot-voice-switch" for="parrotVoiceEnabled">
                    <input id="parrotVoiceEnabled" type="checkbox">
                    <span id="parrotVoiceLabel">Voice report</span>
                </label>
            </div>
            <div class="parrot-voice-level">
                <label for="parrotVoiceAttenuation"><strong>Voice report attenuation</strong></label>
                <input id="parrotVoiceAttenuation" type="number" min="0" max="30" step="0.5" inputmode="decimal" value="6">
                <span id="parrotVoiceAttenuationLabel">-6 dB</span>
            </div>
            <p class="subtle" style="margin:6px 0 0;">0 dB is unchanged PCM level. Higher attenuation values make only the generated voice report quieter; the echoed caller audio is untouched.</p>
            <div id="parrotVoiceStatus" class="parrot-voice-status">Loading current setting…</div>
            <div class="actions" style="margin-top:14px;">
                <button id="parrotVoiceApplyBtn" type="button">Apply &amp; restart HBlink4</button>
                <button id="parrotVoiceRefreshBtn" class="secondary" type="button">Refresh</button>
            </div>
        `;
        editor.insertBefore(card, patterns);

        card.querySelector('#parrotVoiceApplyBtn').addEventListener('click', applySetting);
        card.querySelector('#parrotVoiceRefreshBtn').addEventListener('click', () => loadSetting(true));
        card.querySelector('#parrotVoiceEnabled').addEventListener('change', renderDraft);
        card.querySelector('#parrotVoiceAttenuation').addEventListener('input', renderDraft);
        return card;
    }

    function attenuationValue() {
        const input = document.getElementById('parrotVoiceAttenuation');
        const value = Number(input?.value);
        return Number.isFinite(value) ? value : NaN;
    }

    function renderDraft() {
        const toggle = document.getElementById('parrotVoiceEnabled');
        const label = document.getElementById('parrotVoiceLabel');
        const level = document.getElementById('parrotVoiceAttenuationLabel');
        if (toggle && label) label.textContent = toggle.checked ? 'Voice report ON' : 'Voice report OFF';
        const db = attenuationValue();
        if (level) level.textContent = Number.isFinite(db) ? `${db === 0 ? '0' : `-${db}`} dB` : 'Invalid dB';
    }

    function renderCurrent() {
        const toggle = document.getElementById('parrotVoiceEnabled');
        const attenuation = document.getElementById('parrotVoiceAttenuation');
        const status = document.getElementById('parrotVoiceStatus');
        const apply = document.getElementById('parrotVoiceApplyBtn');
        if (!toggle || !attenuation || !status || !apply) return;

        if (!current) {
            toggle.disabled = true;
            attenuation.disabled = true;
            apply.disabled = true;
            status.textContent = 'Current setting unavailable.';
            return;
        }

        toggle.disabled = !current.parrot_enabled;
        attenuation.disabled = !current.parrot_enabled;
        toggle.checked = !!current.voice_telemetry_enabled;
        attenuation.value = String(Number(current.voice_telemetry_attenuation_db ?? 6));
        apply.disabled = !current.parrot_enabled;
        renderDraft();
        const stateText = current.voice_telemetry_enabled ? 'ON' : 'OFF';
        const db = Number(current.voice_telemetry_attenuation_db ?? 6);
        status.textContent = `Current: ${stateText} · report ${db === 0 ? '0' : `-${db}`} dB · TG ${current.talkgroup} · source ID ${current.voice_telemetry_source_id} · ${current.voice_telemetry_pause_seconds}s pause`;
        if (!current.parrot_enabled) status.textContent += ' · Parrot service is disabled';
    }

    async function loadSetting(showErrors = false) {
        if (loading) return;
        const editor = document.getElementById('editorPanel');
        if (!editor || editor.classList.contains('hidden')) return;
        ensureCard();
        loading = true;
        const status = document.getElementById('parrotVoiceStatus');
        try {
            if (status) status.textContent = 'Loading current setting…';
            current = await api('/api/admin/parrot-voice');
            renderCurrent();
        } catch (error) {
            current = null;
            if (status) {
                status.textContent = error.status === 428
                    ? 'Download and confirm the current config backup before changing this setting.'
                    : `Could not load parrot voice setting: ${error.message}`;
            }
            if (showErrors && error.status !== 428) showMessage(error.message, 'error');
        } finally {
            loading = false;
        }
    }

    async function applySetting() {
        if (!current) {
            await loadSetting(true);
            if (!current) return;
        }
        const toggle = document.getElementById('parrotVoiceEnabled');
        const button = document.getElementById('parrotVoiceApplyBtn');
        const attenuation = attenuationValue();
        if (!toggle || !button) return;
        if (!Number.isFinite(attenuation) || attenuation < 0 || attenuation > 30) {
            showMessage('Voice report attenuation must be between 0 and 30 dB.', 'error');
            return;
        }
        const enabled = !!toggle.checked;
        if (!window.confirm(`Apply voice report ${enabled ? 'ON' : 'OFF'} at ${attenuation === 0 ? '0' : `-${attenuation}`} dB and restart HBlink4?`)) {
            renderCurrent();
            return;
        }

        button.disabled = true;
        try {
            await withBusy(async () => {
                const result = await api('/api/admin/parrot-voice', {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': state.csrf },
                    body: JSON.stringify({
                        revision: current.revision,
                        voice_telemetry_enabled: enabled,
                        voice_telemetry_attenuation_db: attenuation
                    })
                });
                current = result.configuration;
                renderCurrent();
                showMessage('Voice telemetry settings saved. Restarting HBlink4…', 'info');
                const restart = await api('/api/admin/restart', {
                    method: 'POST',
                    headers: { 'X-CSRF-Token': state.csrf }
                });
                showMessage(`Parrot voice report updated; HBlink4 restarted successfully (${restart.status}).`, 'success');
                await loadSetting(false);
            });
        } finally {
            button.disabled = false;
        }
    }

    function watchEditor() {
        const editor = document.getElementById('editorPanel');
        if (!editor) return;
        ensureCard();
        const observer = new MutationObserver(() => {
            if (!editor.classList.contains('hidden')) loadSetting(false);
        });
        observer.observe(editor, { attributes: true, attributeFilter: ['class'] });
        if (!editor.classList.contains('hidden')) loadSetting(false);
    }

    watchEditor();
})();
