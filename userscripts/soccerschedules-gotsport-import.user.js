// ==UserScript==
// @name         SoccerSchedules GotSport browser import
// @namespace    homelab.soccerschedules
// @updateURL     http://192.168.10.12:3008/userscripts/gotsport.user.js
// @downloadURL   http://192.168.10.12:3008/userscripts/gotsport.user.js
// @match        https://system.gotsport.com/org_event/events/*/schedules*
// @match        https://system.gotsport.com/org_event/events/*/results*
// @grant        GM_xmlhttpRequest
// @connect      192.168.10.12
// @run-at       document-idle
// ==/UserScript==

(function () {
  'use strict';
  const endpoint = 'http://192.168.10.12:3008/api/v1/browser-imports';
  const button = document.createElement('button');
  button.textContent = 'Send to SoccerSchedules';
  Object.assign(button.style, {
    position: 'fixed', zIndex: 2147483647, right: '16px', bottom: '16px',
    padding: '10px 14px', background: '#166534', color: '#fff',
    border: '0', borderRadius: '6px', cursor: 'pointer', fontSize: '14px'
  });
  button.addEventListener('click', () => {
    const token = prompt('SoccerSchedules import token (kept only for this tab):');
    if (!token) return;
    button.disabled = true;
    button.textContent = 'Importing...';
    GM_xmlhttpRequest({
      method: 'POST', url: endpoint,
      headers: { 'Content-Type': 'application/json', 'X-SoccerSchedules-Import-Token': token },
      data: JSON.stringify({ source_url: window.location.href, html: document.documentElement.outerHTML }),
      onload: (response) => {
        button.disabled = false; button.textContent = 'Send to SoccerSchedules';
        let body; try { body = JSON.parse(response.responseText); } catch (_) { body = {}; }
        if (response.status >= 200 && response.status < 300) {
          alert(`Imported ${body.games_seen} games (${body.games_created} new, ${body.games_updated} updated; ${body.completed_results} completed).`);
        } else {
          alert(`Import failed (${response.status}): ${body.detail || response.responseText}`);
        }
      },
      onerror: () => { button.disabled = false; button.textContent = 'Send to SoccerSchedules'; alert('SoccerSchedules could not be reached.'); }
    });
  });
  document.body.appendChild(button);
})();
