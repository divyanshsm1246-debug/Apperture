/* nexus-commands.js — load AFTER apperture-v2.js:  <script src="nexus-commands.js"></script>
   Works by voice ("Hey Nexus" in the search bar) or typed in the Nexus box. 30+ commands; say "help" to list them. */
(function () {
  'use strict';
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const norm = (s) => String(s || '').toLowerCase().replace(/[^a-z0-9 ]/g, ' ').split(/\s+/).filter(Boolean);
  const V2 = () => window.AppertureV2;
  let showBoard = () => {}, speakOn = true;

  const say = (t) => { if (speakOn && window.speechSynthesis) { const u = new SpeechSynthesisUtterance(t.replace(/<[^>]+>/g, '')); u.rate = 1.05; speechSynthesis.cancel(); speechSynthesis.speak(u); } };
  function log(title, html) {
    const d = document.createElement('div'); d.className = 'bg-gray-900 border border-cyan-700 rounded-xl p-4 text-sm text-gray-200';
    d.innerHTML = `<b class="text-cyan-300">${esc(title)}</b>${html ? '<div class="mt-2 break-words">' + html + '</div>' : ''}`;
    const b = $('nx-board'); b.prepend(d); say(title);
  }
  const leave = (fn) => { V2().go('#/'); setTimeout(fn, 120); };
  const guest = () => typeof userProfile === 'undefined' || userProfile.name === 'Guest';
  const needLogin = () => (guest() ? (log('Please log in first.'), true) : false);

  // fuzzy listing lookup by title words
  function find(name, mine) {
    const w = norm(name); let best = null, bs = 0;
    allListings.forEach((l) => {
      if (mine && l.sellerId !== currentUserId) return;
      const t = norm(l.title), s = w.filter((x) => t.includes(x)).length / Math.max(w.length, 1) + (t.join(' ') === w.join(' ') ? 1 : 0);
      if (s > bs) { bs = s; best = l; }
    });
    return bs >= 0.5 ? best : null;
  }
  const need = (name, mine) => { const l = find(name, mine); if (!l) log(`I couldn't find ${mine ? 'your listing' : 'a product'} called “${name}”.`); return l; };
  const money = (l) => `${l.currency}${l.price}`;
  const card = (l) => `<a href="#/item/${l.id}" class="text-cyan-400 underline">${esc(l.title)}</a> — ${esc(money(l))} · ⭐${l.starsCount || 0}`;

  // API call with local fallback (offline mock mode)
  async function call(path, method, body, local) {
    try { const r = await API.request(path, method, body); if (r && (r.status === 'ok' || r.status === 'deleted' || r.status === 'boosted' || r.stars !== undefined || r.reward !== undefined)) return r; }
    catch (e) { if (!/Failed|Network/i.test(e.message)) throw e; }
    return local();
  }

  // masked password dialog (passwords are never spoken or logged)
  function passwordDialog() {
    const m = document.createElement('div'); m.className = 'fixed inset-0 z-[400] bg-black/80 flex items-center justify-center p-4';
    m.innerHTML = `<form class="bg-gray-900 border border-purple-500 rounded-xl p-6 w-full max-w-sm space-y-3"><h3 class="font-bold text-lg">Change password</h3>
      <input type="password" id="pw-old" placeholder="Current password" autocomplete="current-password" class="w-full bg-gray-800 rounded px-3 py-2">
      <input type="password" id="pw-new" placeholder="New password (8+ chars)" autocomplete="new-password" class="w-full bg-gray-800 rounded px-3 py-2">
      <input type="password" id="pw-new2" placeholder="Repeat new password" autocomplete="new-password" class="w-full bg-gray-800 rounded px-3 py-2">
      <div class="flex gap-2"><button type="button" id="pw-cancel" class="flex-1 bg-gray-700 rounded py-2">Cancel</button><button class="flex-1 bg-purple-600 rounded py-2 font-bold">Save</button></div></form>`;
    document.body.appendChild(m); m.querySelector('#pw-cancel').onclick = () => m.remove();
    m.querySelector('form').onsubmit = async (e) => {
      e.preventDefault(); const o = $('pw-old').value, n = $('pw-new').value;
      if (n.length < 8 || n !== $('pw-new2').value) return alert('Passwords must match and be 8+ characters.');
      try {
        await call('/account/password', 'POST', { old_password: o, new_password: n }, () => {
          const u = SERVER_DB.users[currentUserId]; if (u.password !== 'GOOGLE_OAUTH_SECURE' && u.password !== o) throw new Error('Current password is wrong.');
          u.password = n; saveDB(); return { status: 'ok' };
        });
        m.remove(); log('Password updated.');
      } catch (err) { alert(err.message); }
    };
  }

  const PAGES = {
    home: () => V2().go('#/'), shop: () => V2().go('#/shop'), profile: () => $('profile-nav-link').click(), sell: () => $('sell-nav-link').click(),
    about: () => $('about-nav-link').click(), chat: () => $('chat-nav-link').click(), chats: () => $('chat-nav-link').click(), cart: () => openModal('cart-modal'),
    notifications: () => $('notification-button').click(), rewards: () => openModal('rewards-modal'), 'daily rewards': () => openModal('rewards-modal'),
    spin: () => openModal('lucky-spin-modal'), wheel: () => openModal('lucky-spin-modal'), guide: () => openModal('guide-modal'), game: () => openModal('game-modal'),
    arcade: () => openModal('game-modal'), ads: () => openAdScheduler(), advertising: () => openAdScheduler(), recommended: () => $('recommended-link').click(),
    language: () => openModal('language-modal'), analytics: () => { $('view-analytics-btn').click(); }, 'local shops': () => V2().go('#/local'),
  };
  const LANGS = { english: 'en', hindi: 'hi', tamil: 'ta', spanish: 'es', french: 'fr', marathi: 'mr', russian: 'ru', chinese: 'zh' };

  // ---------------- command table: [help, regex, handler] ----------------
  const CMDS = [
    ['search <product>', /^(?:search|find|look for)(?: for)? (.+)$/i, (m) => showBoard(m[1])],
    ['change username to <name>', /^(?:change|update|set) (?:my )?(?:user ?name|name)(?: to)? (.+)$/i, async (m) => {
      if (needLogin()) return; const name = m[1].trim().replace(/^["“]|["”]$/g, '');
      if (checkFraud(name).isVulgar) return log('That username is not allowed.');
      try {
        await call('/account/username', 'POST', { username: name }, () => {
          if (Object.values(SERVER_DB.users).some((u) => u.name.toLowerCase() === name.toLowerCase() && u.id !== currentUserId)) throw new Error('Username already taken.');
          SERVER_DB.users[currentUserId].name = name; SERVER_DB.listings.forEach((l) => l.sellerId === currentUserId && (l.sellerName = name)); saveDB(); return { status: 'ok' };
        });
        localStorage.setItem('apperture_user_name', name); log(`Username is now “${name}”.`); runSync();
      } catch (e) { log(e.message); }
    }],
    ['change password (opens a private box)', /^(?:change|update|reset) (?:my )?password$/i, () => (needLogin() ? 0 : (passwordDialog(), log('Opening the private password box. I never hear or store what you type.')))],
    ['update address to <place>', /^(?:update|change|set) (?:my )?(?:address|location) (?:to )?(.+)$/i, async (m) => {
      if (needLogin()) return; await API.request('/profile', 'PUT', { address: m[1].trim() }); log(`Address saved: ${m[1]}`); runSync();
    }],
    ['change tags of <listing> to #a #b', /^(?:change|set|update|replace) tags (?:of|for|on) (.+?) (?:to|with|as) (.+)$/i, async (m) => {
      if (needLogin()) return; const l = need(m[1], true); if (!l) return;
      const tags = (m[2].match(/[a-z0-9_]+/gi) || []).filter((t) => !/^(and|hash|tag|tags)$/i.test(t)).map((t) => '#' + t.toLowerCase());
      if (!tags.length) return log('Tell me at least one tag.');
      await call(`/listings/${l.id}/tags`, 'PUT', { tags }, () => { l.description = (l.description || '').replace(/#[a-z0-9_]+/gi, '').trimEnd() + '\n' + tags.join(' '); saveDB(); return { status: 'ok' }; });
      log(`Tags for “${l.title}” are now ${tags.join(' ')}`); runSync();
    }],
    ['add tags to <listing>: #a #b', /^add tags? (?:to|on) (.+?)[: ]+((?:#?\w+ ?)+)$/i, async (m) => {
      if (needLogin()) return; const l = need(m[1], true); if (!l) return;
      const t = [...new Set([...(l.description.match(/#[a-z0-9_]+/gi) || []), ...(m[2].match(/[a-z0-9_]+/gi) || []).map((x) => '#' + x)].map((x) => x.toLowerCase()))];
      await call(`/listings/${l.id}/tags`, 'PUT', { tags: t }, () => { l.description = l.description.replace(/#[a-z0-9_]+/gi, '').trimEnd() + '\n' + t.join(' '); saveDB(); return { status: 'ok' }; });
      log(`Tags now: ${t.join(' ')}`); runSync();
    }],
    ['open page <home|shop|profile|sell|chat|cart|rewards|spin|guide|arcade|ads|recommended|about…>', /^open (?:the )?(?:page )?(.+?)(?: page)?$/i, (m) => {
      const k = m[1].toLowerCase().trim();
      if (PAGES[k]) return leave(PAGES[k]);
      const l = find(k); if (l) return leave(() => openDetail(l.id)); log(`I don't know a page or product called “${k}”.`);
    }],
    ['open product <name>', /^(?:open|show|view) (?:product|listing|item) (.+)$/i, (m) => { const l = need(m[1]); if (l) leave(() => openDetail(l.id)); }],
    ['make link for <product> (Google + Apperture share link)', /^(?:make|create|get|generate)?\s*(?:a )?(?:google |share )?link (?:for|to|of) (.+)$/i, (m) => {
      const q = m[1].trim(), g = 'https://www.google.com/search?q=' + encodeURIComponent(q + ' Apperture'), a = location.origin + location.pathname + '#/find/' + encodeURIComponent(q);
      navigator.clipboard?.writeText(a).catch(() => {});
      log(`Links ready for “${q}” (Apperture link copied)`, `<a class="text-yellow-400 underline" href="${esc(a)}">🔎 Open on Apperture</a> — click it anywhere and it searches this product<br><a class="text-cyan-400 underline" target="_blank" rel="noopener" href="${esc(g)}">🌐 Search on Google</a>`);
    }],
    ['filter category <name>', /^(?:filter|category|show category) (.+)$/i, (m) => {
      const o = [...$('category-filter').options].find((x) => x.text.toLowerCase().includes(m[1].toLowerCase()));
      o ? leave(() => (V2().go('#/shop/' + encodeURIComponent(o.text)), log(`Showing ${o.text}`))) : log('No such category.');
    }],
    ['cheapest <product>', /^(?:cheapest|lowest price|best price) (.+)$/i, (m) => {
      const w = norm(m[1]), r = allListings.filter((l) => w.every((x) => norm(l.title + ' ' + l.description).includes(x))).sort((a, b) => a.price - b.price).slice(0, 5);
      log(`Cheapest “${m[1]}”`, r.map(card).join('<br>') || 'Nothing found.');
    }],
    ['most starred / top rated', /^(?:most starred|top rated|best rated|top products)$/i, () => log('Top rated', [...allListings].sort((a, b) => (b.starsCount || 0) - (a.starsCount || 0)).slice(0, 5).map(card).join('<br>') || 'No listings yet.')],
    ['price of <product>', /^(?:price of|how much is|how much for) (.+)$/i, (m) => { const l = need(m[1]); l && log(`${l.title}: ${money(l)}`, card(l)); }],
    ['local shops within <1.5-5> km', /^(?:local|nearby|near me)(?: shops| shoppes| stores)?(?: within (\d(?:\.\d)?) ?(?:km|kilometers?))?$/i, (m) => {
      if (m[1]) { const r = $('v2-radius'); r.value = Math.min(5, Math.max(1.5, +m[1])); r.dispatchEvent(new Event('input')); }
      leave(() => V2().go('#/local'));
    }],
    ['show tag <name>', /^(?:show |open )?(?:tag|hashtag|#) ?([a-z0-9_]+)$/i, (m) => leave(() => V2().go('#/tag/' + m[1].toLowerCase()))],
    ['trending tags', /^(?:trending|top|popular) (?:tags|hashtags)$/i, () => log('Trending tags', V2().trendingTags(15).map((t) => `<a class="text-cyan-400 mr-2" href="#/tag/${t.slice(1)}">${esc(t)}</a>`).join('') || 'No tags yet.')],
    ['claim daily reward', /^claim (?:my )?(?:daily )?(?:reward|bonus)$/i, () => { $('claim-reward-btn').click(); log('Claiming your reward…'); }],
    ['spin the wheel', /^spin(?: the)?(?: lucky)?(?: wheel)?$/i, () => { $('spin-btn').click(); log('Spinning…'); }],
    ['add <product> to cart', /^add (.+?) to (?:my )?cart$/i, (m) => { const l = need(m[1]); l && (addToCart(l.id), log(`Added ${l.title} to cart.`)); }],
    ['cart total', /^(?:cart total|what'?s in my cart|show my cart total)$/i, () => log(`Cart: ${myCart.length} item(s)`, myCart.map((l) => esc(l.title) + ' ' + esc(money(l))).join('<br>') + `<br><b>Total ${$('cart-total').textContent}</b>`)],
    ['clear cart', /^(?:clear|empty) (?:my )?cart$/i, () => { myCart.length = 0; updateCartUI(); log('Cart emptied.'); }],
    ['star <product>', /^(?:star|like|upvote) (.+)$/i, async (m) => { if (needLogin()) return; const l = need(m[1]); if (l) { await starItem(l.id); log(`Starred ${l.title}`); } }],
    ['compare <A> and <B>', /^compare (.+?) (?:and|vs|versus|with) (.+)$/i, (m) => {
      const a = need(m[1]), b = need(m[2]); if (!a || !b) return;
      const row = (l) => `${esc(l.title)}: ${esc(money(l))}, ⭐${l.starsCount || 0}, ${esc(l.condition || 'New')}, ${esc(l.address || 'Global')}`;
      const win = (b.starsCount || 0) > (a.starsCount || 0) ? b : a; const cheap = +a.price <= +b.price ? a : b;
      log('Comparison', `${row(a)}<br>${row(b)}<br><b>Trust pick:</b> ${esc(win.title)} · <b>Cheaper:</b> ${esc(cheap.title)}`);
    }],
    ['is <product> fake?', /^is (.+?) (?:fake|real|genuine|safe|legit)\??$/i, (m) => { const l = need(m[1]); l && (runAICheck(l.id), log(`Running AI check on ${l.title}…`)); }],
    ['offer <amount> for <product>', /^(?:offer|bid) (\d+) (?:for|on) (.+)$/i, (m) => {
      if (needLogin()) return; const l = need(m[2]); if (!l) return;
      leave(() => { openDetail(l.id); setTimeout(() => { $('negotiate-price-input').value = m[1]; $('send-offer-btn').click(); }, 300); });
    }],
    ['message seller of <product>', /^(?:message|chat with|contact) (?:the )?seller (?:of|for) (.+)$/i, (m) => { if (needLogin()) return; const l = need(m[1]); l && leave(() => startChat(l.sellerId, l.sellerName)); }],
    ['boost <my listing>', /^boost (.+)$/i, (m) => { if (needLogin()) return; const l = need(m[1], true); l && boostMyListing(l.id); }],
    ['delete <my listing>', /^delete (?:my )?(?:listing )?(.+)$/i, (m) => { if (needLogin()) return; const l = need(m[1], true); if (l && confirm(`Delete “${l.title}” permanently?`)) deleteMyListing(l.id); }],
    ['set language <hindi|tamil|spanish|…>', /^(?:set )?language(?: to)? (\w+)$/i, (m) => { const c = LANGS[m[1].toLowerCase()]; c ? (applyLanguage(c), log(`Language: ${m[1]}`)) : log('Unsupported language.'); }],
    ['read notifications', /^(?:read|show|check) (?:my )?notifications$/i, () => log(`${allNotifications.length} notification(s)`, allNotifications.slice(0, 5).map((n) => esc(n.message)).join('<br>') || 'All clear.')],
    ['turn off dead mode', /^(?:turn off|disable|end) dead mode$/i, () => { const b = document.querySelector('#v2-dead-banner button'); b ? (b.click(), log('Dead mode off. Your listings are visible again.')) : log('Your account is not in dead mode.'); }],
    ['my stats', /^(?:my )?(?:stats|balance|wallet)$/i, () => log('Your wallet', `⭐ ${userProfile.stars} stars · 💎 ${userProfile.gems} gems · 🎟 ${userProfile.tickets} tickets · ${allListings.filter((l) => l.sellerId === currentUserId).length} listings`)],
    ['my listings', /^(?:my|show my) listings$/i, () => log('Your listings', allListings.filter((l) => l.sellerId === currentUserId).map(card).join('<br>') || 'None yet.')],
    ['log out', /^(?:log ?out|sign out)$/i, () => { $('profile-logout-btn').click(); log('Logged out.'); }],
    ['scroll top / bottom', /^(?:scroll|go) (?:to )?(top|bottom)$/i, (m) => leave(() => window.scrollTo({ top: m[1] === 'top' ? 0 : document.body.scrollHeight, behavior: 'smooth' }))],
    ['voice on / off', /^(?:voice|speech) (on|off)$/i, (m) => { speakOn = m[1].toLowerCase() === 'on'; log(`Voice replies ${m[1]}.`); }],
    ['help', /^(?:help|commands|what can you do)$/i, () => log(`${CMDS.length} commands`, CMDS.map((c) => '• ' + esc(c[0])).join('<br>'))],
  ];

  async function run(text, board) {
    showBoard = board || (V2() && V2().showBoard) || showBoard;
    const t = String(text || '').replace(/^\s*hey nexus[, ]*/i, '').replace(/[.?!]+$/, '').replace(/[“”"]/g, '').trim();
    if (!t) return;
    for (const [, re, fn] of CMDS) { const m = re.exec(t); if (m) { try { await fn(m); } catch (e) { log('Error: ' + e.message); } return; } }
    log(`I didn't recognise “${t}”, so I searched for it. Say “help” for commands.`); showBoard(t);
  }
  window.NexusCmd = { run, list: () => CMDS.map((c) => c[0]) };

  // typed command box inside the Nexus overlay
  const ov = $('nx-overlay') || $('nexus-overlay');
  if (ov && !$('nx-type')) {
    const f = document.createElement('form'); f.className = 'w-full max-w-3xl mt-4 flex gap-2';
    f.innerHTML = '<input id="nx-type" autocomplete="off" placeholder="Type a command… e.g. make link for red hoodie" class="flex-1 bg-gray-900 border border-cyan-700 rounded-full px-5 py-3 text-white outline-none"><button class="bg-cyan-600 rounded-full px-6 font-bold">Run</button>';
    $('nx-heard').after(f); f.onsubmit = (e) => { e.preventDefault(); const v = $('nx-type').value; $('nx-type').value = ''; run(v); };
  }

  // shareable search links: #/find/<product>
  function routeFind() {
    const m = /^#\/find\/(.+)$/.exec(location.hash); if (!m) return;
    const q = decodeURIComponent(m[1]); $('search-bar').value = q; renderGrid(); $('shop-section').scrollIntoView({ behavior: 'smooth' });
  }
  window.addEventListener('hashchange', routeFind); setTimeout(routeFind, 1500);
})();
