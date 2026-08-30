(() => {
    'use strict';

    const SERVICE_ID = 'hblinkLocalServices';
    let parrot = null;
    let activity = null;
    let serviceSocket = null;
    let sharedSocket = null;
    let reconnectTimer = null;
    let badgeResetTimer = null;
    const repeaters = new Map();
    const radios = new Map();

    function parrotService(services) {
        return (Array.isArray(services) ? services : []).find(service =>
            service && service.type === 'parrot' && Number.isInteger(service.talkgroup)
        );
    }

    function rememberRepeaters(items) {
        (Array.isArray(items) ? items : []).forEach(item => {
            if (item && Number.isInteger(item.repeater_id)) {
                repeaters.set(item.repeater_id, item.callsign || '');
            }
        });
    }

    function rememberLastHeard(items) {
        (Array.isArray(items) ? items : []).forEach(item => {
            if (item && Number.isInteger(item.radio_id) && item.callsign) {
                radios.set(item.radio_id, item.callsign);
            }
        });
    }

    function repeaterLabel(id) {
        const callsign = repeaters.get(id);
        return callsign ? `${callsign} (${id})` : String(id || 'unknown');
    }

    function radioLabel(id) {
        const callsign = radios.get(id);
        return callsign ? `${callsign} (${id})` : `Radio ${id || 'unknown'}`;
    }

    function formatQuality(quality) {
        if (!quality || typeof quality !== 'object') return 'RF quality: waiting for samples';
        const parts = [];
        if (Number.isFinite(quality.rssi_average_dbm)) {
            let rssi = `RSSI ${quality.rssi_average_dbm} dBm avg`;
            if (Number.isFinite(quality.rssi_min_dbm) && Number.isFinite(quality.rssi_max_dbm)) {
                rssi += ` (${quality.rssi_min_dbm} to ${quality.rssi_max_dbm})`;
            }
            parts.push(rssi);
        }
        if (Number.isFinite(quality.ber_average_percent)) {
            let ber = `BER ${quality.ber_average_percent}% avg`;
            if (Number.isFinite(quality.ber_peak_percent)) {
                ber += ` / ${quality.ber_peak_percent}% peak`;
            }
            parts.push(ber);
        }
        return parts.length ? parts.join(' · ') : 'RF quality: endpoint supplied no samples';
    }

    function phaseLabel(phase) {
        switch (phase) {
        case 'recording': return '🟡 Recording';
        case 'preparing': return '🟠 Preparing playback';
        case 'playback': return '🔵 Playing back';
        case 'telemetry': return '🟣 Voice report';
        case 'complete': return '✅ Test complete';
        case 'cancelled': return '⚠️ Test cancelled';
        default: return '🟢 Ready';
        }
    }

    function renderDashboardBadge(parrotConfig) {
        const badges = document.querySelector('.status-badges');
        if (!badges) return;
        let badge = document.getElementById(`${SERVICE_ID}Badge`);
        if (!badge) {
            badge = document.createElement('div');
            badge.id = `${SERVICE_ID}Badge`;
            badge.className = 'connection-status connected';
            badges.insertBefore(badge, badges.firstChild);
        }
        badge.textContent = `🦜 Parrot TG${parrotConfig.talkgroup}`;
        badge.title = `${parrotConfig.name || 'Parrot / Echo Test'} — local-only echo service; not routed to other repeaters or external DMR networks.`;
    }

    function activityMarkup(prefix) {
        return `
            <div id="${SERVICE_ID}${prefix}Status" style="font-weight:700; margin-top:10px;">🟢 Ready</div>
            <div id="${SERVICE_ID}${prefix}Who" style="margin-top:4px;"></div>
            <div id="${SERVICE_ID}${prefix}Metrics" style="color:#94a3b8; font-size:12px; margin-top:4px; line-height:1.45;">Waiting for the next parrot test.</div>
        `;
    }

    function renderDashboardCard(parrotConfig) {
        const stats = document.querySelector('.stats-bar');
        if (!stats || document.getElementById(`${SERVICE_ID}PublicCard`)) return;

        const card = document.createElement('section');
        card.id = `${SERVICE_ID}PublicCard`;
        card.className = 'card';
        card.innerHTML = `
            <h2>Local DMR Services</h2>
            <div style="display:flex; align-items:flex-start; gap:10px; flex-wrap:wrap;">
                <div class="connection-status connected" style="flex:none;">🦜 TG${parrotConfig.talkgroup}</div>
                <div style="min-width:260px; flex:1;">
                    <strong>${parrotConfig.name || 'Parrot / Echo Test'}</strong>
                    <div style="color:#94a3b8; font-size:12px; margin-top:4px; line-height:1.45;">Local-only echo test. Key TG${parrotConfig.talkgroup}, speak, then unkey; the completed call is played back only to the originating repeater or hotspot. TG${parrotConfig.talkgroup} is intentionally not in the routed talkgroup lists and is not sent to other repeaters or external DMR networks.</div>
                    ${activityMarkup('Public')}
                </div>
            </div>
        `;
        stats.insertAdjacentElement('afterend', card);
    }

    function renderAdminCard(parrotConfig) {
        const editor = document.getElementById('editorPanel');
        if (!editor || document.getElementById(`${SERVICE_ID}AdminCard`)) return;

        const card = document.createElement('section');
        card.id = `${SERVICE_ID}AdminCard`;
        card.className = 'card';
        card.innerHTML = `
            <strong>Local DMR services</strong>
            <p style="margin: 10px 0 4px;"><strong>🦜 TG${parrotConfig.talkgroup} — ${parrotConfig.name || 'Parrot / Echo Test'}</strong></p>
            <p class="subtle" style="margin:0;">Local-only echo service. A completed call is recorded and played back only to the originating repeater or hotspot. TG${parrotConfig.talkgroup} is intentionally excluded from ordinary repeater and external-network routing.</p>
            ${activityMarkup('Admin')}
        `;
        editor.insertBefore(card, editor.firstChild);
    }

    function normaliseActivityData(data) {
        if (!data || typeof data !== 'object') return {};
        const normalised = { ...data };
        if (!Number.isInteger(normalised.src_id) && Number.isInteger(normalised.rf_src)) {
            normalised.src_id = normalised.rf_src;
        }
        if (!Number.isInteger(normalised.packet_count) && Number.isInteger(normalised.packets)) {
            normalised.packet_count = normalised.packets;
        }
        return normalised;
    }

    function updateActivityElements(prefix) {
        const status = document.getElementById(`${SERVICE_ID}${prefix}Status`);
        const who = document.getElementById(`${SERVICE_ID}${prefix}Who`);
        const metrics = document.getElementById(`${SERVICE_ID}${prefix}Metrics`);
        if (!status || !who || !metrics) return;

        if (!activity) {
            status.textContent = '🟢 Ready';
            who.textContent = '';
            metrics.textContent = 'Waiting for the next parrot test.';
            return;
        }

        status.textContent = phaseLabel(activity.phase);
        const details = [];
        if (Number.isInteger(activity.src_id)) details.push(radioLabel(activity.src_id));
        if (Number.isInteger(activity.slot)) details.push(`TS${activity.slot}`);
        if (Number.isInteger(activity.repeater_id)) details.push(repeaterLabel(activity.repeater_id));
        who.textContent = details.join(' · ');

        const measurements = [];
        if (Number.isFinite(activity.duration)) measurements.push(`${activity.duration.toFixed(1)} s`);
        if (Number.isInteger(activity.packet_count)) measurements.push(`${activity.packet_count} packets`);
        measurements.push(formatQuality(activity.rf_quality));
        if (activity.reason) measurements.push(activity.reason);
        metrics.textContent = measurements.join(' · ');
    }

    function renderActivity() {
        updateActivityElements('Public');
        updateActivityElements('Admin');
        if (!parrot) return;
        const badge = document.getElementById(`${SERVICE_ID}Badge`);
        if (!badge) return;
        if (!activity || activity.phase === 'complete' || activity.phase === 'cancelled') {
            badge.textContent = `🦜 Parrot TG${parrot.talkgroup}`;
        } else {
            badge.textContent = `${phaseLabel(activity.phase)} · TG${parrot.talkgroup}`;
        }
    }

    function setActivity(phase, data = {}) {
        if (badgeResetTimer) {
            clearTimeout(badgeResetTimer);
            badgeResetTimer = null;
        }
        activity = { ...(activity || {}), ...normaliseActivityData(data), phase };
        renderActivity();

        if (phase === 'complete' || phase === 'cancelled') {
            badgeResetTimer = setTimeout(() => {
                activity = null;
                renderActivity();
            }, 8000);
        }
    }

    function matchesCurrent(data) {
        if (!activity || !data) return false;
        if (activity.stream_id && data.stream_id && activity.stream_id !== data.stream_id) return false;
        if (Number.isInteger(activity.repeater_id) && Number.isInteger(data.repeater_id) && activity.repeater_id !== data.repeater_id) return false;
        if (Number.isInteger(activity.slot) && Number.isInteger(data.slot) && activity.slot !== data.slot) return false;
        return true;
    }

    function handleDashboardEvent(event) {
        if (!event || typeof event !== 'object') return;
        const data = event.data || {};

        if (event.type === 'initial_state') {
            rememberRepeaters(data.repeaters);
            rememberLastHeard(data.last_heard);
            if (parrot && Array.isArray(data.streams)) {
                const active = data.streams.find(stream => {
                    if (!stream) return false;
                    const connectionType = stream.connection_type || 'repeater';
                    return ['repeater', 'hotspot', 'unknown'].includes(connectionType) &&
                    !stream.is_assumed &&
                    stream.dst_id === parrot.talkgroup &&
                    stream.status === 'active';
                });
                if (active) setActivity('recording', active);
            }
            return;
        }

        if (event.type === 'repeater_connected') {
            rememberRepeaters([data]);
        }
        if (Array.isArray(event.last_heard)) {
            rememberLastHeard(event.last_heard);
        }

        if (!parrot) return;

        switch (event.type) {
        case 'stream_start': {
            const connectionType = data.connection_type || 'repeater';
            if (['repeater', 'hotspot', 'unknown'].includes(connectionType) &&
                !data.is_assumed && data.dst_id === parrot.talkgroup) {
                setActivity('recording', data);
            }
            break;
        }
        case 'stream_update':
            if (activity && activity.phase === 'recording' &&
                data.dst_id === parrot.talkgroup && matchesCurrent(data)) {
                activity = {
                    ...activity,
                    ...normaliseActivityData(data),
                    phase: 'recording'
                };
                renderActivity();
            }
            break;
        case 'stream_end':
            if (activity && activity.phase === 'recording' &&
                data.dst_id === parrot.talkgroup && matchesCurrent(data)) {
                setActivity('preparing', data);
            }
            break;
        case 'parrot_recording_started':
            setActivity('recording', data);
            break;
        case 'parrot_recording_complete':
            setActivity('preparing', data);
            break;
        case 'parrot_playback_started':
            setActivity('playback', data);
            break;
        case 'parrot_telemetry_started':
        case 'parrot_telemetry_complete':
        case 'parrot_telemetry_cancelled':
            setActivity('telemetry', data);
            break;
        case 'parrot_playback_complete':
            setActivity('complete', data);
            break;
        case 'parrot_recording_discarded':
        case 'parrot_playback_cancelled':
            setActivity('cancelled', data);
            break;
        default:
            break;
        }
    }

    function handleSocketMessage(message) {
        if (!message || message.data === 'pong') return;
        try {
            handleDashboardEvent(JSON.parse(message.data));
        } catch (_) {
            // Ignore malformed informational events; the main dashboard remains independent.
        }
    }

    function scheduleReconnect() {
        if (reconnectTimer) clearTimeout(reconnectTimer);
        reconnectTimer = setTimeout(connectActivitySocket, 3000);
    }

    function sharedSocketClosed() {
        if (sharedSocket) {
            sharedSocket.removeEventListener('message', handleSocketMessage);
            sharedSocket.removeEventListener('close', sharedSocketClosed);
        }
        sharedSocket = null;
        scheduleReconnect();
    }

    function attachSharedDashboardSocket() {
        try {
            // dashboard.html already owns a proven /ws connection. Reuse it so
            // Local DMR Services cannot silently diverge behind a second socket.
            if (typeof ws === 'undefined' || !(ws instanceof WebSocket)) return false;
            if (sharedSocket === ws) return true;
            if (sharedSocket) {
                sharedSocket.removeEventListener('message', handleSocketMessage);
                sharedSocket.removeEventListener('close', sharedSocketClosed);
            }
            sharedSocket = ws;
            sharedSocket.addEventListener('message', handleSocketMessage);
            sharedSocket.addEventListener('close', sharedSocketClosed);
            return true;
        } catch (_) {
            sharedSocket = null;
            return false;
        }
    }

    function connectActivitySocket() {
        if (attachSharedDashboardSocket()) return;
        if (serviceSocket && (serviceSocket.readyState === WebSocket.OPEN || serviceSocket.readyState === WebSocket.CONNECTING)) return;
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        serviceSocket = new WebSocket(`${protocol}//${window.location.host}/ws`);
        serviceSocket.onmessage = handleSocketMessage;
        serviceSocket.onclose = () => {
            serviceSocket = null;
            scheduleReconnect();
        };
        serviceSocket.onerror = () => {
            try { serviceSocket.close(); } catch (_) {}
        };
    }

    async function loadLocalServices() {
        try {
            const response = await fetch('/api/local-services', {
                credentials: 'same-origin',
                cache: 'no-store'
            });
            if (!response.ok) return;
            const body = await response.json();
            parrot = parrotService(body.services);
            if (!parrot) return;
            renderDashboardBadge(parrot);
            renderDashboardCard(parrot);
            renderAdminCard(parrot);
            renderActivity();
            connectActivitySocket();
        } catch (_) {
            // Local-service visibility is informational; never break the main UI.
        }
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', loadLocalServices, { once: true });
    } else {
        loadLocalServices();
    }
})();
