// Create your account (10 Nov 2026, round 21): one guided flow instead of a bare username/password box.
//   1 Account   username, password (twice), display name; what a guest did in this browser moves into the account
//   2 Taste     kinds of film you love or avoid (poster groups) and a few well-known films: liked / not for me / not seen
//   3 Limits    streaming services, genres never to recommend, age rating, longest runtime, favourite decades, languages
//   4 History   Netflix or Letterboxd file, or Trakt; skippable
//   5 Profile   display name, who can see what (private by default), appearance; Finish saves everything
// Every step after the first can be skipped and changed later in Profile > Settings. Signed in already? It starts at 2.
import { $, $$, S, h, I, api, post, view, toast, emit, store, displayName, authSheet, shortGenre, CW } from './core.js';

const STEPS = [['account', 'Account'], ['taste', 'Your taste'], ['limits', 'What you watch'], ['history', 'Your history'], ['profile', 'Profile']];
const DECADES = [['pre1970', 'Before 1970'], ['1970s', '70s'], ['1980s', '80s'], ['1990s', '90s'], ['2000s', '2000s'], ['2010s', '2010s'], ['2020s', '2020s']];
const LANGS = [['en', 'English'], ['fr', 'French'], ['es', 'Spanish'], ['ko', 'Korean'], ['ja', 'Japanese'], ['hi', 'Hindi'], ['it', 'Italian'], ['de', 'German'], ['zh', 'Chinese']];
const PRIV = { profile: 'Profile and stats', watchlist: 'Watchlist', history: 'Watch history', ratings: 'Ratings', reviews: 'Written reviews' };

export async function pageJoin(params) {
  view.innerHTML = '';
  try { S.me = await api('/api/me'); } catch {}
  const st = { i: 0, groups: new Set(), avoid: new Set(), films: {}, settings: Object.assign({}, S.me?.settings || {}), name: displayName(S.me) || '', privacy: null };
  const startAt = STEPS.findIndex(s => s[0] === params.get('step'));
  st.i = S.me?.account ? Math.max(1, startAt) : 0;
  const rail = h('ol', { class: 'join-steps', 'aria-label': 'Steps' });
  const body = h('section', { class: 'join-body', 'aria-live': 'polite' });
  view.append(h('div', { class: 'join' }, h('div', { class: 'join-head' }, h('span', { class: 'ic', html: I.sparkle }), h('div', {},
    h('h1', {}, S.me?.account ? 'Set up your Cinemack' : 'Create your Cinemack account'),
    h('p', { class: 'muted' }, 'Five short steps. Only the first is required; everything else can be skipped and changed later.'))), rail, body));
  const draw = () => {
    rail.innerHTML = '';
    STEPS.forEach(([k, l], i) => rail.append(h('li', { class: i === st.i ? 'on' : i < st.i ? 'done' : '', 'aria-current': i === st.i ? 'step' : null },
      h('span', { class: 'n', html: i < st.i ? I.check : String(i + 1) }), h('span', { class: 'l' }, l))));
    body.innerHTML = '';
    STEP[STEPS[st.i][0]](body, st, next, back);
    window.scrollTo(0, 0); body.querySelector('h2')?.focus({ preventScroll: true });
    history.replaceState(null, '', `#/join?step=${STEPS[st.i][0]}`);
  };
  const next = () => { st.i = Math.min(STEPS.length, st.i + 1); if (st.i === STEPS.length) finish(); else draw(); };
  const back = () => { st.i = Math.max(S.me?.account ? 1 : 0, st.i - 1); draw(); };
  async function finish() {
    body.innerHTML = ''; body.append(h('p', { class: 'muted' }, 'Saving…'));
    try {
      const me = await post('/api/onboarding', { name: st.name, groups: [...st.groups], avoid: [...st.avoid], settings: st.settings });
      S.me = me;
      if (st.privacy && Object.keys(st.privacy).length) await post('/api/v8/privacy', { changes: st.privacy }).catch(() => {});
      emit('cm:me');
    } catch (e) { body.innerHTML = ''; body.append(h('div', { class: 'notice warn' }, e.message, ' ', h('button', { class: 'btn secondary sm', type: 'button', onclick: finish }, 'Try again'))); return; }
    rail.querySelectorAll('li').forEach(li => { li.className = 'done'; li.querySelector('.n').innerHTML = I.check; });
    body.innerHTML = '';
    body.append(h('div', { class: 'join-done' }, h('span', { class: 'big', html: I.check }), h('h2', { tabindex: '-1' }, `You're all set${st.name ? `, ${st.name}` : ''}.`),
      h('p', { class: 'muted' }, 'Recommendations now use your taste and limits. Change anything in Profile > Settings.'),
      h('div', { class: 'actions' }, h('a', { class: 'btn primary lg', href: '#/pick' }, 'Pick something for tonight'), h('a', { class: 'btn secondary lg', href: '#/' }, 'Go to Home'),
        h('a', { class: 'btn ghost lg', href: '#/profile?tab=settings' }, 'Settings'))));
    window.scrollTo(0, 0); body.querySelector('h2').focus({ preventScroll: true });
    history.replaceState(null, '', '#/join?step=done');
  }
  draw();
}

const nav = (next, back, opts = {}) => h('div', { class: 'join-nav' },
  back ? h('button', { class: 'btn ghost', type: 'button', html: I.left + 'Back', onclick: back }) : h('span'),
  h('span', { class: 'spacer' }),
  opts.skip ? h('button', { class: 'btn ghost', type: 'button', onclick: next }, opts.skip) : null,
  h('button', { class: 'btn primary', type: 'button', onclick: opts.go || next, html: (opts.label || 'Continue') + I.right }));
const multi = (obj, key, opts) => h('div', { class: 'chips' }, opts.map(([v, l]) => h('button', { class: 'chip', type: 'button', 'aria-pressed': String((obj[key] || []).includes(v)), onclick: (e) => {
  const set = new Set(obj[key] || []); set.has(v) ? set.delete(v) : set.add(v); obj[key] = [...set]; e.currentTarget.setAttribute('aria-pressed', String(set.has(v))); } }, l)));

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

const STEP = {
  account(b, st, next) {
    const u = h('input', { class: 'input', id: 'j-u', autocomplete: 'username', autocapitalize: 'none', spellcheck: 'false', required: true });
    const p1 = h('input', { class: 'input', id: 'j-p', type: 'password', autocomplete: 'new-password', required: true });
    const p2 = h('input', { class: 'input', id: 'j-p2', type: 'password', autocomplete: 'new-password', required: true });
    const nm = h('input', { class: 'input', id: 'j-n', autocomplete: 'nickname', maxlength: '40', value: st.name });
    const keep = h('input', { type: 'checkbox', id: 'j-keep', checked: true });
    const err = h('p', { class: 'err', 'aria-live': 'assertive' });
    const go = async (e) => {
      err.textContent = '';
      if (p1.value !== p2.value) { err.textContent = 'The two passwords are different.'; p2.focus(); return; }
      const btn = e?.currentTarget; btn?.classList.add('busy');
      try {
        S.me = await post('/api/signup', { username: u.value.trim(), password: p1.value, display_name: nm.value.trim(), keep_history: keep.checked });
        st.name = nm.value.trim(); CW.items = null; emit('cm:me'); toast('Account created.'); next();
      } catch (er) { err.textContent = er.message; } finally { btn?.classList.remove('busy'); }
    };
    p2.addEventListener('keydown', (e) => { if (e.key === 'Enter') go(); });
    b.append(h('h2', { tabindex: '-1' }, 'Your account'),
      h('p', { class: 'sub' }, 'Keeps your chats, taste, watchlist, TV progress and history across devices, and lets you add friends.'),
      h('div', { class: 'join-grid' },
        h('div', { class: 'field' }, h('label', { for: 'j-u' }, 'Username'), u, h('span', { class: 'hint' }, '3 to 32 letters, numbers, dots, dashes or underscores.')),
        h('div', { class: 'field' }, h('label', { for: 'j-n' }, 'Display name (optional)'), nm, h('span', { class: 'hint' }, 'What friends see.')),
        h('div', { class: 'field' }, h('label', { for: 'j-p' }, 'Password'), p1, h('span', { class: 'hint' }, 'At least 8 characters.')),
        h('div', { class: 'field' }, h('label', { for: 'j-p2' }, 'Password again'), p2, h('span', { class: 'hint' }, 'The same password once more.'))),
      h('label', { class: 'check' }, keep, ' Move what I did as a guest in this browser (chats, watchlist, ratings, history) into the account'),
      h('p', { class: 'notice warn', style: 'margin-top:14px' }, 'This site does not use HTTPS yet, so your password crosses the network unencrypted. Choose one you use nowhere else. It is stored only as a salted hash.'),
      err, nav(go, null, { go, label: 'Create account' }),
      h('p', { class: 'small muted', style: 'margin-top:14px' }, 'Already have an account? ', h('button', { class: 'btn ghost sm', type: 'button', onclick: () => authSheet('in') }, 'Sign in')));
    setTimeout(() => u.focus(), 30);
  },
  async taste(b, st, next, back) {
    b.append(h('h2', { tabindex: '-1' }, 'What do you love?'), h('p', { class: 'sub' }, 'Pick a few kinds of film. "Not for me" steers recommendations away; nothing is hidden from search.'));
    const box = h('div', {}, h('p', { class: 'muted small' }, 'Loading…')); b.append(box, nav(next, back, { skip: 'Skip' }));
    let o; try { o = await api('/api/onboarding'); } catch (e) { box.innerHTML = ''; box.append(h('p', { class: 'err' }, e.message)); return; }
    box.innerHTML = '';
    const grid = h('div', { class: 'tgroups' });
    for (const g of o.groups || []) {
      const card = h('div', { class: 'tg' }, h('div', { class: 'tg-pp', 'aria-hidden': 'true' }, (g.films || []).map(f => h('img', { src: f.poster, alt: '', loading: 'lazy', width: 70, height: 105 }))),
        h('b', {}, g.label));
      const love = h('button', { class: 'chip', type: 'button', 'aria-pressed': String(st.groups.has(g.key)) }, 'Love it');
      const avoid = h('button', { class: 'chip', type: 'button', 'aria-pressed': String(st.avoid.has(g.key)) }, 'Not for me');
      const sync = () => { love.setAttribute('aria-pressed', String(st.groups.has(g.key))); avoid.setAttribute('aria-pressed', String(st.avoid.has(g.key))); card.dataset.s = st.groups.has(g.key) ? 'love' : st.avoid.has(g.key) ? 'avoid' : ''; };
      love.addEventListener('click', () => { st.avoid.delete(g.key); st.groups.has(g.key) ? st.groups.delete(g.key) : st.groups.add(g.key); sync(); });
      avoid.addEventListener('click', () => { st.groups.delete(g.key); st.avoid.has(g.key) ? st.avoid.delete(g.key) : st.avoid.add(g.key); sync(); });
      card.append(h('div', { class: 'tg-a', role: 'group', 'aria-label': g.label }, love, avoid)); sync(); grid.append(card);
    }
    box.append(grid);
    if ((o.films || []).length) {
      box.append(h('h3', { style: 'margin-top:22px' }, 'Seen any of these?'), h('p', { class: 'sub' }, 'Each answer is saved at once and used like any thumbs-up or thumbs-down.'));
      const row = h('div', { class: 'tfilms' });
      for (const f of o.films) {
        const cur = () => st.films[f.id] ?? f.mine ?? '';
        const bs = [['liked', 'Liked it'], ['not', 'Not for me'], ['unseen', "Haven't seen"]].map(([v, l]) => h('button', { class: 'chip sm', type: 'button', 'aria-pressed': String(cur() === v), 'data-v': v }, l));
        bs.forEach(bt => bt.addEventListener('click', async () => {
          const v = cur() === bt.dataset.v ? '' : bt.dataset.v;
          try { await post('/api/film', { id: f.id, title: f.title, choice: v }); st.films[f.id] = v; bs.forEach(x => x.setAttribute('aria-pressed', String(x.dataset.v === v))); } catch (e) { toast(e.message, null, true); }
        }));
        row.append(h('div', { class: 'tf' }, h('img', { src: f.poster, alt: '', loading: 'lazy', width: 92, height: 138 }), h('b', {}, f.title), h('span', { class: 'small muted' }, String(f.year || '')),
          h('div', { class: 'tf-a', role: 'group', 'aria-label': f.title }, bs)));
      }
      box.append(row);
    }
    // 12 Nov (round 36): TV. Kinds of shows and shows you know; Pick Tonight's TV picks use them (web/tv_prefs.py)
    let tv = null;
    try { tv = await api('/api/v8/onboarding/tv'); } catch { tv = null; }
    if (tv && ((tv.groups || []).length || (tv.shows || []).length)) {
      const tl = new Set(tv.picked?.groups || []), ta = new Set(tv.picked?.avoid || []);
      const saveTv = () => post('/api/v8/onboarding/tv/groups', { groups: [...tl], avoid: [...ta] }).catch((e) => toast(e.message, null, true));
      box.append(h('h3', { style: 'margin-top:30px' }, 'And TV?'), h('p', { class: 'sub' }, 'Kinds of shows you love or skip. Pick Tonight uses them for shows; nothing is hidden from search.'));
      const g2 = h('div', { class: 'tgroups' });
      for (const g of tv.groups || []) {
        const card = h('div', { class: 'tg' }, h('div', { class: 'tg-pp', 'aria-hidden': 'true' }, (g.shows || []).map(s => h('img', { src: s.poster, alt: '', loading: 'lazy', width: 70, height: 105 }))),
          h('b', {}, g.label));
        const love = h('button', { class: 'chip', type: 'button' }, 'Love it');
        const avoid = h('button', { class: 'chip', type: 'button' }, 'Not for me');
        const sync = () => { love.setAttribute('aria-pressed', String(tl.has(g.key))); avoid.setAttribute('aria-pressed', String(ta.has(g.key))); card.dataset.s = tl.has(g.key) ? 'love' : ta.has(g.key) ? 'avoid' : ''; };
        love.addEventListener('click', () => { ta.delete(g.key); tl.has(g.key) ? tl.delete(g.key) : tl.add(g.key); sync(); saveTv(); });
        avoid.addEventListener('click', () => { tl.delete(g.key); ta.has(g.key) ? ta.delete(g.key) : ta.add(g.key); sync(); saveTv(); });
        card.append(h('div', { class: 'tg-a', role: 'group', 'aria-label': g.label }, love, avoid)); sync(); g2.append(card);
      }
      box.append(g2);
      if ((tv.shows || []).length) {
        box.append(h('h3', { style: 'margin-top:22px' }, 'Seen any of these shows?'), h('p', { class: 'sub' }, '"Watching" follows the show: it goes on your Watchlist with new-season alerts. Each answer is saved at once.'));
        const row2 = h('div', { class: 'tfilms' });
        for (const s of tv.shows) {
          let cur = s.mine || '';
          const bs = [['watching', 'Watching'], ['liked', 'Loved it'], ['not', 'Not for me'], ['unseen', "Haven't seen"]].map(([v, l]) => h('button', { class: 'chip sm', type: 'button', 'aria-pressed': String(cur === v), 'data-v': v }, l));
          bs.forEach(bt => bt.addEventListener('click', async () => {
            const v = cur === bt.dataset.v ? '' : bt.dataset.v;
            try {
              await post('/api/v8/tv/choice', { id: s.id, title: s.title, choice: v });
              if (v === 'watching' || cur === 'watching') emit('cm:follow', { id: s.id, on: v === 'watching' });
              cur = v; bs.forEach(x => x.setAttribute('aria-pressed', String(x.dataset.v === v)));
            } catch (e) { toast(e.message, null, true); }
          }));
          row2.append(h('div', { class: 'tf' }, h('img', { src: s.poster, alt: '', loading: 'lazy', width: 92, height: 138 }), h('b', {}, s.title), h('span', { class: 'small muted' }, String(s.year || '')),
            h('div', { class: 'tf-a', role: 'group', 'aria-label': s.title }, bs)));
        }
        box.append(row2);
      }
    }
  },
  limits(b, st, next, back) {
    const s = st.settings, me = S.me || {};
    const cert = h('select', { class: 'input', id: 'j-cert' }, [['', 'Any rating'], ['G', 'G only'], ['PG', 'PG and under'], ['PG-13', 'PG-13 and under']].map(([v, l]) => h('option', { value: v, selected: (s.max_cert || '') === v }, l)));
    const len = h('select', { class: 'input', id: 'j-len' }, [['', 'Any length'], ['90', 'Under 1½ hours'], ['120', 'Under 2 hours'], ['150', 'Under 2½ hours'], ['180', 'Under 3 hours']].map(([v, l]) => h('option', { value: v, selected: String(s.max_runtime || '') === v }, l)));
    cert.addEventListener('change', () => { s.max_cert = cert.value || null; });
    len.addEventListener('change', () => { s.max_runtime = len.value ? Number(len.value) : null; });
    b.append(h('h2', { tabindex: '-1' }, 'What you watch'), h('p', { class: 'sub' }, 'Services decide "On your services" and Pick Tonight. Limits are hard limits for every recommendation, never just hints.'),
      h('div', { class: 'set-card' }, h('h3', {}, 'Your streaming services'), multi(s, 'services', me.services_all || [])),
      h('div', { class: 'set-card' }, h('h3', {}, 'Never recommend'), multi(s, 'never_genres', (me.genres || []).map(g => [g, shortGenre(g)]))),
      h('div', { class: 'set-card join-grid' }, h('div', { class: 'field' }, h('label', { for: 'j-cert' }, 'Age rating limit'), cert, h('span', { class: 'hint' }, 'For example when you mostly watch with kids.')),
        h('div', { class: 'field' }, h('label', { for: 'j-len' }, 'Longest film'), len)),
      h('div', { class: 'set-card' }, h('h3', {}, 'Favourite decades'), h('p', { class: 'sub' }, 'A gentle lift; nothing is hidden.'), multi(s, 'decades', DECADES)),
      h('div', { class: 'set-card' }, h('h3', {}, 'Favourite languages'), multi(s, 'languages', LANGS)),
      nav(next, back, { skip: 'Skip' }));
  },
  async history(b, st, next, back) {
    const res = h('div', { 'aria-live': 'polite' });
    const upload = (kind, label, hint) => {
      const id = `j-up-${kind}`;
      const inp = h('input', { type: 'file', id, accept: '.csv,text/csv', class: 'input' });
      inp.addEventListener('change', async () => {
        const f = inp.files[0]; if (!f) return;
        res.innerHTML = ''; res.append(h('div', { class: 'notice' }, `Importing ${f.name}…`));
        try { const j = await post('/api/v8/import', { kind, text: await f.text() }); res.innerHTML = '';
          res.append(h('div', { class: 'notice' }, h('b', {}, 'Imported. '), `${j.rows} rows: ${j.added ?? j.matched} added, ${j.duplicates} already there, ${j.issues.length} not matched (you can review them in Settings > Imports).`));
          CW.items = null; emit('cm:imported'); }
        catch (e) { res.innerHTML = ''; res.append(h('div', { class: 'notice warn' }, e.message)); }
      });
      return h('div', { class: 'set-card' }, h('h3', {}, h('label', { for: id }, label)), h('p', { class: 'sub' }, hint), inp);
    };
    b.append(h('h2', { tabindex: '-1' }, 'Bring your history'), h('p', { class: 'sub' }, 'Optional. What you have watched fills your profile and Continue Watching, and keeps recommendations from repeating films you know.'),
      upload('netflix', 'Netflix viewing history', 'Netflix: Account, your profile, Viewing activity, Download all (NetflixViewingHistory.csv).'),
      upload('letterboxd_watched', 'Letterboxd watched or diary', 'From your Letterboxd data export: watched.csv or diary.csv.'),
      traktFiles(res, () => { CW.items = null; emit('cm:imported'); }));
    const tr = h('div', { class: 'set-card' }, h('h3', {}, 'Trakt account'), h('p', { class: 'muted small' }, 'Checking…'));
    b.append(tr, res, nav(next, back, { skip: 'Skip for now' }));
    try {
      const p = await api('/api/v8/profile'); const t = p.trakt || {}; tr.lastChild.remove();
      if (!t.available) tr.append(h('p', { class: 'muted small' }, 'Signing in with Trakt is not set up on this server yet. The Trakt export above brings in the same history, ratings and watchlist.'));
      else if (t.connected) tr.append(h('p', { class: 'small' }, 'Connected. Sync from Settings > Imports.'));
      else tr.append(h('p', { class: 'sub' }, 'Import history, ratings and watchlist from Trakt. You will come back here after approving.'),
        h('button', { class: 'btn secondary sm', type: 'button', onclick: async () => { try { store.set('cm.joinReturn', 'profile'); location.href = (await api('/trakt/start')).url; } catch (e) { toast(e.message, null, true); } } }, 'Connect Trakt'));
      st.privacy0 = p.privacy || {};
    } catch { tr.lastChild.textContent = 'Could not check Trakt right now.'; }
  },
  profile(b, st, next, back) {
    const nm = h('input', { class: 'input', id: 'j-name', maxlength: '40', value: st.name });
    nm.addEventListener('input', () => { st.name = nm.value.trim(); });
    st.privacy = st.privacy || {};
    const pv = h('div', { class: 'set-card' }, h('h3', {}, 'Who can see what'), h('p', { class: 'sub' }, 'Private until you change it. "Friends" means people you accept; there is no public profile.'));
    for (const [k, l] of Object.entries(PRIV)) {
      const id = `j-pv-${k}`, cur = st.privacy[k] || st.privacy0?.[k] || 'private';
      const sel = h('select', { class: 'input', id }, [['private', 'Only me'], ['friends', 'Friends']].map(([v, t]) => h('option', { value: v, selected: cur === v }, t)));
      sel.addEventListener('change', () => { st.privacy[k] = sel.value; });
      pv.append(h('div', { class: 'pv-row' }, h('label', { for: id, style: 'font-weight:600' }, l), sel));
    }
    const theme = h('select', { class: 'input', id: 'j-theme', style: 'max-width:280px' }, [['', 'Match my device (dark by default)'], ['dark', 'Dark'], ['light', 'Light']].map(([v, l]) => h('option', { value: v, selected: (store.get('cm.theme') || '') === v }, l)));
    theme.addEventListener('change', () => { store.set('cm.theme', theme.value); if (theme.value) document.documentElement.dataset.theme = theme.value; else delete document.documentElement.dataset.theme; });
    const initial = h('span', { class: 'pf-av join-av', 'aria-hidden': 'true' }, (st.name || S.me?.account?.username || '?')[0].toUpperCase());
    nm.addEventListener('input', () => { initial.textContent = (nm.value.trim() || S.me?.account?.username || '?')[0].toUpperCase(); });
    b.append(h('h2', { tabindex: '-1' }, 'Your profile'), h('p', { class: 'sub' }, 'How you appear to friends, and how Cinemack looks on this device.'),
      h('div', { class: 'set-card', style: 'display:flex;gap:16px;align-items:center' }, initial, h('div', { class: 'field', style: 'flex:1;margin:0' }, h('label', { for: 'j-name' }, 'Display name'), nm)),
      pv, h('div', { class: 'set-card' }, h('div', { class: 'field', style: 'margin:0' }, h('label', { for: 'j-theme' }, 'Appearance'), theme)),
      nav(next, back, { label: 'Finish' }));
  },
};
