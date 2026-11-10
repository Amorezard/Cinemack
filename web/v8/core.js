// Cinemack v8 core (10 Nov 2026 redesign): state, API, dialogs, shared components. Every page module imports from
// here, so a film saved in the chat, on Pick Tonight or in the Calendar updates the same store and every card that
// shows it (event "cm:choice"), and TV progress set on Watchlist or Profile goes through one endpoint and one event
// ("cm:progress"). No build step; plain ES modules.
export const $ = (s, el = document) => el.querySelector(s);
export const $$ = (s, el = document) => [...el.querySelectorAll(s)];
export const view = $('#view');
export const store = {
  get(k) { try { return localStorage.getItem(k); } catch { return null; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch {} },
  json(k, d) { try { const v = JSON.parse(localStorage.getItem(k) || 'null'); return v ?? d; } catch { return d; } },
};
export const newId = () => (crypto.randomUUID ? crypto.randomUUID() : String(Date.now()) + Math.random().toString(16).slice(2))
  .replace(/[^a-zA-Z0-9-]/g, '').slice(0, 36);
export const S = {
  user: store.get('mb.user') || (() => { const u = newId(); store.set('mb.user', u); return u; })(),
  code: store.get('mb.code') || '',
  me: null, choices: {}, pick: null,
  chat: { convId: newId(), history: [], recent: [], conv: null, busy: false, ctrl: null, log: null, title: '' },
  filters: { genres: [] },
};
export const hdrs = (extra = {}) => Object.assign({ 'Content-Type': 'application/json', 'X-Access-Code': S.code, 'X-MB-User': S.user,
  'X-MB-Conv': S.chat.convId, 'X-MB-Private': '0' }, extra);
export const esc = (s) => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
export const h = (tag, attrs = {}, ...kids) => {
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
const sv = (d, extra = '') => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"${extra}>${d}</svg>`;
export const I = {
  bookmark: sv('<path d="M6 3h12v18l-6-4-6 4z"/>'),
  bookmarkOn: '<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M6 3h12v18l-6-4-6 4z"/></svg>',
  star: '<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M12 2.5l2.9 6 6.6.8-4.9 4.6 1.3 6.6L12 17.3 6.1 20.5l1.3-6.6L2.5 9.3l6.6-.8z"/></svg>',
  send: sv('<path d="M5 12l14-7-5 15-3-6z"/><path d="M11 14l8-9"/>'),
  stop: '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="7" y="7" width="10" height="10" rx="2" fill="currentColor"/></svg>',
  x: sv('<path d="M6 6l12 12M18 6L6 18"/>'),
  up: sv('<path d="M7 11v9H4v-9zM7 11l4-8a2 2 0 012 2v4h6a2 2 0 012 2.3l-1.3 7A2 2 0 0117.7 20H7"/>'),
  down: sv('<path d="M17 13V4h3v9zM17 13l-4 8a2 2 0 01-2-2v-4H5a2 2 0 01-2-2.3l1.3-7A2 2 0 016.3 4H17"/>'),
  copy: sv('<rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V5a1 1 0 00-1-1H5a1 1 0 00-1 1v10a1 1 0 001 1h3"/>'),
  filter: sv('<path d="M4 6h16M7 12h10M10 18h4"/>'),
  chat: sv('<path d="M4 5h16v11H9l-5 4z"/>'),
  bubble: sv('<path d="M21 12a8 8 0 01-11.6 7.1L4 20l1-4.6A8 8 0 1121 12z"/>'),
  plus: sv('<path d="M12 5v14M5 12h14"/>'),
  left: sv('<path d="M15 5l-7 7 7 7"/>', ' stroke-width="2.2"'),
  right: sv('<path d="M9 5l7 7-7 7"/>', ' stroke-width="2.2"'),
  chev: sv('<path d="M6 9l6 6 6-6"/>'),
  dleft: sv('<path d="M11 6l-6 6 6 6M19 6l-6 6 6 6"/>'),
  menu: sv('<path d="M4 7h16M4 12h16M4 17h16"/>'),
  dots: '<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><circle cx="5" cy="12" r="1.8"/><circle cx="12" cy="12" r="1.8"/><circle cx="19" cy="12" r="1.8"/></svg>',
  ticket: sv('<path d="M3 7h18v3.5a1.5 1.5 0 000 3V17H3v-3.5a1.5 1.5 0 000-3z"/><path d="M15 7v10" stroke-dasharray="2 2"/>'),
  play: '<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M8 5v14l11-7z"/></svg>',
  tv: sv('<rect x="3" y="6" width="18" height="12" rx="2"/><path d="M8 21h8M9 2l3 3 3-3"/>'),
  film: sv('<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M7 4v16M17 4v16M3 9h4M17 9h4M3 15h4M17 15h4"/>'),
  link: sv('<path d="M10 14a4 4 0 005.7 0l3-3a4 4 0 00-5.7-5.7l-1 1M14 10a4 4 0 00-5.7 0l-3 3a4 4 0 005.7 5.7l1-1"/>'),
  check: sv('<path d="M5 12.5l4.5 4.5L19 7"/>', ' stroke-width="2.4"'),
  bell: sv('<path d="M6 8a6 6 0 1112 0c0 7 3 8 3 8H3s3-1 3-8M10 20a2 2 0 004 0"/>'),
  bellOn: '<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M6 8a6 6 0 1112 0c0 7 3 8 3 8H3s3-1 3-8z"/><path d="M10 20a2 2 0 004 0" stroke="currentColor" stroke-width="2" fill="none" stroke-linecap="round"/></svg>',
  bellOff: sv('<path d="M6 8a6 6 0 019.3-5M18 8c0 7 3 8 3 8H8M10 20a2 2 0 004 0M3 3l18 18"/>'),
  cal: sv('<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M3 10h18M8 3v4M16 3v4"/>'),
  user: sv('<circle cx="12" cy="8" r="4"/><path d="M4 21c1.5-4 4.5-6 8-6s6.5 2 8 6"/>'),
  users: sv('<circle cx="9" cy="8" r="3.5"/><path d="M2.5 20c1-3.5 3.5-5 6.5-5s5.5 1.5 6.5 5M16 4.5a3.5 3.5 0 010 7M18 15c2 .6 3.2 2.2 3.8 5"/>'),
  bolt: sv('<path d="M13 2L4 14h7l-1 8 9-12h-7z"/>'),
  clock: sv('<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>'),
  compass: sv('<circle cx="12" cy="12" r="9"/><path d="M15.5 8.5l-2 5-5 2 2-5z"/>'),
  monitor: sv('<rect x="3" y="4" width="18" height="12" rx="2"/><path d="M8 20h8M12 16v4"/>'),
  sparkle: sv('<path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8zM19 15l.8 2.2L22 18l-2.2.8L19 21l-.8-2.2L16 18l2.2-.8z"/>'),
  trophy: sv('<path d="M8 4h8v5a4 4 0 01-8 0zM8 6H4.5a2.5 2.5 0 003.6 4M16 6h3.5a2.5 2.5 0 01-3.6 4M12 13v4M8 21h8M9 17h6v4H9z"/>'),
  heart: '<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M12 21s-7.5-4.6-9.6-9.2C.9 8.4 3 4.5 6.8 4.5c2.1 0 3.6 1.2 5.2 3 1.6-1.8 3.1-3 5.2-3 3.8 0 5.9 3.9 4.4 7.3C19.5 16.4 12 21 12 21z"/></svg>',
  chart: sv('<path d="M5 20V10M10 20V4M15 20v-7M20 20v-4"/>', ' stroke-width="2.6"'),
  gear: sv('<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 00.3 1.8l.1.1a2 2 0 11-2.8 2.8l-.1-.1a1.7 1.7 0 00-1.8-.3 1.7 1.7 0 00-1 1.5V21a2 2 0 01-4 0v-.1a1.7 1.7 0 00-1.1-1.5 1.7 1.7 0 00-1.8.3l-.1.1a2 2 0 11-2.8-2.8l.1-.1a1.7 1.7 0 00.3-1.8 1.7 1.7 0 00-1.5-1H3a2 2 0 010-4h.1a1.7 1.7 0 001.5-1.1 1.7 1.7 0 00-.3-1.8l-.1-.1a2 2 0 112.8-2.8l.1.1a1.7 1.7 0 001.8.3H9a1.7 1.7 0 001-1.5V3a2 2 0 014 0v.1a1.7 1.7 0 001 1.5 1.7 1.7 0 001.8-.3l.1-.1a2 2 0 112.8 2.8l-.1.1a1.7 1.7 0 00-.3 1.8V9a1.7 1.7 0 001.5 1H21a2 2 0 010 4h-.1a1.7 1.7 0 00-1.5 1z"/>'),
  help: sv('<circle cx="12" cy="12" r="9"/><path d="M9.5 9a2.5 2.5 0 015 .5c0 1.5-2.5 2-2.5 3.5M12 17h.01"/>'),
  share: sv('<circle cx="18" cy="5" r="3"/><circle cx="6" cy="12" r="3"/><circle cx="18" cy="19" r="3"/><path d="M8.6 13.5l6.8 4M15.4 6.5l-6.8 4"/>'),
  edit: sv('<path d="M4 20h4l11-11a2.8 2.8 0 00-4-4L4 16z"/>'),
  upload: sv('<path d="M12 16V4M7 9l5-5 5 5M4 20h16"/>'),
  lock: sv('<rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V7a4 4 0 018 0v4"/>'),
  shield: sv('<path d="M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z"/>'),
  sliders: sv('<path d="M4 6h10M18 6h2M4 12h4M12 12h8M4 18h12M20 18h0"/><circle cx="16" cy="6" r="2"/><circle cx="10" cy="12" r="2"/><circle cx="18" cy="18" r="2"/>'),
  logout: sv('<path d="M15 4h4v16h-4M10 8l-4 4 4 4M6 12h10"/>'),
  trash: sv('<path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13"/>'),
  search: sv('<circle cx="11" cy="11" r="7"/><path d="M20 20l-3.5-3.5"/>'),
  history: sv('<path d="M3 12a9 9 0 103-6.7L3 8M3 3v5h5M12 7v5l3 2"/>'),
};
export const LOGO = '<svg viewBox="0 0 34 24" aria-hidden="true"><path d="M3 2h28a2 2 0 012 2v4.5a3.5 3.5 0 000 7V20a2 2 0 01-2 2H3a2 2 0 01-2-2v-4.5a3.5 3.5 0 000-7V4a2 2 0 012-2z" fill="#F3C24A"/><path d="M23 4v16" stroke="#0A1222" stroke-width="1.4" stroke-dasharray="2 2"/><path d="M10 7.5l7 4.5-7 4.5z" fill="#0A1222"/></svg>';
export const ART = {
  popcorn: '<svg class="art" viewBox="0 0 56 56" fill="none" aria-hidden="true"><path d="M14 22l4 28h20l4-28z" fill="#15233C" stroke="#F3C24A" stroke-width="2" stroke-linejoin="round"/><path d="M22 22l1.5 28M34 22l-1.5 28" stroke="#F3C24A" stroke-width="2"/><circle cx="18" cy="18" r="6" fill="#FFF3CF" stroke="#F3C24A" stroke-width="2"/><circle cx="28" cy="14" r="7" fill="#FFF3CF" stroke="#F3C24A" stroke-width="2"/><circle cx="38" cy="18" r="6" fill="#FFF3CF" stroke="#F3C24A" stroke-width="2"/></svg>',
  ticket: '<svg class="art" viewBox="0 0 56 56" fill="none" aria-hidden="true"><g transform="rotate(-12 28 28)"><path d="M8 16h40v8a4 4 0 000 8v8H8v-8a4 4 0 000-8z" fill="#15233C" stroke="#F3C24A" stroke-width="2" stroke-linejoin="round"/><path d="M36 17v22" stroke="#F3C24A" stroke-width="2" stroke-dasharray="3 3"/><path d="M17 23l9 5-9 5z" fill="#F3C24A"/></g></svg>',
  reel: '<svg class="art" viewBox="0 0 56 56" fill="none" aria-hidden="true"><circle cx="26" cy="26" r="18" fill="#15233C" stroke="#F3C24A" stroke-width="2"/><circle cx="26" cy="26" r="3" fill="#F3C24A"/><g fill="#FFF3CF" stroke="#F3C24A" stroke-width="1.5"><circle cx="26" cy="15" r="5"/><circle cx="36" cy="27" r="5"/><circle cx="26" cy="37" r="5"/><circle cx="16" cy="27" r="5"/></g><path d="M40 40c6 2 10 2 12-1" stroke="#F3C24A" stroke-width="2" stroke-linecap="round"/></svg>',
  tv: '<svg class="art" viewBox="0 0 56 56" fill="none" aria-hidden="true"><rect x="8" y="14" width="40" height="28" rx="4" fill="#15233C" stroke="#F3C24A" stroke-width="2"/><path d="M20 48h16M22 6l6 7 6-7" stroke="#F3C24A" stroke-width="2" stroke-linecap="round"/><path d="M24 22l10 6-10 6z" fill="#F3C24A"/></svg>',
};

// ---------------------------------------------------------------- formatting
export const rt = (m) => m ? `${Math.floor(m / 60)}h ${String(m % 60).padStart(2, '0')}m` : '';
export const iso = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
export const dayOf = (s) => new Date(s + 'T12:00:00');
export const fmtDate = (s, o = { month: 'short', day: 'numeric', year: 'numeric' }) => s ? dayOf(String(s).slice(0, 10)).toLocaleDateString(undefined, o) : '';
export function ago(t) {
  if (!t) return '';
  if (typeof t === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(t)) {
    const d = Math.round((new Date(new Date().toDateString()) - new Date(t + 'T00:00:00')) / 864e5);
    return d <= 0 ? 'today' : d === 1 ? 'yesterday' : d < 7 ? `${d} days ago` : d < 60 ? `${Math.round(d / 7)} wk ago` : fmtDate(t);
  }
  const ms = typeof t === 'number' ? (t < 1e12 ? t * 1000 : t) : Date.parse(t);
  const m = Math.round((Date.now() - ms) / 60000);
  return m < 1 ? 'just now' : m < 60 ? `${m} min ago` : m < 1440 ? `${Math.round(m / 60)} h ago` : m < 2880 ? 'yesterday' : m < 10080 ? `${Math.round(m / 1440)} days ago` : fmtDate(new Date(ms).toISOString().slice(0, 10));
}
export const firstGenre = (g) => (Array.isArray(g) ? g : String(g || '').split(',')).map(s => s.trim()).filter(Boolean);
export const shortGenre = (g) => g === 'Science Fiction' ? 'Sci-Fi' : g;
export function ratingEl(f) {
  if (f.imdb?.rating) return h('span', { class: 'stars', title: `IMDb rating from ${Number(f.imdb.votes || 0).toLocaleString()} votes` }, h('span', { html: I.star }), Number(f.imdb.rating).toFixed(1), h('span', { class: 'src' }, 'IMDb'));
  if (f.rating != null) return h('span', { class: 'stars', title: 'TMDB user rating' }, h('span', { html: I.star }), String(f.rating), h('span', { class: 'src' }, 'TMDB'));
  return null;
}

// ---------------------------------------------------------------- API
export async function api(path, opts = {}) {
  const res = await fetch(path, Object.assign({ headers: hdrs(), cache: 'no-store' }, opts));
  let j = {}; try { j = await res.json(); } catch {}
  if (res.status === 401 && /access code/i.test(j.error || '')) { gate(); throw Object.assign(new Error('Enter the access code.'), { status: 401 }); }
  if (!res.ok) throw Object.assign(new Error(j.error || `The server answered ${res.status}.`), { status: res.status });
  return j;
}
export const post = (path, body) => api(path, { method: 'POST', body: JSON.stringify(body || {}) });
export const emit = (name, detail) => document.dispatchEvent(new CustomEvent(name, { detail }));
export function on(name, fn, el) {          // listener that removes itself once `el` leaves the page
  const f = (e) => { if (el && !document.body.contains(el)) { document.removeEventListener(name, f); return; } fn(e.detail, e); };
  document.addEventListener(name, f);
}

// ---------------------------------------------------------------- feedback + dialogs
export function toast(msg, undo = null, bad = false) {
  const slot = $('#toast-slot'); slot.innerHTML = '';
  const t = h('div', { class: `toast${bad ? ' bad' : ''}`, role: 'status' }, h('span', {}, msg));
  if (undo) t.append(h('button', { type: 'button', onclick: () => { undo(); t.remove(); } }, 'Undo'));
  slot.append(t); clearTimeout(toast.t); toast.t = setTimeout(() => t.remove(), 5000);
}
export function sheet(title, build, opts = {}) {
  const d = h('dialog', { class: `sheet${opts.side ? ' side' : ''}${opts.left ? ' left' : ''}`, 'aria-label': title });
  const body = h('div', { class: 'sh-b' });
  d.append(h('div', { class: 'sh-h' }, h('h2', {}, title), h('button', { class: 'iconbtn', type: 'button', 'aria-label': 'Close', html: I.x, onclick: () => d.close() })), body);
  d.addEventListener('close', () => { d.remove(); opts.onclose?.(); });
  d.addEventListener('click', (e) => { if (e.target === d) d.close(); });
  document.body.append(d);
  build(body, d);
  d.showModal();
  return d;
}
// A small anchored menu (radio choices or actions). Arrow keys move, Esc closes and returns focus to the button.
export function popMenu(anchor, items, opts = {}) {
  closePops();
  const box = h('div', { class: 'pop', role: 'menu', 'aria-label': opts.label || '' });
  if (opts.title) box.append(h('div', { class: 'pop-h' }, opts.title));
  for (const it of items) {
    if (it.sep) { box.append(h('hr', { style: 'border:0;border-top:1px solid var(--line);margin:6px 0' })); continue; }
    const el = h(it.href ? 'a' : 'button', { type: it.href ? null : 'button', href: it.href || null, role: opts.radio ? 'menuitemradio' : 'menuitem',
      'aria-checked': opts.radio ? String(!!it.checked) : null, class: it.danger ? 'err' : null,
      onclick: () => { closePops(); it.onclick?.(); } }, it.icon ? h('span', { html: it.icon, style: 'display:inline-grid;width:18px;height:18px' }) : null, it.label);
    box.append(el);
  }
  document.body.append(box);
  const r = anchor.getBoundingClientRect(), w = Math.max(box.offsetWidth, r.width);
  let left = Math.min(r.left, innerWidth - w - 12); left = Math.max(12, left);
  let top = r.bottom + 6; if (top + box.offsetHeight > innerHeight - 12) top = Math.max(12, r.top - box.offsetHeight - 6);
  Object.assign(box.style, { left: left + 'px', top: top + 'px', minWidth: Math.max(220, r.width) + 'px' });
  anchor.setAttribute('aria-expanded', 'true');
  const btns = $$('[role^="menuitem"]', box);
  (btns.find(b => b.getAttribute('aria-checked') === 'true') || btns[0])?.focus();
  box.addEventListener('keydown', (e) => {
    const i = btns.indexOf(document.activeElement);
    if (e.key === 'ArrowDown') { e.preventDefault(); btns[(i + 1) % btns.length].focus(); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); btns[(i - 1 + btns.length) % btns.length].focus(); }
    else if (e.key === 'Escape' || e.key === 'Tab') { e.preventDefault(); closePops(); anchor.focus(); }
  });
  closePops.cur = { box, anchor };
  setTimeout(() => document.addEventListener('pointerdown', closePops.out = (e) => { if (!box.contains(e.target) && !anchor.contains(e.target)) closePops(); }), 0);
  return box;
}
export function closePops() {
  const c = closePops.cur; if (!c) return;
  c.box.remove(); c.anchor.setAttribute('aria-expanded', 'false'); closePops.cur = null;
  document.removeEventListener('pointerdown', closePops.out);
}
addEventListener('resize', () => closePops());
addEventListener('hashchange', () => closePops());

export function gate(message = '') {
  if ($('#gate-dialog')) return;
  sheet('Access code', (b, d) => {
    d.id = 'gate-dialog';
    const inp = h('input', { class: 'input', type: 'password', id: 'gate-code', autocomplete: 'current-password' });
    const err = h('p', { class: 'err', 'aria-live': 'polite' }, message);
    const go = async () => {
      S.code = inp.value.trim(); store.set('mb.code', S.code);
      try { await api('/api/health'); d.close(); window.dispatchEvent(new HashChangeEvent('hashchange')); emit('cm:me'); } catch { err.textContent = "That code didn't work."; }
    };
    inp.addEventListener('keydown', (e) => { if (e.key === 'Enter') go(); });
    b.append(h('p', { class: 'muted', style: 'margin-bottom:14px' }, 'Cinemack runs on a shared GPU, so it is open to people with the code.'),
      h('div', { class: 'field' }, h('label', { for: 'gate-code' }, 'Code'), inp), err,
      h('button', { class: 'btn primary', type: 'button', onclick: go }, 'Continue'));
    setTimeout(() => inp.focus(), 50);
  });
}

// ---------------------------------------------------------------- watchlist choices (one store for every page)
export const isWant = (id) => S.choices[id] === 'want';
export async function setChoice(f, choice, quiet = false) {
  const prev = S.choices[f.id] ?? f.mine ?? '';
  S.choices[f.id] = choice; syncChoice(f.id);
  try {
    const j = await post('/api/film', { id: f.id, title: f.title || '', choice });
    emit('cm:choice', { id: f.id, choice, film: f });
    if (!quiet) toast(choice === 'want' ? `Saved ${f.title || 'it'} to your watchlist.` : prev === 'want' && !choice ? `Removed ${f.title || 'it'} from your watchlist.` : (j.message || 'Saved.'),
      () => setChoice(f, prev, true));
    return true;
  } catch (e) { S.choices[f.id] = prev; syncChoice(f.id); toast(e.message || "Couldn't save that.", null, true); return false; }
}
export function syncChoice(id) {
  $$(`[data-want="${id}"]`).forEach(b => {
    const on = isWant(id);
    b.setAttribute('aria-pressed', String(on));
    if (b._label) b._label(on); else b.innerHTML = on ? I.bookmarkOn : I.bookmark;
  });
}
export function wantButton(f, style = 'icon') {
  if (!(f.id in S.choices)) S.choices[f.id] = f.mine || '';
  const name = `${f.title || 'this film'}${f.year ? ` (${f.year})` : ''}`;
  const b = h('button', { type: 'button', 'data-want': f.id, 'aria-pressed': String(isWant(f.id)) });
  if (style === 'icon') { b.className = 'iconbtn bm'; b.setAttribute('aria-label', `Watchlist: ${name}`); b._label = (on) => { b.innerHTML = on ? I.bookmarkOn : I.bookmark; }; }
  else {
    b.className = `btn ${style === 'primary' ? 'primary' : 'secondary'} sm`;
    b._label = (on) => { b.innerHTML = (on ? I.check : I.plus) + `<span>${on ? 'On watchlist' : 'Watchlist'}</span>`; };
    b.setAttribute('aria-label', `Watchlist: ${name}`);
  }
  b._label(isWant(f.id));
  b.addEventListener('click', () => { b.classList.remove('pop'); void b.offsetWidth; b.classList.add('pop'); setChoice(f, isWant(f.id) ? '' : 'want'); });
  return b;
}

// ---------------------------------------------------------------- showtimes (search links only; no showtime data)
export const zip = { get: () => (store.get('cm.zip') || ''), set: (z) => store.set('cm.zip', String(z || '').replace(/\D/g, '').slice(0, 5)) };
export function showtimesSheet(f) {
  sheet(`Showtimes: ${f.title}`, async (b) => {
    const z = h('input', { class: 'input', id: 'st-zip', inputmode: 'numeric', autocomplete: 'postal-code', maxlength: '5', placeholder: 'ZIP code (optional)', value: zip.get() });
    const list = h('div', { class: 'stlinks' });
    const draw = async () => {
      list.innerHTML = ''; list.append(h('p', { class: 'muted small' }, 'Loading…'));
      try {
        const t = await api(`/api/v8/theatres/${f.id}?zip=${encodeURIComponent(zip.get())}`);
        list.innerHTML = '';
        if (!t.in_theatres) { list.append(h('p', { class: 'muted' }, "This film isn't on the current US in-theaters list, so there are no showtimes to look up.")); return; }
        t.links.forEach((l, i) => list.append(h('a', { class: `btn ${i ? 'secondary' : 'primary'}`, href: l.url, target: '_blank', rel: 'noopener noreferrer' }, l.label, h('span', { class: 'sr' }, ' (opens a new tab)'))));
        list.append(h('p', { class: 'hint', style: 'flex-basis:100%' }, t.note + (t.zip ? ` Searching near ${t.zip}.` : ' Add a ZIP code for results near you.')));
      } catch (e) { list.innerHTML = ''; list.append(h('p', { class: 'err' }, e.message)); }
    };
    b.append(h('div', { class: 'field' }, h('label', { for: 'st-zip' }, 'Where are you?'),
      h('div', { style: 'display:flex;gap:8px' }, z, h('button', { class: 'btn secondary', type: 'button', onclick: () => { zip.set(z.value); z.value = zip.get(); draw(); } }, 'Update')),
      h('span', { class: 'hint' }, 'Only used to build the search links. It stays in this browser.')), list);
    z.addEventListener('keydown', (e) => { if (e.key === 'Enter') { zip.set(z.value); draw(); } });
    draw();
  });
}

// ---------------------------------------------------------------- posters, cards, shelves
// Missing artwork gets a styled title card; a slow image shows a shimmer (img.ld) and fades in; a broken image (404,
// bad URL) is replaced by the same title card instead of a blank box or a broken-image icon.
export const posterPlaceholder = (f) => h('span', { class: 'ph', 'data-ph': '1' }, h('span', { class: 'ph-ic', html: I.film }),
  h('span', { class: 'ph-t' }, f.title || 'Untitled'), f.year ? h('span', { class: 'ph-y' }, String(f.year).slice(0, 4)) : null);
export function posterImg(f, size = 'poster', w = 160) {
  const src = f[size] || f.poster || f.poster_lg;
  if (!src) return posterPlaceholder(f);
  const img = h('img', { src, alt: '', loading: 'lazy', decoding: 'async', width: w, height: Math.round(w * 1.5), class: 'ld' });
  img.addEventListener('load', () => img.classList.remove('ld'), { once: true });
  img.addEventListener('error', () => img.replaceWith(posterPlaceholder(f)), { once: true });
  return img;
}
export const filmHref = (f) => (f.media === 'tv' || (f.id != null && f.id < 0)) ? null : `#/film/${f.id}`;
// Poster card: poster with the title over it, a watchlist toggle, one line of facts underneath.
export function posterCard(f, opts = {}) {
  if (f.id != null && !(f.id in S.choices)) S.choices[f.id] = f.mine || '';
  const href = opts.href !== undefined ? opts.href : filmHref(f);
  const name = `${f.title || 'Untitled'}${f.year ? ` (${f.year})` : ''}`;
  const c = h('div', { class: 'pc' },
    h(href ? 'a' : 'span', { class: 'pl', href, 'aria-label': href ? `${name}: details` : null }, posterImg(f, 'poster_lg', 220),
      opts.capTitle ? h('span', { class: 'cap', 'aria-hidden': 'true' }, f.title) : null));
  if (!opts.capTitle) c.append(h(href ? 'a' : 'span', { class: 'ti', href, tabindex: href ? '-1' : null }, f.title || 'Untitled'));
  const facts = opts.meta ?? [f.year, f.runtime ? rt(f.runtime) : null].filter(Boolean);
  const me = h('div', { class: 'me' });
  if (f.rating != null && !opts.noRating) me.append(h('span', { class: 'r' }, `★ ${Number(f.rating).toFixed(1)}`));
  facts.forEach(x => me.append(h('span', {}, x)));
  if (me.childNodes.length) c.append(me);
  if (opts.why) c.append(h('div', { class: 'why' }, opts.why));
  if (opts.extra) c.append(opts.extra);
  if (f.id != null && f.media !== 'tv' && f.id > 0 && opts.save !== false) c.append(wantButton(f));
  return c;
}
// A horizontal shelf with arrows that page it; touch, trackpad and keyboard still scroll it.
export function shelf(items, label, mk = (f) => posterCard(f), cls = '') {
  const track = h('div', { class: 'track', tabindex: '0', role: 'list', 'aria-label': label },
    items.map(f => { const c = mk(f); c.setAttribute('role', 'listitem'); return c; }));
  const prev = h('button', { class: 'arrow prev', type: 'button', 'aria-label': `Scroll ${label} back`, html: I.left });
  const next = h('button', { class: 'arrow next', type: 'button', 'aria-label': `Scroll ${label} forward`, html: I.right });
  const smooth = () => (matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth');
  const page = (dir) => track.scrollBy({ left: dir * Math.max(200, track.clientWidth * 0.9), behavior: smooth() });
  prev.addEventListener('click', () => page(-1)); next.addEventListener('click', () => page(1));
  const box = h('div', { class: `shelf ${cls}` }, prev, track, next);
  const sync = () => { const max = track.scrollWidth - track.clientWidth; prev.disabled = track.scrollLeft <= 2; next.disabled = track.scrollLeft >= max - 2; box.classList.toggle('no-arrows', max <= 2); };
  track.addEventListener('scroll', () => { cancelAnimationFrame(sync.r); sync.r = requestAnimationFrame(sync); }, { passive: true });
  new ResizeObserver(sync).observe(track);
  setTimeout(sync, 0);
  return box;
}
export function section(title, opts = {}, ...kids) {
  const head = h('div', { class: 'section-h' }, h('h2', {}, title));
  if (opts.sub) head.append(h('span', { class: 'sub' }, opts.sub));
  if (opts.more) head.append(h('a', { class: 'act', href: opts.more[0] }, opts.more[1] || 'See all'));
  return h('section', { class: 'section', 'aria-label': title, id: opts.id || null }, head, ...kids);
}
export function panel(title, icon, opts = {}, ...kids) {
  const head = h('div', { class: 'panel-h' }, icon ? h('span', { class: 'ico', html: icon }) : null, h(opts.level || 'h2', {}, title));
  if (opts.act) head.append(h('span', { class: 'act' }, opts.act));
  if (opts.sub) head.append(h('span', { class: 'sub' }, opts.sub));
  return h('section', { class: `panel ${opts.cls || ''}`, 'aria-label': title }, head, ...kids);
}
export const skelShelf = (n = 7) => h('div', { class: 'shelf', 'aria-hidden': 'true' }, h('div', { class: 'track' }, Array.from({ length: n }, () => h('div', { class: 'skel', style: 'aspect-ratio:2/3' }))));
export function empty(title, text, art, actions = [], small = false) {
  return h('div', { class: `empty${small ? ' sm' : ''}` }, art ? h('div', { html: ART[art] || '' }) : null, title ? h('h3', {}, title) : null, text ? h('p', {}, text) : null,
    actions.length ? h('div', { class: 'acts' }, actions) : null);
}

// ---------------------------------------------------------------- shared discovery filters (chat + Home)
// The same 18 genres the server filters on (discovery.GENRE_OPTIONS = user_prefs.GENRES); Home replaces this with the
// list the server sends, so page and server can never disagree.
export let GENRES = ['Action', 'Adventure', 'Animation', 'Comedy', 'Crime', 'Documentary', 'Drama', 'Family', 'Fantasy', 'History', 'Horror', 'Music', 'Mystery', 'Romance', 'Science Fiction', 'Thriller', 'War', 'Western'];
export const setGenres = (list) => { if (Array.isArray(list) && list.length) GENRES = list; };
export const FILTERS = {
  era: [['', 'Any era'], ['pre1970', 'Before 1970'], ['1970s', '1970s'], ['1980s', '1980s'], ['1990s', '1990s'], ['2000s', '2000s'], ['2010s', '2010s'], ['2020s', '2020s']],
  max_runtime: [['', 'Any length'], ['90', 'Under 90 min'], ['120', 'Under 2 hours'], ['150', 'Under 2½ hours']],
  max_cert: [['', 'Any rating'], ['G', 'G'], ['PG', 'PG or milder'], ['PG-13', 'PG-13 or milder'], ['R', 'R or milder']] };
S.filters = Object.assign({ genres: [] }, store.json('cm.filters', {}));
if (!Array.isArray(S.filters.genres)) S.filters.genres = [];
export const F = {
  save() { store.set('cm.filters', JSON.stringify(S.filters)); emit('cm:filters'); },
  count() { const f = S.filters; return (F.allGenres() ? 0 : (f.genres || []).length) + ['era', 'max_runtime', 'max_cert', 'services_only'].filter(k => f[k]).length; },
  allGenres() { const g = new Set(S.filters.genres || []); return GENRES.length > 0 && GENRES.every(x => g.has(x)); },
  // Every genre selected means no genre restriction, on Home and in the chat. The chat matches ANY of the genres.
  forChat() { const f = Object.assign({}, S.filters); if (!(f.genres || []).length || F.allGenres()) delete f.genres; delete f.genre_mode; if (f.long) delete f.brief; else f.brief = true; delete f.long;
    for (const k of Object.keys(f)) if (f[k] === '' || f[k] == null || f[k] === false) delete f[k]; return f; },
  toggleGenre(g) { const s = new Set(S.filters.genres || []); s.has(g) ? s.delete(g) : s.add(g); S.filters.genres = GENRES.filter(x => s.has(x)); F.save(); },
};

// ---------------------------------------------------------------- answers: markdown + sources
export const safeUrl = (u) => { try { const x = new URL(u); return (x.protocol === 'https:' || x.protocol === 'http:') ? x.href : null; } catch { return null; } };
export function md(src) {
  const lines = esc(src).split(/\n/); let out = '', list = null;
  const inline = (t) => t
    .replace(/\[([^\]]{1,120})\]\((https?:\/\/[^\s)]+)\)/g, (m, label, url) => { const u = safeUrl(url.replace(/&amp;/g, '&')); return u ? `<a href="${esc(u)}" target="_blank" rel="noopener noreferrer">${label}</a>` : label; })
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>').replace(/(^|[^*])\*(?!\s)(.+?)\*/g, '$1<em>$2</em>');
  for (const l of lines) {
    const m = l.match(/^\s*(?:[-*]|\d+\.)\s+(.*)/);
    if (m) { if (!list) { out += '<ul>'; list = true; } out += `<li>${inline(m[1])}</li>`; continue; }
    if (list) { out += '</ul>'; list = null; }
    if (l.trim()) out += `<p>${inline(l)}</p>`;
  }
  return out + (list ? '</ul>' : '');
}
// The server appends sources as a text line ("*Sources: [Title](url); ... (Wikipedia, CC BY-SA). Film data from TMDB.*").
// Take it off the answer; the page shows a compact Sources control that keeps every required credit.
export function splitSources(answer) {
  const i = answer.search(/\n\s*\*?(Sources|Wikipedia articles):/);
  if (i < 0) return { text: answer, links: [], notes: [] };
  const tail = answer.slice(i), links = [];
  tail.replace(/(?:\[(\d{1,2})\]\s*)?\[([^\]]{1,120})\]\((https?:\/\/[^\s)]+)\)/g, (m, n, label, url) => { const u = safeUrl(url); if (u && !links.some(x => x.url === u)) links.push({ n: n || null, label, url: u }); return m; });
  const notes = [];
  if (/CC BY-SA/i.test(tail)) notes.push('Wikipedia text is licensed CC BY-SA 4.0.');
  if (/reviews link/i.test(tail)) notes.push('Reviews link to their original pages.');
  notes.push('Film data from TMDB.');
  return { text: answer.slice(0, i).trimEnd(), links, notes };
}
export function sourcesEl(src) {
  if (!src.links.length) return null;
  const host = (u) => { try { return new URL(u).hostname.replace(/^www\.|^en\./, ''); } catch { return ''; } };
  return h('details', { class: 'srcs' },
    h('summary', {}, h('span', { html: I.link }), `Sources (${src.links.length})`, h('span', { class: 'cv', html: I.chev })),
    h('div', { class: 'box' }, h('ol', {}, src.links.map(l => h('li', {}, h('a', { href: l.url, target: '_blank', rel: 'noopener noreferrer' }, l.label),
      h('span', { class: 'host' }, ` · ${host(l.url)}`), h('span', { class: 'sr' }, ' (opens a new tab)')))),
      h('p', { class: 'lic' }, src.notes.join(' '))));
}

// ---------------------------------------------------------------- Continue Watching (shared by Watchlist and Profile)
// Only recorded progress or imported episodes; no percentages for episodes (episode counts are not in the data).
export const CW = {
  items: null,
  async load(force = false) { if (!force && CW.items) return CW.items; const j = await api('/api/v8/continue'); CW.items = j.items || []; return CW.items; },
  async save(body, label) {
    try { const j = await post('/api/v8/tv/progress', body); CW.items = j.items || []; emit('cm:progress', CW.items); if (label) toast(label); return true; }
    catch (e) { toast(e.message, null, true); return false; }
  },
};
export function seasonsBar(sb) {
  if (!sb || !sb.total) return null;
  return h('div', {}, h('div', { class: 'sbar', role: 'img', 'aria-label': `${sb.done} of ${sb.total} seasons finished` },
    Array.from({ length: Math.min(sb.total, 24) }, (_, i) => h('i', { class: i < sb.done ? 'on' : '' }))),
    h('div', { class: 'sbar-l' }, `${sb.done} of ${sb.total} season${sb.total === 1 ? '' : 's'} finished`));
}
export function cwCard(it) {
  const last = it.last || {}, nx = it.next;
  const src = last.source === 'you' ? `Updated ${ago(last.at)}` : last.source ? `From your ${last.source[0].toUpperCase() + last.source.slice(1)} history, ${ago(last.at)}` : '';
  const acts = h('div', { class: 'acts' });
  if (nx && nx.episode) acts.append(h('button', { class: 'btn primary sm', type: 'button', html: I.check + `<span>Watched ${esc(nx.label)}</span>`,
    onclick: (e) => { e.currentTarget.classList.add('busy'); CW.save({ id: it.id, season: nx.season, episode: nx.episode }, `${it.title}: ${nx.label} watched.`); } }));
  acts.append(h('button', { class: 'btn secondary sm', type: 'button', onclick: () => progressSheet(it) }, last.source === 'you' ? 'Update' : 'Track progress'));
  return h('div', { class: 'cw', role: 'listitem' },
    h('div', { class: 'pp' }, it.poster ? h('img', { src: it.poster, alt: '', loading: 'lazy', width: 76, height: 114 }) : h('span', { class: 'ph' }, it.title)),
    h('div', { class: 'in' }, h('div', { class: 'tt', title: it.title }, it.title),
      h('div', { class: 'l1' }, 'Last watched: ', last.label || '—'),
      nx ? h('div', { class: 'l2' }, nx.upcoming ? h('b', {}, nx.date ? `${nx.label} ${fmtDate(nx.date)}` : nx.label) : nx.caught_up ? h('span', { class: 'muted' }, nx.label) : ['Next: ', h('b', {}, nx.label)])
        : h('div', { class: 'l2 muted' }, 'Next episode unknown. Set your progress to track it.'),
      seasonsBar(it.seasons_bar), src ? h('div', { class: 'src' }, src) : null, acts));
}
export function progressSheet(show, done) {
  sheet(`Progress: ${show.title}`, (b, d) => {
    const total = show.seasons_out || (show.last?.season || 1);
    const sSel = h('select', { class: 'input', id: 'pg-s' }, Array.from({ length: Math.max(total, show.last?.season || 1) }, (_, i) => h('option', { value: i + 1, selected: (show.last?.season || 1) === i + 1 }, `Season ${i + 1}`)));
    const eIn = h('input', { class: 'input', id: 'pg-e', type: 'number', min: '1', max: '500', inputmode: 'numeric', value: show.last?.episode || '' });
    const err = h('p', { class: 'err', 'aria-live': 'polite' });
    b.append(h('p', { class: 'muted', style: 'margin-bottom:14px' }, 'Cinemack does not see what you play on streaming services. Tell it how far you are and it keeps track here and on your profile.'),
      h('div', { style: 'display:grid;grid-template-columns:1fr 1fr;gap:12px' }, h('div', { class: 'field' }, h('label', { for: 'pg-s' }, 'Season'), sSel),
        h('div', { class: 'field' }, h('label', { for: 'pg-e' }, 'Last episode watched'), eIn)), err,
      h('div', { class: 'actions', style: 'margin:4px 0 0' },
        h('button', { class: 'btn primary', type: 'button', onclick: async () => {
          if (!eIn.value) { err.textContent = 'Type the episode number, or use "Finished this season".'; return; }
          if (await CW.save({ id: show.id, season: +sSel.value, episode: +eIn.value }, `${show.title}: progress saved.`)) { d.close(); done?.(); } } }, 'Save'),
        h('button', { class: 'btn secondary', type: 'button', onclick: async () => { if (await CW.save({ id: show.id, season: +sSel.value, finished: true }, `${show.title}: season ${sSel.value} finished.`)) { d.close(); done?.(); } } }, 'Finished this season'),
        show.last?.source === 'you' ? h('button', { class: 'btn ghost', type: 'button', onclick: async () => { if (await CW.save({ id: show.id, clear: true }, `Cleared your progress for ${show.title}.`)) { d.close(); done?.(); } } }, 'Clear') : null));
  });
}
export function continueRow(items, opts = {}) {
  const box = h('div', { class: 'cont', role: 'list', 'aria-label': 'Continue watching' }, items.map(cwCard));
  return box;
}

// A followed-or-not TV show: follow, alerts and progress from anywhere (search, calendar).
export function showSheet(s) {
  sheet(s.title, async (b, d) => {
    const row = h('div', { style: 'display:grid;grid-template-columns:80px 1fr;gap:16px;margin-bottom:16px' },
      h('div', { style: 'aspect-ratio:2/3;border-radius:8px;overflow:hidden;background:var(--s3)' }, posterImg(s, 'poster', 80)),
      h('div', {}, h('div', { class: 'muted small' }, [s.year, s.network, 'TV series'].filter(Boolean).join(' · ')), h('div', { id: 'sh-st', class: 'small', style: 'margin-top:8px' })));
    const acts = h('div', { class: 'actions', style: 'margin:0' });
    b.append(row, acts, h('p', { class: 'hint', style: 'margin-top:12px' }, 'Following puts the show on your Watchlist and in the Calendar, and alerts you in Cinemack (the bell) when a new season gets a date. Cinemack does not send email or push notifications.'));
    const draw = async () => {
      acts.innerHTML = '';
      let following = !!s.following, notify = true;
      try { const j = await api('/api/v8/watchlist/overview'); const me = (j.shows || []).find(x => x.id === s.id); following = !!me; if (me) { notify = me.notify; $('#sh-st').textContent = me.next_season ? `Season ${me.next_season.number} starts ${new Date(me.next_season.date + 'T12:00').toLocaleDateString()}` : me.latest_season ? `Latest: season ${me.latest_season.number}` : ''; } } catch {}
      acts.append(h('button', { class: `btn ${following ? 'secondary' : 'primary'}`, type: 'button', 'aria-pressed': String(following), html: (following ? I.check : I.plus) + `<span>${following ? 'Following' : 'Follow show'}</span>`,
        onclick: async () => { try { await post('/api/v8/tv/follow', { id: s.id, on: !following }); s.following = !following; toast(following ? `Stopped following ${s.title}.` : `Following ${s.title}.`); emit('cm:follow', { id: s.id, on: !following }); draw(); } catch (e) { toast(e.message, null, true); } } }));
      if (following) acts.append(h('label', { class: 'switch' }, h('input', { type: 'checkbox', checked: notify, onchange: async (e) => { try { await post('/api/v8/tv/follow', { id: s.id, on: true, notify: e.target.checked }); toast(e.target.checked ? 'Season alerts on.' : 'Season alerts off.'); } catch (er) { toast(er.message, null, true); } } }), 'New-season alerts'));
      acts.append(h('button', { class: 'btn secondary', type: 'button', onclick: async () => { d.close(); const items = await CW.load(true).catch(() => []); const it = items.find(x => x.id === s.id) || { id: s.id, title: s.title, seasons_out: s.seasons_out || 0, last: null }; progressSheet(it); } }, 'Track progress'));
    };
    draw();
  });
}

// ---------------------------------------------------------------- accounts
export function authSheet(mode) {
  sheet(mode === 'up' ? 'Create account' : 'Sign in', (b, d) => {
    const u = h('input', { class: 'input', id: 'au-u', autocomplete: 'username' });
    const pw = h('input', { class: 'input', id: 'au-p', type: 'password', autocomplete: mode === 'up' ? 'new-password' : 'current-password' });
    const nm = h('input', { class: 'input', id: 'au-n', autocomplete: 'nickname' });
    const err = h('p', { class: 'err', 'aria-live': 'polite' });
    b.append(h('div', { class: 'field' }, h('label', { for: 'au-u' }, 'Username'), u), h('div', { class: 'field' }, h('label', { for: 'au-p' }, 'Password'), pw));
    if (mode === 'up') b.append(h('div', { class: 'field' }, h('label', { for: 'au-n' }, 'Display name (optional)'), nm),
      h('p', { class: 'notice warn', style: 'margin-bottom:14px' }, 'This site does not use HTTPS yet, so your password is sent unencrypted. Choose one you use nowhere else. Your watchlist, chats and history from this browser move into the new account.'));
    const go = async (e) => {
      const btn = e?.currentTarget; btn?.classList.add('busy');
      try { S.me = await post(mode === 'up' ? '/api/signup' : '/api/login', mode === 'up' ? { username: u.value, password: pw.value, display_name: nm.value, keep_history: true } : { username: u.value, password: pw.value });
        d.close(); toast(mode === 'up' ? 'Account created.' : 'Signed in.'); CW.items = null; emit('cm:me'); window.dispatchEvent(new HashChangeEvent('hashchange')); }
      catch (er) { err.textContent = er.message; } finally { btn?.classList.remove('busy'); }
    };
    pw.addEventListener('keydown', (e) => { if (e.key === 'Enter') go(); });
    b.append(err, h('button', { class: 'btn primary block', type: 'button', onclick: go }, mode === 'up' ? 'Create account' : 'Sign in'),
      h('p', { class: 'hint', style: 'margin-top:12px;text-align:center' }, mode === 'up' ? 'Already have an account? ' : 'New here? ',
        h('button', { type: 'button', class: 'btn ghost sm', onclick: () => { d.close(); authSheet(mode === 'up' ? 'in' : 'up'); } }, mode === 'up' ? 'Sign in' : 'Create an account')));
    setTimeout(() => u.focus(), 50);
  });
}
export async function signOut() { try { await post('/api/logout'); } catch {} S.me = null; CW.items = null; toast('Signed out.'); emit('cm:me'); window.dispatchEvent(new HashChangeEvent('hashchange')); }
export const displayName = (me) => me?.name || me?.account?.display_name || me?.account?.username || '';
