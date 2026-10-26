/* apperture-v2.js — add before </body>, AFTER your main <script>:  <script src="apperture-v2.js"></script> */
(function () {
  'use strict';
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const DAY = 864e5, HOUR = 36e5;
  const LOCAL_TOKEN = '#find,in[LOCALSHOPS]##%Search,"-"';

  // ---------- store (swap for backend calls in production) ----------
  const KEY = 'apperture_v2';
  const S = Object.assign({ shops: {}, active: {}, dead: {}, warned: {}, tagViews: {}, lastPush: {}, pendingShop: null }, JSON.parse(localStorage.getItem(KEY) || '{}'));
  const save = () => { try { localStorage.setItem(KEY, JSON.stringify(S)); } catch (e) {} };
  const me = () => (typeof currentUserId !== 'undefined' ? currentUserId : null);
  let pos = null, radiusKm = 1.5, mode = 'all';

  // ---------- tags ----------
  const tagsOf = (l) => [...new Set(((l.description || '') + ' ' + (l.title || '')).toLowerCase().match(/#[a-z0-9_]+/g) || [])];
  function trendingTags(limit = 100) {
    const c = {};
    allListings.forEach((l) => tagsOf(l).forEach((t) => (c[t] = (c[t] || 0) + 1 + (l.starsCount || 0) / 10)));
    return Object.entries(c).sort((a, b) => b[1] - a[1]).slice(0, limit).map((e) => e[0]);
  }
  // Recommended = listings carrying any top-100 tag (plus the existing >100 stars rule)
  function recommended() {
    const top = new Set(trendingTags(100));
    return allListings.filter((l) => (l.starsCount || 0) > 100 || tagsOf(l).some((t) => top.has(t)));
  }
  // Tag input in sell form; tags get appended to the description so no schema change is needed
  const sellDesc = $('sell-description');
  if (sellDesc && !$('sell-tags')) {
    const i = document.createElement('input');
    i.id = 'sell-tags'; i.placeholder = 'Tags: #motors #shirts #vintage (as many as you want)';
    i.className = 'w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-white';
    sellDesc.after(i);
    $('sell-form').addEventListener('submit', () => {
      const t = (i.value.match(/#?[a-z0-9_]+/gi) || []).map((x) => '#' + x.replace('#', '').toLowerCase());
      if (t.length) sellDesc.value += '\n' + [...new Set(t)].join(' ');
    }, true);
  }

  // ---------- shop fields at signup ----------
  const signup = $('signup-only-fields');
  if (signup && !$('setup-shop-name')) {
    const d = document.createElement('div');
    d.className = 'space-y-3 border border-yellow-600/40 rounded-lg p-3';
    d.innerHTML = '<p class="text-[10px] text-yellow-400 font-bold uppercase tracking-widest">Local Shoppe (sellers)</p>' +
      '<input id="setup-shop-name" placeholder="Shop name / House name" class="w-full bg-gray-950 border border-gray-700 rounded-lg px-4 py-2 text-white">' +
      '<input id="setup-shop-time" placeholder="Timings e.g. 09:00-21:00" class="w-full bg-gray-950 border border-gray-700 rounded-lg px-4 py-2 text-white">' +
      '<button type="button" id="setup-shop-loc" class="w-full text-xs bg-gray-800 border border-gray-600 rounded py-2 text-gray-200">📍 Use my current location</button>';
    signup.prepend(d);
    $('setup-shop-loc').onclick = () => navigator.geolocation.getCurrentPosition((p) => {
      S.pendingShop = Object.assign(S.pendingShop || {}, { lat: p.coords.latitude, lng: p.coords.longitude });
      $('setup-shop-loc').textContent = '✅ Location captured';
    }, () => alert('Location permission denied.'));
    $('setup-profile-form').addEventListener('submit', () => {
      S.pendingShop = Object.assign(S.pendingShop || {}, { name: $('setup-shop-name').value.trim(), timings: $('setup-shop-time').value.trim() });
      save();
    }, true);
  }

  // ---------- local shoppes ----------
  const dist = (a, b) => { const r = Math.PI / 180, dLa = (b.lat - a.lat) * r, dLo = (b.lng - a.lng) * r;
    const h = Math.sin(dLa / 2) ** 2 + Math.cos(a.lat * r) * Math.cos(b.lat * r) * Math.sin(dLo / 2) ** 2; return 12742 * Math.asin(Math.sqrt(h)); };
  const openNow = (t) => { const m = /(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})/.exec(t || ''); if (!m) return null;
    const n = new Date(), x = n.getHours() * 60 + n.getMinutes(), a = +m[1] * 60 + +m[2], b = +m[3] * 60 + +m[4]; return a <= b ? x >= a && x <= b : x >= a || x <= b; };
  const nearbySellers = () => Object.entries(S.shops).filter(([, s]) => pos && s.lat != null && dist(pos, s) <= radiusKm).map(([id, s]) => ({ id, ...s, km: dist(pos, s) })).sort((a, b) => a.km - b.km);

  const bar = $('search-bar');
  if (bar && !$('v2-local-btn')) {
    const w = document.createElement('div');
    w.className = 'flex items-center gap-3 mt-3 flex-wrap';
    w.innerHTML = '<button id="v2-local-btn" class="px-4 py-2 rounded-full text-xs font-bold border border-yellow-500 text-yellow-400 hover:bg-yellow-500 hover:text-black transition">📍 Local-Shoppes</button>' +
      '<label class="text-[10px] text-gray-400 flex items-center gap-2">Radius <input id="v2-radius" type="range" min="1.5" max="5" step="0.5" value="1.5"><b id="v2-radius-val" class="text-yellow-400">1.5 km</b></label>';
    bar.closest('.flex-col, .max-w-xl').appendChild(w);
    const panel = document.createElement('div'); panel.id = 'v2-local-panel'; panel.className = 'hidden mb-6 grid sm:grid-cols-2 gap-3';
    $('product-grid').before(panel);
    $('v2-local-btn').onclick = () => go('#/local');
    $('v2-radius').oninput = (e) => { radiusKm = +e.target.value; $('v2-radius-val').textContent = radiusKm + ' km'; renderGrid(); };
  }
  function enterLocal() {
    mode = 'local'; bar.value = LOCAL_TOKEN; bar.style.color = '#facc15'; bar.style.fontWeight = '700';
    const done = (p) => { if (p) pos = { lat: p.coords.latitude, lng: p.coords.longitude }; renderGrid(); };
    pos ? done() : navigator.geolocation.getCurrentPosition(done, () => alert('Allow location to find shops near you.'));
  }
  function leaveLocal() { mode = 'all'; bar.style.color = ''; bar.style.fontWeight = ''; $('v2-local-panel').classList.add('hidden'); }

  // ---------- dead mode ----------
  const isDead = (uid) => !!S.dead[uid];
  function lifecycle() {
    const u = me(), now = Date.now();
    if (u && !S.dead[u]) { S.active[u] = now; }
    Object.entries(S.active).forEach(([uid, t]) => {
      const age = now - t;
      if (age > 30 * DAY && !S.dead[uid]) { S.dead[uid] = now; push(uid, '💀 Nexus AI: Your account went DEAD after 30 days offline. Your listings are hidden. Open this account and turn off Dead Mode.'); }
      else if (age > 7 * DAY && !S.warned[uid]) { S.warned[uid] = now; push(uid, '⚠️ Nexus AI: You have been away for a week. After 30 days your account and listings will go DEAD.'); }
    });
    save(); banner();
  }
  function push(uid, message) { if (typeof SERVER_DB !== 'undefined') { SERVER_DB.notifications.push({ type: 'sys', message, recipient_id: uid, expiresAt: Date.now() + 7 * DAY }); saveDB(); } }
  function banner() {
    let b = $('v2-dead-banner'); const u = me();
    if (!u || !S.dead[u]) return b && b.remove();
    if (b) return;
    b = document.createElement('div'); b.id = 'v2-dead-banner';
    b.className = 'fixed bottom-4 left-1/2 -translate-x-1/2 z-[200] bg-red-900 border border-red-500 text-white px-5 py-3 rounded-xl shadow-2xl flex items-center gap-4 text-sm';
    b.innerHTML = '<span>💀 Account is in DEAD mode. Listings hidden.</span><button class="bg-white text-red-700 font-bold px-3 py-1 rounded">Turn off Dead Mode</button>';
    b.querySelector('button').onclick = () => { delete S.dead[u]; delete S.warned[u]; S.active[u] = Date.now(); save(); b.remove(); renderGrid(); };
    document.body.appendChild(b);
  }

  // ---------- tag-targeted notifications (>=20 views of a tag, max 1 push / user / hour) ----------
  const _openDetail = window.openDetail;
  window.openDetail = function (id) {
    const l = allListings.find((x) => x.id === id), u = me();
    if (l && u) { const v = (S.tagViews[u] = S.tagViews[u] || {}); tagsOf(l).forEach((t) => (v[t] = (v[t] || 0) + 1)); save(); }
    return _openDetail.apply(this, arguments);
  };
  function targetedPush(message) {
    const tags = message.toLowerCase().match(/#[a-z0-9_]+/g) || []; if (!tags.length) return 0;
    const now = Date.now(); let sent = 0;
    Object.entries(S.tagViews).forEach(([uid, v]) => {
      if (!tags.some((t) => (v[t] || 0) >= 20)) return;
      if (now - (S.lastPush[uid] || 0) < HOUR) return;
      S.lastPush[uid] = now; push(uid, message); sent++;
    });
    save(); return sent; // backend version fans out over WebSocket in batches (see backend_additions.py)
  }
  $('schedule-form-notifications')?.addEventListener('submit', () => { const n = targetedPush($('notif-copy-input').value); if (n) console.log('Tag push reached', n, 'users'); }, true);

  // ---------- grid wrapper (local / tag / dead filters) ----------
  const _render = window.renderGrid;
  window.renderGrid = function () {
    const q = (bar.value || '').trim().toLowerCase(), real = allListings;
    let list = real.filter((l) => !isDead(l.sellerId));
    let tag = null;
    if (q.startsWith('#find,in[localshops]')) { if (mode !== 'local') mode = 'local'; }
    else { if (mode === 'local') leaveLocal(); if (/^#[a-z0-9_]+$/.test(q)) tag = q; }
    const panel = $('v2-local-panel');
    if (mode === 'local') {
      const near = nearbySellers(), ids = new Set(near.map((s) => s.id));
      list = list.filter((l) => ids.has(l.sellerId));
      panel.classList.remove('hidden');
      panel.innerHTML = !pos ? '<p class="text-gray-400 text-sm">Waiting for location…</p>' : near.length ? near.map((s) => {
        const o = openNow(s.timings);
        return `<a href="#/seller/${esc(s.id)}" class="bg-gray-900 border border-yellow-600/50 rounded-lg p-3 block"><b class="text-yellow-400">${esc(s.name)}</b>
          <span class="float-right text-xs text-gray-400">${s.km.toFixed(2)} km</span><p class="text-xs text-gray-400">${esc(s.timings || 'Timings not set')} ${o === null ? '' : o ? '· <span class="text-green-400">Open</span>' : '· <span class="text-red-400">Closed</span>'}</p></a>`;
      }).join('') : `<p class="text-gray-400 text-sm col-span-full">No shops within ${radiusKm} km. Try a wider radius (max 5 km).</p>`;
    } else panel.classList.add('hidden');
    if (tag) list = list.filter((l) => tagsOf(l).includes(tag));
    const keepQ = bar.value; bar.value = ''; allListings = list;
    try { _render(); } finally { allListings = real; bar.value = keepQ; }
    list.forEach(() => 0);
    decorate();
  };
  function decorate() { // tag chips on cards
    document.querySelectorAll('#product-grid h3').forEach((h) => {
      const card = h.closest('.group'); if (!card || card.dataset.v2) return; card.dataset.v2 = 1;
      const l = allListings.find((x) => x.title === h.textContent); if (!l) return;
      const t = tagsOf(l).slice(0, 3).map((x) => `<a href="#/tag/${x.slice(1)}" class="text-[10px] text-cyan-400 mr-1">${esc(x)}</a>`).join('');
      if (t) h.parentElement.insertAdjacentHTML('afterend', `<div class="px-0 mb-2">${t}</div>`);
    });
  }

  // recommended modal uses top-100 tags
  $('rec-search-btn')?.addEventListener('click', () => setTimeout(() => {
    const box = $('rec-results'), r = recommended(); if (!r.length) return;
    box.innerHTML = r.map((l) => `<div class="bg-gray-800 p-4 rounded-xl flex gap-4 border border-green-500/30 cursor-pointer" onclick="openDetail(${l.id})">
      <img src="${esc(l.image_data)}" class="w-24 h-24 object-cover rounded-lg"><div><h4 class="font-bold text-white">${esc(l.title)}</h4>
      <p class="text-purple-400 font-bold">${esc(l.currency)}${esc(l.price)}</p><p class="text-[10px] text-cyan-400">${tagsOf(l).join(' ')}</p></div></div>`).join('');
  }, 0));

  // ---------- Hey Nexus (voice) + analytics board ----------
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  let rec = null;
  const ov = document.createElement('div');
  ov.id = 'nexus-overlay'; ov.className = 'hidden fixed inset-0 z-[300] bg-[#070314]/95 backdrop-blur flex flex-col items-center p-6 overflow-y-auto';
  ov.innerHTML = `<button id="nx-close" class="absolute top-4 right-4 text-gray-400 text-2xl">✕</button>
    <div id="nx-orb" class="w-28 h-28 rounded-full mt-10 bg-gradient-to-tr from-cyan-400 to-purple-600 animate-pulse shadow-[0_0_60px_rgba(34,211,238,.6)]"></div>
    <p id="nx-status" class="mt-6 text-cyan-300 font-mono tracking-widest text-sm uppercase">Nexus is listening… say “search red hoodie”</p>
    <p id="nx-heard" class="text-gray-400 text-xs mt-2 h-4"></p><div id="nx-board" class="w-full max-w-3xl mt-6 space-y-3"></div>`;
  document.body.appendChild(ov);
  $('nx-close').onclick = () => go('#/');
  const median = (a) => { const s = [...a].sort((x, y) => x - y); return s.length ? s[Math.floor(s.length / 2)] : 0; };
  function analyse(q) {
    const words = q.toLowerCase().split(/\s+/).filter(Boolean);
    const pool = recommended().concat(allListings).filter((l, i, a) => a.indexOf(l) === i && !isDead(l.sellerId))
      .filter((l) => words.every((w) => (l.title + ' ' + l.description + ' ' + l.category).toLowerCase().includes(w)));
    const med = median(pool.map((l) => +l.price || 0)), top = new Set(trendingTags(100));
    return pool.map((l) => {
      const f = checkFraud((l.title || '') + ' ' + (l.description || '')), price = +l.price || 0, flags = [];
      let risk = 0;
      if (f.isFraud) { risk += 40; flags.push('Off-platform payment words'); }
      if (f.isVulgar) { risk += 25; flags.push('Flagged language'); }
      if (med && price < med * 0.4) { risk += 25; flags.push('Price far below market'); }
      if (!(l.images && l.images.length > 1) && !l.starsCount) { risk += 10; flags.push('Single image, no stars'); }
      const score = Math.max(0, Math.min(100, 50 + (l.starsCount || 0) * 2 + tagsOf(l).filter((t) => top.has(t)).length * 5 - risk));
      return { l, risk: Math.min(100, risk), score, flags };
    }).sort((a, b) => b.score - a.score).slice(0, 8);
  }
  function showBoard(q) {
    const r = analyse(q), b = $('nx-board');
    $('nx-status').textContent = `Analytical board · “${q}”`;
    b.innerHTML = r.length ? r.map((x, i) => `<div class="bg-gray-900 border ${x.risk >= 40 ? 'border-red-500' : 'border-cyan-700'} rounded-xl p-4 flex gap-4 items-center cursor-pointer" onclick="location.hash='#/item/${x.l.id}'">
      <span class="text-2xl font-black text-cyan-400">#${i + 1}</span><img src="${esc(x.l.image_data)}" class="w-16 h-16 rounded object-cover">
      <div class="flex-1 min-w-0"><b class="block truncate">${esc(x.l.title)}</b><span class="text-purple-400 text-sm">${esc(x.l.currency)}${esc(x.l.price)}</span>
      <p class="text-[10px] text-gray-400">${x.flags.length ? esc(x.flags.join(' · ')) : 'No red flags found'}</p></div>
      <div class="text-right text-xs"><div class="text-green-400 font-bold">Pick score ${x.score}</div><div class="${x.risk >= 40 ? 'text-red-400' : 'text-gray-400'}">${x.risk >= 40 ? 'LIKELY FAKE' : x.risk >= 20 ? 'Caution' : 'Looks genuine'} (${x.risk})</div></div></div>`).join('')
      : '<p class="text-gray-400 text-center">Nothing matched. Try another search.</p>';
    bar.value = q; bar.dispatchEvent(new Event('input'));
  }
  function listen() {
    ov.classList.remove('hidden');
    if (!SR) { $('nx-status').textContent = 'Voice not supported here. Type your search below the bar.'; return; }
    rec = new SR(); rec.continuous = true; rec.interimResults = false; rec.lang = localStorage.getItem('apperture_lang') === 'hi' ? 'hi-IN' : 'en-IN';
    rec.onresult = (e) => {
      const t = e.results[e.results.length - 1][0].transcript.trim(); $('nx-heard').textContent = '“' + t + '”';
      if (window.NexusCmd) window.NexusCmd.run(t, showBoard);
      else { const m = /^(?:hey nexus[, ]*)?search (?:for )?(.+)$/i.exec(t); if (m) showBoard(m[1]); }
    };
    rec.onend = () => { if (!ov.classList.contains('hidden')) try { rec.start(); } catch (e) {} };
    try { rec.start(); } catch (e) {}
  }
  function stopListen() { ov.classList.add('hidden'); try { rec && rec.stop(); } catch (e) {} $('nx-board').innerHTML = ''; }
  bar?.addEventListener('input', () => { if (/^hey nexus/i.test(bar.value.trim())) go('#/nexus'); });

  // ---------- router (#/ style; leaves your #shop-section anchors alone) ----------
  function go(h) { location.hash = h; }
  function route() {
    const h = location.hash; if (!h.startsWith('#/')) return;
    const [, a, b] = h.split('/').map(decodeURIComponent);
    if (a !== 'nexus') stopListen();
    switch (a) {
      case 'local': enterLocal(); $('shop-section').scrollIntoView(); break;
      case 'tag': leaveLocal(); bar.value = '#' + b; renderGrid(); $('shop-section').scrollIntoView(); break;
      case 'shop': leaveLocal(); bar.value = ''; if (b) $('category-filter').value = b; renderGrid(); $('shop-section').scrollIntoView(); break;
      case 'item': openDetail(+b); break;
      case 'seller': openSellerProfile(b, (S.shops[b] || {}).name || 'Seller'); break;
      case 'nexus': listen(); break;
      case 'profile': case 'sell': case 'about': case 'chat': $(a + '-nav-link')?.click(); break;
      default: leaveLocal(); if (!a) { bar.value = ''; renderGrid(); }
    }
  }
  window.addEventListener('hashchange', route);

  // ---------- multiplayer (WebSocket client) ----------
  let ws, retry = 1000;
  function connect() {
    const u = me(); if (!u || ws) return;
    ws = new WebSocket(`ws://localhost:8000/ws/${encodeURIComponent(u)}`);
    ws.onopen = () => (retry = 1000);
    ws.onmessage = (e) => {
      const m = JSON.parse(e.data);
      if (m.type === 'new_listing' || m.type === 'chat_message' || m.type === 'global_alert' || m.type === 'notification') runSync();
      if (m.type === 'presence') document.title = `Apperture · ${m.status === 'online' ? '●' : '○'}`;
    };
    ws.onclose = () => { ws = null; setTimeout(connect, (retry = Math.min(retry * 2, 30000))); };
    ws.onerror = () => ws && ws.close();
  }

  // ---------- boot ----------
  setInterval(() => {
    const u = me();
    if (u && S.pendingShop && S.pendingShop.name) { S.shops[u] = S.pendingShop; S.pendingShop = null; save(); }
  }, 3000);
  setInterval(lifecycle, 60000);
  lifecycle(); route();
  window.AppertureV2 = { showBoard, targetedPush, trendingTags, recommended, go, S };
})();
