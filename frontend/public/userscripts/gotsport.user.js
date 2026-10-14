// ==UserScript==
// @name         SoccerSchedules GotSport browser import
// @namespace    homelab.soccerschedules
// @version      1.1.0
// @updateURL     http://192.168.10.12:3008/userscripts/gotsport.user.js
// @downloadURL   http://192.168.10.12:3008/userscripts/gotsport.user.js
// @match        https://system.gotsport.com/org_event/events/*/schedules*
// @match        https://system.gotsport.com/org_event/events/*/results*
// @grant        GM_xmlhttpRequest
// @grant        GM_getValue
// @grant        GM_setValue
// @grant        GM_registerMenuCommand
// @connect      192.168.10.12
// @run-at       document-idle
// ==/UserScript==

(function () {
  'use strict';
  const endpoint = 'http://192.168.10.12:3008/api/v1/browser-imports';
  const tokenKey = 'soccerSchedulesImportToken';
  const button = document.createElement('button');
  const changeTokenButton = document.createElement('button');
  button.textContent = 'Send to SoccerSchedules';
  changeTokenButton.textContent = 'Change Token';
  const baseStyle = { position: 'fixed', zIndex: 2147483647, bottom: '16px', padding: '10px 14px', color: '#fff', border: '0', borderRadius: '6px', cursor: 'pointer', fontSize: '14px' };
  Object.assign(button.style, baseStyle, { right: '146px', background: '#166534' });
  Object.assign(changeTokenButton.style, baseStyle, { right: '16px', background: '#334155' });

  function getToken() {
    return String(GM_getValue(tokenKey, '') || '').trim();
  }

  function changeToken() {
    const replacement = prompt('Enter the SoccerSchedules import token. It will be stored only in Tampermonkey.');
    if (!replacement || !replacement.trim()) return false;
    GM_setValue(tokenKey, replacement.trim());
    return true;
  }

  function setReady() {
    button.disabled = false;
    button.textContent = 'Send to SoccerSchedules';
  }

  changeTokenButton.addEventListener('click', () => {
    if (changeToken()) alert('SoccerSchedules import token updated.');
  });
  if (typeof GM_registerMenuCommand === 'function') {
    GM_registerMenuCommand('Change SoccerSchedules Token', changeToken);
  }

  button.addEventListener('click', () => {
    let token = getToken();
    if (!token && !changeToken()) return;
    token = getToken();
    button.disabled = true; button.textContent = 'Importing...';
    GM_xmlhttpRequest({ method: 'POST', url: endpoint, headers: { 'Content-Type': 'application/json', 'X-SoccerSchedules-Import-Token': token }, data: JSON.stringify({ source_url: window.location.href, html: document.documentElement.outerHTML }), onload: (response) => {
      setReady();
      let body; try { body = JSON.parse(response.responseText); } catch (_) { body = {}; }
      if (response.status >= 200 && response.status < 300) {
        alert(`Imported ${body.games_seen} games (${body.games_created} new, ${body.games_updated} updated; ${body.completed_results} completed).`);
      } else if (response.status === 401 || response.status === 403) {
        GM_setValue(tokenKey, '');
        alert('SoccerSchedules rejected the saved token. Use Change Token to replace it.');
      } else {
        alert(`Import failed (${response.status}). Check the page and server status.`);
      }
    }, onerror: () => { setReady(); alert('SoccerSchedules could not be reached.'); } });
  });
  document.body.appendChild(button);
  document.body.appendChild(changeTokenButton);
})();
