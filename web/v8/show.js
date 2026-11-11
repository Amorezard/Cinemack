// TV show page (10 Nov 2026, round 21): facts, IMDb and TMDB ratings, where to watch, the next episode, every season's
// episodes (date, title, synopsis, still, runtime, IMDb + TMDB ratings, finale), the best-rated episodes, cast and
// the trailer. Follow, alerts and progress work from here; "Watched up to here" records progress on an episode.
import { $, $$, S, h, I, api, post, view, toast, emit, fmtDate, rt, posterImg, section, CW, progressSheet, empty } from './core.js';

const today = () => new Date().toISOString().slice(0, 10);
const stars = (r, src, votes) => h('span', { class: 'shr' }, h('b', {}, `★ ${Number(r).toFixed(1)}`), h('span', {}, ` ${src}${votes ? ` · ${Number(votes).toLocaleString()} votes` : ''}`));

export async function pageShow(id) {
  view.innerHTML = '';
  view.append(h('div', { class: 'skel', style: 'height:300px;margin-bottom:16px' }));
  let d;
  try { d = await api(`/api/v8/show/${id}`); } catch (e) { view.innerHTML = ''; view.append(empty('No details for this show yet', e.message, 'reel')); return; }
  document.title = `${d.title} · Cinemack`;
  view.innerHTML = '';
  const years = d.year ? (d.end_year && d.end_year !== d.year ? `${d.year}–${d.end_year}` : d.status === 'Returning Series' ? `${d.year}–` : String(d.year)) : null;
  const facts = [years, d.network, d.cert, d.genres, d.episode_runtime ? `${d.episode_runtime}-min episodes` : null,
    d.n_seasons ? `${d.n_seasons} season${d.n_seasons === 1 ? '' : 's'}${d.n_episodes ? `, ${d.n_episodes} episodes` : ''}` : null,
    d.status === 'Ended' ? 'Ended' : d.status === 'Canceled' ? 'Cancelled' : d.status === 'Returning Series' ? 'Still running' : null].filter(Boolean);
  const fol = h('button', { class: 'btn primary', type: 'button' });
  const paint = () => { fol.innerHTML = (d.following ? I.check : I.plus) + `<span>${d.following ? 'Following' : 'Follow show'}</span>`; fol.className = `btn ${d.following ? 'secondary' : 'primary'}`; fol.setAttribute('aria-pressed', String(d.following)); };
  paint();
  fol.addEventListener('click', async () => {
    try { await post('/api/v8/tv/follow', { id: d.id, on: !d.following }); d.following = !d.following; paint();
      toast(d.following ? `Following ${d.title}: it is on your Watchlist, and the bell tells you about new episodes and seasons.` : `Stopped following ${d.title}.`); emit('cm:follow', { id: d.id, on: d.following }); }
    catch (e) { toast(e.message, null, true); }
  });
  const track = h('button', { class: 'btn secondary', type: 'button', html: I.play + '<span>Track progress</span>', onclick: async () => {
    const items = await CW.load(true).catch(() => []); progressSheet(items.find(x => x.id === d.id) || { id: d.id, title: d.title, seasons_out: d.latest_season || 0, last: null }, () => pageShow(id)); } });
  const ask = h('a', { class: 'btn ghost', href: `#/ask?ask=${encodeURIComponent(`Tell me about the TV series ${d.title}${d.year ? ` (${d.year})` : ''}: is it worth watching?`)}`, html: I.bubble + 'Ask about it' });
  const trailer = d.trailer ? h('a', { class: 'btn ghost', href: d.trailer.url, target: '_blank', rel: 'noopener noreferrer', html: I.play + 'Trailer', 'aria-label': `Trailer for ${d.title} on YouTube (opens a new tab)` }) : null;
  const ratings = h('div', { class: 'sh-ratings' }, d.imdb ? stars(d.imdb.rating, 'IMDb', d.imdb.votes) : null, d.tmdb ? stars(d.tmdb.rating, 'TMDB', d.tmdb.votes) : null);
  const hero = h('section', { class: 'sh-hero' + (d.backdrop ? '' : ' nobd') },
    d.backdrop ? h('img', { class: 'sh-bd', src: d.backdrop.replace('/w780/', '/w1280/'), alt: '', 'aria-hidden': 'true' }) : null,
    h('div', { class: 'sh-in' }, h('div', { class: 'sh-pp' }, posterImg(d, 'poster_lg', 220)),
      h('div', { class: 'sh-tx' }, h('span', { class: 'tag tvk' }, 'TV series'), h('h1', {}, d.title), h('div', { class: 'facts' }, facts.map(x => h('span', {}, x))), ratings,
        d.tagline ? h('p', { class: 'sh-tag' }, d.tagline) : null,
        h('div', { class: 'actions' }, fol, track, trailer, ask))));
  view.append(hero);
  // next episode, where to watch, overview
  const top = h('div', { class: 'sh-grid' });
  const left = h('div', {}), right = h('div', {});
  if (d.next_episode) {
    const n = d.next_episode;
    left.append(h('div', { class: 'sh-next' }, h('span', { class: 'ic', html: I.cal }), h('div', {}, h('b', {}, `Next episode: S${n.season} E${n.episode}${n.title ? ` · ${n.title}` : ''}`),
      h('div', { class: 'small muted' }, `Airs ${fmtDate(n.date, { weekday: 'long', month: 'long', day: 'numeric' })}${n.airstamp ? ` at ${new Date(n.airstamp).toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' })} your time` : ''} (${n.src === 'TVmaze' ? 'TVmaze' : n.airstamp ? 'date from TMDB, time from TVmaze' : 'US date from TMDB'})`))));
  }
  if (d.tvmaze?.airs) left.append(h('p', { class: 'sh-airs' }, h('span', { class: 'ic', html: I.tv }), h('span', {}, d.tvmaze.airs)));
  if (d.overview) left.append(h('p', { class: 'sh-ov' }, d.overview));
  if (d.creators?.length) left.append(h('p', { class: 'small muted' }, `Created by ${d.creators.join(', ')}`));
  const pv = d.providers || {};
  const stream = [...(pv.flatrate || []), ...(pv.free || []), ...(pv.ads || [])], buy = [...new Set([...(pv.buy || []), ...(pv.rent || [])])];
  right.append(h('div', { class: 'panel sh-where' }, h('h2', {}, 'Where to watch'),
    stream.length ? h('div', { class: 'chips' }, stream.map(p => h('span', { class: 'chip on' }, p))) : h('p', { class: 'muted small' }, 'Not on a US subscription service that TMDB lists.'),
    buy.length ? h('p', { class: 'small muted', style: 'margin-top:8px' }, `Buy or rent: ${buy.slice(0, 4).join(', ')}`) : null,
    h('p', { class: 'tiny faint', style: 'margin-top:8px' }, 'US availability from TMDB (JustWatch).')));
  top.append(left, right);
  view.append(top);
  // seasons and episodes
  const seasons = d.seasons || [];
  if (seasons.length) {
    const pills = h('div', { class: 'pills sh-seasons', role: 'tablist', 'aria-label': 'Seasons' });
    const list = h('div', { class: 'eps', role: 'tabpanel', 'aria-live': 'polite' });
    const cur = (d.latest_season && seasons.some(s => s.number === d.latest_season)) ? d.latest_season : seasons[seasons.length - 1].number;
    const choose = async (n) => {
      $$('button', pills).forEach(b => { const on = Number(b.dataset.n) === n; b.setAttribute('aria-selected', String(on)); b.tabIndex = on ? 0 : -1; });
      const s = seasons.find(x => x.number === n);
      list.innerHTML = ''; list.append(h('div', { class: 'skel', style: 'height:96px' }), h('div', { class: 'skel', style: 'height:96px;margin-top:10px' }));
      let eps = [];
      try { eps = (await api(`/api/v8/show/${d.id}/season/${n}`)).episodes || []; } catch (e) { list.innerHTML = ''; list.append(h('p', { class: 'err' }, e.message)); return; }
      list.innerHTML = '';
      list.append(h('div', { class: 'eps-h' }, h('b', {}, s?.name || `Season ${n}`), s?.date ? h('span', { class: 'muted small' }, ` · ${s.date <= today() ? 'premiered' : 'premieres'} ${fmtDate(s.date)}`) : null,
        s?.imdb_avg ? h('span', { class: 'muted small' }, ` · average ★ ${s.imdb_avg} on IMDb`) : null));
      if (!eps.length) { list.append(h('p', { class: 'muted' }, d.has_episodes ? 'No episode list for this season yet.' : 'Episode lists are still being loaded on the server.')); return; }
      for (const e of eps) list.append(epRow(d, e));
    };
    seasons.forEach(s => pills.append(h('button', { type: 'button', role: 'tab', 'data-n': s.number, 'aria-selected': 'false', onclick: () => choose(s.number),
      onkeydown: (ev) => { const i = seasons.findIndex(x => x.number === s.number) + ({ ArrowRight: 1, ArrowLeft: -1 }[ev.key] || 0); if (ev.key in { ArrowRight: 1, ArrowLeft: 1 } && seasons[i]) { ev.preventDefault(); choose(seasons[i].number).then(() => $(`button[data-n="${seasons[i].number}"]`, pills)?.focus()); } } },
      `Season ${s.number}`, s.imdb_avg ? h('span', { class: 'avg' }, ` ★${s.imdb_avg}`) : null)));
    view.append(section('Episodes', { sub: 'Dates are US air dates from TMDB; ratings from IMDb and TMDB voters' }, pills, list));
    choose(cur);
  }
  if (d.best_episodes?.length) view.append(section('Highest-rated episodes', { sub: 'IMDb, episodes with 300+ votes' },
    h('ol', { class: 'best-eps' }, d.best_episodes.map(e => h('li', {}, h('span', { class: 'se' }, `S${e.season} E${e.episode}`), h('span', { class: 't' }, e.title || ''),
      h('span', { class: 'r' }, `★ ${e.imdb.rating.toFixed(1)}`, h('span', { class: 'faint' }, ` · ${e.imdb.votes.toLocaleString()}`)))))));
  if (d.cast?.length) view.append(section('Cast', {}, h('div', { class: 'cast' }, d.cast.map(c => h('div', { class: 'cm' },
    h('span', { class: 'av' }, c.photo ? h('img', { src: c.photo, alt: '', loading: 'lazy', width: 72, height: 72 }) : (c.name || '?')[0]),
    h('b', {}, c.name), c.character ? h('span', { class: 'small muted' }, c.character) : null)))));
  view.append(h('p', { class: 'tiny faint', style: 'margin-top:20px' }, 'Show and episode data from TMDB; IMDb ratings from the IMDb datasets (non-commercial use).',
    d.tvmaze ? [' Schedule, air times and some episode summaries: ', h('a', { href: d.tvmaze.url || d.tvmaze.credit?.url || 'https://www.tvmaze.com', target: '_blank', rel: 'noopener noreferrer' }, 'TVmaze'),
      ' (', h('a', { href: 'https://creativecommons.org/licenses/by-sa/4.0/', target: '_blank', rel: 'noopener noreferrer' }, 'CC BY-SA 4.0'), ').'] : null));
}

function epRow(d, e) {
  const aired = e.date && e.date <= today();
  const meta = [e.date ? (aired ? fmtDate(e.date) : `Airs ${fmtDate(e.date, { weekday: 'short', month: 'short', day: 'numeric' })}`) : 'Date not announced', e.runtime ? rt(e.runtime) : null].filter(Boolean);
  const r = h('div', { class: 'ep-r' }, e.imdb ? h('span', { title: `${e.imdb.votes.toLocaleString()} IMDb votes` }, h('b', {}, `★ ${e.imdb.rating.toFixed(1)}`), ' IMDb') : null,
    e.tmdb ? h('span', { title: `${e.tmdb.votes} TMDB votes` }, `★ ${e.tmdb.rating.toFixed(1)} TMDB`) : null);
  const btn = aired ? h('button', { class: 'btn ghost sm', type: 'button', html: I.check + `<span>Watched up to here</span>`,
    'aria-label': `I've watched up to season ${e.season} episode ${e.episode}`, onclick: async () => {
      if (await CW.save({ id: d.id, season: e.season, episode: e.episode }, `${d.title}: progress saved at S${e.season} E${e.episode}.`)) emit('cm:progress'); } }) : null;
  return h('article', { class: 'ep' + (aired ? '' : ' future') },
    h('div', { class: 'ep-still' }, e.still ? h('img', { src: e.still, alt: '', loading: 'lazy', width: 300, height: 169 }) : h('span', { class: 'ph', html: I.tv })),
    h('div', { class: 'ep-in' }, h('div', { class: 'ep-t' }, h('span', { class: 'se' }, `E${e.episode}`), h('b', {}, e.title || `Episode ${e.episode}`),
      e.type === 'finale' ? h('span', { class: 'tag y' }, 'Finale') : e.type === 'mid_season' ? h('span', { class: 'tag' }, 'Mid-season finale') : null),
      h('div', { class: 'small muted' }, meta.join(' · ')), r, e.overview ? h('p', { class: 'ep-ov' }, e.overview, e.overview_src ? h('span', { class: 'tiny faint' }, ` (${e.overview_src})`) : null) : null),
    btn ? h('div', { class: 'ep-a' }, btn) : null);
}
