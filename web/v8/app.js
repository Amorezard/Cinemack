// Cinemack v8 shell (10 Nov 2026 redesign): router, header (search, alerts, profile menu), phone tab bar.
// Each page is its own module, loaded the first time it is opened.
import { $, $$, S, h, I, api, post, popMenu, sheet, view, displayName, authSheet, signOut, ago, posterImg, closePops, showSheet } from './core.js';

const PAGES = {
  home: () => import('./home.js').then(m => m.pageHome),
  ask: () => import('./chat.js').then(m => m.pageChat),
  pick: () => import('./pick.js').then(m => m.pagePick),
  calendar: () => import('./calendar.js').then(m => m.pageCalendar),
  watchlist: () => import('./watchlist.js').then(m => m.pageWatchlist),
  profile: () => import('./profile.js').then(m => m.pageProfile),
  friend: () => import('./profile.js').then(m => m.pageFriend),
  film: () => import('./film.js').then(m => m.pageFilm),
  show: () => import('./show.js').then(m => m.pageShow),          // round 21: TV show page with episodes
  join: () => import('./join.js').then(m => m.pageJoin),          // round 21: create an account and set it up
};
const ROUTES = [[/^#?\/?$/, 'home', 'home'], [/^#\/(ask|discover)/, 'ask', 'ask'], [/^#\/pick/, 'pick', 'pick'], [/^#\/watchlist/, 'watchlist', 'watchlist'],
  [/^#\/calendar/, 'calendar', 'calendar'], [/^#\/profile/, 'profile', 'profile'], [/^#\/film\/(\d+)/, 'film', null], [/^#\/friend\/([^?]+)/, 'friend', 'profile'],
  [/^#\/show\/(\d+)/, 'show', null], [/^#\/join/, 'join', null]];
const TITLES = { home: 'Cinemack', ask: 'Ask Cinemack', pick: 'Pick Tonight', calendar: 'Release Calendar', watchlist: 'My Watchlist', profile: 'Profile', friend: 'Friend', show: 'TV show', join: 'Create your account' };

// round 22: after a deploy, an open page would load new page code with its old stylesheet. On navigation, compare
// the server's build fingerprint with the one this page started with and reload once when it changed.
let BUILD = null, buildAt = 0;
fetch('/api/v8/build', { cache: 'no-store' }).then(r => r.json()).then(j => { BUILD = j.build; buildAt = Date.now(); }).catch(() => {});
async function staleBuild() {
  if (!BUILD || Date.now() - buildAt < 20000 || S.chat?.busy) return false;
  buildAt = Date.now();
  try { const j = await (await fetch('/api/v8/build', { cache: 'no-store' })).json(); return j.build && j.build !== BUILD; } catch { return false; }
}
async function route() {
  if (await staleBuild()) { location.reload(); return; }
  const hash = location.hash || '#/';
  const [path, qs] = hash.split('?');
  const params = new URLSearchParams(qs || '');
  for (const [re, key, nav] of ROUTES) {
    const m = path.match(re);
    if (!m) continue;
    $$('[data-nav]').forEach(a => { if (a.dataset.nav === nav) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current'); });
    document.title = TITLES[key] && key !== 'home' ? `${TITLES[key]} · Cinemack` : 'Cinemack';
    view.className = 'page'; document.body.classList.toggle('on-chat', key === 'ask');
    if (key !== 'ask') window.scrollTo(0, 0);
    const seq = route.seq = (route.seq || 0) + 1;
    try {
      const fn = await PAGES[key]();
      if (seq !== route.seq) return;
      await fn(key === 'film' || key === 'friend' || key === 'show' ? decodeURIComponent(m[1]) : params, params);
    } catch (e) {
      if (seq === route.seq && e.status !== 401) { view.innerHTML = ''; view.append(h('div', { class: 'notice' }, e.message || 'Something went wrong.')); console.error(e); }
    }
    if (route.seen && key !== 'ask') $('#main').focus({ preventScroll: true });
    route.seen = true;
    return;
  }
  location.hash = '#/';
}

// ---------------------------------------------------------------- header search
function searchBox(input, pop, onPick) {
  let t = 0, seq = 0, items = [], cur = -1;
  const close = () => { pop.hidden = true; input.setAttribute('aria-expanded', 'false'); cur = -1; };
  const mark = () => items.forEach((el, i) => { el.setAttribute('aria-selected', String(i === cur)); if (i === cur) { el.scrollIntoView({ block: 'nearest' }); input.setAttribute('aria-activedescendant', el.id); } });
  const draw = (r, q) => {
    pop.innerHTML = ''; items = []; cur = -1;
    const add = (label, list, mk) => { if (!list.length) return; pop.append(h('div', { class: 'grp', role: 'presentation' }, label)); list.forEach(x => { const el = mk(x); el.id = `sr-${items.length}`; items.push(el); pop.append(el); }); };
    add('Movies', r.films || [], (f) => h('a', { class: 'sr-item', role: 'option', href: `#/film/${f.id}`, onclick: () => { close(); onPick?.(); } },
      h('span', { class: 'pp' }, posterImg(f, 'poster', 36)), h('span', {}, h('span', { class: 't' }, f.title), h('span', { class: 'm', style: 'display:block' }, [f.year, (f.genres || '').split(',')[0]].filter(Boolean).join(' · '))),
      f.rating != null ? h('span', { class: 'm' }, `★ ${f.rating}`) : ''));
    add('TV shows', r.shows || [], (s) => h('a', { class: 'sr-item', role: 'option', href: `#/show/${Math.abs(s.id)}`, onclick: () => { close(); onPick?.(); } },
      h('span', { class: 'pp' }, posterImg(s, 'poster', 36)), h('span', {}, h('span', { class: 't' }, s.title), h('span', { class: 'm', style: 'display:block' }, [s.year, s.network, 'TV'].filter(Boolean).join(' · '))),
      s.following ? h('span', { class: 'tag tvk' }, 'Following') : ''));
    pop.append(h('a', { class: 'sr-item', role: 'option', id: `sr-${items.length}`, href: `#/ask?ask=${encodeURIComponent(q)}`, style: 'grid-template-columns:36px 1fr', onclick: () => { close(); onPick?.(); } },
      h('span', { html: I.sparkle, style: 'width:22px;height:22px;color:var(--accent);justify-self:center' }), h('span', {}, h('span', { class: 't' }, `Ask Cinemack: "${q}"`), h('span', { class: 'm', style: 'display:block' }, 'Describe a scene, a plot, or what you are in the mood for'))));
    items.push(pop.lastChild);
    pop.hidden = false; input.setAttribute('aria-expanded', 'true');
  };
  input.addEventListener('input', () => {
    clearTimeout(t); const q = input.value.trim();
    if (q.length < 2) { close(); return; }
    t = setTimeout(async () => { const my = ++seq; try { const r = await api('/api/v8/search?q=' + encodeURIComponent(q)); if (my === seq && input.value.trim() === q) draw(r, q); } catch {} }, 180);
  });
  input.addEventListener('keydown', (e) => {
    if (pop.hidden && e.key !== 'Enter') return;
    if (e.key === 'ArrowDown') { e.preventDefault(); cur = Math.min(items.length - 1, cur + 1); mark(); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); cur = Math.max(0, cur - 1); mark(); }
    else if (e.key === 'Enter') { e.preventDefault(); if (cur >= 0) items[cur].click(); else if (input.value.trim().length > 1) { location.hash = `#/ask?ask=${encodeURIComponent(input.value.trim())}`; close(); onPick?.(); } }
    else if (e.key === 'Escape') { close(); }
  });
  input.addEventListener('blur', () => setTimeout(() => { if (!pop.contains(document.activeElement)) close(); }, 150));
}
searchBox($('#hdr-q'), $('#hdr-res'), () => { $('#hdr-q').value = ''; $('#hdr-q').blur(); });
$('#hdr-search-btn').addEventListener('click', () => sheet('Search', (b, d) => {
  const inp = h('input', { class: 'input', type: 'search', placeholder: 'Search movies and shows', 'aria-label': 'Search movies and shows', autocomplete: 'off', role: 'combobox', 'aria-expanded': 'false', 'aria-controls': 'm-res' });
  const pop = h('div', { id: 'm-res', role: 'listbox', 'aria-label': 'Search results', hidden: true, style: 'margin-top:10px' });
  b.append(inp, pop); searchBox(inp, pop, () => d.close()); setTimeout(() => inp.focus(), 60);
}));

// ---------------------------------------------------------------- alerts + profile menu
function alertsPop(btn) {
  const box = popMenu(btn, [], { label: 'Alerts' });
  box.classList.add('alerts-pop'); box.setAttribute('role', 'dialog'); box.innerHTML = '';
  box.append(h('div', { class: 'pop-h' }, 'Alerts'), h('p', { class: 'muted small', style: 'padding:8px 12px' }, 'Loading…'));
  api('/api/v8/alerts').then(a => {
    box.innerHTML = ''; box.append(h('div', { class: 'pop-h' }, 'Alerts'));
    if (!a.alerts.length) box.append(h('p', { class: 'muted small', style: 'padding:6px 12px 12px' }, 'No alerts yet. Save films and follow shows; you get an alert here when one comes out, starts streaming, or gets a new-season date.'));
    a.alerts.slice(0, 20).forEach(x => box.append(h(x.film?.media === 'tv' ? 'div' : 'a', { class: `alert-row${x.seen ? '' : ' new'}`, href: x.film?.media === 'tv' ? null : `#/film/${x.film?.id}`, onclick: () => closePops() },
      h('span', { class: 'pp' }, x.film ? posterImg(x.film, 'poster', 40) : ''), h('span', {}, h('span', { class: 't', style: 'display:block' }, x.text), h('span', { class: 'm' }, ago(x.at))))));
    box.append(h('hr', { style: 'border:0;border-top:1px solid var(--line);margin:6px 0' }),
      h('a', { role: 'menuitem', href: '#/watchlist?tab=coming', onclick: () => closePops() }, 'Upcoming from your watchlist'),
      h('a', { role: 'menuitem', href: '#/profile?tab=settings&sec=notifications', onclick: () => closePops() }, 'Alert settings'));
    if (a.unseen) post('/api/v8/alerts/seen').then(() => updateBell(0)).catch(() => {});
  }).catch(e => { box.innerHTML = ''; box.append(h('p', { class: 'err', style: 'padding:12px' }, e.message)); });
}
$('#bell').addEventListener('click', (e) => { if (closePops.cur?.anchor === e.currentTarget) { closePops(); return; } alertsPop(e.currentTarget); });
$('#me-btn').addEventListener('click', (e) => {
  const btn = e.currentTarget;
  if (closePops.cur?.anchor === btn) { closePops(); return; }
  const me = S.me, name = displayName(me) || 'Guest';
  const items = [{ label: 'Profile', href: '#/profile', icon: I.user }, { label: 'Watchlist', href: '#/watchlist', icon: I.bookmark },
    { label: 'Settings', href: '#/profile?tab=settings', icon: I.gear }, { label: 'Imports', href: '#/profile?tab=settings&sec=imports', icon: I.upload }, { sep: true }];
  if (me?.account) items.push({ label: 'Sign out', onclick: signOut, icon: I.logout });
  else items.push({ label: 'Sign in', onclick: () => authSheet('in'), icon: I.user }, { label: 'Create account', onclick: () => authSheet('up'), icon: I.plus });
  items.push({ sep: true }, { label: 'How Cinemack works', href: '/how', icon: I.help }, { label: 'Classic page', href: '/', icon: I.film });
  const box = popMenu(btn, items, { label: 'Account' });
  box.prepend(h('div', { class: 'who', style: 'padding:10px 12px 12px;border-bottom:1px solid var(--line);margin-bottom:6px' }, h('b', { style: 'display:block' }, name),
    h('span', { class: 'small muted' }, me?.account ? `@${me.account.username}` : 'Not signed in · saved in this browser')));
});
export function updateBell(n) { const b = $('#bell-n'); b.hidden = !n; b.textContent = n > 9 ? '9+' : String(n); $('#bell').setAttribute('aria-label', n ? `Alerts: ${n} new` : 'Alerts'); }
async function refreshMe() {
  try {
    S.me = await api('/api/me'); const n = displayName(S.me);
    const av = $('#avatar'); if (n) av.textContent = n[0].toUpperCase(); else av.innerHTML = I.user.replace('<svg', '<svg width="18" height="18"');
  } catch {}
  // round 21: signed out -> "Sign in" and "Create account" in the header (the avatar menu keeps both too)
  const out = !S.me?.account;
  $('#hdr-auth').hidden = !out; document.body.classList.toggle('signed-out', out);
  $('#me-btn').setAttribute('aria-label', out ? 'Guest menu: profile, settings, sign in' : 'Your profile and settings');
  try { const a = await api('/api/v8/alerts'); updateBell(a.unseen); } catch {}
}
document.addEventListener('cm:me', refreshMe);
$('#hdr-signin').addEventListener('click', () => authSheet('in'));

// On phones the tab bar steps aside while the keyboard is up, so the composer and inputs keep their room.
document.addEventListener('focusin', (e) => { if (matchMedia('(max-width:1023px)').matches && e.target.matches('input:not([type=checkbox]):not([type=file]),textarea,select')) document.body.classList.add('kb'); });
document.addEventListener('focusout', () => setTimeout(() => { if (!document.activeElement?.matches?.('input,textarea,select')) document.body.classList.remove('kb'); }, 80));

window.addEventListener('hashchange', route);
refreshMe();
route();
