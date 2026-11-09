// Cinemack v8 page: one module, hash routes, no build step. Talks only to the site's own API (web/server.py,
// web/v8routes.py). Same browser id and access code as the classic page, so chats, taste and settings carry over.
const $ = (s, el = document) => el.querySelector(s);
const view = $('#view');
const store = {
  get(k) { try { return localStorage.getItem(k); } catch { return null; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch {} },
};
const newId = () => (crypto.randomUUID ? crypto.randomUUID() : String(Date.now()) + Math.random().toString(16).slice(2))
  .replace(/[^a-zA-Z0-9-]/g, '').slice(0, 36);
const S = {
  user: store.get('mb.user') || (() => { const u = newId(); store.set('mb.user', u); return u; })(),
  code: store.get('mb.code') || '',
  me: null, choices: {}, pick: null,
  chat: { convId: newId(), history: [], recent: [], conv: null, busy: false, ctrl: null, filters: {}, log: null },
};
const hdrs = (extra = {}) => Object.assign({ 'Content-Type': 'application/json', 'X-Access-Code': S.code, 'X-MB-User': S.user,
  'X-MB-Conv': S.chat.convId, 'X-MB-Private': '0' }, extra);
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const h = (tag, attrs = {}, ...kids) => {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === 'class') el.className = v;
    else if (k === 'html') el.innerHTML = v;
    else if (k.startsWith('on')) el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v === true ? '' : v);
  }
  for (const k of kids.flat(Infinity)) if (k != null && k !== false) el.append(k.nodeType ? k : document.createTextNode(k));
  return el;
};
const I = {
  bookmark: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round" aria-hidden="true"><path d="M6 3h12v18l-6-4-6 4z"/></svg>',
  bookmarkOn: '<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M6 3h12v18l-6-4-6 4z"/></svg>',
  star: '<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M12 2.5l2.9 6 6.6.8-4.9 4.6 1.3 6.6L12 17.3 6.1 20.5l1.3-6.6L2.5 9.3l6.6-.8z"/></svg>',
  send: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 19V5M5 12l7-7 7 7"/></svg>',
  stop: '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="7" y="7" width="10" height="10" rx="2" fill="currentColor"/></svg>',
  x: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18"/></svg>',
  up: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round" aria-hidden="true"><path d="M7 11v9H4v-9zM7 11l4-8a2 2 0 012 2v4h6a2 2 0 012 2.3l-1.3 7A2 2 0 0117.7 20H7"/></svg>',
  down: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round" aria-hidden="true"><path d="M17 13V4h3v9zM17 13l-4 8a2 2 0 01-2-2v-4H5a2 2 0 01-2-2.3l1.3-7A2 2 0 016.3 4H17"/></svg>',
  filter: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M4 6h16M7 12h10M10 18h4"/></svg>',
  chats: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round" aria-hidden="true"><path d="M4 5h16v11H9l-5 4z"/></svg>',
  plus: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M12 5v14M5 12h14"/></svg>',
  check: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M5 12.5l4.5 4.5L19 7"/></svg>',
};
const ART = {
  popcorn: '<svg class="art" viewBox="0 0 56 56" fill="none" aria-hidden="true"><path d="M14 22l4 28h20l4-28z" fill="#003767" stroke="#FFDD00" stroke-width="2" stroke-linejoin="round"/><path d="M22 22l1.5 28M34 22l-1.5 28" stroke="#FFDD00" stroke-width="2"/><circle cx="18" cy="18" r="6" fill="#FFF6C2" stroke="#FFDD00" stroke-width="2"/><circle cx="28" cy="14" r="7" fill="#FFF6C2" stroke="#FFDD00" stroke-width="2"/><circle cx="38" cy="18" r="6" fill="#FFF6C2" stroke="#FFDD00" stroke-width="2"/></svg>',
  ticket: '<svg class="art" viewBox="0 0 56 56" fill="none" aria-hidden="true"><g transform="rotate(-12 28 28)"><path d="M8 16h40v8a4 4 0 000 8v8H8v-8a4 4 0 000-8z" fill="#003767" stroke="#FFDD00" stroke-width="2" stroke-linejoin="round"/><path d="M36 17v22" stroke="#FFDD00" stroke-width="2" stroke-dasharray="3 3"/><path d="M17 23l9 5-9 5z" fill="#FFDD00"/></g></svg>',
  reel: '<svg class="art" viewBox="0 0 56 56" fill="none" aria-hidden="true"><circle cx="26" cy="26" r="18" fill="#003767" stroke="#FFDD00" stroke-width="2"/><circle cx="26" cy="26" r="3" fill="#FFDD00"/><g fill="#FFF6C2" stroke="#FFDD00" stroke-width="1.5"><circle cx="26" cy="15" r="5"/><circle cx="36" cy="27" r="5"/><circle cx="26" cy="37" r="5"/><circle cx="16" cy="27" r="5"/></g><path d="M40 40c6 2 10 2 12-1" stroke="#FFDD00" stroke-width="2" stroke-linecap="round"/></svg>',
};
// How each Home row is introduced: a title that says what the row is for, and one plain line.
const RAIL_COPY = {
  watchlist: ['Your list', 'Films you saved for later.'],
  now_playing: ['On the big screen now', 'New releases in US theatres this season.'],
  back_in_theatres: ['Back on the big screen', 'Older films getting another run in theatres.'],
  coming_soon: ['Opening soon', 'Dated US releases from the next few weeks.'],
  new_popular: ["What everyone's watching", 'The most-viewed new releases of the last four months.'],
};
const emptyBox = (text, art, href, label) => h('div', { class: 'empty' }, h('div', { class: 'say', html: ART[art] || '' }, h('p', {}, text)),
  href ? h('a', { class: 'btn secondary small', href }, label) : null);
const runtime = (m) => m ? `${Math.floor(m / 60)} h ${m % 60} min` : '';
const rt = (m) => m ? `${Math.floor(m / 60)}h ${String(m % 60).padStart(2, '0')}m` : '';

// ---------------------------------------------------------------- API
async function api(path, opts = {}) {
  const res = await fetch(path, Object.assign({ headers: hdrs(), cache: 'no-store' }, opts));
  let j = {}; try { j = await res.json(); } catch {}
  if (res.status === 401 && /access code/i.test(j.error || '')) { gate(); throw Object.assign(new Error('Enter the access code.'), { status: 401 }); }
  if (!res.ok) throw Object.assign(new Error(j.error || `The server answered ${res.status}.`), { status: res.status });
  return j;
}
const post = (path, body) => api(path, { method: 'POST', body: JSON.stringify(body || {}) });

function toast(msg, undo = null) {
  const slot = $('#toast-slot'); slot.innerHTML = '';
  const t = h('div', { class: 'toast', role: 'status' }, h('span', {}, msg));
  if (undo) t.append(h('button', { type: 'button', onclick: () => { undo(); t.remove(); } }, 'Undo'));
  slot.append(t); clearTimeout(toast.t); toast.t = setTimeout(() => t.remove(), 5000);
}

// ---------------------------------------------------------------- dialogs
function sheet(title, build) {
  const d = h('dialog', { class: 'sheet', 'aria-label': title });
  const body = h('div', { class: 'sh-b' });
  d.append(h('div', { class: 'sh-h' }, h('h2', {}, title),
    h('button', { class: 'iconbtn', type: 'button', 'aria-label': 'Close', html: I.x, onclick: () => d.close() })), body);
  d.addEventListener('close', () => d.remove());
  d.addEventListener('click', (e) => { if (e.target === d) d.close(); });
  document.body.append(d);
  build(body, d);
  d.showModal();
  return d;
}

function gate(message = '') {
  if ($('#gate-dialog')) return;
  sheet('Access code', (b, d) => {
    d.id = 'gate-dialog';
    const inp = h('input', { class: 'input', type: 'password', id: 'gate-code', autocomplete: 'current-password' });
    const err = h('p', { class: 'err', 'aria-live': 'polite' }, message);
    b.append(h('p', { class: 'muted' }, 'Cinemack runs on a shared GPU, so it is open to people with the code.'),
      h('div', { class: 'field' }, h('label', { for: 'gate-code' }, 'Code'), inp), err,
      h('button', { class: 'btn primary', type: 'button', onclick: async () => {
        S.code = inp.value.trim(); store.set('mb.code', S.code);
        try { await api('/api/health'); d.close(); route(); } catch { err.textContent = "That code didn't work."; }
      } }, 'Continue'));
    setTimeout(() => inp.focus(), 50);
  });
}

// ---------------------------------------------------------------- film choices (shared with the classic page)
async function setChoice(f, choice, quiet = false) {
  const prev = S.choices[f.id] ?? f.mine ?? '';
  S.choices[f.id] = choice; syncChoice(f.id);
  try {
    const j = await post('/api/film', { id: f.id, title: f.title || '', choice });
    if (!quiet) toast(j.message, () => setChoice(f, prev, true));
  } catch (e) { S.choices[f.id] = prev; syncChoice(f.id); toast(e.message || "Couldn't save that."); }
}
function syncChoice(id) {
  document.querySelectorAll(`[data-want="${id}"]`).forEach(b => {
    const on = S.choices[id] === 'want';
    b.setAttribute('aria-pressed', String(on));
    if (b._label) b._label(); else b.innerHTML = on ? I.bookmarkOn : I.bookmark;
  });
  document.querySelectorAll(`[data-choice-for="${id}"]`).forEach(b => b.setAttribute('aria-pressed', String(S.choices[id] === b.dataset.choice)));
}

function posterImg(f, size = 'poster') {
  const src = f[size] || f.poster || f.poster_lg;
  if (!src) return h('span', { class: 'ph' }, f.title || '');
  return h('img', { src, alt: '', loading: 'lazy', decoding: 'async', width: 160, height: 240 });
}
function card(f, opts = {}) {
  if (!(f.id in S.choices)) S.choices[f.id] = f.mine || '';
  const href = f.media === 'tv' ? null : `#/film/${f.id}`;
  const name = `${f.title || 'Untitled'}${f.year ? ` (${f.year})` : ''}`;
  const c = h('div', { class: 'card' },
    h(href ? 'a' : 'span', { class: 'pl', href, 'aria-label': href ? `${name}: details` : null, tabindex: href ? null : null }, posterImg(f)),
    h(href ? 'a' : 'span', { class: 'ti', href, tabindex: href ? '-1' : null }, f.title || 'Untitled'),
    h('div', { class: 'me' }, [f.year, f.rating != null ? `★ ${f.rating}` : null, f.runtime ? rt(f.runtime) : null].filter(Boolean).join('  ')));
  if (opts.why) c.append(h('div', { class: 'why' }, opts.why));
  if (f.on_services && f.on_services.length) c.append(h('div', { class: 'why' }, 'On your ' + f.on_services.join(', ')));
  if (f.id != null && f.media !== 'tv') {
    const on = S.choices[f.id] === 'want';
    c.append(h('button', { class: 'bm', type: 'button', 'data-want': f.id, 'aria-pressed': String(on),
      'aria-label': `Want to watch ${name}`, html: on ? I.bookmarkOn : I.bookmark,
      onclick: (e) => { const b = e.currentTarget; b.classList.remove('pop'); void b.offsetWidth; b.classList.add('pop'); setChoice(f, S.choices[f.id] === 'want' ? '' : 'want'); } }));
  }
  return c;
}
function rail(title, items, opts = {}) {
  const sec = h('section', { class: 'section', 'aria-label': title });
  const head = h('div', { class: 'section-h' }, h('h2', {}, title));
  if (opts.lede) head.append(h('p', { class: 'lede' }, opts.lede));
  if (opts.src) head.append(h('span', { class: 'src' }, opts.src));
  if (opts.more) head.append(h('a', { href: opts.more, class: 'small' }, 'See all'));
  sec.append(head);
  if (!items.length) {
    if (!opts.empty) return null;
    sec.append(emptyBox(opts.empty[0], opts.empty[3] || 'popcorn', opts.empty[1], opts.empty[2]));
    return sec;
  }
  sec.append(h('div', { class: 'rail', tabindex: '0', role: 'list', 'aria-label': title },
    items.map(f => { const c = card(f, { why: opts.why }); c.setAttribute('role', 'listitem'); return c; })));
  return sec;
}
function skeletonRail() {
  return h('section', { class: 'section', 'aria-hidden': 'true' }, h('div', { class: 'skel', style: 'width:180px;height:20px;margin-bottom:12px' }),
    h('div', { class: 'rail' }, Array.from({ length: 6 }, () => h('div', { class: 'skel', style: 'aspect-ratio:2/3' }))));
}

// ---------------------------------------------------------------- Home
async function pageHome() {
  view.innerHTML = '';
  const hero = h('section', { class: 'hero', 'aria-label': 'Ask Cinemack' }, h('div', { class: 'bd', id: 'hero-bd' }),
    h('div', { class: 'bulbs t', 'aria-hidden': 'true' }), h('div', { class: 'bulbs b', 'aria-hidden': 'true' }), h('div', { class: 'stage', id: 'hero-stage' }));
  const q = h('input', { class: '', id: 'home-q', type: 'search', placeholder: 'Describe a film or a scene', 'aria-label': 'Ask Cinemack', enterkeyhint: 'send' });
  const go = () => { const t = q.value.trim(); if (t) { S.pendingAsk = t; location.hash = '#/discover'; } };
  q.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); go(); } });
  hero.append(h('div', { class: 'in' },
    h('div', { id: 'hero-cont' }),
    h('div', { class: 'feat', id: 'hero-feat', 'aria-live': 'off' }),
    h('h1', {}, 'What are we watching tonight?'),
    h('p', { class: 'lead' }, "Describe the scene you half-remember, ask where something's streaming, or let me pick three films for tonight."),
    h('div', { class: 'askbox' }, q, h('button', { class: 'btn primary', type: 'button', onclick: go, 'aria-label': 'Ask', html: I.send })),
    h('div', { class: 'row' }, h('a', { class: 'btn secondary', href: '#/pick' }, 'Pick tonight for me'),
      h('a', { class: 'btn ghost', href: '#/calendar' }, "What's opening soon"))));
  view.append(hero);
  const genres = ['Comedy', 'Thriller', 'Drama', 'Science Fiction', 'Horror', 'Animation', 'Romance', 'Documentary', 'Crime', 'Family'];
  view.append(h('nav', { class: 'chips scroll', 'aria-label': 'Browse by genre' }, genres.map(g =>
    h('a', { class: 'chip', href: `#/discover?genre=${encodeURIComponent(g)}` }, g === 'Science Fiction' ? 'Sci-fi' : g))));
  const holder = h('div', {}, skeletonRail(), skeletonRail());
  view.append(holder);
  let data;
  try { data = await api('/api/v8/home'); } catch (e) { holder.innerHTML = ''; holder.append(h('div', { class: 'notice' }, e.message)); return; }
  holder.innerHTML = '';
  const byKey = Object.fromEntries(data.rails.map(r => [r.key, r]));
  const cont = (byKey.continue?.items || [])[0];
  if (cont) $('#hero-cont')?.append(h('a', { class: 'cont', href: `#/discover?chat=${encodeURIComponent(cont.id)}` },
    h('span', { html: I.chats.replace('<svg', '<svg width="16" height="16"') }), h('span', {}, `Continue: ${cont.title || 'your last chat'}`)));
  const showing = [...(byKey.now_playing?.items || []), ...(byKey.new_popular?.items || [])].filter(f => f.id && (f.poster_lg || f.poster)).slice(0, 6);
  heroStage(showing);
  const ORDER = ['continue', 'watchlist', 'now_playing', 'coming_soon', 'new_popular', 'back_in_theatres'];
  const rank = (k) => k.startsWith('because_') ? 2.5 : (ORDER.indexOf(k) < 0 ? 99 : ORDER.indexOf(k));
  const rails = data.rails.slice().sort((a, b) => rank(a.key) - rank(b.key));
  const wl = rails.findIndex(r => r.key === 'watchlist');
  if (wl >= 0 && !rails[wl].items.length) rails.push(rails.splice(wl, 1)[0]);     // posters first for new visitors
  for (const r of rails) {
    if (r.key === 'continue') continue;             // lives in the banner and the Chats panel
    const copy = RAIL_COPY[r.key] || [r.title, r.key.startsWith('because_') ? 'People who rated it highly also loved these (MovieLens).' : null];
    const el = rail(copy[0], r.items || [], { lede: copy[1],
      src: r.source ? `${r.source}${r.as_of ? `, ${r.as_of}` : ''}` : null,
      why: r.key.startsWith('because_') ? null : null,
      more: r.key === 'watchlist' && r.items.length ? '#/watchlist' : r.key === 'coming_soon' ? '#/calendar' : null,
      empty: r.key === 'watchlist' ? ['Nothing saved yet. Tap the bookmark on any poster and it waits for you here.', '#/discover', 'Find something', 'popcorn'] : null,
    });
    if (el) holder.append(el);
  }
  if (!data.rails.some(r => r.key.startsWith('because_'))) {
    holder.append(h('div', { class: 'notice' }, 'Tell me what you liked (the thumbs-up on any film page) and this page grows "Because you liked…" rows.'));
  }
}
// One poster at a time, cross-fading every 7 s (1.2 s fade, no overlap). The backdrop and the "In theatres now" line
// follow it. Pauses while hovered or focused; holds still for reduced motion.
function heroStage(films) {
  const stage = $('#hero-stage'), bd = $('#hero-bd'), feat = $('#hero-feat');
  if (!films.length || !stage) return;
  clearInterval(heroStage.t);
  const backdrops = {};
  const posters = films.map((f, i) => h('a', { href: `#/film/${f.id}`, 'aria-label': `${f.title}: details`, tabindex: i ? '-1' : '0', class: i ? '' : 'on' },
    h('img', { src: f.poster_lg || f.poster, alt: '', width: 220, height: 330, loading: i ? 'lazy' : 'eager' })));
  const dots = h('div', { class: 'dots', role: 'group', 'aria-label': 'Choose a film' });
  stage.append(...posters, dots);
  let cur = 0, paused = false;
  const reduce = matchMedia('(prefers-reduced-motion: reduce)').matches;
  const show = async (i) => {
    cur = (i + films.length) % films.length;
    posters.forEach((p, j) => { p.classList.toggle('on', j === cur); p.tabIndex = j === cur ? 0 : -1; });
    [...dots.children].forEach((d, j) => d.setAttribute('aria-current', String(j === cur)));
    const f = films[cur];
    if (feat) { feat.innerHTML = ''; feat.append('In theatres now: ', h('a', { href: `#/film/${f.id}` }, f.title)); }
    if (bd) {
      if (!(f.id in backdrops)) { try { backdrops[f.id] = (await api(`/api/film/${f.id}`)).backdrop || ''; } catch { backdrops[f.id] = ''; } }
      if (films[cur] !== f) return;
      bd.classList.remove('on');
      setTimeout(() => { if (films[cur] === f) { bd.style.backgroundImage = backdrops[f.id] ? `url("${backdrops[f.id]}")` : ''; if (backdrops[f.id]) bd.classList.add('on'); } }, reduce ? 0 : 400);
    }
  };
  films.forEach((f, j) => dots.append(h('button', { type: 'button', 'aria-label': f.title, onclick: () => show(j) })));
  const hero = stage.closest('.hero');
  hero.addEventListener('mouseenter', () => { paused = true; });
  hero.addEventListener('mouseleave', () => { paused = false; });
  hero.addEventListener('focusin', () => { paused = true; });
  hero.addEventListener('focusout', () => { paused = false; });
  show(0);
  if (!reduce && films.length > 1) heroStage.t = setInterval(() => { if (!paused && document.visibilityState === 'visible' && $('#hero-stage') === stage) show(cur + 1); else if (!document.body.contains(stage)) clearInterval(heroStage.t); }, 7000);
}

// ---------------------------------------------------------------- Detail
async function pageFilm(id) {
  view.innerHTML = '';
  view.append(h('div', { class: 'skel', style: 'height:260px;margin-bottom:16px' }));
  let d;
  try { d = await api(`/api/film/${id}`); } catch (e) { view.innerHTML = ''; view.append(h('div', { class: 'notice' }, e.message || 'No details for that film yet.')); return; }
  document.title = `${d.title} - Cinemack`;
  S.choices[d.id] = S.choices[d.id] ?? d.mine ?? '';
  view.innerHTML = '';
  const heroEl = h('div', { class: 'detail-hero' }, h('div', { class: 'bd', style: d.backdrop ? `background-image:url("${d.backdrop}")` : '' }));
  const facts = [d.year, d.runtime ? runtime(d.runtime) : null, d.cert, (d.genres || []).slice(0, 3).join(', ')].filter(Boolean);
  const head = h('div', { class: 'detail-head' },
    h('div', { class: 'pl' }, d.poster ? h('img', { src: d.poster, alt: `Poster for ${d.title}`, width: 220, height: 330 }) : h('span', { class: 'ph' })),
    h('div', {}, h('h1', {}, d.title), h('div', { class: 'facts' }, facts.map(x => h('span', {}, x))),
      d.directors && d.directors.length ? h('div', { class: 'facts' }, 'Directed by ' + d.directors.join(', ')) : null));
  const scores = h('div', { class: 'scores' });
  const add = (k, v, tip) => scores.append(h('span', { class: 'score', title: tip }, h('b', {}, v), k));
  if (d.imdb?.rating) add('IMDb', Number(d.imdb.rating).toFixed(1), `${Number(d.imdb.votes || 0).toLocaleString()} votes`);
  if (d.tmdb_rating) add('TMDB', d.tmdb_rating, `${Number(d.tmdb_votes || 0).toLocaleString()} votes`);
  if (d.critics?.rt != null) add('Rotten Tomatoes', d.critics.rt + '%', 'critics positive');
  if (d.critics?.mc != null) add('Metacritic', d.critics.mc, 'out of 100');
  const want = h('button', { class: 'btn primary want', type: 'button', 'aria-label': 'Want to watch', 'data-want': d.id, 'aria-pressed': String(S.choices[d.id] === 'want'),
    onclick: () => setChoice(d, S.choices[d.id] === 'want' ? '' : 'want') }, 'Want to watch');
  const wantLabel = () => { want.innerHTML = S.choices[d.id] === 'want' ? I.bookmarkOn + 'On your watchlist' : I.bookmark + 'Want to watch'; };
  wantLabel(); want._label = wantLabel;
  const watched = h('button', { class: 'btn secondary', type: 'button', onclick: async () => {
    try { await post('/api/v8/watched', { id: d.id }); toast(`Added ${d.title} to your history.`); } catch (e) { toast(e.message); } } }, 'Watched');
  const liked = h('button', { class: 'btn secondary', type: 'button', 'data-choice-for': d.id, 'data-choice': 'liked',
    'aria-pressed': String(S.choices[d.id] === 'liked'), html: I.up + 'Liked it', onclick: () => setChoice(d, S.choices[d.id] === 'liked' ? '' : 'liked') });
  const not = h('button', { class: 'btn secondary', type: 'button', 'data-choice-for': d.id, 'data-choice': 'not',
    'aria-pressed': String(S.choices[d.id] === 'not'), html: I.down + 'Not for me', onclick: () => setChoice(d, S.choices[d.id] === 'not' ? '' : 'not') });
  const rate = h('button', { class: 'btn ghost', type: 'button', onclick: () => reviewSheet(d) }, 'Rate or review');
  const main = h('div', {});
  if (d.tagline) main.append(h('p', { class: 'muted' }, d.tagline));
  if (d.overview) {
    const ov = h('p', { class: 'overview clamp' }, d.overview);
    main.append(ov);
    if (d.overview.length > 260) main.append(h('button', { class: 'btn ghost small', type: 'button', onclick: (e) => { ov.classList.toggle('clamp'); e.target.textContent = ov.classList.contains('clamp') ? 'Read more' : 'Show less'; } }, 'Read more'));
  }
  main.append(h('div', { class: 'actions' }, h('a', { class: 'btn secondary', href: `#/discover?ask=${encodeURIComponent(`Tell me about ${d.title} (${d.year})`)}` }, 'Ask Cinemack about this film'),
    d.links?.trailer ? h('a', { class: 'btn ghost', href: d.links.trailer.url, target: '_blank', rel: 'noopener' }, 'Watch the trailer') : null));
  if (d.cast && d.cast.length) {
    main.append(h('h2', { style: 'margin:24px 0 12px' }, 'Cast'), h('div', { class: 'cast' }, d.cast.map(p =>
      h('div', { class: 'p' }, h('div', { class: 'ph' }, p.photo ? h('img', { src: p.photo, alt: '', loading: 'lazy', width: 72, height: 72 }) : null),
        h('div', {}, p.name), h('div', { class: 'ch' }, p.character || '')))));
  }
  if (d.reception && (d.reception.praise?.length || d.reception.complaints?.length)) {
    const r = d.reception;
    main.append(h('h2', { style: 'margin:24px 0 12px' }, 'How it was received'),
      r.praise?.length ? h('p', {}, h('b', {}, 'Praised: '), r.praise.map(x => x.aspect || x.text || x).join(', ')) : null,
      r.complaints?.length ? h('p', {}, h('b', {}, 'Criticised: '), r.complaints.map(x => x.aspect || x.text || x).join(', ')) : null);
  }
  const side = h('aside', {});
  const p = d.links?.providers || {};
  const label = { flatrate: 'Stream', free: 'Free', ads: 'With ads', rent: 'Rent', buy: 'Buy' };
  const prov = Object.keys(label).filter(k => (p[k] || []).length);
  side.append(h('h2', { style: 'margin:24px 0 12px' }, 'Where to watch (US)'));
  if (prov.length) side.append(h('div', { class: 'providers' }, prov.map(k => h('div', { class: 'row' }, h('span', { class: 'k' }, label[k]),
    p[k].slice(0, 6).map(n => h('span', { class: `tag${(S.me?.settings?.services || []).some(s => n.toLowerCase().includes(s)) ? ' on' : ''}` }, n))))),
    h('p', { class: 'tiny faint', style: 'margin-top:8px' }, 'Listings from JustWatch via TMDB; they change often.'));
  else side.append(h('p', { class: 'muted small' }, 'No US streaming, rental or purchase listing on record.'));
  if (d.money?.budget || d.money?.gross) {
    const m = (v) => v >= 1e9 ? `$${(v / 1e9).toFixed(2)} billion` : `$${(v / 1e6).toFixed(1)} million`;
    side.append(h('h2', { style: 'margin:24px 0 12px' }, 'Box office'), h('p', { class: 'small' },
      d.money.budget ? `Budget ${m(d.money.budget)}. ` : '', d.money.gross ? `Worldwide gross ${m(d.money.gross)}.` : ''));
  }
  const links = [d.imdb_url && ['IMDb', d.imdb_url], d.wiki && ['Wikipedia', d.wiki.url], ['TMDB', d.tmdb_url]].filter(Boolean);
  side.append(h('p', { class: 'small', style: 'margin-top:16px' }, links.map(([t, u], i) => [i ? '  ' : '', h('a', { href: u, target: '_blank', rel: 'noopener' }, t)])));
  view.append(heroEl, head, scores, h('div', { class: 'actions' }, want, watched, liked, not, rate), h('div', { class: 'detail-body' }, main, side));
  const sim = h('div', {}, skeletonRail()); view.append(sim);
  try { const s = await api(`/api/v8/similar/${d.id}`); sim.innerHTML = ''; const r = rail('If you like this', s.items || [], { src: 'MovieLens ratings' }); if (r) sim.append(r); }
  catch { sim.innerHTML = ''; }
}
function reviewSheet(d) {
  sheet(`Rate ${d.title}`, (b, dlg) => {
    let val = 0;
    const stars = h('div', { class: 'stars', role: 'radiogroup', 'aria-label': 'Rating out of 5' });
    for (let i = 1; i <= 5; i++) {
      const s = h('button', { type: 'button', role: 'radio', 'aria-checked': 'false', 'aria-label': `${i} star${i > 1 ? 's' : ''}`, html: I.star,
        onclick: () => { val = i; [...stars.children].forEach((x, j) => { x.classList.toggle('on', j < i); x.setAttribute('aria-checked', String(j + 1 === i)); }); } });
      stars.append(s);
    }
    const txt = h('textarea', { class: 'input', id: 'rv-text', maxlength: '4000' });
    b.append(h('div', { class: 'field' }, h('span', { class: 'lab' }, 'Your rating'), stars),
      h('div', { class: 'field' }, h('label', { for: 'rv-text' }, 'Review (optional)'), txt,
        h('span', { class: 'hint' }, 'Private unless you share ratings or reviews with friends in Profile → Privacy.')),
      h('button', { class: 'btn primary', type: 'button', onclick: async () => {
        try { await post('/api/v8/review', { id: d.id, rating: val || null, text: txt.value }); dlg.close(); toast('Saved your rating.'); } catch (e) { toast(e.message); }
      } }, 'Save rating'));
  });
}

// ---------------------------------------------------------------- Discover (chat)
const FILTERS = { genre: ['Any genre', 'Action', 'Animation', 'Comedy', 'Crime', 'Documentary', 'Drama', 'Family', 'Fantasy', 'Horror', 'Mystery', 'Romance', 'Science Fiction', 'Thriller'],
  era: [['', 'Any era'], ['pre1970', 'Before 1970'], ['1970s', '1970s'], ['1980s', '1980s'], ['1990s', '1990s'], ['2000s', '2000s'], ['2010s', '2010s'], ['2020s', '2020s']],
  max_runtime: [['', 'Any length'], ['90', 'Under 1½ h'], ['120', 'Under 2 h'], ['150', 'Under 2½ h']],
  max_cert: [['', 'Any rating'], ['G', 'G'], ['PG', 'PG or milder'], ['PG-13', 'PG-13 or milder'], ['R', 'R or milder']] };
function md(src) {
  const lines = esc(src).split(/\n/); let out = '', list = null;
  const inline = (t) => t.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>').replace(/(^|[^*])\*(?!\s)(.+?)\*/g, '$1<em>$2</em>');
  for (const l of lines) {
    const m = l.match(/^\s*(?:[-*]|\d+\.)\s+(.*)/);
    if (m) { if (!list) { out += '<ul>'; list = true; } out += `<li>${inline(m[1])}</li>`; continue; }
    if (list) { out += '</ul>'; list = null; }
    if (l.trim()) out += `<p>${inline(l)}</p>`;
  }
  return out + (list ? '</ul>' : '');
}
function pageDiscover(params) {
  view.innerHTML = '';
  const c = S.chat;
  if (params.get('genre')) c.filters = { genre: params.get('genre') };
  const wrap = h('div', { class: 'chat' });
  const msgs = h('div', { class: 'msgs', id: 'msgs', 'aria-live': 'polite' });
  if (c.log && c.log.length) msgs.append(...c.log); else msgs.append(chatIntro());
  const ta = h('textarea', { id: 'chat-q', rows: '1', placeholder: 'Ask about a film, a scene, or what to watch', 'aria-label': 'Message', enterkeyhint: 'send' });
  const send = h('button', { class: 'btn primary', type: 'button', id: 'chat-send', 'aria-label': 'Send', html: I.send });
  const grow = () => { ta.style.height = 'auto'; ta.style.height = Math.min(ta.scrollHeight, 160) + 'px'; };
  ta.addEventListener('input', grow);
  ta.addEventListener('keydown', (e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); submit(); } });
  const submit = () => { if (c.busy) { c.ctrl?.abort(); return; } const t = ta.value.trim(); if (t) { ta.value = ''; grow(); ask(t, msgs, send); } };
  send.addEventListener('click', submit);
  const fcount = () => Object.values(c.filters).filter(Boolean).length;
  const fbtn = h('button', { class: 'btn secondary small', type: 'button', html: I.filter + `Filters${fcount() ? ` (${fcount()})` : ''}`, onclick: () => filterSheet(fbtn) });
  const composer = h('div', { class: 'composer' },
    h('div', { class: 'bar' }, fbtn,
      h('button', { class: 'btn ghost small', type: 'button', html: I.chats + '<span class="lbl">Saved chats</span>', onclick: () => chatsSheet(msgs) }),
      h('button', { class: 'btn ghost small', type: 'button', html: I.plus + '<span class="lbl">New chat</span>', onclick: () => { newChat(); pageDiscover(new URLSearchParams()); } })),
    h('div', { class: 'askbox' }, ta, send),
    h('div', { class: 'fine' }, 'Answers can be wrong: every answer shows the films it was drawn from.'));
  wrap.append(msgs, composer);
  view.append(wrap);
  const pre = S.pendingAsk || params.get('ask'); S.pendingAsk = null;
  if (params.get('chat')) loadChat(params.get('chat'), msgs);
  else if (pre) ask(pre, msgs, send);
  else ta.focus({ preventScroll: true });
}
function newChat() { Object.assign(S.chat, { convId: newId(), history: [], recent: [], conv: null, log: null }); }
function chatIntro() {
  const ex = ['The movie where a family of con artists talk their way into a rich household', 'Is Past Lives worth watching?',
    'Something like Arrival but lighter', 'Who directed the original Godzilla?'];
  return h('div', { class: 'chat-intro' }, h('h1', {}, 'Ask Cinemack'),
    h('p', { class: 'muted' }, 'Describe a film you half-remember, ask about one you know, or ask for recommendations. Every answer shows the films it came from.'),
    h('div', { class: 'examples' }, ex.map(t => h('button', { type: 'button', onclick: () => { $('#chat-q').value = t; $('#chat-send').click(); } }, t))));
}
function filterSheet(btn) {
  const c = S.chat;
  sheet('Filters for this chat', (b, d) => {
    const sel = (key, opts, labelText) => {
      const id = `f-${key}`;
      const s = h('select', { class: 'input', id }, opts.map(o => { const [v, l] = Array.isArray(o) ? o : [o === opts[0] ? '' : o, o]; return h('option', { value: v, selected: String(c.filters[key] || '') === String(v) }, l); }));
      s.addEventListener('change', () => { c.filters[key] = s.value || undefined; });
      return h('div', { class: 'field' }, h('label', { for: id }, labelText), s);
    };
    const svc = h('input', { type: 'checkbox', id: 'f-svc', checked: !!c.filters.services_only, onchange: (e) => { c.filters.services_only = e.target.checked || undefined; } });
    b.append(sel('genre', FILTERS.genre, 'Genre'), sel('era', FILTERS.era, 'Era'), sel('max_runtime', FILTERS.max_runtime, 'Length'),
      sel('max_cert', FILTERS.max_cert, 'Age rating'),
      h('div', { class: 'field' }, h('label', { style: 'display:flex;gap:10px;align-items:center;min-height:44px' }, svc, 'Only what streams on my services')),
      h('p', { class: 'hint' }, 'Filters apply to recommendations in this chat and are enforced in code, not just asked of the model.'),
      h('div', { class: 'actions' }, h('button', { class: 'btn primary', type: 'button', onclick: () => {
        for (const k of Object.keys(c.filters)) if (!c.filters[k]) delete c.filters[k];
        if (c.filters.max_runtime) c.filters.max_runtime = Number(c.filters.max_runtime);
        const n = Object.keys(c.filters).length; btn.innerHTML = I.filter + `Filters${n ? ` (${n})` : ''}`; d.close(); } }, 'Apply'),
        h('button', { class: 'btn ghost', type: 'button', onclick: () => { c.filters = {}; btn.innerHTML = I.filter + 'Filters'; d.close(); } }, 'Clear')));
  });
}
function ago(t) {
  if (!t) return '';
  if (typeof t === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(t)) {            // a date without a time: count days
    const d = Math.round((new Date(new Date().toDateString()) - new Date(t + 'T00:00:00')) / 864e5);
    return d <= 0 ? 'today' : d === 1 ? 'yesterday' : `${d} days ago`;
  }
  const ms = typeof t === 'number' ? (t < 1e12 ? t * 1000 : t) : Date.parse(t);
  const m = Math.round((Date.now() - ms) / 60000);
  return m < 1 ? 'just now' : m < 60 ? `${m} min ago` : m < 1440 ? `${Math.round(m / 60)} h ago` : m < 2880 ? 'yesterday' : `${Math.round(m / 1440)} days ago`;
}
function chatsSheet(msgs) {
  sheet('Your chats', async (b, d) => {
    d.classList.add('side');
    b.append(h('button', { class: 'btn primary', type: 'button', style: 'width:100%;margin-bottom:12px', html: I.plus + 'New chat',
      onclick: () => { d.close(); newChat(); if (location.hash.startsWith('#/discover')) pageDiscover(new URLSearchParams()); else location.hash = '#/discover'; } }));
    const list = h('div', { class: 'chatlist' }, h('p', { class: 'muted' }, 'Loading…')); b.append(list);
    b.append(h('p', { class: 'muted small', style: 'margin-top:12px' }, 'Saved for your account, or for this browser if you are not signed in.'));
    try {
      const j = await api('/api/chats');
      list.innerHTML = '';
      if (!j.chats.length) { list.append(emptyBox('No chats yet. Ask anything and the conversation is saved here.', 'reel')); return; }
      for (const ch of j.chats) list.append(h('div', { class: 'row' },
        h('button', { class: 'open', type: 'button', onclick: () => {
          d.close();
          if (location.hash.startsWith('#/discover') && $('#msgs')) loadChat(ch.id, $('#msgs')); else location.hash = `#/discover?chat=${encodeURIComponent(ch.id)}`; } },
          h('b', {}, ch.title || 'Chat'), h('span', {}, [ago(ch.updated), ch.turns ? `${ch.turns} question${ch.turns > 1 ? 's' : ''}` : null].filter(Boolean).join(', '))),
        h('button', { class: 'iconbtn', type: 'button', 'aria-label': `Delete chat: ${ch.title || ''}`, html: I.x, onclick: async (e) => {
          const row = e.currentTarget.closest('.row');
          try { await api('/api/chats/' + encodeURIComponent(ch.id), { method: 'DELETE' }); row.remove(); toast('Chat deleted.'); } catch (er) { toast(er.message); } } })));
    } catch (e) { list.innerHTML = ''; list.append(h('p', { class: 'err' }, e.message)); }
  });
}
async function loadChat(id, msgs) {
  try {
    const j = await api('/api/chats/' + encodeURIComponent(id));
    newChat(); S.chat.convId = id; msgs.innerHTML = '';
    for (const t of j.turns || []) {
      msgs.append(h('div', { class: 'msg user' }, t.question));
      const ui = botShell(); ui.body.innerHTML = md(t.answer || ''); ui.status.remove();
      if (t.films?.length) ui.strip.append(rail('Films found', t.films));
      msgs.append(ui.el);
      S.chat.history.push({ role: 'user', content: t.question }, { role: 'assistant', content: t.answer || '' });
    }
    S.chat.history = S.chat.history.slice(-12); S.chat.recent = j.recent_titles || []; S.chat.conv = j.conv || null;
    S.chat.log = [...msgs.children];
  } catch (e) { toast(e.message); }
}
function botShell() {
  const status = h('div', { class: 'status' }, h('span', { class: 'dot' }), h('span', {}, 'Finding the right films…'));
  const body = h('div', { class: 'body md' });
  const strip = h('div', { class: 'strip' }), extra = h('div', {});
  const el = h('div', { class: 'msg bot' }, status, body, strip, extra);
  return { el, status, body, strip, extra, setStatus: (t) => { status.lastChild.textContent = t; } };
}
async function ask(text, msgs, sendBtn, opts = {}) {
  const c = S.chat;
  if (c.busy) return;
  $('.chat-intro', msgs)?.remove();
  msgs.append(h('div', { class: 'msg user' }, text));
  const ui = botShell(); msgs.append(ui.el);
  ui.el.scrollIntoView({ block: 'end', behavior: 'smooth' });
  c.busy = true; c.ctrl = new AbortController();
  if (sendBtn) { sendBtn.innerHTML = I.stop; sendBtn.setAttribute('aria-label', 'Stop'); }
  let answer = '', films = [], intent = null, rid = null;
  const handle = (ev, d) => {
    if (ev === 'status') ui.setStatus(d.stage === 'queued' ? `Waiting for the GPU: ${d.ahead} ahead of you` : d.stage === 'answering' ? 'Writing the answer…' : 'Finding the right films…');
    else if (ev === 'sources') { films = d.films || []; intent = d.intent; rid = d.id; if (d.alternatives?.length) renderAlts(ui, d.alternatives, msgs, sendBtn); if (d.choices?.length) renderDym(ui, d.choices, msgs, sendBtn); }
    else if (ev === 'token') { ui.status.hidden = true; answer += d.text; ui.body.innerHTML = md(answer); }
    else if (ev === 'replace') { answer = d.text || ''; ui.body.innerHTML = md(answer); }
    else if (ev === 'done') { rid = d.id || rid; c.recent = d.recent_titles || c.recent; if (d.conv) c.conv = d.conv; }
    else if (ev === 'error') throw Object.assign(new Error(d.message), { friendly: true });
  };
  try {
    const res = await fetch('/api/chat', { method: 'POST', headers: hdrs(), signal: c.ctrl.signal,
      body: JSON.stringify({ message: text, history: c.history, recent_titles: c.recent, conv: c.conv, think: false, filters: opts.filters || c.filters }) });
    if (res.status === 401) { ui.el.remove(); gate("That code didn't work."); return; }
    if (!res.ok) { let m = `The server answered ${res.status}.`; try { m = (await res.json()).error || m; } catch {} throw Object.assign(new Error(m), { friendly: true }); }
    const reader = res.body.getReader(), dec = new TextDecoder(); let buf = '';
    for (;;) {
      const { value, done } = await reader.read(); if (done) break;
      buf += dec.decode(value, { stream: true }); let i;
      while ((i = buf.indexOf('\n\n')) >= 0) {
        const raw = buf.slice(0, i); buf = buf.slice(i + 2); let ev = 'message', data = '';
        for (const line of raw.split('\n')) { if (line.startsWith('event:')) ev = line.slice(6).trim(); else if (line.startsWith('data:')) data += line.slice(5).trim(); }
        if (data) handle(ev, JSON.parse(data));
      }
    }
    if (!answer) throw Object.assign(new Error('The model finished without an answer. Try rephrasing.'), { friendly: true });
    c.history.push({ role: 'user', content: text }, { role: 'assistant', content: answer.trim() }); c.history = c.history.slice(-12);
  } catch (e) {
    ui.status.hidden = true;
    if (e.name === 'AbortError') ui.extra.append(h('p', { class: 'muted small' }, 'Stopped.'));
    else ui.extra.append(h('div', { class: 'err' }, e.friendly ? e.message : "Couldn't reach the Cinemack server.",
      h('button', { class: 'btn secondary small', type: 'button', onclick: () => { ui.el.previousSibling?.remove(); ui.el.remove(); ask(text, msgs, sendBtn, opts); } }, 'Try again')));
  } finally {
    c.busy = false; c.ctrl = null;
    if (sendBtn) { sendBtn.innerHTML = I.send; sendBtn.setAttribute('aria-label', 'Send'); }
    if (answer.trim()) {
      if (films.length) { const r = rail(intent === 'recommend' ? 'Recommended' : 'Films found', films); if (r) ui.strip.append(r); }
      renderFollow(ui, films, intent, msgs, sendBtn);
      renderFeedback(ui, rid);
    }
    c.log = [...msgs.children];
    if (opts.after) opts.after(answer, films);
  }
}
function renderAlts(ui, alts, msgs, sendBtn) {
  const box = h('div', { class: 'alts' }, 'Not the one you meant? ');
  alts.slice(0, 3).forEach(a => box.append(h('button', { type: 'button', onclick: () => ask(`I meant ${a.title}${a.year ? ` (${a.year})` : ''}`, msgs, sendBtn) },
    `${a.title}${a.year ? ` (${a.year})` : ''}`)));
  ui.extra.append(box);
}
function renderDym(ui, choices, msgs, sendBtn) {
  ui.extra.append(h('div', { class: 'follow' }, h('span', { class: 'muted small', style: 'align-self:center' }, 'Did you mean'),
    choices.slice(0, 4).filter(x => x && x.label && x.question).map(x => h('button', { class: 'chip', type: 'button', onclick: () => ask(x.question, msgs, sendBtn) }, x.label))));
}
function renderFollow(ui, films, intent, msgs, sendBtn) {
  const f = films[0]; const T = f ? `${f.title}${f.year ? ` (${f.year})` : ''}` : null;
  const items = [['Tell me more', T ? `Tell me more about ${T}: things you haven't told me yet.` : 'Tell me more about that.']];
  if (T && (intent === 'identify' || intent === 'named_film')) items.push(['Where to watch', `Where can I watch ${T}?`], ['Similar films', `Recommend films similar to ${T}.`]);
  else if (intent === 'recommend') items.push(['More like these', 'More recommendations like those, please.'], ['Something shorter', 'Something similar but under 100 minutes.']);
  ui.extra.append(h('div', { class: 'follow' }, items.map(([l, q]) => h('button', { class: 'chip', type: 'button', onclick: () => ask(q, msgs, sendBtn) }, l))));
}
function renderFeedback(ui, rid) {
  if (!rid) return;
  const vote = async (v, b) => { try { await post('/api/feedback', { id: rid, vote: v, comment: '', reasons: [] }); b.parentElement.querySelectorAll('button').forEach(x => x.setAttribute('aria-pressed', String(x === b))); toast('Thanks: feedback saved.'); } catch (e) { toast(e.message); } };
  const up = h('button', { class: 'iconbtn', type: 'button', 'aria-label': 'Good answer', 'aria-pressed': 'false', html: I.up });
  const dn = h('button', { class: 'iconbtn', type: 'button', 'aria-label': 'Bad answer', 'aria-pressed': 'false', html: I.down });
  up.onclick = () => vote('up', up); dn.onclick = () => vote('down', dn);
  ui.extra.append(h('div', { class: 'fb' }, up, dn));
}

// ---------------------------------------------------------------- Pick tonight
async function pagePick() {
  view.innerHTML = ''; view.classList.add('pick-page');
  let P; try { P = await (await fetch('/v8/pick.json')).json(); } catch { view.append(h('div', { class: 'notice' }, "Couldn't load Pick tonight.")); return; }
  const sel = S.pick || (S.pick = { with: 'me', mood: 'any', time: 'any', novelty: 'familiar', services: false });
  let step = 0;
  const group = (key, title) => {
    const box = h('div', { class: 'chips', role: 'radiogroup', 'aria-label': title });
    for (const [k, v] of Object.entries(P[key])) box.append(h('button', { class: 'chip', type: 'button', role: 'radio', 'aria-checked': String(sel[key] === k),
      onclick: (e) => { sel[key] = k; const me = e.currentTarget; box.querySelectorAll('button').forEach(b => b.setAttribute('aria-checked', String(b === me))); } }, v.label));
    return h('div', { class: 'pick-q' }, h('h2', {}, h('span', { class: 'n', 'aria-hidden': 'true' }, String(++step)), title), box);
  };
  const svc = h('button', { class: 'chip', type: 'button', 'aria-pressed': String(sel.services), onclick: (e) => { sel.services = !sel.services; e.currentTarget.setAttribute('aria-pressed', String(sel.services)); } }, 'Only on my services');
  const out = h('div', { class: 'msgs', 'aria-live': 'polite' });
  const run = async () => {
    if (S.chat.busy) { S.chat.ctrl?.abort(); for (let i = 0; i < 60 && S.chat.busy; i++) await new Promise(r => setTimeout(r, 50)); }
    const f = Object.assign({ strict: true }, P.with[sel.with].filters, P.mood[sel.mood].filters, P.time[sel.time].filters, sel.services ? { services_only: true } : {});
    const q = `Pick tonight: recommend three ${P.mood[sel.mood].words} films ${P.with[sel.with].words}${P.time[sel.time].words ? ', ' + P.time[sel.time].words : ''}. I'd like ${P.novelty[sel.novelty].words}. One line each on why it fits.`;
    newChat(); out.innerHTML = '';
    ask(q, out, null, { filters: f });
  };
  view.append(h('h1', {}, 'Pick tonight'), h('p', { class: 'muted' }, "A few quick questions and I'll pick three films. Time limits, age ratings and your services are hard rules, not suggestions."),
    group('with', 'Who is watching?'), group('mood', 'What mood?'), group('time', 'How much time?'), group('novelty', 'Familiar or different?'),
    h('div', { class: 'pick-q' }, h('h2', {}, h('span', { class: 'n', 'aria-hidden': 'true' }, '5'), 'Where are you watching?'), h('div', { class: 'chips' }, svc,
      S.me && !(S.me.settings?.services || []).length ? h('a', { href: '#/profile?tab=settings', class: 'small textlink' }, 'Add your services') : null)),
    h('div', { class: 'sticky-cta' }, h('button', { class: 'btn primary', type: 'button', onclick: run }, 'Show my picks')), out);
}

// ---------------------------------------------------------------- Watchlist
// The Watchlist page is a plan for what to watch: what you can watch now and why, what just arrived, what is about to
// leave theatres, what you and your friends both want, then everything, newest first.
async function pageWatchlist(params) {
  view.innerHTML = '';
  view.append(h('h1', {}, 'Watchlist'), h('p', { class: 'muted' }, "Your list, sorted into what you can watch tonight, what just arrived, and what you'd enjoy with a friend."));
  const al = h('div', {}), body = h('div', { class: 'plan' }, skeletonRail());
  view.append(al, body);
  try {
    const a = await api('/api/v8/alerts');
    if (a.unseen) post('/api/v8/alerts/seen').then(() => updateBell(0)).catch(() => {});
    if (params.get('alerts') && !a.alerts.length) al.append(h('p', { class: 'muted' }, 'No alerts yet. You get one when a film on your list starts streaming or comes out.'));
  } catch {}
  let o;
  try { o = await api('/api/v8/watchlist/overview'); } catch (e) { body.innerHTML = ''; body.append(h('p', { class: 'err' }, e.message)); return; }
  body.innerHTML = '';
  if (!o.items.length) { const e = emptyBox('Nothing saved yet. Tap the bookmark on any poster and it waits for you here.', 'popcorn', '#/', 'Browse films'); e.style.marginTop = '16px'; body.append(e); return; }
  const sec = (title, lede, ...kids) => h('section', { class: 'section' }, h('div', { class: 'section-h' }, h('h2', {}, title), lede ? h('p', { class: 'lede' }, lede) : null), ...kids);
  const row = (f, line) => h('div', { class: 'wl-row' },
    h('a', { href: `#/film/${f.id}`, tabindex: '-1', 'aria-hidden': 'true' }, f.poster ? h('img', { src: f.poster, alt: '', loading: 'lazy', width: 56, height: 84 }) : h('span', { class: 'ph' })),
    h('div', {}, h('a', { class: 't', href: `#/film/${f.id}` }, `${f.title}${f.year ? ` (${f.year})` : ''}`), h('div', { class: 's' }, line)));
  if (o.watch_next.length) {
    body.append(sec('Watch next', 'Ready to watch now, best reason first.', h('div', { class: 'rail', role: 'list', 'aria-label': 'Watch next' },
      o.watch_next.map(f => { const c = card(Object.assign({}, f, { on_services: null }), { why: f.why }); c.setAttribute('role', 'listitem'); return c; }))));
  } else body.append(sec('Watch next', null, h('p', { class: 'muted' }, o.has_services ? 'Nothing on your list is streaming on your services or in theatres right now.'
    : 'Add your streaming services in Profile, Settings and this shows what you can watch tonight.')));
  if (o.just_landed.length) body.append(sec('Just landed', 'Films on your list that started streaming or came out in the last 30 days.',
    h('div', {}, o.just_landed.map(x => row(x.film, `${x.text}, ${x.at}`)))));
  if (o.last_chance.length) body.append(sec('Last chance in theatres', 'In theatres now and opened a while ago. Theatres usually drop a film 6 to 10 weeks in; this is an estimate.',
    h('div', {}, o.last_chance.map(x => row(x.film, `Opened ${x.weeks} weeks ago`)))));
  if (o.together.length) {
    body.append(sec('Watch together', 'Friends who share their list or their ratings with you.', h('div', {}, o.together.map(t => {
      const taste = t.taste.pct != null ? `You agree on ${t.taste.agree} of ${t.taste.films} films you've both rated (${t.taste.pct}% match)`
        : t.shares_ratings ? 'Not enough films rated by both of you yet' : null;
      const box = h('div', { class: 'pair' }, h('h3', {}, t.name, t.taste.pct != null ? h('span', { class: 'match' }, `${t.taste.pct}% taste match`) : null),
        taste ? h('p', { class: 'muted small', style: 'margin:4px 0 0' }, taste) : null);
      if (t.both.length) { box.append(h('p', { style: 'margin:12px 0 6px' }, h('b', {}, `You both want to watch ${t.both.length === 1 ? 'this' : 'these'}. Pick a night.`)));
        box.append(h('div', { class: 'rail' }, t.both.map(f => card(f)))); }
      if (t.loved.length) { box.append(h('p', { style: 'margin:12px 0 6px' }, h('b', {}, `${t.name} loved ${t.loved.length === 1 ? 'one' : 'these'} from your list`)));
        box.append(h('div', { class: 'rail' }, t.loved.map(f => card(f)))); }
      return box;
    }))));
  } else if (S.me?.account) body.append(sec('Watch together', null, h('p', { class: 'muted' }, 'When a friend shares their watchlist with you (Profile, Privacy), the films you both want show up here.')));
  const filt = h('div', { class: 'chips', role: 'radiogroup', 'aria-label': 'Show' });
  const grid = h('div', { class: 'grid', style: 'margin-top:12px' });
  const label = { streaming: 'Streaming', theatres: 'In theatres', out: 'Out, not streaming', coming: 'Not out yet' };
  const draw = (k) => {
    filt.querySelectorAll('button').forEach(b => b.setAttribute('aria-checked', String(b.dataset.k === k)));
    grid.innerHTML = '';
    const items = o.items.filter(c => k === 'all' || c.status === k);
    if (!items.length) { grid.append(h('p', { class: 'muted' }, 'Nothing here.')); return; }
    items.forEach(c => { const el = card(c); el.append(h('div', { class: 'why' }, [label[c.status], c.added ? `added ${ago(c.added)}` : null].filter(Boolean).join(', '))); grid.append(el); });
  };
  [['all', `All ${o.items.length}`], ['streaming', 'Streaming'], ['theatres', 'In theatres'], ['coming', 'Not out yet']].forEach(([k, l]) =>
    filt.append(h('button', { class: 'chip', type: 'button', role: 'radio', 'data-k': k, 'aria-checked': 'false', onclick: () => draw(k) }, l)));
  body.append(sec('Everything on your list', 'Newest first.', filt, grid));
  draw('all');
}

// ---------------------------------------------------------------- Calendar
async function pageCalendar() {
  view.innerHTML = '';
  view.append(h('h1', {}, 'Release calendar'), h('p', { class: 'muted' }, "What's opening in US theatres, landing on streaming, and starting a new TV season over the next three months."));
  const tabs = h('div', { class: 'tabs', role: 'tablist', 'aria-label': 'Show' });
  const body = h('div', { class: 'narrow' }, h('div', { class: 'skel', style: 'height:200px' }));
  view.append(tabs, body);
  let data; try { data = await api('/api/v8/calendar?days=90'); } catch (e) { body.innerHTML = ''; body.append(h('p', { class: 'err' }, e.message)); return; }
  const kinds = [['all', 'All'], ['theatrical', 'In theatres'], ['digital', 'Streaming'], ['tv_season', 'TV'], ['mine', 'My watchlist']];
  const show = (k) => {
    tabs.querySelectorAll('button').forEach(b => b.setAttribute('aria-selected', String(b.dataset.k === k)));
    body.innerHTML = '';
    const ev = data.events.filter(e => k === 'all' || (k === 'mine' ? e.on_watchlist : k === 'theatrical' ? e.kind.startsWith('theatrical') : e.kind === k));
    if (!ev.length) { body.append(emptyBox('Nothing dated in the next 90 days for this view.', 'reel')); return; }
    let day = '';
    for (const e of ev) {
      if (e.date !== day) { day = e.date; body.append(h('h2', { class: 'day' }, new Date(day + 'T12:00:00').toLocaleDateString(undefined, { weekday: 'short', month: 'long', day: 'numeric' }))); }
      const it = e.film || e.show; const isFilm = !!e.film;
      body.append(h('div', { class: 'li' }, h('div', { class: 'th' }, it.poster ? h('img', { src: it.poster, alt: '', loading: 'lazy', width: 48, height: 72 }) : null),
        h('div', {}, isFilm ? h('a', { class: 't', href: `#/film/${it.id}` }, it.title) : h('span', { class: 't' }, it.title),
          h('div', { class: 's' }, h('span', { class: `kind ${e.kind}` }, e.label), e.on_watchlist ? '  On your watchlist' : '')),
        e.showtimes ? h('a', { class: 'btn ghost small', href: e.showtimes.url, target: '_blank', rel: 'noopener', title: e.showtimes.label }, 'Find showtimes') : h('span')));
    }
  };
  kinds.forEach(([k, l]) => tabs.append(h('button', { type: 'button', role: 'tab', 'data-k': k, onclick: () => show(k) }, l)));
  show('all');
  view.append(h('p', { class: 'tiny faint', style: 'margin-top:16px' }, data.note + (data.as_of ? ` Lists updated ${data.as_of}.` : '')));
}

// ---------------------------------------------------------------- Profile
async function pageProfile(params) {
  view.innerHTML = '';
  const me = S.me || (S.me = await api('/api/me').catch(() => null));
  let p; try { p = await api('/api/v8/profile'); } catch (e) { view.append(h('p', { class: 'err' }, e.message)); return; }
  const name = me?.name || me?.account?.display_name || me?.account?.username || 'Guest';
  view.append(h('div', { style: 'display:flex;gap:16px;align-items:center;margin-bottom:8px' },
    h('span', { class: 'avatar', style: 'width:56px;height:56px;font-size:22px' }, name[0].toUpperCase()),
    h('div', {}, h('h1', {}, name), h('div', { class: 'muted small' }, me?.account ? `Signed in as ${me.account.username}` : 'Not signed in: your data is kept for this browser only'))));
  const st = p.stats || {};
  view.append(h('div', { class: 'stat' }, [[st.films || 0, 'films watched'], [st.shows || 0, 'shows'], [p.watchlist.length, 'on your watchlist'], [p.taste?.likes || 0, 'liked']]
    .map(([n, l]) => h('div', {}, h('b', {}, String(n)), h('span', {}, l)))));
  if (!me?.account) view.append(h('div', { class: 'notice', style: 'margin-top:16px;display:flex;flex-wrap:wrap;gap:8px 16px;align-items:center' },
    h('span', { style: 'flex:1 1 260px' }, 'Create an account to keep your watchlist and history on every device and to add friends.'),
    h('button', { class: 'btn primary small', type: 'button', onclick: () => authSheet('up') }, 'Create account'),
    h('button', { class: 'btn secondary small', type: 'button', onclick: () => authSheet('in') }, 'Sign in')));
  const tabs = [['history', 'History'], ['ratings', 'Ratings'], ['friends', 'Friends'], ['privacy', 'Privacy'], ['imports', 'Imports'], ['settings', 'Settings'], ['account', 'Account']];
  const bar = h('div', { class: 'tabs', role: 'tablist', 'aria-label': 'Profile sections' }), body = h('div', { role: 'tabpanel' });
  view.append(bar, body);
  const show = (k) => { bar.querySelectorAll('button').forEach(b => b.setAttribute('aria-selected', String(b.dataset.k === k))); body.innerHTML = ''; PROFILE[k](body, p, me); };
  tabs.forEach(([k, l]) => bar.append(h('button', { type: 'button', role: 'tab', 'data-k': k, onclick: () => show(k) }, l)));
  const tr = params.get('trakt'); if (tr) toast({ connected: 'Trakt connected and imported.', failed: "Trakt sign-in didn't work. Try again.", cancelled: 'Trakt sign-in cancelled.' }[tr] || '');
  show(params.get('tab') || (tr ? 'imports' : 'history'));
}
const needAccount = (b, what) => b.append(h('div', { class: 'empty' }, h('p', {}, `Sign in to ${what}.`), h('button', { class: 'btn secondary small', type: 'button', onclick: () => authSheet('in') }, 'Sign in')));
const PROFILE = {
  history(b, p) {
    if (p.taste?.tastes?.length) b.append(h('p', { class: 'small muted' }, 'Your taste so far: ' + p.taste.tastes.slice(0, 5).map(t => t.split(':')[0]).join(', ')));
    if (!p.history.length) { const e = emptyBox('No watch history yet. Bring it in from Netflix, Letterboxd or Trakt, or tap Watched on any film.', 'ticket'); e.append(h('button', { class: 'btn secondary small', type: 'button', onclick: () => bar_click('imports') }, 'Import history')); b.append(e); return; }
    b.append(h('div', { class: 'list' }, p.history.map(x => h('div', { class: 'li', style: 'grid-template-columns:1fr auto' },
      h('div', {}, x.media === 'movie' ? h('a', { class: 't', href: `#/film/${x.id}` }, x.title) : h('span', { class: 't' }, x.title),
        h('div', { class: 's' }, [x.year, x.media === 'tv' ? `${x.episodes} episode${x.episodes === 1 ? '' : 's'}` : null, `from ${x.source}`].filter(Boolean).join(', '))),
      h('span', { class: 's' }, x.last || '')))));
  },
  ratings(b, p) {
    if (!p.reviews.length) { b.append(h('div', { class: 'empty' }, h('p', {}, 'No ratings yet. Use "Rate or review" on any film page.'))); return; }
    b.append(h('div', { class: 'list' }, p.reviews.map(r => h('div', { class: 'li', style: 'grid-template-columns:1fr auto' },
      h('div', {}, h('a', { class: 't', href: `#/film/${r.id}` }, `Film ${r.id}`), r.text ? h('div', { class: 's' }, r.text.slice(0, 140)) : null),
      h('span', {}, r.rating ? `★ ${r.rating}` : '')))));
  },
  friends(b, p) {
    if (!p.account) return needAccount(b, 'add friends');
    const fr = p.friends || { friends: [], incoming: [], outgoing: [] };
    b.append(h('div', { class: 'notice' }, 'Your friend code: ', h('b', {}, p.code), '. Share it with someone; they add you with it and you accept. Nothing is shared until you choose what to share in Privacy.'));
    const inp = h('input', { class: 'input', id: 'fc', placeholder: 'Friend code', autocomplete: 'off', style: 'text-transform:uppercase' });
    b.append(h('div', { class: 'field' }, h('label', { for: 'fc' }, 'Add a friend'), h('div', { style: 'display:flex;gap:8px' }, inp,
      h('button', { class: 'btn primary', type: 'button', onclick: async () => { try { const j = await post('/api/v8/friends/request', { code: inp.value }); toast({ requested: 'Request sent.', accepted: 'You are now friends.', already_friends: 'You are already friends.' }[j.result]); route(); } catch (e) { toast(e.message); } } }, 'Send request'))));
    const row = (f, btns) => h('div', { class: 'li', style: 'grid-template-columns:1fr auto' }, h('div', {}, h('span', { class: 't' }, f.name)), h('div', { class: 'actions', style: 'margin:0' }, btns));
    const act = (path, body, msg) => async () => { try { await post(path, body); toast(msg); route(); } catch (e) { toast(e.message); } };
    if (fr.incoming.length) b.append(h('h2', { class: 'day' }, 'Requests'), h('div', { class: 'list' }, fr.incoming.map(f => row(f, [
      h('button', { class: 'btn primary small', type: 'button', onclick: act('/api/v8/friends/respond', { key: f.key, accept: true }, 'Accepted.') }, 'Accept'),
      h('button', { class: 'btn ghost small', type: 'button', onclick: act('/api/v8/friends/respond', { key: f.key, accept: false }, 'Declined.') }, 'Decline')]))));
    b.append(h('h2', { class: 'day' }, 'Friends'));
    if (!fr.friends.length) b.append(h('p', { class: 'muted' }, 'No friends yet.'));
    else b.append(h('div', { class: 'list' }, fr.friends.map(f => row(f, [h('a', { class: 'btn secondary small', href: `#/friend/${encodeURIComponent(f.key)}` }, 'View'),
      h('button', { class: 'btn ghost small', type: 'button', onclick: act('/api/v8/friends/remove', { key: f.key }, 'Removed.') }, 'Remove'),
      h('button', { class: 'btn ghost small', type: 'button', onclick: act('/api/v8/friends/block', { key: f.key }, 'Blocked.') }, 'Block')]))));
    if (fr.outgoing.length) b.append(h('p', { class: 'small muted' }, `${fr.outgoing.length} request${fr.outgoing.length > 1 ? 's' : ''} waiting for an answer.`));
  },
  privacy(b, p) {
    if (!p.account) return needAccount(b, 'choose what friends see');
    b.append(h('p', { class: 'muted' }, 'Everything is private until you change it. "Friends" means people you accepted; there is no public profile.'));
    const labels = { profile: 'Profile and stats', watchlist: 'Watchlist', history: 'Watch history', ratings: 'Ratings', reviews: 'Written reviews' };
    for (const [k, l] of Object.entries(labels)) {
      const id = `pv-${k}`;
      const s = h('select', { class: 'input', id }, [['private', 'Only me'], ['friends', 'Friends']].map(([v, t]) => h('option', { value: v, selected: p.privacy[k] === v }, t)));
      s.addEventListener('change', async () => { try { await post('/api/v8/privacy', { changes: { [k]: s.value } }); toast(`${l}: ${s.value === 'friends' ? 'shared with friends' : 'only you'}.`); } catch (e) { toast(e.message); } });
      b.append(h('div', { class: 'field' }, h('label', { for: id }, l), s));
    }
  },
  imports(b, p) {
    const res = h('div', {});
    const upload = (kind, label, hint) => {
      const id = `up-${kind}`;
      const inp = h('input', { type: 'file', id, accept: '.csv,text/csv', class: 'input' });
      inp.addEventListener('change', async () => {
        const f = inp.files[0]; if (!f) return;
        res.innerHTML = ''; res.append(h('p', { class: 'muted' }, 'Importing…'));
        try { const j = await post('/api/v8/import', { kind, text: await f.text() }); showImport(res, j); } catch (e) { res.innerHTML = ''; res.append(h('p', { class: 'err' }, e.message)); }
      });
      return h('div', { class: 'field' }, h('label', { for: id }, label), inp, h('span', { class: 'hint' }, hint));
    };
    b.append(upload('netflix', 'Netflix viewing history (CSV)', 'Netflix: Account → Profile → Viewing activity → Download all. ViewingActivity.csv from a data request works too.'),
      upload('letterboxd_watched', 'Letterboxd watched or diary (CSV)', 'From your Letterboxd export: watched.csv or diary.csv.'), res);
    const t = p.trakt || {};
    const tb = h('div', { class: 'notice' }, h('b', {}, 'Trakt '), t.connected ? `connected${t.last_sync ? `, last synced ${t.last_sync.slice(0, 10)}` : ''}. ` : 'not connected. ');
    if (!t.available) tb.append('Trakt is not set up on this server.');
    else if (t.connected) tb.append(h('button', { class: 'btn secondary small', type: 'button', onclick: async () => { try { const j = await post('/api/v8/trakt/sync'); showImport(res, j.history); } catch (e) { toast(e.message); } } }, 'Sync now'), ' ',
      h('button', { class: 'btn ghost small', type: 'button', onclick: async () => { try { const j = await post('/api/v8/trakt/disconnect'); toast(`Disconnected; removed ${j.removed_events} imported rows.`); route(); } catch (e) { toast(e.message); } } }, 'Disconnect'));
    else tb.append(h('button', { class: 'btn primary small', type: 'button', onclick: async () => { try { const j = await api('/trakt/start'); location.href = j.url; } catch (e) { toast(e.message); } } }, 'Connect Trakt'));
    b.append(tb);
    if (p.imports?.length) b.append(h('h2', { class: 'day' }, 'Past imports'), h('div', { class: 'list' }, p.imports.map(j => h('div', { class: 'li', style: 'grid-template-columns:1fr auto' },
      h('div', {}, h('span', { class: 't' }, j.source), h('div', { class: 's' }, `${j.rows} rows: ${j.matched} matched, ${j.duplicates} already there, ${j.issues} not matched`)), h('span', { class: 's' }, j.at.slice(0, 10))))));
  },
  settings(b, p, me) {
    if (!me) return b.append(h('p', { class: 'err' }, 'Settings unavailable.'));
    const s = Object.assign({}, me.settings);
    const multi = (key, opts, title, hint) => {
      const box = h('div', { class: 'chips' });
      opts.forEach(([v, l]) => box.append(h('button', { class: 'chip', type: 'button', 'aria-pressed': String((s[key] || []).includes(v)), onclick: (e) => {
        const set = new Set(s[key] || []); set.has(v) ? set.delete(v) : set.add(v); s[key] = [...set]; e.currentTarget.setAttribute('aria-pressed', String(set.has(v))); } }, l)));
      return h('div', { class: 'field' }, h('span', { class: 'lab' }, title), box, hint ? h('span', { class: 'hint' }, hint) : null);
    };
    b.append(multi('services', me.services_all, 'My streaming services', 'Used for "On your services" and the Only-my-services filter.'),
      multi('decades', [['pre1970', 'Before 1970'], ['1970s', '70s'], ['1980s', '80s'], ['1990s', '90s'], ['2000s', '2000s'], ['2010s', '2010s'], ['2020s', '2020s']], 'Favourite decades', 'A gentle lift in recommendations; nothing is hidden.'),
      multi('languages', [['en', 'English'], ['fr', 'French'], ['es', 'Spanish'], ['ko', 'Korean'], ['ja', 'Japanese'], ['hi', 'Hindi'], ['it', 'Italian'], ['de', 'German'], ['zh', 'Chinese']], 'Favourite languages', 'Also a lift, never a filter.'),
      multi('never_genres', me.genres.map(g => [g, g]), 'Never recommend', 'A hard limit for recommendations.'));
    const theme = h('select', { class: 'input', id: 'theme' }, [['', 'Match my device'], ['dark', 'Dark'], ['light', 'Light']].map(([v, l]) => h('option', { value: v, selected: (store.get('cm.theme') || '') === v }, l)));
    theme.addEventListener('change', () => { store.set('cm.theme', theme.value); if (theme.value) document.documentElement.dataset.theme = theme.value; else delete document.documentElement.dataset.theme; });
    b.append(h('div', { class: 'field' }, h('label', { for: 'theme' }, 'Appearance'), theme),
      h('button', { class: 'btn primary', type: 'button', onclick: async () => { try { const j = await post('/api/settings', { settings: s }); S.me.settings = j.settings; toast('Settings saved.'); } catch (e) { toast(e.message); } } }, 'Save settings'),
      h('p', { class: 'small muted', style: 'margin-top:16px' }, 'Answer length, spoilers, memory and more are on the ', h('a', { href: '/' }, 'classic page'), ' for now.'));
  },
  account(b, p, me) {
    if (!me?.account) {
      b.append(h('p', { class: 'muted' }, 'An account keeps your chats, taste, watchlist and history across devices, and lets you add friends.'),
        h('div', { class: 'actions' }, h('button', { class: 'btn primary', type: 'button', onclick: () => authSheet('up') }, 'Create account'),
          h('button', { class: 'btn secondary', type: 'button', onclick: () => authSheet('in') }, 'Sign in')));
      return;
    }
    b.append(h('div', { class: 'actions' }, h('button', { class: 'btn secondary', type: 'button', onclick: async () => { await post('/api/logout'); S.me = null; toast('Signed out.'); route(); } }, 'Sign out')),
      h('h2', { class: 'day' }, 'Delete account'), h('p', { class: 'muted small' }, 'Deletes your account and everything stored for it: chats, taste, watchlist, history, ratings, friends and Trakt link.'),
      h('button', { class: 'btn ghost', type: 'button', style: 'color:var(--bad)', onclick: () => deleteSheet() }, 'Delete my account'));
  },
};
function bar_click(k) { document.querySelector(`.tabs [data-k="${k}"]`)?.click(); }
function showImport(el, j) {
  el.innerHTML = '';
  el.append(h('div', { class: 'notice' }, `${j.rows} rows read: ${j.added ?? j.matched} added, ${j.duplicates} already there, ${j.issues.length} not matched.`));
  if (j.issues.length) el.append(h('details', {}, h('summary', { class: 'small' }, 'Rows that were not matched'),
    h('div', { class: 'list' }, j.issues.slice(0, 200).map(i => h('div', { class: 'small', style: 'padding:6px 0;border-bottom:1px solid var(--line)' }, `${i.raw}: ${i.reason}`)))));
}
function authSheet(mode) {
  sheet(mode === 'up' ? 'Create account' : 'Sign in', (b, d) => {
    const u = h('input', { class: 'input', id: 'au-u', autocomplete: 'username' });
    const pw = h('input', { class: 'input', id: 'au-p', type: 'password', autocomplete: mode === 'up' ? 'new-password' : 'current-password' });
    const nm = h('input', { class: 'input', id: 'au-n', autocomplete: 'nickname' });
    const err = h('p', { class: 'err', 'aria-live': 'polite' });
    b.append(h('div', { class: 'field' }, h('label', { for: 'au-u' }, 'Username'), u), h('div', { class: 'field' }, h('label', { for: 'au-p' }, 'Password'), pw));
    if (mode === 'up') b.append(h('div', { class: 'field' }, h('label', { for: 'au-n' }, 'Display name (optional)'), nm),
      h('p', { class: 'hint' }, 'This site uses plain HTTP: choose a password you use nowhere else.'));
    b.append(err, h('button', { class: 'btn primary', type: 'button', onclick: async () => {
      try { S.me = await post(mode === 'up' ? '/api/signup' : '/api/login', mode === 'up' ? { username: u.value, password: pw.value, display_name: nm.value, keep_history: true } : { username: u.value, password: pw.value });
        d.close(); toast(mode === 'up' ? 'Account created.' : 'Signed in.'); refreshMe(); route(); } catch (e) { err.textContent = e.message; } } }, mode === 'up' ? 'Create account' : 'Sign in'));
    setTimeout(() => u.focus(), 50);
  });
}
function deleteSheet() {
  sheet('Delete account', (b, d) => {
    const pw = h('input', { class: 'input', id: 'del-p', type: 'password', autocomplete: 'current-password' });
    const err = h('p', { class: 'err', 'aria-live': 'polite' });
    b.append(h('p', {}, 'This cannot be undone.'), h('div', { class: 'field' }, h('label', { for: 'del-p' }, 'Password'), pw), err,
      h('button', { class: 'btn primary', type: 'button', style: 'background:var(--bad);color:#fff', onclick: async () => {
        try { await post('/api/account/delete', { password: pw.value }); d.close(); S.me = null; toast('Account deleted.'); location.hash = '#/'; } catch (e) { err.textContent = e.message; } } }, 'Delete everything'));
  });
}
async function pageFriend(key) {
  view.innerHTML = '';
  let f; try { f = await api(`/api/v8/friend/${encodeURIComponent(key)}`); } catch (e) { view.append(h('p', { class: 'err' }, e.message)); return; }
  view.append(h('h1', {}, f.name));
  const none = !f.watchlist && !f.history && !f.ratings;
  if (none) view.append(h('p', { class: 'muted' }, `${f.name} hasn't shared anything with friends yet.`));
  if (f.watchlist) { const r = rail('Watchlist', f.watchlist, { empty: ['Nothing on their watchlist.'] }); if (r) view.append(r); }
  if (f.history) view.append(h('h2', { class: 'day' }, 'Recently watched'), h('div', { class: 'list' }, f.history.slice(0, 50).map(x => h('div', { class: 'li', style: 'grid-template-columns:1fr auto' }, h('span', { class: 't' }, x.title), h('span', { class: 's' }, x.last || '')))));
  if (f.ratings) view.append(h('h2', { class: 'day' }, 'Ratings'), h('div', { class: 'list' }, f.ratings.map(r => h('div', { class: 'li', style: 'grid-template-columns:1fr auto' }, h('a', { class: 't', href: `#/film/${r.id}` }, r.text || `Film ${r.id}`), h('span', {}, r.rating ? `★ ${r.rating}` : '')))));
}

// ---------------------------------------------------------------- router
const ROUTES = [[/^#?\/?$/, pageHome, 'home'], [/^#\/discover/, pageDiscover, 'discover'], [/^#\/pick/, pagePick, 'pick'], [/^#\/watchlist/, pageWatchlist, 'watchlist'],
  [/^#\/calendar/, pageCalendar, 'calendar'], [/^#\/profile/, pageProfile, 'profile'], [/^#\/film\/(\d+)/, pageFilm, null], [/^#\/friend\/([^?]+)/, pageFriend, 'profile']];
async function route() {
  const hash = location.hash || '#/';
  const [path, qs] = hash.split('?');
  const params = new URLSearchParams(qs || '');
  for (const [re, fn, nav] of ROUTES) {
    const m = path.match(re);
    if (!m) continue;
    document.querySelectorAll('[data-nav]').forEach(a => { if (a.dataset.nav === nav) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current'); });
    document.title = 'Cinemack'; view.className = 'page';
    if (fn !== pageDiscover) window.scrollTo(0, 0);
    try { await fn(m[1] ? decodeURIComponent(m[1]) : params, params); } catch (e) { if (e.status !== 401) { view.innerHTML = ''; view.append(h('div', { class: 'notice' }, e.message || 'Something went wrong.')); } }
    if (route.seen) $('#main').focus({ preventScroll: true });     // move focus on navigation, not on first load (keeps the skip link first)
    route.seen = true;
    return;
  }
  location.hash = '#/';
}
function updateBell(n) { const b = $('#bell-n'); b.hidden = !n; b.textContent = String(n); $('#bell').setAttribute('aria-label', n ? `Alerts: ${n} new` : 'Alerts'); }
async function refreshMe() {
  try { S.me = await api('/api/me'); const n = S.me.name || S.me.account?.display_name || S.me.account?.username || '';
    const av = $('#avatar'); if (n) av.textContent = n[0].toUpperCase();
    else av.innerHTML = '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><circle cx="12" cy="8" r="4"/><path d="M4 21c1.5-4 4.5-6 8-6s6.5 2 8 6"/></svg>'; } catch {}
  try { const a = await api('/api/v8/alerts'); updateBell(a.unseen); } catch {}
}
$('#chats-top').addEventListener('click', () => chatsSheet());
$('#ask-top').addEventListener('click', () => { location.hash = '#/discover'; setTimeout(() => $('#chat-q')?.focus(), 80); });
window.addEventListener('hashchange', route);
refreshMe();
route();
