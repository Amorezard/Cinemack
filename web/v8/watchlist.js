// Watchlist (10 Nov 2026 redesign): the person's library. Up Next (a real saved film, chosen by the server's
// "watch next" reasons), more posters beside it, Coming From Your Watchlist, Continue Watching (shared with Profile),
// dense shelves that never repeat a film, then every film. Tabs: Overview, Movies, TV Shows, Coming Soon, Released,
// and Shared only when a friend shares a list. Nothing here is invented: an empty list gets onboarding instead.
import { $, $$, S, h, I, api, post, view, store, toast, sheet, emit, on, rt, ago, fmtDate, firstGenre, shortGenre, posterImg, posterCard,
  shelf, section, panel, skelShelf, empty, wantButton, setChoice, isWant, showtimesSheet, CW, continueRow, cwCard, progressSheet, showSheet } from './core.js';

const TABS = [['overview', 'Overview'], ['movies', 'Movies'], ['tv', 'TV Shows'], ['coming', 'Coming Soon'], ['released', 'Released']];
const STATUS = { streaming: 'Streaming', theatres: 'In theaters', out: 'Out, not streaming', coming: 'Not out yet' };
let D = null;          // overview + upcoming + continue watching for this visit

export async function pageWatchlist(params) {
  view.innerHTML = '';
  D = null;                     // every visit reads fresh data: progress or saves made on other pages must show here
  const tab = TABS.some(t => t[0] === params.get('tab')) || params.get('tab') === 'shared' ? params.get('tab') : 'overview';
  const tabs = h('div', { class: 'pills', role: 'tablist', 'aria-label': 'Watchlist sections', style: 'margin:-4px 0 22px' });
  const body = h('div', { role: 'tabpanel', id: 'wl-panel' }, skelShelf(), skelShelf());
  view.append(h('div', { class: 'page-h' }, h('span', { class: 'ic', html: I.bookmark }),
    h('div', { class: 'tx' }, h('h1', {}, 'My Watchlist'), h('p', {}, "Everything you want to watch, what you're part-way through, and what's coming next.")),
    h('button', { class: 'btn outline', type: 'button', html: I.plus + 'Add to Watchlist', onclick: () => addSheet(() => show(cur, true)) })), tabs, body);
  let cur = tab;
  const drawTabs = (k) => {
    tabs.innerHTML = '';
    const list = [...TABS, ...(D?.o.together?.length ? [['shared', 'Shared']] : [])];
    list.forEach(([key, label]) => tabs.append(h('button', { type: 'button', role: 'tab', 'aria-selected': String(key === k), 'aria-controls': 'wl-panel', onclick: () => show(key) }, label,
      key === 'movies' && D?.o.items.length ? h('span', { class: 'sr' }, ` (${D.o.items.length})`) : null)));
  };
  drawTabs(tab);                // tabs first, so the panel does not move down when the data arrives
  async function show(k, reload = false) {
    cur = k;
    history.replaceState(null, '', k === 'overview' ? '#/watchlist' : `#/watchlist?tab=${k}`);
    if (!D || reload) {
      body.setAttribute('aria-busy', 'true');
      try {
        const [o, u, c] = await Promise.all([api('/api/v8/watchlist/overview'), api('/api/v8/watchlist/upcoming').catch(() => ({ items: [] })), CW.load(true).catch(() => [])]);
        D = { o, up: u.items || [], cw: c };
      } catch (e) { body.innerHTML = ''; body.append(h('div', { class: 'notice warn' }, e.message)); return; }
      body.removeAttribute('aria-busy');
    }
    drawTabs(k);
    body.innerHTML = '';
    ({ overview, movies, tv, coming, released, shared })[k](body);
  }
  on('cm:progress', (items) => { if (!D) return; D.cw = items; const box = $('#wl-cw'); if (box) box.replaceWith(cwSection()); }, body);
  on('cm:follow', () => { if (D) show(cur, true); }, body);
  show(tab);
}

// ---------------------------------------------------------------- pieces
const meta = (c) => [c.year, c.runtime ? rt(c.runtime) : null].filter(Boolean);
function availability(c) {
  if (c.on_services?.length) return `Streaming on your ${c.on_services.slice(0, 2).join(', ')}`;
  if (c.streaming_on?.length) return `Streaming on ${c.streaming_on.slice(0, 2).join(', ')}`;
  if (c.status === 'theatres') return 'In theaters now';
  if (c.status === 'coming') return 'Not out yet';
  return 'Out; no US streaming listing on record';
}
function featured(f) {
  const card = h('article', { class: 'feat', 'aria-label': `Up next: ${f.title}` }, h('div', { class: 'bd ph-bd' }));
  const gs = firstGenre(f.genres).slice(0, 2).map(shortGenre);
  const acts = h('div', { class: 'acts' });
  if (f.status === 'theatres' || f.in_theatres) acts.append(h('button', { class: 'btn primary', type: 'button', html: I.ticket + 'Showtimes', onclick: () => showtimesSheet(f) }));
  else acts.append(h('a', { class: 'btn primary', href: `#/film/${f.id}?go=watch`, html: I.play + 'Where to watch' }));
  acts.append(h('a', { class: 'btn secondary', href: `#/film/${f.id}` }, 'Details'));
  const syn = h('p', { class: 'syn' }, f.why ? `Why now: ${f.why}.` : '');
  card.append(h('div', { class: 'in' }, h('span', { class: 'k' }, 'Up next from your watchlist'),
    h('h3', {}, f.title, f.year ? h('span', { class: 'yr' }, ` (${f.year})`) : null),
    h('div', { class: 'mm' }, [gs.join(', '), f.runtime ? rt(f.runtime) : null, f.cert].filter(Boolean).map(x => h('span', {}, x))),
    syn, h('div', { class: 'av' }, availability(f)), acts));
  api(`/api/film/${f.id}`).then(d => {
    if (d.backdrop) { const bd = $('.bd', card); bd.classList.remove('ph-bd'); bd.style.backgroundImage = `url("${d.backdrop}")`; }
    if (d.overview) syn.textContent = (f.why ? `Why now: ${f.why}. ` : '') + d.overview;
  }).catch(() => {});
  return card;
}
function upcomingList(items, max = 6) {
  const box = h('div', {});
  if (!items.length) { box.append(h('p', { class: 'muted small' }, 'Nothing dated yet. Saved films that are not out, and followed shows with a new season, appear here.')); return box; }
  for (const it of items.slice(0, max)) {
    const t = it.film || it.show, tv = it.media === 'tv';
    const when = it.date ? fmtDate(it.date) : 'Date not announced';
    const label = tv ? `Season ${it.season} · ${when}` : `${when}`;
    const bell = h('button', { class: 'iconbtn bell', type: 'button', 'aria-pressed': String(!!it.notify), 'aria-label': `Alerts for ${t.title}: ${it.notify ? 'on' : 'off'}`, html: it.notify ? I.bellOn : I.bellOff });
    bell.addEventListener('click', async () => {
      const want = !it.notify;
      try {
        if (tv) await post('/api/v8/tv/follow', { id: it.id, on: true, notify: want }); else await post('/api/v8/alerts/mute', { id: it.id, muted: !want });
        it.notify = want; bell.innerHTML = want ? I.bellOn : I.bellOff; bell.setAttribute('aria-pressed', String(want)); bell.setAttribute('aria-label', `Alerts for ${t.title}: ${want ? 'on' : 'off'}`);
        toast(want ? `Alerts on for ${t.title}.` : `Alerts off for ${t.title}.`);
      } catch (e) { toast(e.message, null, true); }
    });
    const badgeCls = tv ? 'tvk' : it.kind === 'digital' ? 'stk' : 'y';
    box.append(h('div', { class: 'row-i' },
      h(tv ? 'span' : 'a', { class: 'pp', href: tv ? null : `#/film/${it.id}`, tabindex: '-1', 'aria-hidden': 'true' }, posterImg(t, 'poster', 44)),
      h('div', {}, h(tv ? 'span' : 'a', { class: 't', href: tv ? null : `#/film/${it.id}` }, t.title),
        h('div', { class: 'tagline' }, h('span', { class: `tag ${badgeCls}` }, it.badge), h('span', { class: 'm' }, label, it.date && !it.confirmed ? ' (type not confirmed)' : ''))),
      bell));
  }
  return box;
}
function cwSection() {
  const items = (D.cw || []).filter(x => !x.caught_up);
  const sec = section('Continue Watching', { sub: 'Pick up where you left off. Based on progress you record or import.', id: 'wl-cw' });
  if (items.length) sec.append(continueRow(items));
  else sec.append(empty(null, (D.o.shows || []).length ? 'Tell Cinemack how far you are in a show you follow and it keeps your place here and on your profile.' : 'Follow a show, or import your Netflix history, and the episodes you are part-way through show up here.',
    null, [h('button', { class: 'btn secondary sm', type: 'button', onclick: () => { $$('.pills [role=tab]')[2]?.click(); } }, (D.o.shows || []).length ? 'Track a show' : 'Find a show to follow'),
      h('a', { class: 'btn ghost sm', href: '#/profile?tab=settings&sec=imports' }, 'Import history')], true));
  return sec;
}
function onboarding() {
  return h('div', {}, empty('Start your watchlist', 'Save films from anywhere in Cinemack with the bookmark, follow TV shows, and this becomes your library: what to watch next, what is coming out, and where you left off.', 'popcorn'),
    h('div', { class: 'onboard' },
      h('button', { type: 'button', onclick: () => addSheet(() => location.reload()) }, h('span', { html: I.search }), h('b', {}, 'Search and add'), h('span', {}, 'Find a film or a show by name.')),
      h('a', { href: '#/pick' }, h('span', { html: I.sparkle }), h('b', {}, 'Get picks for tonight'), h('span', {}, 'Three ranked films; save any with one tap.')),
      h('a', { href: '#/calendar' }, h('span', { html: I.cal }), h('b', {}, 'See what is coming out'), h('span', {}, 'Save upcoming releases and follow new seasons.')),
      h('a', { href: '#/profile?tab=settings&sec=imports' }, h('span', { html: I.upload }), h('b', {}, 'Import your history'), h('span', {}, 'Netflix or Letterboxd files, or connect Trakt.'))));
}

// ---------------------------------------------------------------- tabs
function overview(b) {
  const o = D.o;
  if (!o.items.length) {
    b.append(onboarding());
    if ((D.cw || []).length || (o.shows || []).length) b.append(cwSection());
    if (D.up.length) b.append(section('Coming from your watchlist', {}, h('div', { class: 'panel upc' }, upcomingList(D.up, 12))));
    return;
  }
  const byId = Object.fromEntries(o.items.map(c => [c.id, c]));
  const used = new Set();
  const feat = (o.watch_next[0] && byId[o.watch_next[0].id] ? Object.assign({}, byId[o.watch_next[0].id], { why: o.watch_next[0].why }) : null) || o.items[0];
  used.add(feat.id);
  const nextIds = [...o.watch_next.slice(1).map(x => x.id), ...o.items.map(x => x.id)].filter(id => !used.has(id));
  const strip = [...new Set(nextIds)].slice(0, 10).map(id => byId[id]);
  strip.forEach(x => used.add(x.id));
  const top = h('div', { class: 'wl-top' },
    h('div', { style: 'min-width:0' }, h('div', { class: 'section-h' }, h('h2', {}, 'Up Next'), h('span', { class: 'sub' }, o.watch_next.length ? 'From your watchlist, ready to watch now.' : 'The latest film you saved.')),
      h('div', { class: 'upnext' }, featured(feat), strip.length ? h('div', { class: 'next-strip' }, shelf(strip, 'More from your watchlist', (f) => posterCard(f, { capTitle: true, meta: [availability(f).replace('Streaming on your ', 'On ')] }))) : null)),
    panel('Coming From Your Watchlist', I.cal, { cls: 'upc', act: h('button', { class: 'btn ghost sm', type: 'button', onclick: () => $$('.pills [role=tab]')[3]?.click() }, 'See all') }, upcomingList(D.up, 5)));
  b.append(top, cwSection());
  const shelfOf = (title, sub, pred, sort) => {
    let items = o.items.filter(c => !used.has(c.id) && pred(c));
    if (sort) items = items.sort(sort);
    if (!items.length) return;
    items.forEach(c => used.add(c.id));
    b.append(section(title, { sub }, shelf(items, title, (f) => posterCard(f, { meta: meta(f) }))));
  };
  shelfOf('Available on your services', o.has_services ? 'Streaming now on services you have.' : null, (c) => (c.on_services || []).length);
  shelfOf('In theaters now', 'Showtimes are a tap away.', (c) => c.status === 'theatres');
  shelfOf('Streaming elsewhere', 'On services you have not added.', (c) => c.status === 'streaming');
  shelfOf('Coming soon', 'Saved before release. You get an alert when each one is out.', (c) => c.status === 'coming');
  shelfOf('Recently added', null, () => true, (a, z) => (z.added || '').localeCompare(a.added || ''));
  if (!o.has_services) b.append(h('p', { class: 'notice', style: 'margin-top:24px' }, 'Add the streaming services you have and Cinemack shows which saved films you can watch tonight. ', h('a', { href: '#/profile?tab=settings&sec=services' }, 'Add services')));
  const all = section(`All movies (${o.items.length})`, { sub: 'Your complete list.', more: ['#/watchlist?tab=movies', 'Filters and sorting'] }); b.append(all); movieGrid(all, true);
}
function movieGrid(host, compact = false) {
  const o = D.o;
  const st = Object.assign({ f: 'all', sort: 'added' }, store.json('cm.wlf', {}));
  const chips = h('div', { class: 'chips', role: 'group', 'aria-label': 'Show' });
  const sortSel = h('select', { class: 'input', 'aria-label': 'Sort by' }, [['added', 'Recently added'], ['rating', 'Highest rated'], ['title', 'Title A–Z'], ['year', 'Newest first']].map(([v, l]) => h('option', { value: v, selected: st.sort === v }, l)));
  const grid = h('div', { class: 'grid', role: 'list', 'aria-label': 'Films on your watchlist' });
  const FIL = [['all', 'All'], ['now', 'Available now'], ['streaming', 'Streaming'], ['theatres', 'In theaters'], ['coming', 'Unreleased']];
  const count = (k) => o.items.filter(c => test(k, c)).length;
  const test = (k, c) => k === 'all' || (k === 'now' ? ['streaming', 'theatres'].includes(c.status) : c.status === k);
  const draw = () => {
    store.set('cm.wlf', JSON.stringify(st));
    chips.innerHTML = '';
    FIL.forEach(([k, l]) => { const n = count(k); if (k !== 'all' && !n) return; chips.append(h('button', { class: 'chip', type: 'button', 'aria-pressed': String(st.f === k), onclick: () => { st.f = k; draw(); } }, l, h('span', { class: 'n' }, String(n)))); });
    let items = o.items.filter(c => test(st.f, c));
    const by = { added: (a, z) => (z.added || '').localeCompare(a.added || ''), rating: (a, z) => (z.rating || 0) - (a.rating || 0), title: (a, z) => (a.title || '').localeCompare(z.title || ''), year: (a, z) => (z.year || 0) - (a.year || 0) };
    items = items.sort(by[st.sort]);
    grid.innerHTML = '';
    if (!items.length) { grid.append(h('p', { class: 'muted' }, st.f === 'all' ? 'No films saved yet.' : 'Nothing with this filter.')); return; }
    // Overview shows the first rows; the rest render on demand (fewer posters loaded, shorter page on phones).
    const cap = compact && !st.all ? (matchMedia('(max-width:599px)').matches ? 12 : 21) : items.length;
    items.slice(0, cap).forEach(c => { const el = posterCard(c, { meta: [STATUS[c.status], c.added ? `added ${ago(c.added.slice(0, 10))}` : null].filter(Boolean) }); el.setAttribute('role', 'listitem'); grid.append(el); });
    more.hidden = cap >= items.length; more.textContent = `Show all ${items.length}`;
  };
  const more = h('button', { class: 'btn secondary', type: 'button', style: 'margin:18px auto 0;display:flex', onclick: () => { st.all = true; draw(); st.all = false; } });
  if (st.f !== 'all' && !count(st.f)) st.f = 'all';
  sortSel.addEventListener('change', () => { st.sort = sortSel.value; draw(); });
  host.append(h('div', { class: 'wl-filters' }, chips, h('span', { class: 'spacer' }), sortSel), grid, more);
  draw();
}
function movies(b) {
  if (!D.o.items.length) { b.append(onboarding()); return; }
  movieGrid(b);
}
function tv(b) {
  const shows = D.o.shows || [];
  b.append(cwSection());
  const sec = section('Shows you follow', { sub: 'Following is separate from saving a film: it tracks new seasons and your progress.' });
  const q = h('input', { class: 'input', id: 'tv-q', type: 'search', placeholder: 'Find a show to follow', 'aria-label': 'Find a show to follow', autocomplete: 'off', style: 'max-width:440px' });
  const res = h('div', {});
  let t = 0;
  q.addEventListener('input', () => { clearTimeout(t); t = setTimeout(async () => {
    res.innerHTML = ''; if (q.value.trim().length < 2) return;
    try { const j = await api('/api/v8/tv/search?q=' + encodeURIComponent(q.value.trim()));
      if (!j.shows.length) { res.append(h('p', { class: 'muted small', style: 'margin-top:8px' }, 'No show by that name in the database.')); return; }
      j.shows.forEach(sh => res.append(h('div', { class: 'row-i' }, h('span', { class: 'pp' }, posterImg(sh, 'poster', 48)), h('div', {}, h('span', { class: 't' }, `${sh.title}${sh.year ? ` (${sh.year})` : ''}`)),
        h('button', { class: `btn ${sh.following ? 'secondary' : 'primary'} sm`, type: 'button', disabled: sh.following, onclick: async () => { try { await post('/api/v8/tv/follow', { id: sh.id, on: true }); toast(`Following ${sh.title}.`); emit('cm:follow', { id: sh.id, on: true }); } catch (e) { toast(e.message, null, true); } } }, sh.following ? 'Following' : 'Follow'))));
    } catch (e) { res.append(h('p', { class: 'err' }, e.message)); } }, 250); });
  sec.append(q, res);
  if (!shows.length) sec.append(empty(null, 'Follow a show and its next season appears here as soon as it has a date. You can also record which episode you are on.', 'tv', [], true));
  const box = h('div', { class: 'panel', style: 'margin-top:14px;padding-top:4px;padding-bottom:4px' });
  for (const sh of shows) {
    const cw = (D.cw || []).find(x => x.id === sh.id);
    const line = [sh.next_season ? `Season ${sh.next_season.number} starts ${fmtDate(sh.next_season.date)}` : sh.latest_season ? `Season ${sh.latest_season.number} came out ${fmtDate(sh.latest_season.date)}; next season not dated` : 'No season dates on record', sh.network].filter(Boolean).join(' · ');
    const notify = h('input', { type: 'checkbox', checked: sh.notify });
    notify.addEventListener('change', async () => { try { await post('/api/v8/tv/follow', { id: sh.id, on: true, notify: notify.checked }); toast(notify.checked ? `Season alerts on for ${sh.title}.` : `Season alerts off for ${sh.title}.`); } catch (e) { toast(e.message, null, true); notify.checked = !notify.checked; } });
    box.append(h('div', { class: 'tv-row' }, h('div', { class: 'pp' }, posterImg(sh, 'poster', 56)),
      h('div', { style: 'min-width:0' }, h('div', { class: 't' }, sh.title, sh.badge ? h('span', { class: 'tag tvk', style: 'margin-left:8px' }, sh.badge) : null), h('div', { class: 'm' }, line),
        h('div', { class: 'm' }, cw ? `Your progress: ${cw.last.label}` : sh.episodes_watched ? `${sh.episodes_watched} episode${sh.episodes_watched === 1 ? '' : 's'} in your imported history` : 'No progress recorded'),
        h('div', { class: 'acts' }, h('label', { class: 'switch small' }, notify, 'New-season alerts'),
          h('button', { class: 'btn secondary sm', type: 'button', onclick: () => progressSheet(cw || { id: sh.id, title: sh.title, seasons_out: sh.seasons_out, last: null }) }, cw ? 'Update progress' : 'Track progress'),
          h('button', { class: 'btn ghost sm', type: 'button', onclick: async () => { try { await post('/api/v8/tv/follow', { id: sh.id, on: false }); toast(`Stopped following ${sh.title}. Your progress is kept.`); emit('cm:follow', { id: sh.id, on: false }); } catch (e) { toast(e.message, null, true); } } }, 'Unfollow')))));
  }
  if (shows.length) sec.append(box);
  b.append(sec);
}
function coming(b) {
  b.append(h('div', { class: 'panel upc' }, h('div', { class: 'panel-h' }, h('span', { class: 'ico', html: I.cal }), h('h2', {}, 'Coming from your watchlist'),
    h('span', { class: 'act' }, h('a', { class: 'btn ghost sm', href: '#/calendar?f=mine' }, 'Open in calendar')),
    h('span', { class: 'sub' }, 'US dates from TMDB. The bell turns alerts on or off for each title; alerts appear in Cinemack only.')), upcomingList(D.up, 200)));
  const notYet = D.o.items.filter(c => c.status === 'coming');
  if (notYet.length) b.append(section('Saved before release', {}, shelf(notYet, 'Saved before release', (f) => posterCard(f, { meta: meta(f) }))));
}
function released(b) {
  const o = D.o;
  if (o.just_landed.length) b.append(section('Just landed', { sub: 'Films on your list that started streaming or came out in the last 30 days.' },
    h('div', { class: 'panel', style: 'padding-top:4px;padding-bottom:4px' }, o.just_landed.map(x => h('div', { class: 'row-i' }, h('a', { class: 'pp', href: `#/film/${x.film.id}`, tabindex: '-1', 'aria-hidden': 'true' }, posterImg(x.film, 'poster', 48)),
      h('div', {}, h('a', { class: 't', href: `#/film/${x.film.id}` }, x.film.title), h('div', { class: 'm' }, x.text)), h('span', { class: 'r' }, x.at))))));
  if (o.last_chance.length) b.append(section('Last chance in theaters', { sub: 'Opened a while ago; most films leave theaters 6 to 10 weeks in. This is an estimate.' },
    shelf(o.last_chance.map(x => x.film), 'Last chance in theaters', (f) => posterCard(f, { meta: [`Opened ${o.last_chance.find(x => x.film.id === f.id)?.weeks} weeks ago`] }))));
  const out = o.items.filter(c => c.status !== 'coming');
  const sec = section(`Released (${out.length})`, { sub: 'Out now: streaming, in theaters, or released without a streaming listing.' });
  if (!out.length) sec.append(empty(null, 'Nothing on your list is out yet.', null, [], true));
  else { const g = h('div', { class: 'grid' }); out.forEach(c => g.append(posterCard(c, { meta: [STATUS[c.status]] }))); sec.append(g); }
  b.append(sec);
}
function shared(b) {
  for (const t of D.o.together || []) {
    const taste = t.taste.pct != null ? `You agree on ${t.taste.agree} of ${t.taste.films} films you have both rated (${t.taste.pct}% match).` : t.shares_ratings ? 'Not enough films rated by both of you yet.' : '';
    const sec = section(t.name, { sub: taste });
    if (t.both.length) sec.append(h('p', { style: 'margin:4px 0 10px' }, h('b', {}, `You both want to watch ${t.both.length === 1 ? 'this' : 'these'}`)), shelf(t.both, `You and ${t.name} both want`, (f) => posterCard(f)));
    if (t.loved.length) sec.append(h('p', { style: 'margin:14px 0 10px' }, h('b', {}, `${t.name} loved ${t.loved.length === 1 ? 'one' : 'these'} from your list`)), shelf(t.loved, `${t.name} loved`, (f) => posterCard(f)));
    b.append(sec);
  }
  b.append(h('p', { class: 'hint', style: 'margin-top:20px' }, 'Shown only for friends who chose to share their watchlist or ratings with you. ', h('a', { href: '#/profile?tab=friends' }, 'Friends')));
}

// ---------------------------------------------------------------- add sheet (search films and shows)
function addSheet(after) {
  let changed = false;
  sheet('Add to Watchlist', (b) => {
    const q = h('input', { class: 'input', type: 'search', placeholder: 'Search movies and shows', 'aria-label': 'Search movies and shows', autocomplete: 'off' });
    const res = h('div', { style: 'margin-top:12px' }, h('p', { class: 'muted small' }, 'Type at least two letters.'));
    let t = 0, seq = 0;
    q.addEventListener('input', () => { clearTimeout(t); t = setTimeout(async () => {
      const s = q.value.trim(); if (s.length < 2) return; const my = ++seq;
      try { const r = await api('/api/v8/search?q=' + encodeURIComponent(s)); if (my !== seq) return; res.innerHTML = '';
        if (!r.films.length && !r.shows.length) { res.append(h('p', { class: 'muted' }, `Nothing called "${s}" in the database.`)); return; }
        r.films.forEach(f => res.append(h('div', { class: 'row-i' }, h('span', { class: 'pp' }, posterImg(f, 'poster', 48)), h('div', {}, h('span', { class: 't' }, f.title), h('div', { class: 'm' }, [f.year, 'Film'].filter(Boolean).join(' · '))),
          (() => { const w = wantButton(f, 'text'); w.addEventListener('click', () => { changed = true; }); return w; })())));
        r.shows.forEach(sh => res.append(h('div', { class: 'row-i' }, h('span', { class: 'pp' }, posterImg(sh, 'poster', 48)), h('div', {}, h('span', { class: 't' }, sh.title), h('div', { class: 'm' }, [sh.year, 'TV series'].filter(Boolean).join(' · '))),
          h('button', { class: `btn ${sh.following ? 'secondary' : 'outline'} sm`, type: 'button', disabled: sh.following, onclick: async (e) => { try { await post('/api/v8/tv/follow', { id: sh.id, on: true }); changed = true; e.currentTarget.textContent = 'Following'; e.currentTarget.disabled = true; toast(`Following ${sh.title}.`); } catch (er) { toast(er.message, null, true); } } }, sh.following ? 'Following' : 'Follow'))));
      } catch (e) { res.innerHTML = ''; res.append(h('p', { class: 'err' }, e.message)); } }, 200); });
    b.append(q, res); setTimeout(() => q.focus(), 60);
  }, { onclose: () => { if (changed) after?.(); } });
}
