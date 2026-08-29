(() => {
    'use strict';

    const panel = $('adminProfilePanel');
    if (!panel || $('adminProfileCompact')) return;

    const details = document.createElement('div');
    details.id = 'adminProfileDetails';
    while (panel.firstChild) details.appendChild(panel.firstChild);
    panel.appendChild(details);

    const compact = document.createElement('div');
    compact.id = 'adminProfileCompact';
    compact.className = 'hidden';
    compact.innerHTML = `
        <div class="actions" style="justify-content: space-between;">
            <div>
                <strong>Administrator profile</strong>
                <div id="adminProfileCompactStatus" class="subtle" style="margin-top: 4px;"></div>
            </div>
            <button id="adminProfileChangeBtn" class="secondary" type="button">Change administrator profile</button>
        </div>`;
    panel.insertBefore(compact, details);

    let expanded = false;

    function profileComplete() {
        return !!(
            $('profileGivenName').value.trim()
            && $('profileFamilyName').value.trim()
            && $('profileCallsign').value.trim()
            && $('profileEmail').value.trim()
        );
    }

    function profileSummary() {
        const name = [$('profileGivenName').value.trim(), $('profileFamilyName').value.trim()]
            .filter(Boolean).join(' ');
        const callsign = $('profileCallsign').value.trim();
        const email = $('profileEmail').value.trim();
        const identity = callsign ? `${name} (${callsign})` : name;
        return [identity, email].filter(Boolean).join(' · ');
    }

    function render() {
        const complete = profileComplete();
        $('adminProfileCompactStatus').textContent = complete ? profileSummary() : '';
        if (complete && !expanded) {
            compact.classList.remove('hidden');
            details.classList.add('hidden');
        } else {
            compact.classList.add('hidden');
            details.classList.remove('hidden');
        }
    }

    $('adminProfileChangeBtn').addEventListener('click', () => {
        expanded = true;
        render();
        $('profileGivenName').focus();
    });

    $('profileSaveBtn').addEventListener('click', () => {
        const observer = new MutationObserver(() => {
            if ($('profileHint').textContent === 'Profile complete.') {
                expanded = false;
                render();
                observer.disconnect();
            }
        });
        observer.observe($('profileHint'), { childList: true, characterData: true, subtree: true });
    });

    const observer = new MutationObserver(render);
    observer.observe($('profileHint'), { childList: true, characterData: true, subtree: true });
    for (const id of ['profileGivenName', 'profileFamilyName', 'profileCallsign', 'profileEmail']) {
        $(id).addEventListener('input', render);
    }

    render();
})();