// Home (10 Nov 2026 redesign). Hero carousel (unfiltered new films in theatres), Explore by Genre, then shelves that
// each answer one question with its own ranking rule (server: discovery.home):
//   In Theaters Now (TMDB now-playing order) · Recommended for You (MovieLens, only with real likes) · Top Rated
//   (weighted rating) · Trending Now (popularity, last 120 days, not repeating the theatre row) · Opening Soon (by date)
//   · New Seasons and Top Rated Shows (TV, 10 Nov (round 18)) · Your List (saved films) · Back on the Big Screen (re-releases).
// Short films (40 minutes or less) are left out of the film rows on the server.
// Genre chips filter every shelf (Match Any by default, Match All optional); every genre selected = no filter. The page
// never re-sorts what the server ranked. Only the hero moves on its own.
import { $, $$, S, h, I, api, view, F, GENRES, setGenres, shortGenre, firstGenre, section, shelf, posterCard, empty, rt, fmtDate,
  showtimesSheet, CW, continueRow, wantButton, posterImg, ratingEl, on, tvCard, authSheet, displayName } from './core.js';

const PRIMARY = 9;          // chips shown before "More"
const SHELF = {
  now_playing: ['In Theaters Now', "What's playing in theaters", '#/calendar?f=theaters', 'Calendar'],
  top_rated: ['Top Rated', 'The best-rated films, weighted so a handful of votes cannot win'],
  trending: ['Trending Now', 'Most popular new releases of the last four months'],
  new_seasons: ['New Seasons', 'TV seasons that premiered in the last 30 days, most popular first', '#/calendar?f=tv', 'Calendar'],
  top_shows: ['Top Rated Shows', 'The best-rated TV series, weighted so a handful of votes cannot win'],
  coming_soon: ['Opening Soon', 'Upcoming releases, earliest first', '#/calendar', 'Calendar'],
  watchlist: ['Your List', 'Films you saved', '#/watchlist', 'Open watchlist'],
  back_in_theatres: ['Back on the Big Screen', 'Classics and anniversaries in theaters again'],
};
const KIND = { theatrical: 'In theaters', digital: 'Streaming', us_release: 'US release' };
const joinG = (gs, mode) => { const n = gs.map(shortGenre); return n.length <= 2 ? n.join(mode === 'all' ? ' and ' : ' or ') : `${n.slice(0, -1).join(', ')} ${mode === 'all' ? 'and' : 'or'} ${n[n.length - 1]}`; };

export async function pageHome() {
  if (!S.me) { try { S.me = await api('/api/me'); } catch {} }      // round 21: decides the account card below, before first paint
  view.innerHTML = '';
  view.classList.add('home');
  const hero = h('section', { class: 'hh', 'aria-label': 'Featured' }, h('div', { class: 'hh-bd', 'aria-hidden': 'true' }), h('div', { class: 'hh-bd hh-bd2', 'aria-hidden': 'true' }));
  const q = h('input', { id: 'home-q', type: 'search', placeholder: 'Describe a film, a scene, or a mood', 'aria-label': 'Ask Cinemack', enterkeyhint: 'send' });
  const go = () => { const t = q.value.trim(); if (t) { S.pendingAsk = t; location.hash = '#/ask'; } };
  q.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); go(); } });
  const feat = h('div', { class: 'hh-feat', 'aria-live': 'off' });
  hero.append(h('div', { class: 'hh-in' },
    h('div', { class: 'hh-ask' }, h('h1', {}, 'What are we watching tonight?'),
      h('div', { class: 'hh-row' }, h('div', { class: 'askbox' }, h('span', { html: I.sparkle, class: 'ab-ic' }), q, h('button', { class: 'btn primary', type: 'button', onclick: go, 'aria-label': 'Ask Cinemack', html: I.send })),
        h('a', { class: 'btn outline hh-pick', href: '#/pick', html: I.sparkle + '<span>Pick Tonight</span>' }))),
    feat));
  view.append(hero);
  const ac = accountCard();
  if (ac) view.append(ac);

  // ---------------------------------------------------------------- Explore by Genre
  const f = S.filters;
  if (!['any', 'all'].includes(f.genre_mode)) f.genre_mode = 'any';
  let more = (f.genres || []).some(g => GENRES.indexOf(g) >= PRIMARY);
  const chips = h('div', { class: 'gx-chips', role: 'group', 'aria-label': 'Genres' });
  const seg = h('div', { class: 'seg sm', role: 'group', 'aria-label': 'Match' });
  const clear = h('button', { class: 'btn ghost sm', type: 'button', html: I.x + '<span>Clear all</span>', onclick: () => { f.genres = []; F.save(); sync(); } });
  const summary = h('p', { class: 'gx-sum', 'aria-live': 'polite' });
  const panelEl = h('section', { class: 'gx', 'aria-label': 'Explore by genre' },
    h('div', { class: 'gx-h' }, h('span', { class: 'gx-ic', html: I.sliders }), h('div', {}, h('h2', {}, 'Explore by Genre'), h('p', {}, 'Filter every row on this page. Ask Cinemack uses the same genres.'))),
    chips, h('div', { class: 'gx-ctl' }, h('span', { class: 'gx-lab', id: 'gx-match' }, 'Match'), seg, clear), summary);
  view.append(panelEl);
  function drawChips() {
    const sel = new Set(f.genres || []);
    chips.innerHTML = '';
    const list = more ? GENRES : GENRES.filter((g, i) => i < PRIMARY || sel.has(g));
    list.forEach(g => chips.append(h('button', { class: 'chip', type: 'button', 'aria-pressed': String(sel.has(g)), 'data-g': g,
      onclick: () => { F.toggleGenre(g); sync(); } }, sel.has(g) ? h('span', { html: I.check, class: 'ck' }) : null, shortGenre(g))));
    const hidden = GENRES.length - list.length;
    chips.append(h('button', { class: 'chip add', type: 'button', 'aria-expanded': String(more), onclick: () => { more = !more; drawChips(); chips.querySelector(more ? `[data-g="${GENRES[PRIMARY]}"]` : '.chip.add')?.focus(); } },
      more ? 'Fewer' : `More (${hidden})`, h('span', { html: I.chev, class: more ? 'cv up' : 'cv' })));
    seg.innerHTML = '';
    const n = sel.size, all = F.allGenres();
    [['any', 'Any'], ['all', 'All']].forEach(([k, l]) => seg.append(h('button', { type: 'button', 'aria-pressed': String(f.genre_mode === k), disabled: n < 2 || all,
      'aria-describedby': 'gx-match', title: k === 'any' ? 'Films in at least one selected genre' : 'Films in every selected genre', onclick: () => { f.genre_mode = k; F.save(); sync(); } }, l)));
    clear.disabled = !n;
    summary.textContent = !n ? 'No genre filter: every row shows its normal ranking.'
      : all ? 'Every genre selected, which is the same as no filter.'
      : `Showing films that are ${joinG(GENRES.filter(g => sel.has(g)), n > 1 ? f.genre_mode : 'any')}. Each row keeps its own order.`;
  }
  const holder = h('div', { id: 'home-rails' }, ['now_playing', 'top_rated', 'trending'].map(k => skelSection(SHELF[k][0])));
  view.append(holder);

  // ---------------------------------------------------------------- data (debounced; only the newest answer is drawn)
  let seq = 0, ctrl = null, t = 0, first = true;
  const cw = CW.load(true).catch(() => []);
  function sync() { drawChips(); clearTimeout(t); holder.setAttribute('aria-busy', 'true'); holder.classList.add('busy'); t = setTimeout(load, first ? 0 : 180); }
  async function load() {
    const my = ++seq;
    ctrl?.abort(); ctrl = new AbortController();
    const gs = F.allGenres() ? [] : (f.genres || []);
    const qs = gs.length ? `?genres=${encodeURIComponent(gs.join(','))}&mode=${f.genre_mode}` : '';
    let data;
    try { data = await api('/api/v8/home' + qs, { signal: ctrl.signal }); }
    catch (e) { if (e.name === 'AbortError' || my !== seq) return; holder.classList.remove('busy'); holder.innerHTML = ''; holder.append(h('div', { class: 'notice warn' }, e.message)); return; }
    if (my !== seq) return;
    const items = await cw;
    if (my !== seq) return;
    if (data.genre_options && data.genre_options.join() !== GENRES.join()) { setGenres(data.genre_options); drawChips(); }
    if (first) { first = false; heroCarousel(hero, feat, (data.hero || []).slice(0, 6)); }
    drawRails(holder, data, items.filter(x => !x.caught_up).slice(0, 6));
    holder.removeAttribute('aria-busy'); holder.classList.remove('busy');
  }
  on('cm:filters', () => drawChips(), panelEl);
  sync();
  followRow(view);
}

// v8.1 round 50: "People you follow" (FOLLOW_PEOPLE). The server answers 404 while the switch is off: then nothing is
// drawn. Each person: the next dated films, the latest one, and stored Guardian pieces that name them in full.
async function followRow(view) {
  let r;
  try { r = await api('/api/v8/follows'); } catch { return; }
  const people = (r.people || []).filter(p => p.coming?.length || p.latest?.length || p.articles?.length);
  if (!people.length || !view.isConnected) return;
  const film = (x, when) => h('li', {}, h('a', { href: `#/film/${x.id}` }, x.title), h('span', { class: 'small muted' }, ` \u00b7 ${when} \u00b7 ${x.role}`));
  const card = (p) => h('article', { class: 'ff' }, h('h3', {}, p.name),
    p.coming?.length ? h('div', {}, h('div', { class: 'ff-k' }, 'Coming'), h('ul', {}, p.coming.slice(0, 3).map(x => film(x, fmtDate(x.date))))) : null,
    p.latest?.length ? h('div', {}, h('div', { class: 'ff-k' }, 'Latest'), h('ul', {}, p.latest.slice(0, 2).map(x => film(x, x.date.slice(0, 4))))) : null,
    p.articles?.length ? h('div', {}, h('div', { class: 'ff-k' }, 'In the Guardian'), h('ul', {}, p.articles.slice(0, 2)
      .filter(a => /^https:\/\/www\.theguardian\.com\//.test(a.url || ''))
      .map(a => h('li', {}, h('a', { href: a.url, target: '_blank', rel: 'noopener' }, a.headline || 'Article'), h('span', { class: 'small muted' }, ` \u00b7 ${a.published}`))))) : null);
  const sec = section('People you follow', { sub: `${people.length} ${people.length === 1 ? 'person' : 'people'} \u00b7 from stored release dates and Guardian articles` },
    h('div', { class: 'follow-feed' }, people.map(card)));
  const rails = view.querySelector('#home-rails');
  if (rails) rails.after(sec); else view.append(sec);
}

function skelSection(title) {
  return h('section', { class: 'section', 'aria-hidden': 'true' }, h('div', { class: 'section-h' }, h('h2', {}, title)),
    h('div', { class: 'shelf' }, h('div', { class: 'track' }, Array.from({ length: 7 }, () => h('div', { class: 'skel', style: 'aspect-ratio:2/3' })))));
}

// ---------------------------------------------------------------- shelves
function drawRails(holder, d, cwItems = []) {
  const gs = d.genres || [], mode = d.mode, filtered = gs.length > 0;
  const label = filtered ? joinG(gs, mode) : '';
  const by = Object.fromEntries(d.rails.map(r => [r.key, r]));
  const out = [];
  const emptyNote = (what) => h('div', { class: 'rail-empty' }, h('span', {}, `No ${what} match ${label}.`),
    mode === 'all' && gs.length > 1 ? h('button', { class: 'btn ghost sm', type: 'button', onclick: () => { S.filters.genre_mode = 'any'; F.save(); $('.gx .seg button')?.click(); } }, 'Match any instead') : null,
    h('button', { class: 'btn ghost sm', type: 'button', onclick: () => $('.gx .btn.ghost')?.click() }, 'Clear filters'));
  const sub = (r, base) => filtered ? `${base} · ${label} · ${r.total} match${r.total === 1 ? '' : 'es'}` : base;
  const add = (key, mk, what) => {
    const r = by[key]; if (!r) return;
    const [title, base, link, linkLabel] = SHELF[key];
    if (!r.items.length) {
      if (r.media === 'tv' && !r.as_of) return;        // no TV data fetched on this server yet: no row, no false "none match"
      if (filtered && key !== 'back_in_theatres') out.push(section(title, { sub: base }, emptyNote(what)));
      return;
    }
    out.push(section(title, { sub: sub(r, base), more: link ? [link, linkLabel] : null }, shelf(r.items, title, mk)));
  };
  add('now_playing', (x) => posterCard(x, { meta: [x.year, x.runtime ? rt(x.runtime) : null].filter(Boolean),
    extra: h('button', { class: 'btn ghost sm pc-act', type: 'button', html: I.ticket + 'Showtimes', 'aria-label': `Showtimes: ${x.title}`, onclick: () => showtimesSheet(x) }) }), 'films in theaters');
  // Recommended for You: only from real likes; guests and new people get an honest prompt instead
  const recs = d.rails.filter(r => r.key.startsWith('because_'));
  if (recs.length) {
    const sec = section('Recommended for You', { sub: 'People who rated your favourites highly also loved these (MovieLens)' });
    recs.forEach(r => sec.append(h('h3', { class: 'rec-h' }, `Because you liked ${r.seed || 'a film you liked'}`), shelf(r.items, r.title, (x) => posterCard(x))));
    out.push(sec);
  } else if (!filtered) out.push(section('Recommended for You', {}, h('div', { class: 'rail-prompt' }, h('span', { html: I.heart, class: 'rp-ic' }),
    h('div', {}, h('b', {}, 'Recommendations start with your taste.'), h('p', {}, 'Give a thumbs-up to a few films you loved, or import your Letterboxd history, and Cinemack finds films that the same fans rated highly. Nothing here is guessed before then.')),
    h('div', { class: 'rp-acts' }, h('a', { class: 'btn secondary sm', href: '#/pick' }, 'Try Pick Tonight'), h('a', { class: 'btn ghost sm', href: '#/profile?tab=settings&sec=imports' }, 'Import history')))));
  add('top_rated', (x) => posterCard(x, { meta: [x.year] }), 'top-rated films');           // the TMDB rating it is ranked by
  add('trending', (x) => posterCard(x), 'trending films');
  add('new_seasons', tvCard, 'shows with a new season');
  add('top_shows', tvCard, 'top-rated shows');
  add('coming_soon', (x) => posterCard(x, { noRating: true, meta: x.release ? [h('span', { class: `kd ${x.release.kind === 'digital' ? 'st' : x.release.kind === 'theatrical' ? 'th' : 'un'}` }, KIND[x.release.kind]), fmtDate(x.release.date, { month: 'short', day: 'numeric' })] : [] }), 'upcoming films');
  // Your List: the saved films; one or two get a compact row instead of a near-empty carousel
  const wl = by.watchlist;
  if (wl) {
    const [title, base, link, linkLabel] = SHELF.watchlist;
    const s2 = filtered ? `${wl.total} of your ${wl.saved} saved film${wl.saved === 1 ? '' : 's'} match ${label}` : `${wl.saved} saved`;
    if (wl.items.length >= 3) out.push(section(title, { sub: s2, more: [link, linkLabel] }, shelf(wl.items, title, (x) => posterCard(x))));
    else if (wl.items.length) out.push(section(title, { sub: s2, more: [link, linkLabel] }, h('div', { class: 'mini-list' }, wl.items.map(miniCard))));
    else if (!filtered) out.push(section(title, {}, h('div', { class: 'rail-prompt' }, h('span', { html: I.bookmark, class: 'rp-ic' }),
      h('div', {}, h('b', {}, 'Nothing saved yet.'), h('p', {}, 'Tap the bookmark on any poster and it waits for you here and on your Watchlist.')))));
    else if (wl.saved) out.push(section(title, { sub: base }, h('div', { class: 'rail-empty' }, h('span', {}, `None of your ${wl.saved} saved films match ${label}.`), h('a', { class: 'btn ghost sm', href: '#/watchlist' }, 'See all saved'))));
  }
  add('back_in_theatres', (x) => posterCard(x, { meta: [x.year] }), 're-releases');
  const any = d.rails.some(r => r.items.length);
  holder.innerHTML = '';
  if (cwItems.length) holder.append(section('Continue Watching', { sub: 'From the progress you record or import', more: ['#/watchlist?tab=tv', 'All shows'] }, continueRow(cwItems)));
  if (filtered && !any) {
    holder.append(empty('Nothing matches these genres', `Nothing in any row is ${label}. Clear the filter${mode === 'all' ? ', or match any of these genres instead of all of them' : ''}. Cinemack never fills a row with titles outside your filter.`, 'reel',
      [h('button', { class: 'btn primary', type: 'button', onclick: () => $('.gx .btn.ghost')?.click() }, 'Clear filters'),
        mode === 'all' ? h('button', { class: 'btn secondary', type: 'button', onclick: () => $('.gx .seg button')?.click() }, 'Match any') : null].filter(Boolean)));
    return;
  }
  holder.append(...out);
}
// round 21: signed out -> invite to create an account or sign in; signed in but not set up -> finish setting up.
// "Not now" hides it for this browser session only.
function accountCard() {
  let hidden = false; try { hidden = sessionStorage.getItem('cm.acct') === 'hide'; } catch {}
  if (hidden || !S.me) return null;
  const hide = (el) => { try { sessionStorage.setItem('cm.acct', 'hide'); } catch {} el.remove(); };
  if (!S.me.account) {
    const card = h('section', { class: 'acct', 'aria-label': 'Create an account' }, h('span', { class: 'acct-ic', html: I.user }),
      h('div', { class: 'acct-tx' }, h('h2', {}, 'Make Cinemack yours'),
        h('p', {}, 'Create a free account to keep your watchlist, ratings, chats and TV progress on every device, get picks from your own taste, and add friends. What you did here as a guest comes with you.')),
      h('div', { class: 'acct-a' }, h('a', { class: 'btn primary', href: '#/join' }, 'Create account'), h('button', { class: 'btn secondary', type: 'button', onclick: () => authSheet('in') }, 'Sign in'),
        h('button', { class: 'btn ghost sm', type: 'button', onclick: () => hide(card) }, 'Not now')));
    return card;
  }
  if (!S.me.onboarded) {
    const card = h('section', { class: 'acct', 'aria-label': 'Finish setting up' }, h('span', { class: 'acct-ic', html: I.sliders }),
      h('div', { class: 'acct-tx' }, h('h2', {}, `Finish setting up${displayName(S.me) ? `, ${displayName(S.me)}` : ''}`),
        h('p', {}, 'Tell Cinemack what you love, which services you have and any limits. About two minutes; every step can be skipped.')),
      h('div', { class: 'acct-a' }, h('a', { class: 'btn primary', href: '#/join?step=taste' }, 'Continue set-up'), h('button', { class: 'btn ghost sm', type: 'button', onclick: () => hide(card) }, 'Not now')));
    return card;
  }
  return null;
}
function miniCard(x) {
  return h('div', { class: 'mini' }, h('a', { class: 'pp', href: `#/film/${x.id}`, tabindex: '-1', 'aria-hidden': 'true' }, posterImg(x, 'poster', 64)),
    h('div', { class: 'in' }, h('a', { class: 'tt', href: `#/film/${x.id}` }, x.title), h('span', { class: 'mm' }, [x.year, x.runtime ? rt(x.runtime) : null].filter(Boolean).join(' · ')),
      x.on_services?.length ? h('span', { class: 'on' }, 'On your ' + x.on_services.join(', ')) : null),
    wantButton(x));
}

// ---------------------------------------------------------------- hero carousel
// Moves every 5 s; arrows, dots, swipe, arrow keys and a pause button. Pauses on hover, focus, touch, open dialogs and
// hidden tabs, and never moves on its own with reduced motion. Backdrops cross-fade between two layers.
function heroCarousel(hero, feat, films) {
  clearInterval(heroCarousel.t);
  if (!films.length) { feat.append(h('p', { class: 'muted' }, 'No in-theater list loaded yet.')); return; }
  const reduce = matchMedia('(prefers-reduced-motion: reduce)').matches;
  const [bd1, bd2] = $$('.hh-bd', hero);
  let cur = 0, paused = false, stopped = reduce, layer = 0;
  const info = h('div', { class: 'hh-info', role: 'group', 'aria-roledescription': 'slide' });
  const prev = h('button', { class: 'hh-arrow prev', type: 'button', 'aria-label': 'Previous featured film', html: I.left });
  const next = h('button', { class: 'hh-arrow next', type: 'button', 'aria-label': 'Next featured film', html: I.right });
  const dots = h('div', { class: 'hh-dots', role: 'group', 'aria-label': 'Choose a featured film' });
  const pause = h('button', { class: 'hh-pause', type: 'button', 'aria-label': stopped ? 'Play slideshow' : 'Pause slideshow', html: stopped ? I.play : '<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><rect x="6" y="5" width="4" height="14" rx="1"/><rect x="14" y="5" width="4" height="14" rx="1"/></svg>' });
  const count = h('span', { class: 'hh-count', 'aria-hidden': 'true' });
  const sPrev = h('button', { class: 'hh-step', type: 'button', 'aria-label': 'Previous featured film', html: I.left, onclick: () => show(cur - 1, true) });
  const sNext = h('button', { class: 'hh-step', type: 'button', 'aria-label': 'Next featured film', html: I.right, onclick: () => show(cur + 1, true) });
  feat.append(info, h('div', { class: 'hh-nav' }, films.length > 1 ? [sPrev, count, sNext] : null, dots, films.length > 1 ? pause : null));
  if (films.length > 1) hero.append(prev, next);
  films.forEach((x, j) => dots.append(h('button', { type: 'button', 'aria-label': `${j + 1} of ${films.length}: ${x.title}`, onclick: () => show(j, true) })));
  films.forEach(x => { if (x.backdrop) { const im = new Image(); im.src = x.backdrop; } });     // warm the cache: no blank fade
  function show(i, user = false) {
    cur = (i + films.length) % films.length;
    const x = films[cur];
    const gs = firstGenre(x.genres).slice(0, 2).map(shortGenre);
    info.setAttribute('aria-label', `${cur + 1} of ${films.length}: ${x.title}`);
    info.innerHTML = '';
    info.append(h('span', { class: 'hh-k' }, h('span', { class: 'tag y' }, 'Featured'), x.in_theatres ? ' In theaters now' : ''),
      h('h2', { class: 'hh-t' }, h('a', { href: `#/film/${x.id}` }, x.title)),
      h('div', { class: 'hh-m' }, [x.year, x.cert, gs.join(' · ') || null, x.runtime ? rt(x.runtime) : null].filter(Boolean).map(v => h('span', {}, String(v))), ratingEl(x)),
      x.overview ? h('p', { class: 'hh-o' }, x.overview) : null,
      h('div', { class: 'hh-acts' }, h('a', { class: 'btn primary', href: `#/film/${x.id}`, html: I.play + 'Details' }), wantButton(x, 'text'),
        x.in_theatres ? h('button', { class: 'btn ghost', type: 'button', html: I.ticket + 'Showtimes', onclick: () => showtimesSheet(x) }) : null));
    [...dots.children].forEach((d, j) => d.setAttribute('aria-current', String(j === cur)));
    count.textContent = `${cur + 1} / ${films.length}`;
    const on = layer ? bd2 : bd1, off = layer ? bd1 : bd2; layer = 1 - layer;
    on.style.backgroundImage = x.backdrop ? `url("${x.backdrop}")` : '';
    on.classList.toggle('none', !x.backdrop); on.classList.add('on'); off.classList.remove('on');
    if (user) restart();
  }
  prev.addEventListener('click', () => show(cur - 1, true)); next.addEventListener('click', () => show(cur + 1, true));
  hero.addEventListener('keydown', (e) => { if (e.target.closest('input')) return; if (e.key === 'ArrowLeft') { e.preventDefault(); show(cur - 1, true); } else if (e.key === 'ArrowRight') { e.preventDefault(); show(cur + 1, true); } });
  pause.addEventListener('click', () => { stopped = !stopped; pause.innerHTML = stopped ? I.play : '<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><rect x="6" y="5" width="4" height="14" rx="1"/><rect x="14" y="5" width="4" height="14" rx="1"/></svg>';
    pause.setAttribute('aria-label', stopped ? 'Play slideshow' : 'Pause slideshow'); restart(); });
  let x0 = null, y0 = null;
  hero.addEventListener('touchstart', (e) => { x0 = e.touches[0].clientX; y0 = e.touches[0].clientY; paused = true; }, { passive: true });
  hero.addEventListener('touchend', (e) => { if (x0 != null) { const dx = e.changedTouches[0].clientX - x0, dy = e.changedTouches[0].clientY - y0; if (Math.abs(dx) > 50 && Math.abs(dx) > Math.abs(dy)) show(cur + (dx < 0 ? 1 : -1), true); } x0 = null; paused = false; }, { passive: true });
  hero.addEventListener('mouseenter', () => { paused = true; }); hero.addEventListener('mouseleave', () => { paused = false; });
  hero.addEventListener('focusin', () => { paused = true; }); hero.addEventListener('focusout', () => { paused = false; });
  function restart() {
    clearInterval(heroCarousel.t);
    if (stopped || films.length < 2) return;
    heroCarousel.t = setInterval(() => {
      if (!document.body.contains(hero)) { clearInterval(heroCarousel.t); return; }
      if (!paused && document.visibilityState === 'visible' && !document.querySelector('dialog[open]')) show(cur + 1);
    }, 5000);
  }
  show(0); restart();
}
