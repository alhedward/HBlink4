(() => {
  'use strict';

  const BROWSER_PEER_IDS = new Set(['16777001', '50525419']);
  const BROWSER_PEER_NAMES = new Set(['SGARSWEB', 'SGARS Browser DMR Local Peer']);

  function isBrowserPeerSource(text) {
    const value = String(text || '').trim();
    if (!value) return false;
    if (BROWSER_PEER_NAMES.has(value)) return true;
    for (const id of BROWSER_PEER_IDS) {
      if (value === id || value.includes(`(${id})`) || value.includes(id)) return true;
    }
    return false;
  }

  function relabelBrowserSources() {
    const body = document.getElementById('lastHeardTable');
    if (!body) return;
    for (const row of body.querySelectorAll('tr')) {
      const cells = row.querySelectorAll('td');
      if (cells.length < 3 || !isBrowserPeerSource(cells[2].textContent)) continue;
      const radioId = String(cells[0].textContent || '').trim();
      const callsign = String(cells[1].textContent || '').trim();
      if (!radioId) continue;
      cells[2].textContent = `${callsign && callsign !== '-' ? callsign : 'DMR'} (${radioId})`;
    }
  }

  const body = document.getElementById('lastHeardTable');
  if (!body) return;
  relabelBrowserSources();
  new MutationObserver(relabelBrowserSources).observe(body, {childList: true, subtree: true});
})();
