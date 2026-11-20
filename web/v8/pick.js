// Pick Tonight (10 Nov 2026 redesign): five quick choices, then up to three ranked films. Uses the existing
// recommendation pipeline (/api/chat with strict filters) but shows structured results, never the chat transcript.
// Hard limits (time, age rating, never-genres, required services, the mood's genre) are enforced on the server AND
// re-checked here; a film whose fact is unknown fails the limit. Fewer than three films? It says why and offers to
// loosen a specific limit, and only does so when the person presses that button.
import { $, $$, S, h, I, api, post, view, store, toast, popMenu, newId, hdrs, md, splitSources, ratingEl, rt, firstGenre, shortGenre,
  wantButton, setChoice, isWant, showtimesSheet, empty, emit, posterImg } from './core.js';

let P = null;
const CERT = ['G', 'PG', 'PG-13', 'R', 'NC-17'];
const ICON = { kind: I.tv, with: I.user, mood: I.bolt, time: I.clock, novelty: I.compass, services: I.monitor };
const Q = { kind: 'Movie or show?', with: 'Who is watching?', mood: 'What mood?', time: 'How much time?', novelty: 'Familiar or different?', services: 'Which services?' };

export async function pagePick() {
  view.innerHTML = '';
  if (!P) { try { P = await (await fetch('/v8/pick.json')).json(); } catch { view.append(h('div', { class: 'notice' }, "Couldn't load Pick Tonight.")); return; } }
  const sel = S.pick || (S.pick = Object.assign({ kind: 'movie', with: 'me', mood: 'any', time: 'any', novelty: 'familiar', services: false }, store.json('cm.pick', {})));
  if (!P.kind[sel.kind]) sel.kind = 'movie';
  const prefs = h('div', { class: 'prefs', role: 'group', 'aria-label': 'Your preferences' });
  const go = h('button', { class: 'btn primary lg go', type: 'button', html: I.sparkle + '<span>Find My Picks</span>', onclick: () => run() });
  const drawPrefs = () => {
    prefs.innerHTML = '';
    for (const key of ['kind', 'with', 'mood', 'time', 'novelty']) {
      const b = h('button', { class: 'pref', type: 'button', 'aria-haspopup': 'menu', 'aria-expanded': 'false', 'aria-label': `${Q[key]} ${P[key][sel[key]].label}`, 'data-k': key },
        h('span', { class: 'pi', html: ICON[key] }), h('span', { class: 'tx' }, h('span', { class: 'q' }, Q[key]), h('span', { class: 'v' }, P[key][sel[key]].label)), h('span', { class: 'cv', html: I.chev }));
      b.addEventListener('click', () => popMenu(b, Object.entries(P[key]).map(([k, v]) => ({ label: v.label, checked: sel[key] === k, onclick: () => { sel[key] = k; save(); drawPrefs(); $(`.pref[data-k="${key}"]`)?.focus(); } })), { radio: true, label: Q[key], title: Q[key] }));
      prefs.append(b);
    }
    const n = (S.me?.settings?.services || []).length;
    const sb = h('button', { class: 'pref svc', type: 'button', 'aria-haspopup': 'menu', 'aria-expanded': 'false', 'data-k': 'services', 'aria-label': `${Q.services} ${sel.services ? `Only my services (${n})` : 'Any service'}` },
      h('span', { class: 'pi', html: ICON.services }), h('span', { class: 'tx' }, h('span', { class: 'q' }, Q.services), h('span', { class: 'v' }, sel.services ? `My services (${n})` : 'Any service')), h('span', { class: 'cv', html: I.chev }));
    sb.addEventListener('click', () => popMenu(sb, [{ label: 'Any service, theaters too', checked: !sel.services, onclick: () => { sel.services = false; save(); drawPrefs(); } },
      { label: n ? `Only my services (${n})` : 'Only my services (add them first)', checked: sel.services, onclick: () => { if (!n) { location.hash = '#/profile?tab=settings&sec=services'; toast('Add the services you have, then come back.'); return; } sel.services = true; save(); drawPrefs(); } },
      { sep: true }, { label: 'Edit my services', href: '#/profile?tab=settings&sec=services' }], { radio: true, label: Q.services, title: Q.services }));
    prefs.append(sb, go);
  };
  const save = () => store.set('cm.pick', JSON.stringify(sel));
  const out = h('section', { class: 'pick-out', 'aria-live': 'polite', 'aria-label': 'Your picks' });
  view.append(h('div', { class: 'pick-hero' }, h('span', { class: 'reel', html: '<svg viewBox="0 0 100 100" fill="none" stroke="currentColor" stroke-width="3" aria-hidden="true"><circle cx="50" cy="50" r="44"/><circle cx="50" cy="50" r="7"/><circle cx="50" cy="24" r="11"/><circle cx="75" cy="42" r="11"/><circle cx="66" cy="72" r="11"/><circle cx="34" cy="72" r="11"/><circle cx="25" cy="42" r="11"/></svg>' }),
    h('div', { class: 'page-h' }, h('span', { class: 'ic', html: I.sparkle }), h('div', { class: 'tx' }, h('h1', {}, 'Pick Tonight'),
      h('p', {}, "Tell us what you're in the mood for and we'll rank three films or shows. Time, age rating and services are hard limits, never suggestions."))),
    prefs), out);
  drawPrefs();
  if (S.pickResult) drawResult(out, S.pickResult); else out.append(h('p', { class: 'muted', style: 'text-align:center;margin-top:8px' }, 'Change any choice above, then press Find My Picks.'));

  // ---------------------------------------------------------------- run
  async function run(extraExclude = []) {
    if (sel.kind === 'tv') return runTv(extraExclude);
    if (run.ctrl) run.ctrl.abort();
    const ctrl = run.ctrl = new AbortController();
    const limits = limitsOf(sel);
    const exclude = [...new Set([...(extraExclude || [])])];
    const f = Object.assign({ strict: true, brief: true }, P.with[sel.with].filters, P.mood[sel.mood].filters, P.time[sel.time].filters, sel.services ? { services_only: true } : {});
    const avoid = exclude.length ? ` Not ${S.pickResult?.picks?.filter(p => exclude.includes(p.id)).map(p => p.title).join(', ')}.` : '';
    const q = `Pick tonight: recommend three ${P.mood[sel.mood].words} films ${P.with[sel.with].words}${P.time[sel.time].words ? ', ' + P.time[sel.time].words : ''}. I'd like ${P.novelty[sel.novelty].words}.${avoid} One line each on why it fits.`;
    const conv = newId();
    const res = { sel: Object.assign({}, sel), limits, picks: [], removed: [], answer: '', conv, exclude, pending: true, t0: performance.now() };
    S.pickResult = res;
    go.classList.add('busy'); go.setAttribute('aria-busy', 'true');     // stays focusable; pressing again restarts
    drawResult(out, res);
    out.scrollIntoView({ behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth', block: 'start' });
    const tick = setInterval(() => { const s = $('.pick-wait .s', out); if (s) s.textContent = `${Math.round((performance.now() - res.t0) / 1000)} s`; }, 1000);
    try {
      const r = await fetch('/api/chat', { method: 'POST', signal: ctrl.signal, headers: hdrs({ 'X-MB-Conv': conv }),
        body: JSON.stringify({ message: q, history: [], recent_titles: [], conv: null, think: false, filters: f }) });
      if (!r.ok) { let m = `The server answered ${r.status}.`; try { m = (await r.json()).error || m; } catch {} throw new Error(m); }
      const reader = r.body.getReader(), dec = new TextDecoder(); let buf = '';
      for (;;) {
        const { value, done } = await reader.read(); if (done) break;
        buf += dec.decode(value, { stream: true }); let i;
        while ((i = buf.indexOf('\n\n')) >= 0) {
          const raw = buf.slice(0, i); buf = buf.slice(i + 2); let ev = 'message', data = '';
          for (const line of raw.split('\n')) { if (line.startsWith('event:')) ev = line.slice(6).trim(); else if (line.startsWith('data:')) data += line.slice(5).trim(); }
          if (!data) continue;
          const d = JSON.parse(data);
          if (ev === 'sources') {
            const films = (d.films || []).filter(x => x.id > 0 && !exclude.includes(x.id));
            const g = guard(films, limits);
            res.candidates = g.ok; res.removed0 = g.removed; res.removed = g.removed; res.picks = g.ok.slice(0, 3); res.firstAt = performance.now() - res.t0;
            if (S.pickResult === res) drawResult(out, res);
            details(res);
          } else if (ev === 'token') res.answer += d.text;
          else if (ev === 'replace') res.answer = d.text || '';
          else if (ev === 'error') throw new Error(d.message);
        }
      }
    } catch (e) {
      if (e.name !== 'AbortError') res.error = e.message || "Couldn't reach the Cinemack server.";
    } finally {
      clearInterval(tick);
      if (run.ctrl === ctrl) { run.ctrl = null; go.classList.remove('busy'); go.removeAttribute('aria-busy'); }
      res.pending = false; res.doneAt = performance.now() - res.t0;
      if (S.pickResult === res && document.body.contains(out)) drawResult(out, res);
    }
  }
  pagePick.run = run;

  // ---------------------------------------------------------------- TV (round 21): ranked in code from facts.db, no model call
  async function runTv(exclude = []) {
    if (run.ctrl) run.ctrl.abort();
    const L = limitsOf(sel), res = { tv: true, sel: Object.assign({}, sel), limits: L, picks: [], removed: {}, pending: true, exclude, t0: performance.now() };
    S.pickResult = res; drawResult(out, res);
    out.scrollIntoView({ behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth', block: 'start' });
    const q = new URLSearchParams({ genre: L.genre || '', max_cert: L.cert || '', max_runtime: L.cap || 0, services: L.services ? 1 : 0, novelty: sel.novelty, exclude: exclude.join(',') });
    try { const j = await api(`/api/v8/pick/tv?${q}`); Object.assign(res, { picks: j.picks || [], candidates: j.candidates || [], removed: j.removed || {}, considered: j.considered }); }
    catch (e) { res.error = e.message || "Couldn't reach the Cinemack server."; }
    res.pending = false; res.doneAt = performance.now() - res.t0;
    if (S.pickResult === res && document.body.contains(out)) drawResult(out, res);
  }
}

// ---------------------------------------------------------------- constraints
function limitsOf(sel) {
  const L = { cap: Number(P.time[sel.time].filters?.max_runtime) || null, cert: P.with[sel.with].filters?.max_cert || null,
    genre: P.mood[sel.mood].filters?.genre || null, services: !!sel.services, never: S.me?.settings?.never_genres || [] };
  const words = [];
  L.tv = sel.kind === 'tv';
  if (L.cap) words.push(L.tv ? `episodes under ${L.cap} minutes` : `under ${L.cap} minutes`);
  if (L.cert) words.push(`rated ${L.cert} or milder`);
  if (L.genre) words.push(`${shortGenre(L.genre).toLowerCase()}`);
  if (L.services) words.push('on your services');
  if (L.never.length) words.push(`no ${L.never.map(g => shortGenre(g).toLowerCase()).join(' or ')}`);
  L.words = words;
  return L;
}
// The second check. The server already removed these; if anything slips through (or a fact is missing) it is dropped
// here and counted, so the page can say how many were left out and why.
function guard(films, L) {
  const ok = [], removed = [];
  for (const f of films) {
    const gs = firstGenre(f.genres);
    let why = null;
    if (L.cap && !f.runtime) why = 'runtime unknown';
    else if (L.cap && f.runtime > L.cap) why = `${rt(f.runtime)} is over ${L.cap} min`;
    else if (L.cert && !f.cert) why = 'age rating unknown';
    else if (L.cert && (CERT.indexOf(f.cert) < 0 || CERT.indexOf(f.cert) > CERT.indexOf(L.cert))) why = `rated ${f.cert}`;
    else if (L.genre && gs.length && !gs.includes(L.genre)) why = `not ${L.genre.toLowerCase()}`;
    else if (L.never.length && gs.some(g => L.never.includes(g))) why = 'a genre you never want';
    else if (L.services && !(f.on_services || []).length) why = 'not on your services';
    if (why) removed.push({ id: f.id, title: f.title, why }); else ok.push(f);
  }
  return { ok, removed };
}
async function details(res) {
  const need = res.picks.filter(p => !p._d);
  if (!need.length) return;
  await Promise.all(need.map(async (p) => {
    p._d = true;
    try { const d = await api(`/api/film/${p.id}`); Object.assign(p, { overview: d.overview, cert: p.cert || d.cert, backdrop: d.backdrop, imdb: p.imdb || d.imdb, runtime: p.runtime || d.runtime }); }
    catch { /* the card shows without a synopsis */ }
  }));
  // Facts may have arrived with the details: check the limits again over the whole candidate list, never show a film
  // that now fails one, and fetch details for any film that moved up into the top three.
  const g = guard(res.candidates || res.picks, res.limits);
  res.removed = [...(res.removed0 || []), ...g.removed];
  res.picks = g.ok.slice(0, 3);
  if (res.picks.some(p => !p._d)) return details(res);
  if (S.pickResult === res) { const out = $('.pick-out'); if (out) drawResult(out, res); }
}

// ---------------------------------------------------------------- reasons: only what is known about the film and the choices
function reasonsOf(f, res) {
  const L = res.limits, sel = res.sel, out = [];
  const gs = firstGenre(f.genres);
  if (L.genre && gs.includes(L.genre)) out.push(`Fits your ${P.mood[sel.mood].label.toLowerCase()} mood (${shortGenre(L.genre)})`);
  else if (gs.length) out.push(gs.slice(0, 2).map(shortGenre).join(' and '));
  if (L.cap && f.runtime) out.push(`${rt(f.runtime)}: under your ${L.cap}-minute limit`);
  else if (f.runtime) out.push(`Runs ${rt(f.runtime)}`);
  if (L.cert && f.cert) out.push(`Rated ${f.cert}: fine ${P.with[sel.with].words}`);
  if ((f.on_services || []).length) out.push(`On your ${f.on_services.join(', ')}`);
  else if (f.in_theatres) out.push('In theaters now');
  const r = f.imdb?.rating || f.rating;
  if (r && r >= 7.5) out.push(`Highly rated (${Number(r).toFixed(1)} ${f.imdb?.rating ? 'IMDb' : 'TMDB'})`);
  if (f.why) out.push(f.why);
  return out;
}
const plain = (s) => s.replace(/\*\*|__|\*|_/g, '').replace(/\[([^\]]+)\]\([^)]+\)/g, '$1').trim();
const norm = (s) => (s || '').toLowerCase().normalize('NFKD').replace(/[̀-ͯ]/g, '').replace(/[^a-z0-9]+/g, ' ').trim();
// Round 55 (20 Nov): the note for one film is made of whole sentences of Cinemack's answer that name THAT film and no
// other film. A sentence that also names a film the search found but the page does not show, or carries a second
// "Title (year)", is left out. Before, the first line that mentioned the title was cut after the title, so a
// one-paragraph answer put the rest of the paragraph (other films included) under the card.
export function sentencesOf(text) {
  const out = [];
  for (const para of (text || '').split(/\n+/)) {
    const p = plain(para).replace(/^\s*(?:[-*•]|\d+[.)])\s*/, '');
    if (!p) continue;
    // split after . ! ? followed by a space and a capital letter, a quote or a digit; keeps "Dr. No" style breaks rare
    for (const x of p.split(/(?<=[.!?])\s+(?=["“'(A-Z0-9])/)) if (x.trim()) out.push(x.trim());
  }
  return out;
}
export function noteFor(f, answer, others = []) {
  if (!answer || !f.title) return null;
  const t = norm(f.title);
  if (!t) return null;
  const otherT = others.map(norm).filter(o => o && o !== t && !t.includes(o));
  const keep = [];
  for (const s of sentencesOf(splitSources(answer).text)) {
    const n = ` ${norm(s)} `;
    if (!n.includes(` ${t} `)) continue;
    if (otherT.some(o => n.includes(` ${o} `))) continue;
    const years = s.match(/\((?:19|20)\d{2}\)/g) || [];
    if (years.length > 1) continue;                       // two "(year)"s = two films in one sentence
    keep.push(s);
    if (keep.length === 2) break;
  }
  return keep.length ? keep.join(' ') : null;
}
// What is known about a film, as one sentence, for when the answer has no clean sentence about it.
export function factLine(reasons) {
  if (!reasons.length) return null;
  const r = reasons.map((x, i) => (i ? x.charAt(0).toLowerCase() + x.slice(1) : x));
  return r.join('; ') + '.';
}

// ---------------------------------------------------------------- results
function rankCard(f, n, res) {
  const best = n === 1;
  const name = `${f.title}${f.year ? ` (${f.year})` : ''}`;
  const gs = firstGenre(f.genres).slice(0, 2).map(shortGenre).join(', ');
  const acts = h('div', { class: 'acts' },
    h('a', { class: `btn ${best ? 'primary' : 'outline'} sm`, href: `#/film/${f.id}?go=watch`, html: I.play + 'Where to watch' }),
    f.in_theatres ? h('button', { class: 'btn secondary sm', type: 'button', html: I.ticket + 'Showtimes', onclick: () => showtimesSheet(f) }) : null,
    wantButton(f, 'text'),
    h('a', { class: 'btn ghost sm', href: `#/ask?chat=${encodeURIComponent(res.conv)}&ask=${encodeURIComponent(`Tell me more about ${name}: why is it a good pick for tonight?`)}`, html: I.bubble + 'Ask about it' }));
  return h('article', { class: `rk${best ? ' rk1' : ''}`, 'aria-label': `Rank ${n}: ${name}${best ? ', best match' : ''}` },
    best ? h('span', { class: 'best', html: I.trophy + 'Best Match' }) : null,
    h('span', { class: 'num', 'aria-hidden': 'true' }, String(n)),
    h('a', { class: 'pp', href: `#/film/${f.id}`, tabindex: '-1', 'aria-hidden': 'true' }, f.poster ? h('img', { src: f.poster_lg || f.poster, alt: '', width: best ? 200 : 96, height: best ? 300 : 144 }) : h('span', { class: 'ph' }, f.title)),
    h('div', { class: 'in' }, h('a', { class: 'tt', href: `#/film/${f.id}` }, f.title, f.year ? h('span', { class: 'yr' }, ` (${f.year})`) : null),
      h('div', { class: 'mm' }, ratingEl(f) || null, f.runtime ? h('span', {}, rt(f.runtime)) : null, gs ? h('span', {}, gs) : null, f.cert ? h('span', {}, f.cert) : null),
      f.overview ? h('p', { class: 'syn' }, f.overview) : h('div', { class: 'skel', style: 'height:38px;width:90%', 'aria-hidden': 'true' }),
      acts));
}
const skelRank = (best) => h('div', { class: `rk${best ? ' rk1' : ''}`, 'aria-hidden': 'true' }, h('span', { class: 'num' }), h('div', { class: 'skel', style: 'aspect-ratio:2/3' }),
  h('div', { style: 'display:grid;gap:10px;align-content:start' }, h('div', { class: 'skel', style: 'height:22px;width:60%' }), h('div', { class: 'skel', style: 'height:14px;width:40%' }), h('div', { class: 'skel', style: 'height:44px;width:92%' })));

function drawResult(out, res) {
  if (res.tv) return drawTv(out, res);
  out.innerHTML = '';
  const head = h('div', { class: 'section-h' }, h('span', { class: 'ico', html: I.trophy }), h('h2', {}, 'Your Top Picks'));
  if (res.limits.words.length) head.append(h('span', { class: 'sub' }, 'Only films ' + res.limits.words.join(', ') + '.'));
  out.append(head);
  if (res.error && !res.picks.length) { out.append(h('div', { class: 'notice warn' }, res.error, ' ', h('button', { class: 'btn secondary sm', type: 'button', onclick: () => pagePick.run() }, 'Try again'))); return; }
  if (res.pending && !res.picks.length) {
    out.append(h('p', { class: 'pick-wait' }, h('span', { class: 'status' }, h('span', { class: 'dot' })), 'Finding your picks… ', h('span', { class: 's' }, ''), h('span', { class: 'faint' }, ' Films appear as soon as the search finishes.')),
      h('div', { class: 'ranks' }, skelRank(true), skelRank(false), skelRank(false)));
    return;
  }
  if (!res.picks.length) {
    out.append(empty('No film fits every limit', `Nothing in the results was ${res.limits.words.join(', ') || 'a match'}. Loosen one limit and try again; nothing changes until you choose.`, 'reel', relaxButtons(res)));
    return;
  }
  out.append(h('div', { class: 'ranks' }, res.picks.map((f, i) => rankCard(f, i + 1, res))));
  if (res.picks.length < 3 && !res.pending) {
    const n = res.picks.length;
    out.append(h('div', { class: 'notice pick-limits' }, h('b', {}, `Only ${n} film${n === 1 ? '' : 's'} fit every limit you set`),
      res.limits.words.length ? ` (${res.limits.words.join(', ')})` : '', '. ',
      res.removed.length ? `${res.removed.length} other${res.removed.length === 1 ? ' was' : 's were'} left out: ${res.removed.slice(0, 4).map(r => `${r.title} (${r.why})`).join('; ')}. ` : '',
      'Cinemack will not stretch a limit on its own. Loosen one?', h('div', { class: 'chips' }, relaxButtons(res))));
  }
  const sum = [`You asked for ${P.mood[res.sel.mood].label.toLowerCase() === 'surprise me' ? 'anything good' : P.mood[res.sel.mood].words + ' films'} ${P.with[res.sel.with].words}`];
  if (res.limits.cap) sum.push(`under ${res.limits.cap} minutes`);
  if (res.limits.services) sum.push('on your services');
  const box = h('div', { class: 'pick-sum' }, h('span', { html: I.sparkle }), h('div', {}, h('h3', {}, 'Why these picks?'),
    h('p', { class: 'muted small' }, sum.join(', ') + `. ${res.sel.novelty === 'different' ? 'You wanted something less obvious.' : 'You wanted something well loved.'} Ranked by how well each matches, best first.`)));
  // Round 55: the explanation lives here only - one short line per film on the page, best first.
  const shown = new Set(res.picks.map(p => p.id));
  const others = [...(res.candidates || []), ...(res.removed || [])].filter(x => !shown.has(x.id)).map(x => x.title).filter(Boolean);
  const list = h('ol', { class: 'pick-notes' }, res.picks.map((f) => {
    const note = noteFor(f, res.answer, others) || (res.pending ? null : factLine(reasonsOf(f, res)));
    return h('li', {}, h('a', { href: `#/film/${f.id}` }, h('b', {}, f.title)), f.year ? h('span', { class: 'faint' }, ` (${f.year})`) : null, ': ',
      note ? h('span', {}, note) : h('span', { class: 'faint' }, 'Cinemack is writing a note on this pick…'));
  }));
  box.lastChild.append(list);
  out.append(box);
  const unsaved = res.picks.filter(p => !isWant(p.id));
  out.append(h('div', { class: 'pick-actions' },
    h('button', { class: 'btn secondary', type: 'button', html: I.sparkle + 'Try different picks', disabled: res.pending, onclick: () => tryDifferent(res) }),
    h('button', { class: 'btn secondary', type: 'button', html: I.sliders + 'Refine preferences', onclick: () => { scrollTo({ top: 0, behavior: 'smooth' }); $('.pref')?.focus(); } }),
    h('button', { class: 'btn secondary', type: 'button', html: I.bookmark + (unsaved.length ? `Save all ${res.picks.length} to watchlist` : 'All saved'), disabled: !unsaved.length,
      onclick: async (e) => { const b = e.currentTarget; b.classList.add('busy'); let ok = 0; for (const p of unsaved) ok += (await setChoice(p, 'want', true)) ? 1 : 0; b.classList.remove('busy');
        toast(`Saved ${ok} film${ok === 1 ? '' : 's'} to your watchlist.`); drawResult(out, res); } }),
    h('a', { class: 'btn ghost', href: `#/ask?chat=${encodeURIComponent(res.conv)}`, html: I.bubble + 'Ask Cinemack about these' })));
  if (res.firstAt && !res.pending) out.append(h('p', { class: 'tiny faint', style: 'margin-top:12px' }, `Picks found in ${(res.firstAt / 1000).toFixed(1)} s; notes finished in ${(res.doneAt / 1000).toFixed(1)} s.`));
}
function tryDifferent(res) {
  const seen = [...new Set([...(res.exclude || []), ...res.picks.map(p => p.id)])];
  const more = (res.candidates || []).filter(f => !seen.includes(f.id));
  if (more.length >= 3) {                     // the same search already found more films that pass every limit
    const next = Object.assign({}, res, { candidates: more, picks: more.slice(0, 3), exclude: seen });
    S.pickResult = next; details(next); drawResult($('.pick-out'), next); return;
  }
  pagePick.run(seen);
}
function relaxButtons(res) {
  const sel = S.pick, L = res.limits, b = [];
  const rerun = (fn) => () => { fn(); store.set('cm.pick', JSON.stringify(sel)); pagePick(); setTimeout(() => pagePick.run(), 0); };
  if (L.cap) b.push(h('button', { class: 'chip', type: 'button', onclick: rerun(() => { sel.time = L.cap < 120 ? '120' : 'any'; }) }, L.cap < 120 ? 'Allow up to 2 hours' : 'Any length'));
  if (L.genre) b.push(h('button', { class: 'chip', type: 'button', onclick: rerun(() => { sel.mood = 'any'; }) }, 'Any mood'));
  if (L.services) b.push(h('button', { class: 'chip', type: 'button', onclick: rerun(() => { sel.services = false; }) }, 'Include all services'));
  if (L.cert) b.push(h('button', { class: 'chip', type: 'button', onclick: () => { scrollTo({ top: 0, behavior: 'smooth' }); $('.pref[data-k="with"]')?.click(); } }, "Change who's watching"));
  if (L.never.length) b.push(h('a', { class: 'chip', href: '#/profile?tab=settings&sec=preferences' }, 'Edit never-recommend genres'));
  return b;
}

// ---------------------------------------------------------------- TV results (round 21)
function tvCardRank(x, n, res) {
  const best = n === 1, href = `#/show/${x.id}`;
  const fol = h('button', { class: `btn ${best ? 'secondary' : 'ghost'} sm`, type: 'button' });
  const paint = () => { fol.innerHTML = (x.following ? I.check : I.plus) + `<span>${x.following ? 'Following' : 'Follow'}</span>`; fol.setAttribute('aria-pressed', String(!!x.following)); };
  paint();
  fol.addEventListener('click', async () => { try { await post('/api/v8/tv/follow', { id: x.id, on: !x.following }); x.following = !x.following; paint(); toast(x.following ? `Following ${x.title}.` : `Stopped following ${x.title}.`); emit('cm:follow', { id: x.id, on: x.following }); } catch (e) { toast(e.message, null, true); } });
  const mm = [x.rating ? `★ ${x.rating} ${x.rating_src}` : null, x.episode_runtime ? `${x.episode_runtime}-min episodes` : null, x.seasons ? `${x.seasons} season${x.seasons === 1 ? '' : 's'}` : null, x.cert, x.network].filter(Boolean);
  return h('article', { class: `rk${best ? ' rk1' : ''}`, 'aria-label': `Rank ${n}: ${x.title}${best ? ', best match' : ''}` },
    best ? h('span', { class: 'best', html: I.trophy + 'Best Match' }) : null, h('span', { class: 'num', 'aria-hidden': 'true' }, String(n)),
    h('a', { class: 'pp', href, tabindex: '-1', 'aria-hidden': 'true' }, posterImg(x, 'poster_lg', best ? 200 : 96)),
    h('div', { class: 'in' }, h('a', { class: 'tt', href }, x.title, x.year ? h('span', { class: 'yr' }, ` (${x.year})`) : null, h('span', { class: 'tag tvk', style: 'margin-left:8px' }, 'TV')),
      h('div', { class: 'mm' }, mm.map(t => h('span', {}, t))), x.overview ? h('p', { class: 'syn' }, x.overview) : null,
      h('div', { class: 'acts' }, h('a', { class: `btn ${best ? 'primary' : 'outline'} sm`, href, html: I.play + 'Episodes and where to watch' }), fol,
        h('a', { class: 'btn ghost sm', href: `#/ask?ask=${encodeURIComponent(`Tell me more about the TV series ${x.title}: why is it a good pick for tonight?`)}`, html: I.bubble + 'Ask about it' }))));
}
function drawTv(out, res) {
  out.innerHTML = '';
  const head = h('div', { class: 'section-h' }, h('span', { class: 'ico', html: I.trophy }), h('h2', {}, 'Your Top Shows'));
  if (res.limits.words.length) head.append(h('span', { class: 'sub' }, 'Only shows ' + res.limits.words.join(', ') + '.'));
  out.append(head);
  if (res.pending) { out.append(h('p', { class: 'pick-wait' }, h('span', { class: 'status' }, h('span', { class: 'dot' })), 'Ranking shows…')); return; }
  if (res.error) { out.append(h('div', { class: 'notice warn' }, res.error, ' ', h('button', { class: 'btn secondary sm', type: 'button', onclick: () => pagePick.run() }, 'Try again'))); return; }
  const left = Object.entries(res.removed || {}).sort((a, b) => b[1] - a[1]).map(([w, n]) => `${n} ${w}`).join('; ');
  if (!res.picks.length) { out.append(empty('No show fits every limit', `${left ? `Left out: ${left}. ` : ''}Loosen one limit and try again; nothing changes until you choose.`, 'reel', relaxButtons(res))); return; }
  out.append(h('div', { class: 'ranks' }, res.picks.map((x, i) => tvCardRank(x, i + 1, res))));
  if (res.picks.length < 3) out.append(h('div', { class: 'notice pick-limits' }, h('b', {}, `Only ${res.picks.length} show${res.picks.length === 1 ? '' : 's'} fit every limit`), left ? ` (left out: ${left})` : '', '. Loosen one?', h('div', { class: 'chips' }, relaxButtons(res))));
  out.append(h('div', { class: 'pick-sum' }, h('span', { html: I.sparkle }), h('div', {}, h('h3', {}, 'How these were picked'),
    h('p', { class: 'muted small' }, `Ranked in code from ${Number(res.considered || 0).toLocaleString()} shows you have not followed or watched: IMDb rating weighted by votes${res.sel.novelty === 'different' ? ', among less-voted shows (something different)' : ', with a little weight on popularity'}. ${left ? `Left out by your limits: ${left}.` : ''} Episode length, US TV rating and services are hard limits; a show whose fact is unknown is left out.`),
    h('ol', { class: 'pick-notes' }, res.picks.map(x => h('li', {}, h('a', { href: `#/show/${x.id}` }, h('b', {}, x.title)), ': ', factLine(x.reasons || []) || 'ranked by rating and votes.'))))));
  out.append(h('div', { class: 'pick-actions' },
    h('button', { class: 'btn secondary', type: 'button', html: I.sparkle + 'Try different shows', onclick: () => {
      const seen = [...new Set([...(res.exclude || []), ...res.picks.map(p => p.id)])];
      const more = (res.candidates || []).filter(x => !seen.includes(x.id));
      if (more.length >= 3) { const next = Object.assign({}, res, { picks: more.slice(0, 3), candidates: more.slice(3), exclude: seen }); S.pickResult = next; drawTv(out, next); }
      else pagePick.run(seen);
    } }),
    h('button', { class: 'btn secondary', type: 'button', html: I.sliders + 'Refine preferences', onclick: () => { scrollTo({ top: 0, behavior: 'smooth' }); $('.pref')?.focus(); } })));
}
