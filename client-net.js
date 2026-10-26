/* client-net.js — load in <head> BEFORE your main <script>:  <script src="client-net.js"></script>
   Adds: signed-token login, auto-reconnecting WebSocket (heartbeat, replay of missed events), presence, typing, live viewers. */
(function () {
  'use strict';
  const TK = 'apperture_token', SEQ = 'apperture_seq', LOCAL = 'http://localhost:8000';
  const served = /^https?:/.test(location.origin);
  const BASE = served ? location.origin : LOCAL;
  const nativeFetch = window.fetch.bind(window);

  // ---- 1. attach token to API calls, capture tokens from /auth and /account/password ----
  window.fetch = async function (url, opts) {
    let u = String(url);
    if (!u.startsWith(LOCAL) && !u.startsWith(BASE + '/')) return nativeFetch(url, opts);
    if (served) u = u.replace(LOCAL, BASE);
    const o = Object.assign({}, opts), h = new Headers(o.headers || {}), t = localStorage.getItem(TK);
    if (t) h.set('Authorization', 'Bearer ' + t);
    o.headers = h;
    const res = await nativeFetch(u, o);
    try {
      if (res.ok && /\/(auth|account\/password)(\?|$)/.test(u)) { const j = await res.clone().json(); if (j.token) { localStorage.setItem(TK, j.token); setTimeout(connect, 50); } }
      if (res.status === 401 && t) { localStorage.removeItem(TK); }
    } catch (e) {}
    return res;
  };
  setInterval(() => { if (!localStorage.getItem('apperture_user_id') && localStorage.getItem(TK)) { localStorage.removeItem(TK); localStorage.removeItem(SEQ); ws && ws.close(1000); } }, 2000); // logout

  // ---- 2. small UI helpers ----
  let online = new Set();
  const badge = document.createElement('div');
  badge.style.cssText = 'position:fixed;left:12px;bottom:12px;z-index:150;font:600 11px Inter,sans-serif;color:#9ca3af;background:#000c;border:1px solid #374151;border-radius:999px;padding:4px 10px;pointer-events:none';
  const paintBadge = (live) => { badge.innerHTML = `<span style="color:${live ? '#22c55e' : '#ef4444'}">●</span> ${live ? 'live · ' + online.size + ' online' : 'reconnecting…'}`; };
  function toast(text) {
    const d = document.createElement('div');
    d.style.cssText = 'position:fixed;top:76px;right:16px;z-index:500;max-width:300px;background:#111827;color:#fff;border:1px solid #8A2BE2;border-radius:12px;padding:10px 14px;font:13px Inter,sans-serif;box-shadow:0 8px 30px #0008';
    d.textContent = text; document.body.appendChild(d); setTimeout(() => d.remove(), 5000);
  }
  document.addEventListener('DOMContentLoaded', () => { document.body.appendChild(badge); paintBadge(false); });

  // ---- 3. resilient WebSocket ----
  let ws = null, retry = 1000, hb = null, since = +localStorage.getItem(SEQ) || 0, syncT = null;
  const wsURL = () => BASE.replace(/^http/, 'ws') + '/ws?token=' + encodeURIComponent(localStorage.getItem(TK)) + '&since=' + since;
  const REFRESH = new Set(['new_listing', 'listing_update', 'listing_removed', 'chat_message', 'new_chat', 'notification', 'global_alert', 'profile_update', 'read']);
  const send = (o) => ws && ws.readyState === 1 && ws.send(JSON.stringify(o));

  function connect() {
    if (ws || !localStorage.getItem(TK)) return;
    ws = new WebSocket(wsURL());
    ws.onopen = () => { retry = 1000; clearInterval(hb); hb = setInterval(() => send({ type: 'ping' }), 20000); };
    ws.onmessage = (e) => {
      let m; try { m = JSON.parse(e.data); } catch (_) { return; }
      if (m.seq && m.type !== 'hello') { since = m.seq; localStorage.setItem(SEQ, since); }
      switch (m.type) {
        case 'hello': online = new Set(m.online); paintBadge(true); if (m.seq < since) { since = m.seq; localStorage.setItem(SEQ, since); } break; // server restarted
        case 'presence': m.status === 'online' ? online.add(m.user_id) : online.delete(m.user_id); paintBadge(true); break;
        case 'typing_indicator': showTyping(m.from_user); break;
        case 'viewers': showViewers(m); break;
        case 'global_alert': case 'notification': toast(m.data && m.data.message || 'New notification'); break;
        case 'chat_message': if (m.message_data && m.message_data.sender_id !== (typeof currentUserId !== 'undefined' ? currentUserId : null)) toast('💬 ' + m.message_data.text); break;
      }
      window.dispatchEvent(new CustomEvent('apperture:event', { detail: m }));
      if (REFRESH.has(m.type) && typeof runSync === 'function') { clearTimeout(syncT); syncT = setTimeout(runSync, 150); }
    };
    ws.onclose = (e) => {
      clearInterval(hb); ws = null; paintBadge(false);
      if (e.code === 4401) { localStorage.removeItem(TK); return; }              // token invalid: need to log in again
      setTimeout(connect, (retry = Math.min(retry * 1.7, 30000)) + Math.random() * 500); // backoff + jitter
    };
    ws.onerror = () => ws && ws.close();
  }
  window.addEventListener('online', () => { ws && ws.close(); setTimeout(connect, 200); });
  setInterval(connect, 3000);

  // ---- 4. typing + live viewers ----
  let typingT;
  function showTyping(uid) {
    const h = document.getElementById('chat-header-name'); if (!h || typeof myChats === 'undefined') return;
    const c = typeof activeChatId !== 'undefined' && myChats.find((x) => x.id === activeChatId); if (!c || !c.participants.includes(uid)) return;
    let s = document.getElementById('v2-typing'); if (!s) { s = document.createElement('span'); s.id = 'v2-typing'; s.style.cssText = 'font-size:11px;color:#22d3ee;margin-left:8px'; h.after(s); }
    s.textContent = 'typing…'; clearTimeout(typingT); typingT = setTimeout(() => (s.textContent = ''), 2500);
  }
  function showViewers(m) {
    if (typeof activeDetailItem === 'undefined' || !activeDetailItem || activeDetailItem.id !== m.listing_id) return;
    let s = document.getElementById('v2-viewers');
    if (!s) { s = document.createElement('span'); s.id = 'v2-viewers'; s.className = 'text-[10px] text-green-400 bg-gray-800 border border-gray-700 px-2 py-1 rounded'; document.getElementById('detail-category').parentElement.appendChild(s); }
    s.textContent = m.count > 1 ? `👁 ${m.count} viewing now` : '';
  }
  document.addEventListener('DOMContentLoaded', () => {
    const typeNow = () => {
      if (typeof myChats === 'undefined' || typeof activeChatId === 'undefined' || !activeChatId) return;
      const c = myChats.find((x) => x.id === activeChatId), r = c && c.participants.find((p) => p !== currentUserId);
      r && send({ type: 'typing', recipient_id: r });
    };
    ['admin-message-input', 'user-message-select'].forEach((id) => document.getElementById(id) && document.getElementById(id).addEventListener(id.includes('input') ? 'input' : 'change', typeNow));
    const od = window.openDetail, cm = window.closeModal;
    if (od) window.openDetail = function (id) { send({ type: 'view', listing_id: id }); return od.apply(this, arguments); };
    if (cm) window.closeModal = function (id) { if (id === 'product-detail-modal') send({ type: 'unview' }); return cm.apply(this, arguments); };
  });
  window.AppertureNet = { send, toast, isOnline: (id) => online.has(id), onlineCount: () => online.size };
})();
