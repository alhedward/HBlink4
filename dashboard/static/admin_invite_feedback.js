(() => {
    'use strict';

    const inviteButton = $('inviteBtn');
    if (!inviteButton) return;

    const defaultButtonText = inviteButton.textContent;
    let invitePending = false;

    const status = document.createElement('span');
    status.id = 'inviteStatus';
    status.className = 'subtle';
    status.setAttribute('role', 'status');
    status.setAttribute('aria-live', 'polite');
    inviteButton.insertAdjacentElement('afterend', status);

    function clearInviteFields() {
        ['inviteGivenName', 'inviteFamilyName', 'inviteCallsign', 'inviteEmail'].forEach(id => {
            const field = $(id);
            if (field) field.value = '';
        });
    }

    function setInviteStatus(text, kind = 'info') {
        status.textContent = text;
        status.style.color = kind === 'success'
            ? '#86efac'
            : kind === 'error'
                ? '#fca5a5'
                : '';
    }

    document.addEventListener('click', event => {
        const button = event.target.closest && event.target.closest('#inviteBtn');
        if (!button || button.disabled) return;
        invitePending = true;
        button.textContent = 'Sending…';
        setInviteStatus('Sending invitation…');
    }, true);

    const previousShowMessage = window.showMessage;
    window.showMessage = function(text, kind = 'info') {
        previousShowMessage(text, kind);
        if (!invitePending) return;

        if (kind === 'success' && /^Invitation sent to .+\.$/.test(text)) {
            clearInviteFields();
            setInviteStatus(text, 'success');
            inviteButton.textContent = 'Send another invitation';
            invitePending = false;
            return;
        }

        if (kind === 'error') {
            setInviteStatus(`Invitation failed: ${text}`, 'error');
            inviteButton.textContent = defaultButtonText;
            invitePending = false;
        }
    };

    const previousSetBusy = window.setBusy;
    window.setBusy = function(busy) {
        previousSetBusy(busy);
        if (!invitePending) return;
        inviteButton.textContent = busy ? 'Sending…' : defaultButtonText;
    };
})();
