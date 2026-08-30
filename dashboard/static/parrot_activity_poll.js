(() => {
    'use strict';

    const SERVICE_ID = 'hblinkLocalServices';
    const POLL_MS = 750;
    let lastTimestamp = 0;
    let timer = null;

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

    function formatQuality(quality) {
        if (!quality || typeof quality !== 'object') return 'RF quality: waiting for samples';
        const parts = [];
        if (Number.isFinite(quality.rssi_average_dbm)) {
            let value = `RSSI ${quality.rssi_average_dbm} dBm avg`;
            if (Number.isFinite(quality.rssi_min_dbm) && Number.isFinite(quality.rssi_max_dbm)) {
                value += ` (${quality.rssi_min_dbm} to ${quality.rssi_max_dbm})`;
            }
            parts.push(value);
        }
        if (Number.isFinite(quality.ber_average_percent)) {
            let value = `BER ${quality.ber_average_percent}% avg`;
            if (Number.isFinite(quality.ber_peak_percent)) {
                value += ` / ${quality.ber_peak_percent}% peak`;
            }
            parts.push(value);
        }
        return parts.length ? parts.join(' · ') : 'RF quality: endpoint supplied no samples';
    }

    function update(prefix, activity) {
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

        const data = activity.data || {};
        status.textContent = phaseLabel(activity.phase);

        const details = [];
        const src = Number.isInteger(data.src_id) ? data.src_id : data.rf_src;
        if (Number.isInteger(src)) details.push(`Radio ${src}`);
        if (Number.isInteger(data.slot)) details.push(`TS${data.slot}`);
        if (Number.isInteger(data.repeater_id)) details.push(`Repeater ${data.repeater_id}`);
        who.textContent = details.join(' · ');

        const measurements = [];
        if (Number.isFinite(data.duration)) measurements.push(`${Number(data.duration).toFixed(1)} s`);
        const packets = Number.isInteger(data.packet_count) ? data.packet_count : data.packets;
        if (Number.isInteger(packets)) measurements.push(`${packets} packets`);
        measurements.push(formatQuality(data.rf_quality));
        if (data.reason) measurements.push(String(data.reason));
        metrics.textContent = measurements.join(' · ');
    }

    function render(talkgroup, activity) {
        update('Public', activity);
        update('Admin', activity);

        const badge = document.getElementById(`${SERVICE_ID}Badge`);
        if (badge) {
            if (!activity || activity.phase === 'complete' || activity.phase === 'cancelled') {
                badge.textContent = `🦜 Parrot TG${talkgroup}`;
            } else {
                badge.textContent = `${phaseLabel(activity.phase)} · TG${talkgroup}`;
            }
        }
    }

    function configuredTalkgroup() {
        const badge = document.getElementById(`${SERVICE_ID}Badge`);
        if (badge) {
            const match = badge.textContent.match(/TG(\d+)/);
            if (match) return Number(match[1]);
        }
        const card = document.getElementById(`${SERVICE_ID}PublicCard`) ||
                     document.getElementById(`${SERVICE_ID}AdminCard`);
        if (card) {
            const match = card.textContent.match(/TG(\d+)/);
            if (match) return Number(match[1]);
        }
        return null;
    }

    async function poll() {
        const talkgroup = configuredTalkgroup();
        if (!Number.isInteger(talkgroup)) {
            timer = setTimeout(poll, POLL_MS);
            return;
        }

        try {
            const response = await fetch(`/api/local-services/activity?talkgroup=${talkgroup}`, {
                credentials: 'same-origin',
                cache: 'no-store'
            });
            if (response.ok) {
                const body = await response.json();
                const activity = body && body.activity ? body.activity : null;
                const timestamp = activity && Number.isFinite(activity.timestamp)
                    ? activity.timestamp : 0;
                if (!activity) {
                    if (lastTimestamp !== 0) {
                        lastTimestamp = 0;
                        render(talkgroup, null);
                    }
                } else if (timestamp >= lastTimestamp) {
                    lastTimestamp = timestamp;
                    render(talkgroup, activity);
                }
            }
        } catch (_) {
            // This is a recovery path. The existing WebSocket-driven UI remains primary.
        }
        timer = setTimeout(poll, POLL_MS);
    }

    function start() {
        if (timer) return;
        poll();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', start, { once: true });
    } else {
        start();
    }
})();
