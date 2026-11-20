// Film page (carried over from v8, restyled): facts, scores, where to watch, showtimes, rate, similar films.
import { S, h, I, rt, api, post, view, toast, sheet, setChoice, isWant, zip, showtimesSheet, section, shelf, posterCard, skelShelf, emit } from './core.js';
const RUNNING_DAYS = 120;                      // round 57: younger than this = probably still earning


export async function pageFilm(id, params) {
  view.innerHTML = '';
  view.append(h('div', { class: 'skel', style: 'height:260px;margin-bottom:16px' }));
  let d;
  try { d = await api(`/api/film/${id}`); } catch (e) { view.innerHTML = ''; view.append(h('div', { class: 'notice' }, e.message || 'No details for that film yet.')); return; }
  document.title = `${d.title} · Cinemack`;
  S.choices[d.id] = S.choices[d.id] ?? d.mine ?? '';
  view.innerHTML = '';
  const facts = [d.year, d.runtime ? rt(d.runtime) : null, d.runtime > 0 && d.runtime <= 40 ? 'Short film' : null, d.cert, (d.genres || []).slice(0, 3).join(', ')].filter(Boolean);
  const head = h('div', { class: 'detail-head' },
    h('div', { class: 'pl' }, d.poster ? h('img', { src: d.poster, alt: `Poster for ${d.title}`, width: 200, height: 300 }) : h('span', { class: 'ph' })),
    h('div', {}, h('h1', {}, d.title), h('div', { class: 'facts' }, facts.map(x => h('span', {}, x))),
      d.directors?.length ? h('div', { class: 'facts' }, 'Directed by ' + d.directors.join(', ')) : null));
  const scores = h('div', { class: 'scores' });
  const add = (k, v, tip) => scores.append(h('span', { class: 'score', title: tip }, h('b', {}, v), k));
  if (d.imdb?.rating) add('IMDb', Number(d.imdb.rating).toFixed(1), `${Number(d.imdb.votes || 0).toLocaleString()} votes`);
  if (d.tmdb_rating) add('TMDB', d.tmdb_rating, `${Number(d.tmdb_votes || 0).toLocaleString()} votes`);
  if (d.critics?.rt != null) add('Rotten Tomatoes', d.critics.rt + '%', 'critics positive');
  if (d.critics?.mc != null) add('Metacritic', d.critics.mc, 'out of 100');
  const want = h('button', { class: 'btn primary', type: 'button', 'data-want': d.id, 'aria-pressed': String(isWant(d.id)), onclick: () => setChoice(d, isWant(d.id) ? '' : 'want') });
  want._label = (on) => { want.innerHTML = on ? I.check + 'On your watchlist' : I.plus + 'Add to watchlist'; };
  want._label(isWant(d.id));
  const watched = h('button', { class: 'btn secondary', type: 'button', onclick: async (e) => {
    const b = e.currentTarget; b.disabled = true; b.classList.add('busy');
    try { await post('/api/v8/watched', { id: d.id }); b.innerHTML = I.check + 'Watched'; toast(`Added ${d.title} to your history.`); emit('cm:watched', { id: d.id }); }
    catch (er) { toast(er.message, null, true); } finally { b.disabled = false; b.classList.remove('busy'); } } }, 'Mark watched');
  const choice = (c, icon, label) => { const b = h('button', { class: 'btn secondary', type: 'button', 'data-choice-for': d.id, 'aria-pressed': String(S.choices[d.id] === c), html: icon + label,
    onclick: () => { setChoice(d, S.choices[d.id] === c ? '' : c).then(() => { document.querySelectorAll(`[data-choice-for="${d.id}"]`).forEach(x => x.setAttribute('aria-pressed', String(S.choices[d.id] === x.dataset.choice))); }); } }); b.dataset.choice = c; return b; };
  const rate = h('button', { class: 'btn ghost', type: 'button', html: I.star + 'Rate or review', onclick: () => reviewSheet(d) });
  const main = h('div', {});
  if (d.tagline) main.append(h('p', { class: 'muted', style: 'margin-bottom:10px' }, d.tagline));
  if (d.overview) {
    const ov = h('p', { class: 'overview clamp' }, d.overview);
    main.append(ov);
    if (d.overview.length > 260) main.append(h('button', { class: 'btn ghost sm', type: 'button', onclick: (e) => { ov.classList.toggle('clamp'); e.currentTarget.textContent = ov.classList.contains('clamp') ? 'Read more' : 'Show less'; } }, 'Read more'));
  }
  main.append(h('div', { class: 'actions' }, h('a', { class: 'btn outline', href: `#/ask?ask=${encodeURIComponent(`Tell me about ${d.title} (${d.year})`)}`, html: I.sparkle + 'Ask Cinemack about this film' }),
    d.links?.trailer ? h('a', { class: 'btn ghost', href: d.links.trailer.url, target: '_blank', rel: 'noopener' }, 'Watch the trailer') : null));
  if (d.cast?.length) main.append(h('h2', { style: 'margin:24px 0 12px' }, 'Cast'), h('div', { class: 'cast' }, d.cast.map(p =>
    h('div', { class: 'p' }, h('div', { class: 'ph' }, p.photo ? h('img', { src: p.photo, alt: '', loading: 'lazy', width: 72, height: 72 }) : null), h('div', {}, p.name), h('div', { class: 'ch' }, p.character || '')))));
  if (d.reception && (d.reception.praise?.length || d.reception.complaints?.length)) {
    const r = d.reception;
    main.append(h('h2', { style: 'margin:24px 0 12px' }, 'How it was received'),
      r.praise?.length ? h('p', {}, h('b', {}, 'Praised: '), r.praise.map(x => x.aspect || x.text || x).join(', ')) : null,
      r.complaints?.length ? h('p', { style: 'margin-top:6px' }, h('b', {}, 'Criticised: '), r.complaints.map(x => x.aspect || x.text || x).join(', ')) : null);
  }
  const side = h('aside', {});
  const p = d.links?.providers || {};
  const label = { flatrate: 'Stream', free: 'Free', ads: 'With ads', rent: 'Rent', buy: 'Buy' };
  const prov = Object.keys(label).filter(k => (p[k] || []).length);
  const where = h('h2', { style: 'margin:0 0 12px', id: 'where', tabindex: '-1' }, 'Where to watch (US)');
  side.append(where);
  const stBox = h('div', { class: 'st-box', hidden: true }); side.append(stBox);
  api(`/api/v8/theatres/${d.id}?zip=${encodeURIComponent(zip.get())}`).then(t => {
    if (!t.in_theatres) return;
    stBox.hidden = false;
    stBox.append(h('div', { class: 'row' }, h('span', { class: 'tag y' }, t.rerelease ? 'Back in theaters' : 'In theaters now')),
      h('button', { class: 'btn primary', type: 'button', id: 'showtimes-btn', html: I.ticket + 'Showtimes near you', onclick: () => showtimesSheet(d) }));
  }).catch(() => {});
  const mine = (S.me?.settings?.services || []);
  if (prov.length) side.append(h('div', { class: 'providers' }, prov.map(k => h('div', { class: 'row' }, h('span', { class: 'k' }, label[k]),
    p[k].slice(0, 6).map(n => h('span', { class: `tag${mine.some(s => n.toLowerCase().includes(s)) ? ' on' : ''}` }, n))))),
    h('p', { class: 'tiny faint', style: 'margin-top:8px' }, 'Listings from JustWatch via TMDB; they change often.'));
  else side.append(h('p', { class: 'muted small' }, 'No US streaming, rental or purchase listing on record.'));
  // round 57: a film out for less than RUNNING_DAYS is usually still earning - its reported gross is a running total
  const days = d.release_date ? (Date.now() - Date.parse(d.release_date)) / 864e5 : null;
  const running = days != null && days >= 0 && days < RUNNING_DAYS;
  if (d.money?.budget || d.money?.gross) {
    const m = (v) => v >= 1e9 ? `$${(v / 1e9).toFixed(2)} billion` : `$${(v / 1e6).toFixed(1)} million`;
    side.append(h('h2', { style: 'margin:24px 0 12px' }, 'Box office'), h('p', { class: 'small' }, d.money.budget ? `Budget ${m(d.money.budget)}. ` : '',
      d.money.gross ? (running ? `Worldwide gross so far ${m(d.money.gross)}.` : `Worldwide gross ${m(d.money.gross)}.`) : ''),
      running && d.money.gross ? h('p', { class: 'tiny faint' }, 'Still in cinemas or just released: a running total, not the final result.') : null);
  }
  const links = [d.imdb_url && ['IMDb', d.imdb_url], d.wiki && ['Wikipedia', d.wiki.url], ['TMDB', d.tmdb_url]].filter(Boolean);
  side.append(h('p', { class: 'small', style: 'margin-top:16px;display:flex;gap:14px' }, links.map(([t, u]) => h('a', { href: u, target: '_blank', rel: 'noopener' }, t))));
  view.append(h('div', { class: 'detail-hero' }, h('div', { class: 'bd', style: d.backdrop ? `background-image:url("${d.backdrop}")` : '' })), head, scores,
    h('div', { class: 'actions' }, want, watched, choice('liked', I.up, 'Liked it'), choice('not', I.down, 'Not for me'), rate), h('div', { class: 'detail-body' }, main, side));
  if (params?.get?.('go') === 'watch') requestAnimationFrame(() => { where.scrollIntoView({ block: 'center' }); where.focus({ preventScroll: true }); });
  // v8.1 round 47: Guardian articles for this film (stored first, a live lookup only when none are stored). The server
  // answers 404 while GUARDIAN_LIVE is off, and then nothing is shown. Text nodes only; every link opens the Guardian.
  api(`/api/v8/film/${d.id}/press`).then(p => {
    const arts = (p.articles || []).filter(a => a.url && /^https:\/\/(www\.)?theguardian\.com\//.test(a.url));
    if (!arts.length) return;
    const stars = (n) => n ? ` \u00b7 ${'\u2605'.repeat(Math.round(n))}${'\u2606'.repeat(5 - Math.round(n))}` : '';
    main.append(h('section', { class: 'press', 'aria-label': 'In the Guardian' }, h('h2', { style: 'margin:24px 0 8px' }, 'In the Guardian'),
      h('ul', { class: 'press-list' }, arts.slice(0, 6).map(a => h('li', {}, h('a', { href: a.url, target: '_blank', rel: 'noopener' }, a.headline || 'Article'),
        h('div', { class: 'small' }, [a.kind === 'review' ? 'Review' : 'Article', a.published, a.byline].filter(Boolean).join(' \u00b7 ') + stars(a.stars)),
        a.summary ? h('p', { class: 'small' }, a.summary) : null))),
      h('p', { class: 'small' }, p.credit || 'From the Guardian')));
  }).catch(() => {});
  // v8.1 round 50: follow the director or the cast (FOLLOW_PEOPLE). The server answers 404 while the switch is off,
  // and then nothing is shown. Following is private; Home then shows their next films and stored Guardian pieces.
  api(`/api/v8/film/${d.id}/people`).then(r => {
    const ppl = (r.people || []).slice(0, 9);
    if (!ppl.length) return;
    const row = (p) => {
      const b = h('button', { class: 'btn secondary sm', type: 'button' });
      const paint = () => { b.setAttribute('aria-pressed', String(!!p.following)); b.setAttribute('aria-label', `${p.following ? 'Unfollow' : 'Follow'} ${p.name}`);
        b.innerHTML = p.following ? I.check : I.plus; b.append(h('span', {}, p.following ? 'Following' : 'Follow')); };
      paint();
      b.addEventListener('click', async () => {
        b.disabled = true;
        try { const j = await post('/api/v8/follows', { person_id: p.person_id, on: !p.following }); p.following = !!j.following; paint();
          toast(p.following ? `Following ${p.name}. Their new films show on Home.` : `Stopped following ${p.name}.`); emit('cm:follow-person', { id: p.person_id, on: p.following }); }
        catch (e) { toast(e.message, null, true); } finally { b.disabled = false; }
      });
      return h('div', { class: 'fp' }, h('div', { class: 'fp-n' }, h('b', {}, p.name), h('span', { class: 'small muted' }, p.role || '')), b);
    };
    main.append(h('section', { class: 'people-follow', 'aria-label': 'Follow the people behind it' }, h('h2', { style: 'margin:24px 0 4px' }, 'Follow the people behind it'),
      h('p', { class: 'small muted', style: 'margin:0 0 12px' }, 'See their next films on Home. Only you see who you follow.'),
      h('div', { class: 'follow-list' }, ppl.map(row))));
  }).catch(() => {});
  // v8.1 round 48: the box-office forecast panel. The server answers 404 while FORECAST_PANEL is off (and for films
  // with no stored forecast); then nothing is loaded and nothing is shown. The panel code is fetched only on a 200.
  // Round 57: a released film may also have an update made after its first week (first reviews and ratings): shown
  // first, with the before-release forecast folded underneath. A film still earning gets a "running total" note.
  Promise.allSettled([api(`/api/v8/forecast/movie:${d.id}?horizon=update`), api(`/api/v8/forecast/movie:${d.id}?horizon=d7`)]).then(async ([u, b]) => {
    const upd = u.status === 'fulfilled' ? u.value : null, pre = b.status === 'fulfilled' ? b.value : null;
    if (!upd && !pre) return;
    const [fp, i18n, en] = await Promise.all([import('./forecast_panel.js'), import('./i18n.js'), api('/api/v8/i18n/en.json')]);
    i18n.configure('en', { en });               // the words come through api() so the access code travels with the request
    const box = fp.renderPanel(document, upd || pre);
    if (!box) return;
    if (running && d.money?.gross) {
      const m = (v) => v >= 1e9 ? `$${(v / 1e9).toFixed(2)} billion` : `$${(v / 1e6).toFixed(1)} million`;
      box.insertBefore(h('p', { class: 'fcast-note fcast-running' }, fp.runningNote(m(d.money.gross))), box.children[1] || null);
    }
    if (upd && pre) {
      const old = fp.renderPanel(document, pre);
      if (old) box.append(h('details', { class: 'fcast-before' }, h('summary', {}, fp.beforeLabel()), old));
    }
    main.append(box);
  }).catch(() => {});
  // v8.1 round 57: rent / buy prices (DEALS; 404 while off = nothing shown). Each price says where and when it was
  // checked; nothing is compared or called a deal.
  api(`/api/v8/film/${d.id}/prices`).then(p => {
    if (!(p?.rent?.length || p?.buy?.length)) return;
    const tag = (x) => { const txt = `${x.service}${x.format ? ' ' + x.format : ''} $${Number(x.price).toFixed(2)}`;
      return x.url ? h('a', { class: 'tag', href: x.url, target: '_blank', rel: 'noopener noreferrer' }, txt) : h('span', { class: 'tag' }, txt); };
    const row = (k, label) => p[k]?.length ? h('div', { class: 'row' }, h('span', { class: 'k' }, label), p[k].slice(0, 4).map(tag)) : null;
    side.append(h('section', { class: 'prices', 'aria-label': 'Rent or buy' }, h('h2', { style: 'margin:24px 0 12px' }, 'Rent or buy (US)'),
      h('div', { class: 'providers' }, row('rent', 'Rent'), row('buy', 'Buy')),
      h('p', { class: 'tiny faint', style: 'margin-top:8px' }, `Checked ${p.checked || 'recently'}. ${p.credit || ''}`)));
  }).catch(() => {});
  const sim = h('div', {}, skelShelf()); view.append(sim);
  try { const s = await api(`/api/v8/similar/${d.id}`); sim.innerHTML = ''; if (s.items?.length) sim.append(section('If you like this', { sub: 'MovieLens ratings' }, shelf(s.items, 'If you like this'))); }
  catch { sim.innerHTML = ''; }
}

export function reviewSheet(d, done) {
  sheet(`Rate ${d.title}`, (b, dlg) => {
    let val = d.myRating || 0;
    const stars = h('div', { class: 'stars', role: 'radiogroup', 'aria-label': 'Rating out of 5', style: 'gap:2px' });
    const paint = () => [...stars.children].forEach((x, j) => { x.style.color = j < val ? 'var(--accent)' : 'var(--s4)'; x.setAttribute('aria-checked', String(j + 1 === val)); });
    for (let i = 1; i <= 5; i++) stars.append(h('button', { type: 'button', role: 'radio', class: 'iconbtn', 'aria-checked': 'false', 'aria-label': `${i} star${i > 1 ? 's' : ''}`, html: I.star, style: 'width:44px;height:44px', onclick: () => { val = i; paint(); } }));
    paint();
    const txt = h('textarea', { class: 'input', id: 'rv-text', maxlength: '4000' }, d.myReview || '');
    b.append(h('div', { class: 'field' }, h('span', { class: 'lab' }, 'Your rating'), stars),
      h('div', { class: 'field' }, h('label', { for: 'rv-text' }, 'Review (optional)'), txt,
        h('span', { class: 'hint' }, 'Private unless you share ratings or reviews with friends (Profile, Settings, Privacy).')),
      h('button', { class: 'btn primary', type: 'button', onclick: async () => {
        try { await post('/api/v8/review', { id: d.id, rating: val || null, text: txt.value }); dlg.close(); toast('Saved your rating.'); emit('cm:review', { id: d.id }); done?.(); } catch (e) { toast(e.message, null, true); }
      } }, 'Save rating'));
  });
}
