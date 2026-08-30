(() => {
    'use strict';

    const SERVICE_ID = 'hblinkLocalServices';

    function parrotService(services) {
        return (Array.isArray(services) ? services : []).find(service =>
            service && service.type === 'parrot' && Number.isInteger(service.talkgroup)
        );
    }

    function renderDashboardBadge(parrot) {
        const badges = document.querySelector('.status-badges');
        if (!badges || document.getElementById(`${SERVICE_ID}Badge`)) return;

        const badge = document.createElement('div');
        badge.id = `${SERVICE_ID}Badge`;
        badge.className = 'connection-status connected';
        badge.textContent = `🦜 Parrot TG${parrot.talkgroup}`;
        badge.title = `${parrot.name || 'Parrot / Echo Test'} — local-only echo service; not routed to other repeaters or external DMR networks.`;
        badges.insertBefore(badge, badges.firstChild);
    }

    function renderDashboardCard(parrot) {
        const stats = document.querySelector('.stats-bar');
        if (!stats || document.getElementById(`${SERVICE_ID}PublicCard`)) return;

        const card = document.createElement('section');
        card.id = `${SERVICE_ID}PublicCard`;
        card.className = 'card';
        card.innerHTML = `
            <h2>Local DMR Services</h2>
            <div style="display:flex; align-items:flex-start; gap:10px; flex-wrap:wrap;">
                <div class="connection-status connected" style="flex:none;">🦜 TG${parrot.talkgroup}</div>
                <div>
                    <strong>${parrot.name || 'Parrot / Echo Test'}</strong>
                    <div style="color:#94a3b8; font-size:12px; margin-top:4px; line-height:1.45;">Local-only echo test. Key TG${parrot.talkgroup}, speak, then unkey; the completed call is played back only to the originating repeater or hotspot. This talkgroup is not routed to other repeaters or external DMR networks.</div>
                </div>
            </div>
        `;
        stats.insertAdjacentElement('afterend', card);
    }

    function renderAdminCard(parrot) {
        const editor = document.getElementById('editorPanel');
        if (!editor || document.getElementById(`${SERVICE_ID}AdminCard`)) return;

        const card = document.createElement('section');
        card.id = `${SERVICE_ID}AdminCard`;
        card.className = 'card';
        card.innerHTML = `
            <strong>Local DMR services</strong>
            <p style="margin: 10px 0 4px;"><strong>🦜 TG${parrot.talkgroup} — ${parrot.name || 'Parrot / Echo Test'}</strong></p>
            <p class="subtle" style="margin:0;">Local-only echo service. A completed call is recorded and played back only to the originating repeater or hotspot. TG${parrot.talkgroup} is intentionally excluded from ordinary repeater and external-network routing.</p>
        `;
        editor.insertBefore(card, editor.firstChild);
    }

    async function loadLocalServices() {
        try {
            const response = await fetch('/api/local-services', {
                credentials: 'same-origin',
                cache: 'no-store'
            });
            if (!response.ok) return;
            const body = await response.json();
            const parrot = parrotService(body.services);
            if (!parrot) return;
            renderDashboardBadge(parrot);
            renderDashboardCard(parrot);
            renderAdminCard(parrot);
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
