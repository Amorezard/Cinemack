// Release Calendar (10 Nov 2026 redesign): Week and Month views that share one state (selected date, type filter,
// streaming service), kept in the URL so switching views, reloading or sharing keeps it.
// 10 Nov, round 18: the week columns list EVERY release of the day (no "+N more"): a group of one or two films gets wide backdrop
// tiles, a busier group or day gets compact poster rows, TV is always rows; a busy week is simply taller. Month cells
// are larger with up to three posters, and the day panel is a list of rows like the approved reference.
// Dates are US release dates from TMDB; types are kept apart (theatrical, re-release, digital, unconfirmed type,
// TV season premiere). Nothing is estimated: unknown services say so, and showtimes are web searches.
import { $, $$, S, h, I, api, post, view, store, toast, iso, dayOf, fmtDate, rt, wantButton, showtimesSheet, showSheet, emit, empty, firstGenre, shortGenre, setChoice, isWant, posterImg } from './core.js';

const KIND = { theatrical: ['In theaters', 'th'], theatrical_rerelease: ['Back in theaters', 're'], theatrical_or_unknown: ['US release, type not confirmed', 'un'],
  digital: ['Streaming', 'st'], tv_season: ['TV season premiere', 'tv'], tv_episode: ['New episode', 'tv'] };
const GROUPS = [['theaters', 'In Theaters', 'th', (e) => e.kind.startsWith('theatrical'), I.ticket], ['streaming', 'Streaming', 'st', (e) => e.kind === 'digital', I.play], ['tv', 'TV', 'tv', (e) => e.kind === 'tv_season' || e.kind === 'tv_episode', I.tv]];
const FILTER = [['all', 'All Releases', null], ['theaters', 'In Theaters', I.ticket], ['streaming', 'Streaming', I.play], ['tv', 'TV', I.tv], ['mine', 'My Watchlist', I.bookmark]];
const cache = new Map();
document.addEventListener('cm:choice', () => cache.clear());     // watchlist flags on events change when a film is saved

export async function pageCalendar(params) {
  view.innerHTML = '';
  const today = iso(new Date());
  const st = {
    view: ['week', 'month'].includes(params.get('view')) ? params.get('view') : (store.get('cm.calview') || (matchMedia('(min-width:1024px)').matches ? 'week' : 'week')),
    sel: /^\d{4}-\d{2}-\d{2}$/.test(params.get('date') || '') ? params.get('date') : today,
    f: FILTER.some(x => x[0] === params.get('f')) ? params.get('f') : 'all',
    p: params.get('p') || '',
  };
  const seg = h('div', { class: 'seg', role: 'group', 'aria-label': 'Calendar view' },
    ['week', 'month'].map(v => h('button', { type: 'button', 'aria-pressed': String(st.view === v), onclick: () => { st.view = v; store.set('cm.calview', v); draw(); } }, v === 'week' ? 'Week' : 'Month')));
  const chips = h('div', { class: 'chips scroll', role: 'group', 'aria-label': 'Show' });
  const lbl = h('span', { class: 'lbl', 'aria-live': 'polite' });
  const nav = h('div', { class: 'cal-nav' },
    h('button', { class: 'iconbtn bordered', type: 'button', 'aria-label': 'Previous', html: I.left, onclick: () => move(-1) }), lbl,
    h('button', { class: 'iconbtn bordered', type: 'button', 'aria-label': 'Next', html: I.right, onclick: () => move(1) }),
    h('button', { class: 'btn secondary sm', type: 'button', id: 'cal-today', onclick: () => { st.sel = today; draw(); } }, 'Today'));
  const sub = h('div', { class: 'cal-sub', role: 'group', 'aria-label': 'Streaming service' });
  const wrap = h('div', { class: 'cal-wrap' });
  const note = h('p', { class: 'cal-note' });
  view.append(h('div', { class: 'page-h' }, h('span', { class: 'ic', html: I.cal }),
    h('div', { class: 'tx' }, h('h1', {}, 'Release Calendar'), h('p', {}, 'New movies, streaming releases and TV episodes, all in one place.')), seg),
    h('div', { class: 'cal-bar' }, chips, nav), sub, wrap, note);
  FILTER.forEach(([k, l, ic]) => chips.append(h('button', { class: 'chip', type: 'button', 'data-k': k, 'aria-pressed': 'false', html: (ic || '') + `<span>${l}</span>`, onclick: () => { st.f = k; st.p = ''; draw(); } })));

  function range() {
    const d = dayOf(st.sel);
    if (st.view === 'week') { const a = new Date(d); a.setDate(a.getDate() - a.getDay()); const z = new Date(a); z.setDate(z.getDate() + 6); return [iso(a), iso(z)]; }
    const first = new Date(d.getFullYear(), d.getMonth(), 1, 12), last = new Date(d.getFullYear(), d.getMonth() + 1, 0, 12);
    const a = new Date(first); a.setDate(a.getDate() - a.getDay()); const z = new Date(last); z.setDate(z.getDate() + (6 - z.getDay()));
    return [iso(a), iso(z)];
  }
  function move(n) {
    const d = dayOf(st.sel);
    if (st.view === 'week') d.setDate(d.getDate() + 7 * n);
    else { const day = d.getDate(); d.setDate(1); d.setMonth(d.getMonth() + n); d.setDate(Math.min(day, new Date(d.getFullYear(), d.getMonth() + 1, 0).getDate())); }
    st.sel = iso(d); draw();
  }
  async function load(a, z) {
    const days = Math.round((dayOf(z) - dayOf(a)) / 864e5);
    const k = `${a}|${days}`;
    if (!cache.has(k)) cache.set(k, api(`/api/v8/calendar?start=${a}&days=${days}`).catch(e => { cache.delete(k); throw e; }));
    return cache.get(k);
  }
  const match = (e) => {
    if (st.f === 'mine') return e.on_watchlist || e.following;
    if (st.f === 'theaters') return e.kind.startsWith('theatrical');
    if (st.f === 'streaming') return e.kind === 'digital' && (!st.p || (e.providers || []).includes(st.p) || (st.p === '?' && !(e.providers || []).length));
    if (st.f === 'tv') return e.kind === 'tv_season' || e.kind === 'tv_episode';
    return true;
  };
  let seq = 0, data = null;
  async function draw() {
    history.replaceState(null, '', `#/calendar?view=${st.view}&date=${st.sel}${st.f !== 'all' ? `&f=${st.f}` : ''}${st.p ? `&p=${encodeURIComponent(st.p)}` : ''}`);
    $$('button', seg).forEach((b, i) => b.setAttribute('aria-pressed', String(['week', 'month'][i] === st.view)));
    $$('.chip', chips).forEach(b => b.setAttribute('aria-pressed', String(b.dataset.k === st.f)));
    const [a, z] = range(), d = dayOf(st.sel);
    lbl.textContent = st.view === 'week'
      ? (dayOf(a).getMonth() === dayOf(z).getMonth() ? `${fmtDate(a, { month: 'short', day: 'numeric' })} – ${dayOf(z).getDate()}, ${dayOf(z).getFullYear()}` : `${fmtDate(a, { month: 'short', day: 'numeric' })} – ${fmtDate(z)}`)
      : d.toLocaleDateString(undefined, { month: 'long', year: 'numeric' });
    $('#cal-today').disabled = st.sel === today;
    wrap.className = `cal-wrap v-${st.view}`;
    const my = ++seq;
    if (!data || data.from > a || data.to < z) wrap.setAttribute('aria-busy', 'true');
    let j;
    try { j = await load(a, z); } catch (e) { if (my === seq) { wrap.innerHTML = ''; wrap.append(h('div', { class: 'notice warn' }, e.message)); } return; }
    if (my !== seq) return;
    data = j; wrap.removeAttribute('aria-busy');
    const by = {};
    for (const e of j.events || []) if (match(e)) (by[e.date] = by[e.date] || []).push(e);
    // service chips: only services that actually appear in this period
    sub.innerHTML = '';
    if (st.f === 'streaming') {
      const names = [...new Set((j.events || []).filter(e => e.kind === 'digital').flatMap(e => e.providers || []))].sort();
      const unknown = (j.events || []).some(e => e.kind === 'digital' && !(e.providers || []).length);
      if (names.length || unknown) {
        sub.append(h('span', { class: 'small muted' }, 'Service:'));
        [['', 'Any'], ...names.map(n => [n, n]), ...(unknown ? [['?', 'Not announced']] : [])].forEach(([v, l]) =>
          sub.append(h('button', { class: 'chip', type: 'button', 'aria-pressed': String(st.p === v), onclick: () => { st.p = v; draw(); } }, l)));
      }
    }
    wrap.innerHTML = '';
    if (st.view === 'week') drawWeek(a, by); else drawMonth(a, z, by);
    wrap.append(dayPanel(by[st.sel] || []));
    const total = Object.values(by).reduce((n, x) => n + x.length, 0);
    note.textContent = `${total} release${total === 1 ? '' : 's'} in this ${st.view}${st.f !== 'all' ? ' with this filter' : ''}. Dates are US dates from TMDB${j.as_of ? `, lists updated ${j.as_of}` : ''}. TV: season premieres, season finales of well-known shows, and every episode of the shows you follow. Cinemack has no showtimes of its own: Showtimes opens a web search.`;
    note.querySelector('.tvm')?.remove();
    for (const c of (j.credits || [])) note.append(' ', h('a', { class: 'tvm', href: c.url, target: '_blank', rel: 'noopener noreferrer' }, `${c.text}.`));
  }
  const kinds = (es) => [...new Set(es.map(e => (KIND[e.kind] || ['', 'un'])[1]))];
  const thumb = (e) => { const it = e.film || e.show; return h('span', { class: 'pp' }, it.poster ? h('img', { src: it.poster, alt: '', loading: 'lazy', width: 54, height: 81 }) : h('span', { class: 'ph' })); };
  const ghead = (cls, icon, label, n) => h('span', { class: `kd ki ${cls}`, html: `${icon}<span>${label} (${n})</span>` });
  const filmMeta = (e) => {
    const f = e.film, g = firstGenre(f.genres).slice(0, 1).map(shortGenre)[0];
    if (e.kind === 'digital' && (e.providers || []).length) return e.providers.slice(0, 2).join(', ');
    return [g, f.runtime ? rt(f.runtime) : null].filter(Boolean).join(' · ') || (e.kind === 'theatrical_rerelease' ? 'Re-release' : '');
  };
  // round 27: TVmaze air time, shown in the viewer's own time zone (airstamp is UTC)
  const airTime = (e) => { if (!e.airstamp) return ''; const t = new Date(e.airstamp); return isNaN(t) ? '' : t.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' }); };
  const epLabel = (e) => (e.kind === 'tv_episode' ? `S${e.show.season} E${e.show.episode}${e.finale ? ' · finale' : ''}` : `S${e.show.season} premiere`) + (airTime(e) ? ` · ${airTime(e)}` : '');
  // one release inside a week column: a wide tile (film with a backdrop, quiet group) or a compact row
  function weekItem(e, tile) {
    if (e.show) return h('a', { class: 'wr', href: `#/show/${e.show.id}`, title: e.show.title },
      h('span', { class: 'pp' }, posterImg(e.show, 'poster', 40)), h('span', { class: 'in' }, h('span', { class: 'tt' }, e.show.title), h('span', { class: 'mm' }, epLabel(e))));
    const f = e.film, href = `#/film/${f.id}`;
    if (tile && f.backdrop) {
      const img = h('img', { src: f.backdrop, alt: '', loading: 'lazy', width: 300, height: 169, class: 'ld' });
      img.addEventListener('load', () => img.classList.remove('ld'), { once: true });
      img.addEventListener('error', () => img.replaceWith(h('span', { class: 'ph' })), { once: true });
      return h('a', { class: 'wt', href, title: f.title }, img, h('span', { class: 'tt' }, f.title));
    }
    const m = filmMeta(e);
    return h('a', { class: 'wr', href, title: f.title }, h('span', { class: 'pp' }, posterImg(f, 'poster', 40)),
      h('span', { class: 'in' }, h('span', { class: 'tt' }, f.title), m ? h('span', { class: 'mm' }, m) : null));
  }
  function select(dt) { st.sel = dt; draw().then(() => { if (!matchMedia('(min-width:1280px)').matches || st.view === 'week') $('.dpanel')?.scrollIntoView({ behavior: 'smooth', block: 'nearest' }); }); }
  function keyNav(e) {
    const step = { ArrowLeft: -1, ArrowRight: 1, ArrowUp: -7, ArrowDown: 7 }[e.key];
    if (!step || (st.view === 'week' && Math.abs(step) === 7)) return;
    e.preventDefault(); const d = dayOf(st.sel); d.setDate(d.getDate() + step); st.sel = iso(d);
    draw().then(() => $(`[data-d="${st.sel}"]`, wrap)?.focus());
  }
  function drawWeek(a, by) {
    const ds = Array.from({ length: 7 }, (_, i) => { const d = dayOf(a); d.setDate(d.getDate() + i); return iso(d); });
    const grid = h('div', { class: 'week', role: 'group', 'aria-label': 'Days of the week' });
    const strip = h('div', { class: 'wstrip', role: 'tablist', 'aria-label': 'Days of the week' });
    for (const dt of ds) {
      const es = by[dt] || [], n = es.length, d = dayOf(dt);
      const label = `${d.toLocaleDateString(undefined, { weekday: 'long', month: 'long', day: 'numeric' })}: ${n} release${n === 1 ? '' : 's'}`;
      const col = h('div', { class: `wday${dt === today ? ' today' : ''}${dt < today ? ' past' : ''}${dt === st.sel ? ' sel' : ''}`, 'data-day': dt },
        h('button', { class: 'dh', type: 'button', 'aria-pressed': String(dt === st.sel), 'aria-label': label,
          tabindex: dt === st.sel ? '0' : '-1', 'data-d': dt, onclick: () => select(dt), onkeydown: keyNav },
          h('span', { class: 'wd' }, d.toLocaleDateString(undefined, { weekday: 'short' })),
          h('span', { class: 'dn' }, d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })), dt === today ? h('span', { class: 'now' }, 'Today') : null));
      const body = h('div', { class: 'db' });
      body.addEventListener('click', (ev) => { if (!ev.target.closest('a,button')) select(dt); });
      if (!n) body.append(h('span', { class: 'none' }, 'No dated releases'));
      else for (const [, gl, cls, test, icon] of GROUPS) {
        const g = es.filter(test); if (!g.length) continue;
        const tiles = cls !== 'tv' && g.length <= 2 && n <= 6;          // quiet day: wide tiles; busy: compact rows
        body.append(h('div', { class: `grp${tiles ? ' tiles' : ''}` }, h('div', { class: 'gh' }, ghead(cls, icon, cls === 'tv' ? 'TV' : gl, g.length)),
          h('div', { class: 'items' }, g.map(e => weekItem(e, tiles)))));
      }
      col.append(body); grid.append(col);
      strip.append(h('button', { type: 'button', role: 'tab', class: dt === today ? 'today' : '', 'aria-selected': String(dt === st.sel), 'aria-label': label, tabindex: dt === st.sel ? '0' : '-1',
        'data-d': dt, onclick: () => select(dt), onkeydown: keyNav },
        h('span', { class: 'wd' }, d.toLocaleDateString(undefined, { weekday: 'narrow' })), h('span', { class: 'dn' }, String(d.getDate())), h('span', { class: 'ct' }, n ? String(n) : '')));
    }
    wrap.append(grid, strip);
    let x0 = null;
    strip.addEventListener('touchstart', (e) => { x0 = e.touches[0].clientX; }, { passive: true });
    strip.addEventListener('touchend', (e) => { if (x0 == null) return; const dx = e.changedTouches[0].clientX - x0; x0 = null; if (Math.abs(dx) > 60) move(dx < 0 ? 1 : -1); }, { passive: true });
  }
  function drawMonth(a, z, by) {
    const grid = h('div', { class: 'month', role: 'grid', 'aria-label': dayOf(st.sel).toLocaleDateString(undefined, { month: 'long', year: 'numeric' }) });
    const row0 = h('div', { role: 'row', style: 'display:contents' });
    Array.from({ length: 7 }, (_, i) => { const d = dayOf(a); d.setDate(d.getDate() + i); row0.append(h('div', { class: 'mh', role: 'columnheader' }, d.toLocaleDateString(undefined, { weekday: 'short' }))); });
    grid.append(row0);
    const month = dayOf(st.sel).getMonth();
    let row = null;
    for (let d = dayOf(a), i = 0; iso(d) <= z; d.setDate(d.getDate() + 1), i++) {
      if (i % 7 === 0) { row = h('div', { role: 'row', style: 'display:contents' }); grid.append(row); }
      const dt = iso(d), es = by[dt] || [], n = es.length;
      const ks = kinds(es);
      const cell = h('button', { class: `mcell${d.getMonth() !== month ? ' out' : ''}${dt === today ? ' today' : ''}`, type: 'button', role: 'gridcell', 'aria-selected': String(dt === st.sel),
        tabindex: dt === st.sel ? '0' : '-1', 'data-d': dt, onclick: () => select(dt), onkeydown: keyNav,
        'aria-label': `${d.toLocaleDateString(undefined, { weekday: 'long', month: 'long', day: 'numeric' })}: ${n ? `${n} release${n === 1 ? '' : 's'}` : 'no releases'}` },
        h('span', { class: 'n' }, String(d.getDate())), n ? h('span', { class: 'c', 'aria-hidden': 'true' }, String(n)) : null,
        ks.length ? h('span', { class: 'dots', 'aria-hidden': 'true' }, ks.map(k => h('i', { class: k }))) : null,
        n ? h('span', { class: 'mp', 'aria-hidden': 'true' }, es.slice(0, 2).map(thumb)) : null);
      row.append(cell);
    }
    wrap.append(h('div', {}, grid, h('div', { class: 'legend', 'aria-hidden': 'true' }, [['th', 'In theaters'], ['re', 'Re-release'], ['st', 'Streaming'], ['tv', 'TV season'], ['un', 'Type not confirmed']].map(([k, l]) => h('span', { class: `kd ${k}` }, l)))));
  }
  function dayPanel(es) {
    const d = dayOf(st.sel);
    const p = h('section', { class: 'dpanel', role: 'tabpanel', 'aria-label': `Releases on ${d.toLocaleDateString(undefined, { weekday: 'long', month: 'long', day: 'numeric' })}` },
      h('h2', {}, d.toLocaleDateString(undefined, { weekday: 'long', month: 'short', day: 'numeric', year: 'numeric' })),
      h('p', { class: 'sub' }, es.length ? `${es.length} release${es.length === 1 ? '' : 's'}${st.sel === today ? ' today' : ''}` : 'Nothing dated for this day.'));
    if (!es.length) { p.append(empty(null, st.f === 'mine' ? 'Nothing from your watchlist or followed shows on this day.' : 'Try another day, or switch to Month to see where the releases are.', null, [], true)); return p; }
    const groups = h('div', { class: 'dgroups' }); p.append(groups);
    for (const [key, gl, cls, test, icon] of GROUPS) {
      const g = es.filter(test); if (!g.length) continue;
      groups.append(h('div', { class: 'dgroup', 'data-g': key }, h('div', { class: 'gh' }, ghead(cls, icon, gl, g.length)),
        h('div', { class: 'evs' }, g.map(evCard))));
    }
    // Add reminders: saves every film of the day that is not saved yet and follows every show not followed yet
    const films = es.filter(e => e.film && !isWant(e.film.id)), shows = [...new Map(es.filter(e => e.show && !e.following).map(e => [e.show.id, e])).values()];
    if (st.sel >= today && (films.length || shows.length)) {
      const n = films.length + shows.length;
      const b = h('button', { class: 'btn outline block remind', type: 'button', html: I.bell + `<span>Add reminders${n > 1 ? ` (${n})` : ''}</span>` });
      b.addEventListener('click', async () => {
        b.disabled = true;
        await Promise.all(films.map(e => setChoice(e.film, 'want', true)));
        let ok = 0;
        for (const e of shows) { try { await post('/api/v8/tv/follow', { id: e.show.id, on: true }); e.following = true; ok++; emit('cm:follow', { id: e.show.id, on: true }); } catch {} }
        cache.clear();
        toast([films.length ? `Saved ${films.length} film${films.length === 1 ? '' : 's'}` : '', ok ? `followed ${ok} show${ok === 1 ? '' : 's'}` : ''].filter(Boolean).join(' and ') + '. The bell tells you when they come out.');
        draw();
      });
      p.append(b);
    }
    p.append(h('p', { class: 'hint', style: 'margin-top:12px' }, 'Saving a film turns on its alerts: you hear in Cinemack (the bell) when it comes out or starts streaming. Following a show alerts you when a new season gets a date.'));
    return p;
  }
  function evCard(e) {
    const film = e.film, show = e.show, [lab, cls] = KIND[e.kind] || [e.label, 'un'];
    if (film) {
      const href = `#/film/${film.id}`, m = [firstGenre(film.genres).slice(0, 1).map(shortGenre)[0], film.runtime ? rt(film.runtime) : null].filter(Boolean).join(' · ');
      const acts = h('div', { class: 'ea' });
      if (e.kind.startsWith('theatrical') && film.in_theatres) acts.append(h('button', { class: 'iconbtn', type: 'button', html: I.ticket, 'aria-label': `Showtimes: ${film.title}`, title: 'Showtimes', onclick: () => showtimesSheet(film) }));
      if (e.kind === 'digital') acts.append(h('a', { class: 'iconbtn', href: `${href}?go=watch`, html: I.play, 'aria-label': `Where to watch ${film.title}`, title: 'Where to watch' }));
      acts.append(wantButton(film));
      const note = e.kind === 'digital' ? ((e.providers || []).length ? h('span', { class: 'sv' }, 'On ' + e.providers.slice(0, 3).join(', ')) : h('span', { class: 'unk' }, 'Service not announced yet'))
        : e.kind === 'theatrical_or_unknown' ? h('span', { class: 'unk' }, 'US date; TMDB does not say theatrical or digital')
        : e.kind === 'theatrical_rerelease' ? h('span', { class: 'sv' }, 'Back in theaters') : null;
      return h('article', { class: 'ev', 'data-kind': cls }, h('a', { class: 'pp', href, tabindex: '-1', 'aria-hidden': 'true' }, posterImg(film, 'poster', 48)),
        h('div', { class: 'in' }, h('a', { class: 'tt', href }, film.title), m ? h('span', { class: 'mm' }, m) : null, note), acts);
    }
    const fol = h('button', { class: 'iconbtn bm', type: 'button', 'aria-pressed': String(!!e.following), 'aria-label': `Follow ${show.title}` });
    const paint = () => { fol.innerHTML = e.following ? I.bellOn : I.bell; fol.setAttribute('aria-pressed', String(!!e.following)); fol.title = e.following ? 'Following' : 'Follow'; };
    paint();
    fol.addEventListener('click', async () => { try { await post('/api/v8/tv/follow', { id: show.id, on: !e.following }); e.following = !e.following; paint(); cache.clear();
      toast(e.following ? `Following ${show.title}. It is on your Watchlist, with season alerts on.` : `Stopped following ${show.title}.`); emit('cm:follow', { id: show.id, on: e.following }); } catch (er) { toast(er.message, null, true); } });
    return h('article', { class: 'ev', 'data-kind': 'tv' }, h('a', { class: 'pp', href: `#/show/${show.id}`, tabindex: '-1', 'aria-hidden': 'true' }, posterImg(show, 'poster', 48)),
      h('div', { class: 'in' }, h('a', { class: 'tt', href: `#/show/${show.id}` }, show.title),
        h('span', { class: 'mm' }, e.kind === 'tv_episode' ? `S${show.season} E${show.episode}${show.episode_title ? ` · ${show.episode_title}` : ''}${e.finale ? ' (season finale)' : ''}` : `Season ${show.season} premieres`),
        (e.where || e.network || airTime(e)) ? h('span', { class: 'sv' }, [airTime(e) ? `${airTime(e)} your time` : null, e.where || e.network ? `${e.streaming ? 'streams on' : 'on'} ${e.where || e.network}` : null].filter(Boolean).join(' · ')) : null),
      h('div', { class: 'ea' }, fol));
  }
  draw();
}
