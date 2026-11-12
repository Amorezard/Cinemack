// Profile (10 Nov 2026 redesign): cinematic header with real statistics, an Overview dashboard (Continue Watching,
// Recent Activity, Your Taste, Favorite Films), Ratings, Reviews, Watch History, Friends, and a settings area with its
// own navigation. Every number comes from stored data (/api/v8/me/summary); a new person sees zeros and next steps.
// The banner is a backdrop from the person's own top-rated or most recent film, or plain default artwork.
import { $, $$, S, h, I, api, post, view, store, toast, sheet, emit, on, rt, ago, fmtDate, shortGenre, posterImg, posterCard, shelf, section, panel,
  empty, CW, continueRow, displayName, authSheet, signOut, wantButton, hdrs } from './core.js';
import { reviewSheet } from './film.js';

// 12 Nov (round 36): Trakt's own export (trakt.tv Settings > Data > Export, free): JSON files, several at once.
// Works without "Connect Trakt" (which needs a Trakt app key on the server).
function traktFiles(res, after) {
  const id = 'up-trakt-' + Math.random().toString(36).slice(2, 7);
  const inp = h('input', { type: 'file', id, accept: '.json,application/json', multiple: true, class: 'input' });
  inp.addEventListener('change', async () => {
    const files = [...inp.files]; if (!files.length) return;
    res.innerHTML = ''; res.append(h('div', { class: 'notice' }, `Importing ${files.length} Trakt file${files.length > 1 ? 's' : ''}…`));
    const t = { added: 0, duplicates: 0, issues: 0, ratings: 0, watchlist: 0 }, skipped = [], errs = [];
    for (const f of files) {
      try {
        const j = await post('/api/v8/import', { kind: 'trakt', name: f.name, text: await f.text() });
        t.added += j.added || 0; t.duplicates += j.duplicates || 0; t.issues += (j.issues || []).length; t.ratings += j.ratings || 0; t.watchlist += j.watchlist || 0;
        if (!j.rows && !j.ratings && !j.watchlist) skipped.push(f.name);
      } catch (e) { errs.push(`${f.name}: ${e.message}`); }
    }
    res.innerHTML = '';
    res.append(h('div', { class: errs.length ? 'notice warn' : 'notice' }, h('b', {}, 'Trakt imported. '),
      `${t.added} views added, ${t.duplicates} already there, ${t.ratings} film ratings, ${t.watchlist} watchlist items` + (t.issues ? `, ${t.issues} without a TMDB id` : '') + '.'
      + (skipped.length ? ` Nothing to import in ${skipped.join(', ')}.` : '') + (errs.length ? ' ' + errs.join(' ') : '')));
    if (after) after();
  });
  return h('div', { class: 'set-card' }, h('h3', {}, h('label', { for: id }, 'Trakt export')),
    h('p', { class: 'sub' }, 'Trakt: Settings, Data, Export now (free). Trakt emails a ZIP: unzip it and choose the .json files here; you can select them all at once. Watch history, film ratings and your watchlist come in.'), inp);
}

// 12 Nov (round 38): Connect Trakt with a code (Trakt only returns to https sites; this one is http): the person
// enters the code at trakt.tv/activate and this page checks every few seconds, then imports.
function traktConnect(box, after) {
  const btn = h('button', { class: 'btn primary sm', type: 'button' }, 'Connect Trakt');
  const out = h('div', { 'aria-live': 'polite' });
  btn.addEventListener('click', async () => {
    btn.classList.add('busy');
    let d;
    try { d = await post('/api/v8/trakt/device/start', {}); } catch (e) { toast(e.message, null, true); btn.classList.remove('busy'); return; }
    btn.classList.remove('busy'); btn.hidden = true; out.innerHTML = '';
    out.append(h('div', { class: 'notice' }, 'Go to ', h('a', { href: d.url, target: '_blank', rel: 'noopener' }, d.url.replace(/^https?:\/\//, '')), ' and enter this code:',
      h('div', { class: 'trakt-code' }, d.user_code), h('span', { class: 'small muted' }, 'This page checks every few seconds, then imports your history, ratings and watchlist.')));
    const until = Date.now() + d.expires_in * 1000; let wait = d.interval * 1000;
    const fail = (msg) => { out.innerHTML = ''; out.append(h('div', { class: 'notice warn' }, msg, ' Press Connect Trakt to try again.')); btn.hidden = false; };
    const tick = async () => {
      if (!document.body.contains(out)) return;
      if (Date.now() > until) return fail('The code expired.');
      let r; try { r = await post('/api/v8/trakt/device/poll', {}); } catch { r = { status: 'pending' }; }
      if (r.status === 'connected') {
        const s = r.sync || {}, hs = s.history || {};
        out.innerHTML = ''; out.append(h('div', { class: 'notice' }, h('b', {}, 'Trakt connected. '), `${hs.added ?? 0} views added, ${s.ratings ?? 0} film ratings, ${s.watchlist ?? 0} watchlist items.` + (s.error ? ` Import problem: ${s.error}` : '')));
        if (after) after(); return;
      }
      const msg = { denied: 'You declined on Trakt.', expired: 'The code expired.', invalid: 'Trakt did not recognise the code.', used: 'That code was already used.',
        unauthorized: 'Trakt refused the sign-in. The server may also need the Trakt client secret (TRAKT_CLIENT_SECRET in keys.env).' }[r.status];
      if (msg) return fail(msg);
      if (r.status === 'slow_down') wait += 1000;
      setTimeout(tick, wait);
    };
    setTimeout(tick, wait);
  });
  box.append(btn, out);
}

// 12 Nov (round 38): the onboarding taste (kinds of film, kinds of show), editable in Settings > Movie and TV preferences
function tasteCard(me) {
  const card = h('div', { class: 'set-card' }, h('h3', {}, 'Your taste'),
    h('p', { class: 'sub' }, 'Kinds of films and shows you love or skip. They steer recommendations and Pick Tonight; nothing is hidden from search.'));
  const box = h('div', {}, h('p', { class: 'muted small' }, 'Loading…'));
  card.append(box);
  (async () => {
    let f, t;
    try { [f, t] = await Promise.all([api('/api/onboarding'), api('/api/v8/onboarding/tv')]); }
    catch (e) { box.innerHTML = ''; box.append(h('p', { class: 'err' }, e.message)); return; }
    const mine = (me && me.groups) || (S.me && S.me.groups) || [];
    const fl = new Set(mine.filter(k => !String(k).startsWith('-'))), fa = new Set(mine.filter(k => String(k).startsWith('-')).map(k => String(k).slice(1)));
    const tl = new Set((t.picked && t.picked.groups) || []), ta = new Set((t.picked && t.picked.avoid) || []);
    const rows = (groups, L, A) => h('div', { class: 'taste-rows' }, groups.map(g => {
      const love = h('button', { class: 'chip sm', type: 'button' }, 'Love it');
      const skip = h('button', { class: 'chip sm', type: 'button' }, 'Not for me');
      const sync = () => { love.setAttribute('aria-pressed', String(L.has(g.key))); skip.setAttribute('aria-pressed', String(A.has(g.key))); };
      love.addEventListener('click', () => { A.delete(g.key); L.has(g.key) ? L.delete(g.key) : L.add(g.key); sync(); });
      skip.addEventListener('click', () => { L.delete(g.key); A.has(g.key) ? A.delete(g.key) : A.add(g.key); sync(); });
      sync();
      return h('div', { class: 'taste-row' }, h('span', {}, g.label), h('div', { class: 'tg-a', role: 'group', 'aria-label': g.label }, love, skip));
    }));
    box.innerHTML = '';
    box.append(h('h4', {}, 'Films'), rows(f.groups || [], fl, fa), h('h4', {}, 'TV shows'), rows(t.groups || [], tl, ta),
      h('div', { class: 'taste-save' }, h('button', { class: 'btn primary', type: 'button', onclick: async (e) => {
        const b = e.currentTarget; b.classList.add('busy');
        try {
          S.me = await post('/api/onboarding', { groups: [...fl], avoid: [...fa] });
          await post('/api/v8/onboarding/tv/groups', { groups: [...tl], avoid: [...ta] });
          emit('cm:me'); toast('Taste saved.');
        } catch (er) { toast(er.message, null, true); } finally { b.classList.remove('busy'); }
      } }, 'Save taste'), h('a', { class: 'btn ghost', href: '#/join?step=taste' }, 'Mark films and shows you know')));
  })();
  return card;
}

const TABS = [['overview', 'Overview'], ['reviews', 'Reviews'], ['history', 'Watch History'], ['friends', 'Friends']];   // 12 Nov: Ratings merged into Reviews
const SETS = [['profile', 'Profile and appearance', I.user], ['services', 'Streaming services', I.monitor], ['preferences', 'Movie and TV preferences', I.sliders],
  ['chat', 'Chat and memory', I.bubble], ['notifications', 'Notifications', I.bell], ['privacy', 'Privacy', I.lock], ['imports', 'Imports and connections', I.upload], ['account', 'Account and security', I.shield]];
const SRC = { netflix: 'Netflix', letterboxd: 'Letterboxd', trakt: 'Trakt', cinemack: 'Cinemack', you: 'You' };

export async function pageProfile(params) {
  view.innerHTML = '';
  view.append(h('div', { class: 'skel', style: 'height:260px;border-radius:20px' }));
  let me, p, sum;
  try {
    [me, p, sum] = await Promise.all([api('/api/me'), api('/api/v8/profile'), api('/api/v8/me/summary?activity=20')]);
    S.me = me;
  } catch (e) { view.innerHTML = ''; view.append(h('div', { class: 'notice warn' }, e.message)); return; }
  view.innerHTML = '';
  const ctx = { me, p, sum, account: !!me?.account };
  view.append(header(ctx));
  const tabs = h('div', { class: 'tabs', role: 'tablist', 'aria-label': 'Profile sections' });
  const gear = h('button', { class: 'btn secondary sm set', type: 'button', 'aria-pressed': 'false', 'aria-label': 'Settings', html: I.gear + '<span>Settings</span>', onclick: () => show('settings') });
  const body = h('div', { role: 'tabpanel', id: 'pf-panel' });
  view.append(h('div', { class: 'pf-tabs' }, tabs, gear), body);
  TABS.forEach(([k, l]) => tabs.append(h('button', { type: 'button', role: 'tab', 'data-k': k, 'aria-controls': 'pf-panel', onclick: () => show(k) }, l,
    k === 'ratings' && sum.stats.ratings ? h('span', { class: 'count' }, String(sum.stats.ratings)) : null, k === 'reviews' && (sum.stats.ratings || sum.stats.reviews) ? h('span', { class: 'count' }, String(Math.max(sum.stats.ratings || 0, sum.stats.reviews || 0))) : null)));
  function show(k, sec) {
    $$('[role=tab]', tabs).forEach(b => b.setAttribute('aria-selected', String(b.dataset.k === k)));
    gear.setAttribute('aria-pressed', String(k === 'settings'));
    history.replaceState(null, '', `#/profile${k === 'overview' ? '' : `?tab=${k}`}${sec ? `&sec=${sec}` : ''}`);
    body.innerHTML = '';
    (k === 'settings' ? (b) => settings(b, ctx, sec) : PANES[k])(body, ctx);
  }
  const tr = params.get('trakt'); if (tr) toast({ connected: 'Trakt connected and imported.', failed: "Trakt sign-in didn't work. Try again.", cancelled: 'Trakt sign-in cancelled.' }[tr] || '');
  if (params.get('tab') === 'ratings') params.set('tab', 'reviews');          // 12 Nov: old links to the Ratings tab
  const t0 = params.get('tab') === 'settings' || tr ? 'settings' : (TABS.some(t => t[0] === params.get('tab')) ? params.get('tab') : 'overview');
  show(t0, tr ? 'imports' : params.get('sec'));
  on('cm:progress', (items) => { const box = $('#pf-cw'); if (box) box.replaceWith(cwPanel(items)); }, body);
}

// ---------------------------------------------------------------- header
function header({ me, p, sum, account }) {
  const name = displayName(me) || 'Guest';
  const s = sum.stats;
  const banner = h('div', { class: `pf-banner${sum.banner ? '' : ' def'}`, style: sum.banner?.url ? `background-image:url("${sum.banner.url}")` : null });
  if (sum.banner?.custom) loadBanner(banner);                  // round 27: an uploaded banner (fetched with the page's headers)
  const acts = h('div', { class: 'acts' }, h('button', { class: 'btn secondary sm', type: 'button', html: I.edit + 'Edit profile', onclick: () => editSheet(me) }));
  if (account && p.code) acts.append(h('button', { class: 'btn secondary sm', type: 'button', html: I.share + 'Share friend code', onclick: async () => {
    const text = `Add me on Cinemack: my friend code is ${p.code}`;
    try { if (navigator.share) await navigator.share({ text }); else { await navigator.clipboard.writeText(text); toast('Friend code copied.'); } } catch { /* cancelled */ } } }));
  if (!account) acts.append(h('button', { class: 'btn primary sm', type: 'button', onclick: () => authSheet('up') }, 'Create account'), h('button', { class: 'btn secondary sm', type: 'button', onclick: () => authSheet('in') }, 'Sign in'));
  const sub = account ? `@${me.account.username}` : 'Guest · saved in this browser only';
  const line = s.films ? `${s.films} film${s.films === 1 ? '' : 's'} watched` : 'Nothing watched yet';
  const tile = (icon, n, l, title) => h('div', { class: 'stat', title }, h('span', { html: icon }), h('b', {}, n), h('span', {}, l));
  const stats = h('div', { class: 'pf-stats', role: 'list', 'aria-label': 'Your statistics' },
    [tile(I.film, s.films, 'Films watched'), tile(I.tv, s.shows, 'Shows watched'), tile(I.star, s.ratings, 'Ratings'), tile(I.edit, s.reviews, 'Reviews'),
      tile(I.bookmark, s.watchlist, 'On watchlist'), tile(I.chart, s.avg_rating != null ? s.avg_rating.toFixed(1) : '—', 'Avg rating', s.avg_rating == null ? 'Rate a film to see this' : 'Average of your star ratings')]
      .map(x => { x.setAttribute('role', 'listitem'); return x; }));
  return h('section', { class: 'pf-head', 'aria-label': 'Profile' }, banner,
    h('div', { class: 'pf-in' }, h('div', { class: 'pf-id' }, h('span', { class: 'pf-av', 'aria-hidden': 'true' }, name[0].toUpperCase()),
      h('div', { style: 'min-width:0' }, h('h1', {}, name), h('div', { class: 'u' }, sub, ' · ', line), acts)), h('span', {}), stats),
    sum.banner && !sum.banner.custom ? h('span', { class: 'tiny', style: 'position:absolute;right:14px;top:10px;color:#C9D3E3;opacity:.8' }, `Backdrop: ${sum.banner.from}`) : null);
}
async function loadBanner(el) {
  try { const r = await fetch('/api/v8/banner', { headers: hdrs(), cache: 'no-store' }); if (!r.ok) return;
    el.style.backgroundImage = `url("${URL.createObjectURL(await r.blob())}")`; el.classList.remove('def'); } catch { /* keep the default artwork */ }
}
const readImage = (file) => new Promise((ok, bad) => {
  if (!/^image\/(jpeg|png|webp)$/.test(file.type)) return bad(new Error('Use a JPEG, PNG or WebP image.'));
  const img = new Image(), url = URL.createObjectURL(file);
  img.onload = () => {        // scale down to at most 1600 px wide in the browser, so uploads stay small
    const w = Math.min(1600, img.naturalWidth), hgt = Math.round(img.naturalHeight * w / img.naturalWidth);
    const c = document.createElement('canvas'); c.width = w; c.height = hgt; c.getContext('2d').drawImage(img, 0, 0, w, hgt);
    URL.revokeObjectURL(url); ok(c.toDataURL('image/jpeg', 0.85)); };
  img.onerror = () => { URL.revokeObjectURL(url); bad(new Error('That image could not be read.')); };
  img.src = url; });
function editSheet(me) {
  sheet('Edit profile', (b, d) => {
    const nm = h('input', { class: 'input', id: 'pf-name', value: displayName(me), maxlength: '40', autocomplete: 'nickname' });
    b.append(h('div', { class: 'field' }, h('label', { for: 'pf-name' }, 'Display name'), nm, h('span', { class: 'hint' }, 'Shown to friends you accept. Leave empty to use your username.')),
      bannerField(d),
      h('button', { class: 'btn primary', type: 'button', onclick: async () => { try { await post('/api/settings', { settings: me.settings, name: nm.value.trim() }); d.close(); toast('Profile saved.'); emit('cm:me'); location.reload(); } catch (e) { toast(e.message, null, true); } } }, 'Save'));
  });
}

function bannerField(d) {
  const inp = h('input', { type: 'file', id: 'pf-banner', accept: 'image/jpeg,image/png,image/webp', class: 'sr-only' });
  const msg = h('span', { class: 'hint', 'aria-live': 'polite' }, 'Your own image, or (by default) a backdrop from your highest-rated or most recently watched film. JPEG, PNG or WebP; it is resized to 1600 px wide and only you see it.');
  inp.addEventListener('change', async () => {
    const f = inp.files?.[0]; if (!f) return;
    try { msg.textContent = 'Uploading…'; await post('/api/v8/banner', { data: await readImage(f) }); toast('Banner saved.'); d.close(); location.reload(); }
    catch (e) { msg.textContent = e.message; }
  });
  return h('div', { class: 'field' }, h('span', { class: 'lab' }, 'Banner'),
    h('div', { class: 'chips' }, h('label', { class: 'btn secondary sm', for: 'pf-banner', tabindex: '0', role: 'button',
      onkeydown: (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); inp.click(); } } }, 'Upload an image'), inp,
      h('button', { class: 'btn ghost sm', type: 'button', onclick: async () => { try { await api('/api/v8/banner', { method: 'DELETE' }); toast('Back to the film backdrop.'); d.close(); location.reload(); } catch (e) { msg.textContent = e.message; } } }, 'Use a film backdrop')),
    msg);
}

// round 27: email digest of alerts - verified by a 6-digit code, one email a day at most, unsubscribe in every email
function emailCard(account) {
  const card = h('div', { class: 'set-card', 'aria-live': 'polite' });
  if (!account) { card.append(h('h3', {}, 'Email digest'), h('p', { class: 'muted', style: 'margin-bottom:12px' }, 'Email alerts need an account.'), h('button', { class: 'btn primary sm', type: 'button', onclick: () => authSheet('up') }, 'Create account')); return card; }
  const draw = async () => {
    let st; try { st = await api('/api/v8/email'); } catch (e) { card.innerHTML = ''; card.append(h('p', { class: 'err' }, e.message)); return; }
    card.innerHTML = '';
    card.append(h('h3', {}, 'Email digest'), h('p', { class: 'sub' }, 'One email a day at most, only when there is something new, with a link to stop them.' + (st.sandbox ? ' (Test mode: emails are saved on the server, not sent.)' : '')));
    const msg = h('p', { class: 'hint', 'aria-live': 'polite' });
    if (st.email && st.verified) {
      const box = h('input', { type: 'checkbox', checked: st.digest });
      box.addEventListener('change', async () => { try { await post('/api/v8/email/digest', { on: box.checked }); toast(box.checked ? 'Email digest on.' : 'Email digest off.'); } catch (e) { box.checked = !box.checked; toast(e.message, null, true); } });
      card.append(h('div', { class: 'pv-row' }, h('div', {}, h('b', {}, st.email), h('div', { class: 'small muted' }, 'Confirmed')), h('label', { class: 'switch' }, box, h('span', { class: 'sr' }, 'Email digest'))),
        h('button', { class: 'btn ghost sm', type: 'button', onclick: async () => { await api('/api/v8/email', { method: 'DELETE' }); toast('Email address removed.'); draw(); } }, 'Remove address'));
      return;
    }
    const em = h('input', { class: 'input', id: 'em-addr', type: 'email', autocomplete: 'email', placeholder: 'you@example.com' });
    const code = h('input', { class: 'input', id: 'em-code', inputmode: 'numeric', autocomplete: 'one-time-code', maxlength: '6', placeholder: '6-digit code' });
    card.append(h('div', { class: 'field' }, h('label', { for: 'em-addr' }, st.email ? `Waiting for the code sent to ${st.email}, or use another address` : 'Email address'), em),
      h('button', { class: 'btn secondary sm', type: 'button', onclick: async () => { try { const r = await post('/api/v8/email', { email: em.value }); msg.textContent = r.message; } catch (e) { msg.textContent = e.message; } } }, 'Send code'),
      h('div', { class: 'field', style: 'margin-top:12px' }, h('label', { for: 'em-code' }, 'Code from the email'), code),
      h('button', { class: 'btn primary sm', type: 'button', onclick: async () => { try { const r = await post('/api/v8/email/verify', { code: code.value }); toast(r.message); draw(); } catch (e) { msg.textContent = e.message; } } }, 'Confirm'), msg);
  };
  draw();
  return card;
}

// ---------------------------------------------------------------- overview
function cwPanel(items) {
  const open = (items || []).filter(x => !x.caught_up);
  const pnl = panel('Continue Watching', I.play, { sub: 'The same progress as your Watchlist; update it in either place.', act: h('a', { class: 'btn ghost sm', href: '#/watchlist?tab=tv' }, 'All shows'), cls: 'span2' });
  pnl.id = 'pf-cw';
  if (open.length) pnl.append(continueRow(open));
  else pnl.append(empty(null, 'No shows in progress. Follow a show or import your Netflix history, then record which episode you are on.', null,
    [h('a', { class: 'btn secondary sm', href: '#/watchlist?tab=tv' }, 'Follow a TV show'), h('button', { class: 'btn ghost sm', type: 'button', onclick: () => go('settings', 'imports') }, 'Import history')], true));
  return pnl;
}
const go = (tab, sec) => { const b = tab === 'settings' ? $('.pf-tabs .set') : $(`.pf-tabs [data-k="${tab}"]`); if (b) { b.click(); if (sec) setTimeout(() => $(`.set-nav [data-s="${sec}"]`)?.click(), 0); } };
const VERB = { watched: 'Watched', watched_tv: 'Watched', rated: 'Rated', reviewed: 'Reviewed', followed: 'Followed', saved: 'Added to watchlist' };
function actRow(a) {
  const link = a.media === 'movie' ? `#/film/${a.id}` : null;
  const detail = [VERB[a.kind], a.kind === 'watched_tv' && a.episodes ? `${a.episodes} episode${a.episodes === 1 ? '' : 's'}` : null, a.rating != null ? `★ ${a.rating}` : null,
    a.source && a.source !== 'cinemack' ? `via ${SRC[a.source] || a.source}` : null].filter(Boolean).join(' · ');
  return h('div', { class: 'act-row' }, h(link ? 'a' : 'span', { class: 'pp', href: link, tabindex: '-1', 'aria-hidden': 'true' }, a.poster ? h('img', { src: a.poster, alt: '', loading: 'lazy', width: 40, height: 60 }) : h('span', { class: 'ph' })),
    h('div', {}, h(link ? 'a' : 'span', { class: 't', href: link }, a.title, a.year ? h('span', { class: 'muted', style: 'font-weight:400' }, ` (${a.year})`) : null), h('div', { class: 'm' }, detail)),
    h('span', { class: 'when' }, ago(String(a.at).slice(0, 10))));
}
const PANES = {
  async overview(b, { sum }) {
    const grid = h('div', { class: 'pf-grid' });
    const items = await CW.load(true).catch(() => []);
    const act = panel('Recent Activity', I.clock, { sub: 'Your latest watches, ratings and saves. Only you see this page.' });
    if (sum.activity.length) {
      const list = h('div', {}, sum.activity.slice(0, 6).map(actRow)); act.append(list);
      if (sum.activity_total > 6) act.append(h('button', { class: 'btn ghost sm', type: 'button', style: 'margin-top:8px', onclick: async (e) => {
        const btn = e.currentTarget; btn.classList.add('busy');
        try { const j = await api('/api/v8/me/summary?activity=200'); list.innerHTML = ''; j.activity.forEach(a => list.append(actRow(a))); btn.remove(); } catch (er) { toast(er.message, null, true); btn.classList.remove('busy'); } } }, `View all activity (${sum.activity_total})`));
    } else act.append(empty(null, 'Nothing yet. Rate a film you have seen, save one for later, or import your history.', null,
      [h('button', { class: 'btn primary sm', type: 'button', onclick: () => rateSearch() }, 'Rate your first movie'), h('a', { class: 'btn ghost sm', href: '#/pick' }, 'Explore recommendations')], true));
    grid.append(cwPanel(items), act);
    // taste
    const t = sum.taste;
    const tp = panel('Your Taste', I.chart, { sub: t.inferred.length ? (t.enough ? `From ${t.basis} films you watched, liked or rated well.` : `Early signal: only ${t.basis} film${t.basis === 1 ? '' : 's'} so far, so treat this as a hint, not a profile.`) : 'What you watch and rate, by genre.' });
    tp.classList.add('taste');
    if (t.inferred.length) {
      const max = Math.max(...t.inferred.map(x => x.pct));
      t.inferred.slice(0, 5).forEach(x => tp.append(h('div', { class: 'bar' }, h('span', {}, shortGenre(x.genre)), h('span', { class: 'tr', role: 'img', 'aria-label': `${x.genre} ${x.pct}%` }, h('i', { style: `width:${Math.round(100 * x.pct / max)}%` })), h('span', { class: 'p' }, `${x.pct}%`))));
      tp.append(h('p', { class: 'src' }, 'Share of genre tags across those films. Inferred from your history, not chosen by you.'));
    } else tp.append(empty(null, 'Watch or rate a few films and your genre breakdown appears here. Nothing is guessed before then.', null, [h('a', { class: 'btn secondary sm', href: '#/profile?tab=settings&sec=imports' }, 'Import viewing history')], true));
    const ex = t.explicit, exBox = h('div', { class: 'exp' });
    const chipRow = (label, arr, fmt = (x) => x) => { if (arr?.length) exBox.append(h('div', {}, h('div', { class: 'lab' }, label), h('div', { class: 'chips', style: 'margin-top:6px' }, arr.map(x => h('span', { class: 'tag' }, fmt(x)))))); };
    chipRow('You chose: decades', ex.decades, (d) => d === 'pre1970' ? 'Before 1970' : d);
    chipRow('You chose: languages', ex.languages, (l) => ({ en: 'English', fr: 'French', es: 'Spanish', ko: 'Korean', ja: 'Japanese', hi: 'Hindi', it: 'Italian', de: 'German', zh: 'Chinese' }[l] || l));
    chipRow('Never recommend', ex.never, shortGenre);
    chipRow('You told Cinemack', ex.told);
    if (exBox.childNodes.length) tp.append(exBox);
    else tp.append(h('p', { class: 'hint', style: 'margin-top:12px' }, 'No preferences chosen yet. ', h('button', { class: 'btn ghost sm', type: 'button', onclick: () => go('settings', 'preferences') }, 'Set preferences')));
    // favourites
    const fv = sum.favorites;
    const fp = panel('Favorite Films', I.heart, { sub: fv.items.length ? `${fv.label}.` : 'Your highest-rated films show here.', cls: 'span2' });
    if (fv.items.length) fp.append(shelf(fv.items, 'Favorite films', (f) => posterCard(f, { capTitle: true, noRating: true, save: false, meta: f.rating != null ? [`★ ${f.rating}`] : [] }), 'compact'));
    else fp.append(empty(null, 'Rate films four stars or more, or give them a thumbs-up, and they appear here.', null, [h('button', { class: 'btn secondary sm', type: 'button', onclick: () => rateSearch() }, 'Rate a film')], true));
    grid.append(tp, fp);
    b.append(grid);
  },
  // 12 Nov (round 38): one Reviews tab instead of Ratings and Reviews: write a review, all your ratings and reviews
  // (filter and sort), and reviews from friends who share theirs (GET /api/v8/reviews/friends)
  async reviews(b, { sum, account }) {
    const mine = sum.reviews.filter(r => r.rating != null || (r.text || '').trim());
    const written = (r) => !!(r.text || '').trim();
    const card = (r, friend) => h('article', { class: 'rv-card' },
      h('a', { class: 'pp', href: `#/film/${r.id}`, tabindex: '-1', 'aria-hidden': 'true' }, r.poster ? h('img', { src: r.poster, alt: '', loading: 'lazy', width: 64, height: 96 }) : h('span', { class: 'ph' })),
      h('div', { style: 'min-width:0' }, h('a', { class: 't', href: `#/film/${r.id}` }, r.title || 'Unknown film', r.year ? ` (${r.year})` : ''),
        h('div', { class: 'small muted', style: 'margin-top:2px' }, [friend || null, r.rating != null ? `★ ${r.rating}` : null, r.updated ? fmtDate(r.updated.slice(0, 10)) : null].filter(Boolean).join(' · ')),
        written(r) ? h('p', { class: 'tx' }, r.text) : (friend ? null : h('p', { class: 'small muted', style: 'margin:6px 0 0' }, 'Rated, no review written yet.')),
        friend ? null : h('button', { class: 'btn ghost sm', type: 'button', style: 'margin:6px 0 0 -8px',
          onclick: () => reviewSheet({ id: r.id, title: r.title, myRating: r.rating ? Math.round(r.rating) : 0, myReview: r.text }, () => location.reload()) }, written(r) ? 'Edit' : 'Write a review')));
    b.append(h('div', { class: 'rv-head' }, h('h3', {}, 'Your reviews'), h('button', { class: 'btn primary sm', type: 'button', html: I.edit + 'Write a review', onclick: () => rateSearch() })));
    if (!mine.length) {
      b.append(empty('No reviews yet', 'Rate a film you have seen and add a few lines if you like. Reviews are private unless you share them with friends.', 'ticket',
        [h('button', { class: 'btn primary', type: 'button', onclick: () => rateSearch() }, 'Write your first review')]));
    } else {
      const kind = h('select', { class: 'input', 'aria-label': 'Show', style: 'width:auto' }, [['all', 'All'], ['text', 'Written reviews'], ['stars', 'Ratings only']].map(([v, l]) => h('option', { value: v }, l)));
      const sortSel = h('select', { class: 'input', 'aria-label': 'Sort reviews', style: 'width:auto' }, [['new', 'Newest'], ['high', 'Highest rated'], ['low', 'Lowest rated'], ['title', 'Title A–Z']].map(([v, l]) => h('option', { value: v }, l)));
      const list = h('div', { class: 'rv-list' });
      const draw = () => {
        const by = { new: (a, z) => (z.updated || '').localeCompare(a.updated || ''), high: (a, z) => (z.rating ?? -1) - (a.rating ?? -1), low: (a, z) => (a.rating ?? 9) - (z.rating ?? 9), title: (a, z) => (a.title || '').localeCompare(z.title || '') };
        const keep = { all: () => true, text: written, stars: (r) => !written(r) }[kind.value];
        const rows = mine.filter(keep).sort(by[sortSel.value]);
        list.innerHTML = '';
        if (!rows.length) list.append(h('p', { class: 'muted' }, 'Nothing here with this filter.'));
        rows.forEach(r => list.append(card(r)));
      };
      kind.addEventListener('change', draw); sortSel.addEventListener('change', draw);
      b.append(h('div', { class: 'wl-filters' }, h('span', { class: 'muted small' }, `${mine.length} rated · ${mine.filter(written).length} written`), h('span', { class: 'spacer' }), kind, sortSel), list);
      draw();
    }
    const fr = h('section', { class: 'rv-friends' }, h('div', { class: 'rv-head' }, h('h3', {}, 'From your friends')));
    b.append(fr);
    if (!account) { fr.append(h('p', { class: 'muted' }, 'Create an account and add friends to see what they think of films.')); return; }
    const fl = h('div', { class: 'rv-list' }, h('p', { class: 'muted small' }, 'Loading…'));
    fr.append(fl);
    try {
      const j = await api('/api/v8/reviews/friends');
      fl.innerHTML = '';
      if (!j.reviews.length) fl.append(h('p', { class: 'muted' }, !j.friends ? 'Add friends (Friends tab) to see their reviews here.'
        : !j.sharing ? 'None of your friends share their ratings or reviews yet; each person chooses that in Settings, Privacy.' : 'Your friends have not rated anything yet.'));
      j.reviews.forEach(r => fl.append(card(r, r.friend)));
    } catch (e) { fl.innerHTML = ''; fl.append(h('p', { class: 'err' }, e.message)); }
  },
  history(b, { sum, p }) {
    const hist = sum.history;
    if (!hist.length) { b.append(empty('No watch history yet', 'Bring it in from Netflix, Letterboxd or Trakt, or press Mark watched on any film page.', 'ticket',
      [h('button', { class: 'btn primary', type: 'button', onclick: () => go('settings', 'imports') }, 'Import your viewing history')])); return; }
    const srcs = [...new Set(hist.map(x => x.source))];
    let f = 'all';
    const chips = h('div', { class: 'chips', role: 'group', 'aria-label': 'Source' });
    const list = h('div', { class: 'panel', style: 'padding-top:4px;padding-bottom:4px' });
    const draw = () => {
      chips.innerHTML = '';
      [['all', 'All'], ...srcs.map(s => [s, SRC[s] || s])].forEach(([k, l]) => chips.append(h('button', { class: 'chip', type: 'button', 'aria-pressed': String(f === k), onclick: () => { f = k; draw(); } }, l)));
      list.innerHTML = '';
      let month = null;
      for (const x of hist.filter(x => f === 'all' || x.source === f)) {
        const m = x.last ? dayOfMonth(x.last) : 'Date unknown';
        if (m !== month) { list.append(h('h3', { style: 'font-size:13px;color:var(--faint);margin:14px 0 4px;text-transform:uppercase;letter-spacing:.06em' }, m)); month = m; }
        const link = x.media === 'movie' ? `#/film/${x.id}` : null;
        list.append(h('div', { class: 'row-i' }, h(link ? 'a' : 'span', { class: 'pp', href: link, tabindex: '-1', 'aria-hidden': 'true' }, x.poster ? h('img', { src: x.poster, alt: '', loading: 'lazy', width: 48, height: 72 }) : h('span', { class: 'ph' })),
          h('div', {}, h(link ? 'a' : 'span', { class: 't', href: link }, x.title, x.year ? ` (${x.year})` : ''),
            h('div', { class: 'm' }, [x.media === 'tv' ? `TV · ${x.episodes} episode${x.episodes === 1 ? '' : 's'}` : 'Film', `from ${SRC[x.source] || x.source}`].join(' · '))),
          h('span', { class: 'r' }, x.last ? fmtDate(x.last, { month: 'short', day: 'numeric' }) : '')));
      }
    };
    b.append(h('div', { class: 'wl-filters' }, chips, h('span', { class: 'spacer' }), h('button', { class: 'btn secondary sm', type: 'button', html: I.upload + 'Import more', onclick: () => go('settings', 'imports') })), list);
    draw();
  },
  friends(b, { p, account }) {
    if (!account) { b.append(empty('Friends need an account', 'Create an account to add friends with a code and choose exactly what each of them can see. Nothing is ever public.', 'popcorn',
      [h('button', { class: 'btn primary', type: 'button', onclick: () => authSheet('up') }, 'Create account'), h('button', { class: 'btn secondary', type: 'button', onclick: () => authSheet('in') }, 'Sign in')])); return; }
    const fr = p.friends || { friends: [], incoming: [], outgoing: [] };
    const act = (path, body, msg) => async () => { try { await post(path, body); toast(msg); location.reload(); } catch (e) { toast(e.message, null, true); } };
    const inp = h('input', { class: 'input', id: 'fc', placeholder: 'Friend code', autocomplete: 'off', style: 'text-transform:uppercase;max-width:220px' });
    b.append(h('div', { class: 'pf-grid' },
      panel('Your friend code', I.users, { sub: 'Share it with someone; they add you and you accept. Nothing is shared until you choose it in Privacy.' },
        h('div', { class: 'code', style: 'margin:6px 0 14px' }, p.code || '—'),
        h('div', { class: 'field', style: 'margin:0' }, h('label', { for: 'fc' }, 'Add a friend'), h('div', { style: 'display:flex;gap:8px;flex-wrap:wrap' }, inp,
          h('button', { class: 'btn primary', type: 'button', onclick: async () => { try { const j = await post('/api/v8/friends/request', { code: inp.value }); toast({ requested: 'Request sent.', accepted: 'You are now friends.', already_friends: 'You are already friends.' }[j.result] || 'Done.'); location.reload(); } catch (e) { toast(e.message, null, true); } } }, 'Send request')))),
      (() => { const pn = panel(`Friends (${fr.friends.length})`, I.users, { cls: 'span2', sub: fr.outgoing.length ? `${fr.outgoing.length} request${fr.outgoing.length > 1 ? 's' : ''} waiting for an answer.` : null });
        if (fr.incoming.length) { pn.append(h('h3', { style: 'font-size:14px;margin:4px 0 6px' }, 'Requests')); fr.incoming.forEach(f => pn.append(h('div', { class: 'row-i', style: 'grid-template-columns:40px 1fr auto' }, h('span', { class: 'avatar' }, (f.name || '?')[0].toUpperCase()), h('span', { class: 't' }, f.name),
          h('div', { style: 'display:flex;gap:6px' }, h('button', { class: 'btn primary sm', type: 'button', onclick: act('/api/v8/friends/respond', { key: f.key, accept: true }, 'Accepted.') }, 'Accept'), h('button', { class: 'btn ghost sm', type: 'button', onclick: act('/api/v8/friends/respond', { key: f.key, accept: false }, 'Declined.') }, 'Decline'))))); }
        if (!fr.friends.length) pn.append(h('p', { class: 'muted small' }, 'No friends yet. Share your code to get started.'));
        fr.friends.forEach(f => pn.append(h('div', { class: 'row-i', style: 'grid-template-columns:40px 1fr auto' }, h('span', { class: 'avatar' }, (f.name || '?')[0].toUpperCase()), h('span', { class: 't' }, f.name),
          h('div', { style: 'display:flex;gap:6px;flex-wrap:wrap;justify-content:flex-end' }, h('a', { class: 'btn secondary sm', href: `#/friend/${encodeURIComponent(f.key)}` }, 'View'),
            h('button', { class: 'btn ghost sm', type: 'button', onclick: act('/api/v8/friends/remove', { key: f.key }, 'Removed.') }, 'Remove'),
            h('button', { class: 'btn ghost sm', type: 'button', onclick: act('/api/v8/friends/block', { key: f.key }, 'Blocked.') }, 'Block')))));
        return pn; })()));
  },
};
const dayOfMonth = (s) => new Date(String(s).slice(0, 10) + 'T12:00:00').toLocaleDateString(undefined, { month: 'long', year: 'numeric' });
function rateSearch() {
  sheet('Rate a movie you have seen', (b, d) => {
    const q = h('input', { class: 'input', type: 'search', placeholder: 'Search movies', 'aria-label': 'Search movies', autocomplete: 'off' });
    const res = h('div', { style: 'margin-top:12px' });
    let t = 0;
    q.addEventListener('input', () => { clearTimeout(t); t = setTimeout(async () => { const s = q.value.trim(); if (s.length < 2) return;
      try { const r = await api('/api/v8/search?q=' + encodeURIComponent(s)); res.innerHTML = '';
        if (!r.films.length) res.append(h('p', { class: 'muted' }, 'No film by that name.'));
        r.films.forEach(f => res.append(h('div', { class: 'row-i' }, h('span', { class: 'pp' }, posterImg(f, 'poster', 48)), h('div', {}, h('span', { class: 't' }, f.title), h('div', { class: 'm' }, f.year || '')),
          h('button', { class: 'btn primary sm', type: 'button', html: I.star + 'Rate', onclick: () => { d.close(); reviewSheet(f, () => location.reload()); } }))));
      } catch (e) { res.innerHTML = ''; res.append(h('p', { class: 'err' }, e.message)); } }, 200); });
    b.append(q, res); setTimeout(() => q.focus(), 60);
  });
}

// ---------------------------------------------------------------- settings
function settings(b, ctx, sec0) {
  const nav = h('div', { class: 'set-nav', role: 'tablist', 'aria-label': 'Settings' });
  const pane = h('div', { class: 'set-sec', role: 'tabpanel' });
  b.append(h('div', { class: 'set-wrap' }, nav, pane));
  const open = (k) => {
    $$('button', nav).forEach(x => x.setAttribute('aria-selected', String(x.dataset.s === k)));
    history.replaceState(null, '', `#/profile?tab=settings&sec=${k}`);
    pane.innerHTML = ''; SET[k](pane, ctx);
  };
  SETS.forEach(([k, l, ic]) => nav.append(h('button', { type: 'button', role: 'tab', 'data-s': k, html: ic + `<span>${l}</span>`, onclick: () => open(k) })));
  open(SETS.some(s => s[0] === sec0) ? sec0 : 'profile');
}
const head = (pane, title, sub) => pane.append(h('h2', {}, title), h('p', { class: 'sub' }, sub));
async function saveSettings(s, ok = 'Settings saved.') { try { const j = await post('/api/settings', { settings: s }); S.me.settings = j.settings; toast(ok); emit('cm:me'); return true; } catch (e) { toast(e.message, null, true); return false; } }
const multi = (s, key, opts) => h('div', { class: 'chips' }, opts.map(([v, l]) => h('button', { class: 'chip', type: 'button', 'aria-pressed': String((s[key] || []).includes(v)), onclick: (e) => {
  const set = new Set(s[key] || []); set.has(v) ? set.delete(v) : set.add(v); s[key] = [...set]; e.currentTarget.setAttribute('aria-pressed', String(set.has(v))); } }, l)));
const SET = {
  profile(pane, { me }) {
    head(pane, 'Profile and appearance', 'How you appear to friends, and how Cinemack looks on this device.');
    const nm = h('input', { class: 'input', id: 'st-name', value: displayName(me), maxlength: '40' });
    const theme = h('select', { class: 'input', id: 'theme', style: 'max-width:280px' }, [['', 'Match my device (dark by default)'], ['dark', 'Dark'], ['light', 'Light']].map(([v, l]) => h('option', { value: v, selected: (store.get('cm.theme') || '') === v }, l)));
    theme.addEventListener('change', () => { store.set('cm.theme', theme.value); if (theme.value) document.documentElement.dataset.theme = theme.value; else delete document.documentElement.dataset.theme; toast('Appearance updated on this device.'); });
    pane.append(h('div', { class: 'set-card' }, h('div', { class: 'field' }, h('label', { for: 'st-name' }, 'Display name'), nm),
      h('button', { class: 'btn primary sm', type: 'button', onclick: async () => { try { await post('/api/settings', { settings: me.settings, name: nm.value.trim() }); toast('Name saved.'); emit('cm:me'); } catch (e) { toast(e.message, null, true); } } }, 'Save name')),
      h('div', { class: 'set-card' }, h('div', { class: 'field', style: 'margin:0' }, h('label', { for: 'theme' }, 'Appearance'), theme)));
  },
  services(pane, { me }) {
    head(pane, 'Streaming services', 'Used for "On your services", the My services filter in Ask Cinemack, and Pick Tonight.');
    const s = Object.assign({}, me.settings);
    pane.append(h('div', { class: 'set-card' }, multi(s, 'services', me.services_all), h('button', { class: 'btn primary', type: 'button', style: 'margin-top:16px', onclick: () => saveSettings(s, 'Services saved.') }, 'Save services')));
  },
  preferences(pane, { me }) {
    head(pane, 'Movie and TV preferences', 'Decades and languages gently lift recommendations. Never-recommend genres are a hard limit everywhere.');
    pane.append(tasteCard(me));
    const s = Object.assign({}, me.settings);
    pane.append(h('div', { class: 'set-card' }, h('h3', {}, 'Favorite decades'), h('p', { class: 'sub' }, 'A lift in recommendations; nothing is hidden.'),
        multi(s, 'decades', [['pre1970', 'Before 1970'], ['1970s', '70s'], ['1980s', '80s'], ['1990s', '90s'], ['2000s', '2000s'], ['2010s', '2010s'], ['2020s', '2020s']])),
      h('div', { class: 'set-card' }, h('h3', {}, 'Favorite languages'), h('p', { class: 'sub' }, 'Also a lift, never a filter.'),
        multi(s, 'languages', [['en', 'English'], ['fr', 'French'], ['es', 'Spanish'], ['ko', 'Korean'], ['ja', 'Japanese'], ['hi', 'Hindi'], ['it', 'Italian'], ['de', 'German'], ['zh', 'Chinese']])),
      h('div', { class: 'set-card' }, h('h3', {}, 'Never recommend'), h('p', { class: 'sub' }, 'A hard limit for every recommendation, including Pick Tonight.'), multi(s, 'never_genres', me.genres.map(g => [g, shortGenre(g)]))),
      h('div', { style: 'display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin-top:16px' }, h('button', { class: 'btn primary', type: 'button', onclick: () => saveSettings(s, 'Preferences saved.') }, 'Save preferences'),
        h('span', { class: 'hint' }, 'Answer length, spoilers and memory: ', h('button', { class: 'btn ghost sm', type: 'button', onclick: () => go('settings', 'chat') }, 'Chat and memory'))));
  },
  // round 27: answer settings and memory (notes + chat cards) in the v8 site; the classic page no longer needed for these
  async chat(pane, { me }) {
    head(pane, 'Chat and memory', 'How Ask Cinemack answers you, and what it remembers between chats. Private chats never use or add to memory.');
    const s = Object.assign({}, me.settings);
    const seg = (key, label, opts) => { const g = h('div', { class: 'seg', role: 'group', 'aria-label': label });
      opts.forEach(([v, l]) => g.append(h('button', { type: 'button', 'aria-pressed': String(s[key] === v), onclick: (e) => { s[key] = v; $$('button', g).forEach(b => b.setAttribute('aria-pressed', String(b === e.currentTarget))); } }, l)));
      return h('div', { class: 'field' }, h('span', { class: 'lab' }, label), g); };
    const sw = (key, label, hint) => { const box = h('input', { type: 'checkbox', checked: s[key] !== false }); box.addEventListener('change', () => { s[key] = box.checked; });
      return h('div', { class: 'field' }, h('label', { class: 'switch' }, box, label), hint ? h('span', { class: 'hint' }, hint) : null); };
    pane.append(h('div', { class: 'set-card' },
      seg('length', 'Answer length', [['short', 'Short'], ['normal', 'Normal'], ['detailed', 'Detailed']]),
      seg('spoilers', 'Spoilers', [['site', 'Only if I ask'], ['never', 'Never'], ['warn', 'Warn first'], ['allow', 'Allow']]),
      seg('voice', 'Voice', [['persona', 'Cinemack'], ['plain', 'Plain']]),
      sw('memory', 'Remember what I tell Cinemack about myself', 'Short notes from your own messages ("watches with two kids"); you can delete any of them below.'),
      sw('recall', 'Use my earlier chats when they relate to a new question'),
      sw('sources', 'Show sources under answers'),
      h('button', { class: 'btn primary', type: 'button', onclick: () => saveSettings(s, 'Chat settings saved.') }, 'Save chat settings')));
    const mem = h('div', { class: 'set-card', 'aria-live': 'polite' }, h('p', { class: 'muted small' }, 'Loading memory…'));
    pane.append(mem);
    const draw = async () => {
      let m; try { m = await api('/api/memory'); } catch (e) { mem.innerHTML = ''; mem.append(h('p', { class: 'err' }, e.message)); return; }
      mem.innerHTML = '';
      mem.append(h('h3', {}, 'What Cinemack remembers'), h('p', { class: 'sub' }, 'Notes come from what you said about yourself; chat cards sum up each saved chat (topic, films, limits you set). Deleting a chat deletes its card.'));
      const row = (title, sub, del) => h('div', { class: 'pv-row' }, h('div', { style: 'min-width:0' }, h('b', {}, title), sub ? h('div', { class: 'small muted' }, sub) : null),
        h('button', { class: 'iconbtn', type: 'button', html: I.trash, 'aria-label': `Delete: ${title}`, title: 'Delete', onclick: async () => { try { await del(); toast('Deleted.'); draw(); } catch (e) { toast(e.message, null, true); } } }));
      mem.append(h('h4', { style: 'margin:14px 0 6px' }, `Notes (${m.notes.length})`));
      if (!m.notes.length) mem.append(h('p', { class: 'muted small' }, 'No notes yet.'));
      m.notes.forEach(n => mem.append(row(n.text, null, () => api(`/api/memory/${n.id}`, { method: 'DELETE' }))));
      if (m.cards_on || (m.cards || []).length) {
        mem.append(h('h4', { style: 'margin:14px 0 6px' }, `Chat cards (${(m.cards || []).length})`));
        if (!(m.cards || []).length) mem.append(h('p', { class: 'muted small' }, 'Cards appear after your next saved chats.'));
        (m.cards || []).forEach(c => mem.append(row(c.topic, [c.films.length ? c.films.slice(0, 4).join(', ') : null, (c.limits || []).length ? c.limits.join('; ') : null].filter(Boolean).join(' · ') || null,
          () => api(`/api/memory/card/${encodeURIComponent(c.conv)}`, { method: 'DELETE' }))));
      }
      if (m.notes.length || (m.cards || []).length) mem.append(h('button', { class: 'btn ghost sm', type: 'button', style: 'margin-top:12px', html: I.trash + 'Forget everything',
        onclick: async () => { try { await api('/api/memory', { method: 'DELETE' }); toast('Memory cleared.'); draw(); } catch (e) { toast(e.message, null, true); } } }));
    };
    draw();
  },
  async notifications(pane, { account }) {
    head(pane, 'Notifications', 'Alerts appear in Cinemack under the bell, and (if you add an address) in one email digest a day. Push notifications need HTTPS, which this server does not have yet.');
    pane.append(emailCard(account));
    const card = h('div', { class: 'set-card' }, h('p', { class: 'muted small' }, 'Loading…')); pane.append(card);
    try {
      const [u, o] = await Promise.all([api('/api/v8/watchlist/upcoming'), api('/api/v8/watchlist/overview')]);
      card.innerHTML = '';
      card.append(h('h3', {}, 'What sends an alert'), h('p', { class: 'sub' }, 'A saved film comes out or starts streaming on a new service; a followed show gets a dated new season or a season starts.'));
      const rows = [...(o.shows || []).map(sh => ({ tv: true, id: sh.id, title: sh.title, notify: sh.notify, line: 'Followed show' })),
        ...u.items.filter(x => x.media === 'movie').map(x => ({ tv: false, id: x.id, title: x.film.title, notify: x.notify, line: x.date ? `Saved · ${x.badge} ${fmtDate(x.date)}` : 'Saved · date not announced' }))];
      if (!rows.length) card.append(h('p', { class: 'muted' }, 'Nothing to alert you about yet. Save upcoming films or follow shows.'));
      rows.forEach(r => { const box = h('input', { type: 'checkbox', checked: r.notify });
        box.addEventListener('change', async () => { try { if (r.tv) await post('/api/v8/tv/follow', { id: r.id, on: true, notify: box.checked }); else await post('/api/v8/alerts/mute', { id: r.id, muted: !box.checked }); toast(`${r.title}: alerts ${box.checked ? 'on' : 'off'}.`); } catch (e) { box.checked = !box.checked; toast(e.message, null, true); } });
        card.append(h('div', { class: 'pv-row' }, h('div', {}, h('b', {}, r.title), h('div', { class: 'small muted' }, r.line)), h('label', { class: 'switch' }, box, h('span', { class: 'sr' }, `Alerts for ${r.title}`)))); });
    } catch (e) { card.innerHTML = ''; card.append(h('p', { class: 'err' }, e.message)); }
  },
  privacy(pane, { p, account }) {
    head(pane, 'Privacy', 'Everything is private until you change it. "Friends" means people you accepted; there is no public profile.');
    if (!account) { pane.append(h('div', { class: 'set-card' }, h('p', { class: 'muted', style: 'margin-bottom:12px' }, 'Sharing needs an account. As a guest, nothing is shared with anyone.'), h('button', { class: 'btn primary sm', type: 'button', onclick: () => authSheet('up') }, 'Create account'))); return; }
    const labels = { profile: 'Profile and stats', watchlist: 'Watchlist', history: 'Watch history', ratings: 'Ratings', reviews: 'Written reviews' };
    const card = h('div', { class: 'set-card' });
    for (const [k, l] of Object.entries(labels)) {
      const id = `pv-${k}`;
      const s = h('select', { class: 'input', id }, [['private', 'Only me'], ['friends', 'Friends']].map(([v, t]) => h('option', { value: v, selected: p.privacy[k] === v }, t)));
      s.addEventListener('change', async () => { const before = p.privacy[k]; try { await post('/api/v8/privacy', { changes: { [k]: s.value } }); p.privacy[k] = s.value; toast(`${l}: ${s.value === 'friends' ? 'shared with friends' : 'only you'}.`); } catch (e) { s.value = before; toast(e.message, null, true); } });
      card.append(h('div', { class: 'pv-row' }, h('label', { for: id, style: 'font-weight:600' }, l), s));
    }
    pane.append(card, h('p', { class: 'hint', style: 'margin-top:12px' }, 'Changes apply at once, on the page and in the API: a friend\'s request for anything you keep private gets nothing back.'));
  },
  imports(pane, { p }) {
    head(pane, 'Imports and connections', 'Bring in what you have already watched. Cinemack matches titles to its database and shows you what it could not match.');
    const res = h('div', { 'aria-live': 'polite' });
    const upload = (kind, label, hint) => {
      const id = `up-${kind}`;
      const inp = h('input', { type: 'file', id, accept: '.csv,text/csv', class: 'input' });
      inp.addEventListener('change', async () => {
        const f = inp.files[0]; if (!f) return;
        res.innerHTML = ''; res.append(h('div', { class: 'notice' }, h('span', { class: 'status' }, h('span', { class: 'dot' }), `Importing ${f.name}…`)));
        try { const j = await post('/api/v8/import', { kind, text: await f.text() }); showImport(res, j); CW.items = null; emit('cm:imported'); } catch (e) { res.innerHTML = ''; res.append(h('div', { class: 'notice warn' }, e.message)); }
      });
      return h('div', { class: 'set-card' }, h('h3', {}, label), h('p', { class: 'sub' }, hint), inp);
    };
    pane.append(upload('netflix', 'Netflix viewing history', 'Netflix: Account, your profile, Viewing activity, Download all (NetflixViewingHistory.csv). ViewingActivity.csv from a data request works too. Episodes are grouped by show.'),
      upload('letterboxd_watched', 'Letterboxd watched films or diary', 'From your Letterboxd data export: watched.csv or diary.csv.'),
      traktFiles(res, () => { CW.items = null; emit('cm:imported'); }), res);
    const t = p.trakt || {};
    const tc = h('div', { class: 'set-card' }, h('h3', {}, 'Trakt'), h('p', { class: 'sub' }, t.connected ? `Connected${t.last_sync ? `, last synced ${fmtDate(t.last_sync.slice(0, 10))}` : ''}. Syncs history, ratings and watchlist when you press Sync.` : 'Connect your Trakt account to import history, ratings and watchlist.'));
    if (!t.available) tc.append(h('p', { class: 'muted small' }, 'Signing in with Trakt is not set up on this server yet. The Trakt export above brings in the same history, ratings and watchlist.'));
    else if (t.connected) tc.append(h('div', { class: 'actions', style: 'margin:0' }, h('button', { class: 'btn secondary sm', type: 'button', onclick: async (e) => { e.currentTarget.classList.add('busy'); try { const j = await post('/api/v8/trakt/sync'); showImport(res, j.history); } catch (er) { toast(er.message, null, true); } e.currentTarget?.classList.remove('busy'); } }, 'Sync now'),
      h('button', { class: 'btn ghost sm', type: 'button', onclick: async () => { try { const j = await post('/api/v8/trakt/disconnect'); toast(`Disconnected; removed ${j.removed_events} imported rows.`); location.reload(); } catch (e) { toast(e.message, null, true); } } }, 'Disconnect')));
    else traktConnect(tc, () => { CW.items = null; emit('cm:imported'); });
    pane.append(tc, h('p', { class: 'hint', style: 'margin-top:12px' }, 'Cinemack cannot see what you play on Netflix or other services by itself; these files and Trakt are the only sources of watch history.'));
    if (p.imports?.length) pane.append(h('div', { class: 'set-card' }, h('h3', {}, 'Past imports'), p.imports.map(j => h('div', { class: 'pv-row' },
      h('div', {}, h('b', {}, SRC[j.source] || j.source), h('div', { class: 'small muted' }, `${j.rows} rows: ${j.matched} matched, ${j.duplicates} already there, ${j.issues} not matched · ${fmtDate(j.at.slice(0, 10))}`),
        j.issue_rows?.length ? h('details', { style: 'margin-top:6px' }, h('summary', { class: 'small' }, `Review ${j.issue_rows.length} unmatched title${j.issue_rows.length === 1 ? '' : 's'}`),
          h('div', { style: 'max-height:240px;overflow:auto;margin-top:6px' }, j.issue_rows.map(i => h('div', { class: 'small', style: 'padding:5px 0;border-bottom:1px solid var(--line)' }, `${i.raw}: ${i.reason}`)))) : null),
      h('button', { class: 'btn ghost sm', type: 'button', onclick: async () => { try { const r = await post('/api/v8/history/clear', { kind: j.source, text: '' }); toast(`Removed ${r.removed} ${SRC[j.source] || j.source} rows.`); location.reload(); } catch (e) { toast(e.message, null, true); } } }, 'Remove these rows')))));
  },
  account(pane, { me, account }) {
    head(pane, 'Account and security', account ? `Signed in as @${me.account.username}.` : 'You are using Cinemack as a guest. Your data is kept for this browser only.');
    pane.append(h('div', { class: 'notice warn', style: 'margin-bottom:14px' }, 'This site does not use HTTPS yet, so sign-in details cross the network unencrypted. Use a password you use nowhere else. Passwords are stored as salted scrypt or Argon2id hashes, never as text.'));
    if (!account) { pane.append(h('div', { class: 'set-card' }, h('h3', {}, 'Create an account'), h('p', { class: 'sub' }, 'Keeps your chats, taste, watchlist, progress and history across devices, and lets you add friends. What you did as a guest moves into the account.'),
      h('div', { class: 'actions', style: 'margin:0' }, h('button', { class: 'btn primary', type: 'button', onclick: () => authSheet('up') }, 'Create account'), h('button', { class: 'btn secondary', type: 'button', onclick: () => authSheet('in') }, 'Sign in')))); return; }
    pane.append(h('div', { class: 'set-card' }, h('h3', {}, 'Session'), h('p', { class: 'sub' }, 'Sign out of this browser.'), h('button', { class: 'btn secondary', type: 'button', html: I.logout + 'Sign out', onclick: signOut })),
      h('div', { class: 'set-card danger', style: 'margin-top:28px' }, h('h3', { class: 'err' }, 'Delete account'), h('p', { class: 'sub' }, 'Deletes your account and everything stored for it: chats, taste, watchlist, TV progress, history, ratings, friends and the Trakt link. This cannot be undone.'),
        h('button', { class: 'btn danger', type: 'button', html: I.trash + 'Delete my account', onclick: deleteSheet })));
  },
};
function showImport(el, j) {
  el.innerHTML = '';
  el.append(h('div', { class: 'notice', style: 'margin-top:14px' }, h('b', {}, 'Import finished. '), `${j.rows} rows read: ${j.added ?? j.matched} added, ${j.duplicates} already there, ${j.issues.length} not matched.`));
  if (j.issues.length) el.append(h('details', { class: 'set-card', style: 'margin-top:10px' }, h('summary', {}, `Review ${j.issues.length} unmatched row${j.issues.length === 1 ? '' : 's'}`),
    h('div', { style: 'max-height:280px;overflow:auto;margin-top:8px' }, j.issues.slice(0, 200).map(i => h('div', { class: 'small', style: 'padding:6px 0;border-bottom:1px solid var(--line)' }, `${i.raw}: ${i.reason}`)))));
}
function deleteSheet() {
  sheet('Delete account', (b, d) => {
    const pw = h('input', { class: 'input', id: 'del-p', type: 'password', autocomplete: 'current-password' });
    const confirm = h('input', { class: 'input', id: 'del-c', autocomplete: 'off', placeholder: 'DELETE' });
    const err = h('p', { class: 'err', 'aria-live': 'polite' });
    b.append(h('p', { style: 'margin-bottom:14px' }, 'This removes everything and cannot be undone.'), h('div', { class: 'field' }, h('label', { for: 'del-p' }, 'Password'), pw),
      h('div', { class: 'field' }, h('label', { for: 'del-c' }, 'Type DELETE to confirm'), confirm), err,
      h('button', { class: 'btn danger', type: 'button', onclick: async () => {
        if (confirm.value.trim() !== 'DELETE') { err.textContent = 'Type DELETE to confirm.'; return; }
        try { await post('/api/account/delete', { password: pw.value }); d.close(); S.me = null; toast('Account deleted.'); emit('cm:me'); location.hash = '#/'; } catch (e) { err.textContent = e.message; } } }, 'Delete everything'));
  });
}

// ---------------------------------------------------------------- a friend's page (only what they share with you)
export async function pageFriend(key) {
  view.innerHTML = '';
  let f; try { f = await api(`/api/v8/friend/${encodeURIComponent(key)}`); } catch (e) { view.append(h('div', { class: 'notice warn' }, e.message)); return; }
  view.append(h('div', { class: 'page-h' }, h('span', { class: 'pf-av', style: 'width:56px;height:56px;font-size:24px', 'aria-hidden': 'true' }, (f.name || '?')[0].toUpperCase()),
    h('div', { class: 'tx' }, h('h1', {}, f.name), h('p', {}, 'Only what they chose to share with friends.')), h('a', { class: 'btn secondary sm', href: '#/profile?tab=friends' }, 'All friends')));
  if (!f.watchlist && !f.history && !f.ratings) view.append(empty(null, `${f.name} hasn't shared anything with friends yet.`, 'popcorn'));
  if (f.watchlist) view.append(section('Watchlist', {}, f.watchlist.length ? shelf(f.watchlist, `${f.name}'s watchlist`) : h('p', { class: 'muted' }, 'Nothing on their watchlist.')));
  if (f.history) view.append(section('Recently watched', {}, h('div', { class: 'panel', style: 'padding-top:4px;padding-bottom:4px' }, f.history.slice(0, 50).map(x => h('div', { class: 'row-i', style: 'grid-template-columns:1fr auto' }, h('span', { class: 't' }, x.title), h('span', { class: 'r' }, x.last ? fmtDate(x.last) : ''))))));
  if (f.ratings) view.append(section('Ratings', {}, f.ratings.length ? h('div', { class: 'grid' }, f.ratings.map(r => posterCard({ id: r.id, title: r.title || 'Film', year: r.year, poster: r.poster }, { noRating: true, meta: [r.rating != null ? `★ ${r.rating}` : null, r.text ? `"${r.text.slice(0, 60)}${r.text.length > 60 ? '…' : ''}"` : null].filter(Boolean) }))) : h('p', { class: 'muted' }, 'No ratings shared.')));
}
