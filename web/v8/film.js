// Film page (carried over from v8, restyled): facts, scores, where to watch, showtimes, rate, similar films.
import { S, h, I, rt, api, post, view, toast, sheet, setChoice, isWant, zip, showtimesSheet, section, shelf, posterCard, skelShelf, emit } from './core.js';


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
  if (d.money?.budget || d.money?.gross) {
    const m = (v) => v >= 1e9 ? `$${(v / 1e9).toFixed(2)} billion` : `$${(v / 1e6).toFixed(1)} million`;
    side.append(h('h2', { style: 'margin:24px 0 12px' }, 'Box office'), h('p', { class: 'small' }, d.money.budget ? `Budget ${m(d.money.budget)}. ` : '', d.money.gross ? `Worldwide gross ${m(d.money.gross)}.` : ''));
  }
  const links = [d.imdb_url && ['IMDb', d.imdb_url], d.wiki && ['Wikipedia', d.wiki.url], ['TMDB', d.tmdb_url]].filter(Boolean);
  side.append(h('p', { class: 'small', style: 'margin-top:16px;display:flex;gap:14px' }, links.map(([t, u]) => h('a', { href: u, target: '_blank', rel: 'noopener' }, t))));
  view.append(h('div', { class: 'detail-hero' }, h('div', { class: 'bd', style: d.backdrop ? `background-image:url("${d.backdrop}")` : '' })), head, scores,
    h('div', { class: 'actions' }, want, watched, choice('liked', I.up, 'Liked it'), choice('not', I.down, 'Not for me'), rate), h('div', { class: 'detail-body' }, main, side));
  if (params?.get?.('go') === 'watch') requestAnimationFrame(() => { where.scrollIntoView({ block: 'center' }); where.focus({ preventScroll: true }); });
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
