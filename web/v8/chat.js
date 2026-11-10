// Ask Cinemack (10 Nov 2026 redesign): conversation sidebar (search, grouped by date, rename, delete, collapsible),
// a focused conversation column, recommendation cards, a Sources disclosure, follow-ups, and filter chips that share
// one store with Home. Streaming is unchanged: /api/chat sends status, sources (films), tokens and done events.
import { $, $$, S, h, I, LOGO, ART, api, post, view, store, toast, sheet, popMenu, newId, F, FILTERS, GENRES, shortGenre, firstGenre,
  md, splitSources, sourcesEl, ratingEl, rt, wantButton, showtimesSheet, showSheet, gate, hdrs, esc, on, emit, fmtDate } from './core.js';

const c = S.chat;
let ui = null;                 // the mounted page's elements
export function newChat(priv = false) { Object.assign(c, { convId: newId(), history: [], recent: [], conv: null, log: null, title: '', private: !!priv }); }
const clock = () => new Date().toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });

export function pageChat(params) {
  view.innerHTML = '';
  view.className = 'chat-shell' + (store.get('cm.side') === '0' ? ' collapsed' : '');
  // ---------------------------------------------------------------- sidebar
  const q = h('input', { type: 'search', placeholder: 'Search conversations', 'aria-label': 'Search conversations', autocomplete: 'off' });
  const list = h('nav', { class: 'clist', 'aria-label': 'Conversations' });
  const collapse = h('button', { class: 'iconbtn', type: 'button', 'aria-label': 'Hide conversation list', html: I.dleft, onclick: () => setSide(false) });
  const side = h('aside', { class: 'cside', 'aria-label': 'Conversation history' }, h('div', { class: 'cside-in' },
    h('div', { class: 'cside-top' }, h('button', { class: 'btn primary', type: 'button', html: I.plus + 'New chat', onclick: () => startNew() }),
      h('button', { class: 'iconbtn bordered', type: 'button', 'aria-label': 'New private chat', title: 'Private chat: not saved, nothing remembered', html: I.lock, onclick: () => startNew(true) }), collapse),
    h('div', { class: 'csearch' }, h('span', { html: I.search }), q), list,
    h('div', { class: 'cside-foot' }, h('a', { href: '#/profile?tab=settings&sec=preferences', html: I.gear + 'Chat settings' }), h('a', { href: '/how', html: I.help + 'How Cinemack works' }))));
  q.addEventListener('input', () => drawList(list, q.value));
  // ---------------------------------------------------------------- main column
  const msgs = h('div', { class: 'msgs', id: 'msgs' });
  const scroller = h('div', { class: 'cscroll', id: 'msgs-wrap', tabindex: '-1', role: 'log', 'aria-label': 'Conversation', 'aria-live': 'polite' }, msgs);
  const ta = h('textarea', { id: 'chat-q', rows: '1', placeholder: 'Ask about a movie, show, or what to watch…', 'aria-label': 'Message Cinemack', enterkeyhint: 'send' });
  const send = h('button', { class: 'send', type: 'button', id: 'chat-send', 'aria-label': 'Send', html: I.send });
  const grow = () => { ta.style.height = 'auto'; ta.style.height = Math.min(ta.scrollHeight, 160) + 'px'; };
  ta.addEventListener('input', grow);
  ta.addEventListener('keydown', (e) => { if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); submit(); } });
  const submit = () => { if (c.busy) { c.ctrl?.abort(); return; } const t = ta.value.trim(); if (t) { ta.value = ''; grow(); ask(t); } };
  send.addEventListener('click', submit);
  const fbar = h('div', { class: 'cfilters', role: 'group', 'aria-label': 'Filters for recommendations' });
  const expand = h('button', { class: 'iconbtn bordered tog desk', type: 'button', 'aria-label': 'Show conversation list', html: I.menu, onclick: () => setSide(true) });
  const head = h('div', { class: 'chead' },
    h('button', { class: 'iconbtn bordered tog mob', type: 'button', 'aria-label': 'Your conversations', html: I.menu, onclick: openDrawer }),
    expand, h('span', { class: 'ico', html: I.bubble }),
    h('div', { style: 'flex:1;min-width:0' }, h('h1', {}, 'Ask Cinemack'), h('p', {}, 'Recommendations, film facts, where to watch, and what is coming out.')),
    h('button', { class: 'iconbtn bordered tog mob', type: 'button', 'aria-label': 'New chat', html: I.plus, onclick: () => startNew() }));
  // round 21: private chat (X-MB-Private: 1): not saved to the list, nothing remembered, history and taste not used
  const pbar = h('div', { class: 'pbar', role: 'status', hidden: !c.private }, h('span', { html: I.lock }),
    h('span', { class: 'pt' }, h('b', {}, 'Private chat. '), 'Not saved to your chats, nothing remembered, and your history is not used.'),
    h('button', { class: 'btn ghost sm', type: 'button', onclick: () => startNew() }, 'End private chat'));
  const main = h('section', { class: 'cmain' + (c.private ? ' private' : ''), 'aria-label': 'Conversation' }, head, pbar, scroller,
    h('div', { class: 'cbottom' }, h('div', { class: 'cbottom-in' }, fbar,
      h('div', { class: 'composer' }, h('span', { class: 'spark', html: I.sparkle }), ta, send),
      h('div', { class: 'cfine' }, 'Cinemack can be wrong; answers show their sources. ', h('a', { href: '/about' }, 'Credits')))));
  view.append(side, main);
  ui = { msgs, scroller, send, ta, list, fbar, expand, side, pbar, main };
  syncSideButtons();
  drawFilters();
  on('cm:filters', drawFilters, fbar);
  // ---------------------------------------------------------------- content
  if (c.log?.length) { msgs.append(...c.log); requestAnimationFrame(() => { scroller.scrollTop = scroller.scrollHeight; }); } else msgs.append(intro());
  loadList();
  const pre = S.pendingAsk || params.get('ask'); S.pendingAsk = null;
  if (params.get('chat')) loadChat(params.get('chat')).then(() => { if (pre) ask(pre); });
  else if (pre) { history.replaceState(null, '', '#/ask'); if (c.log?.length) { newChat(); msgs.innerHTML = ''; } ask(pre); }
  else if (matchMedia('(min-width:1024px)').matches) ta.focus({ preventScroll: true });
}

function setSide(open) {
  store.set('cm.side', open ? '1' : '0');
  view.classList.toggle('collapsed', !open); syncSideButtons();
  (open ? $('.cside .btn.primary') : ui.expand)?.focus();
}
function syncSideButtons() { const col = view.classList.contains('collapsed'); ui.expand.hidden = !col || !matchMedia('(min-width:1024px)').matches; }
addEventListener('resize', () => { if (ui && document.body.contains(ui.expand)) syncSideButtons(); });
function startNew(priv = false) {
  if (c.busy) c.ctrl?.abort(); newChat(priv); ui.msgs.innerHTML = ''; ui.msgs.append(intro()); markCurrent();
  ui.pbar.hidden = !priv; ui.main.classList.toggle('private', priv);
  ui.ta.placeholder = priv ? 'Private: ask anything; nothing is saved' : 'Ask about a movie, show, or what to watch…';
  if (priv) toast('Private chat: not saved, nothing remembered.');
  ui.ta.focus(); history.replaceState(null, '', '#/ask');
}
function openDrawer() {
  sheet('Your conversations', (b, d) => {
    d.classList.add('drawer-list');
    const q2 = h('input', { class: 'input', type: 'search', placeholder: 'Search conversations', 'aria-label': 'Search conversations' });
    const l2 = h('nav', { class: 'clist', 'aria-label': 'Conversations', style: 'margin-top:12px' });
    b.append(h('button', { class: 'btn primary block', type: 'button', html: I.plus + 'New chat', onclick: () => { d.close(); startNew(); } }),
      h('button', { class: 'btn secondary block', type: 'button', style: 'margin-top:8px', html: I.lock + 'New private chat', onclick: () => { d.close(); startNew(true); } }), h('div', { style: 'margin-top:12px' }, q2), l2);
    q2.addEventListener('input', () => drawList(l2, q2.value, d));
    drawList(l2, '', d);
  }, { side: true, left: true });
}

// ---------------------------------------------------------------- conversation list
let chatList = [];
async function loadList() {
  try { chatList = (await api('/api/chats')).chats || []; } catch { chatList = []; }
  if (ui && document.body.contains(ui.list)) drawList(ui.list, $('.csearch input')?.value || '');
}
function groupOf(ts) {
  const d = new Date((ts < 1e12 ? ts * 1000 : ts)), t = new Date(new Date().toDateString());
  const days = Math.floor((t - new Date(d.toDateString())) / 864e5);
  return days <= 0 ? 'Today' : days === 1 ? 'Yesterday' : days < 7 ? 'Previous 7 days' : days < 31 ? 'Previous 30 days' : 'Older';
}
function whenOf(ts) {
  const d = new Date((ts < 1e12 ? ts * 1000 : ts)), g = groupOf(ts);
  return g === 'Today' ? d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' }) : g === 'Yesterday' ? 'Yesterday' : g === 'Previous 7 days' ? d.toLocaleDateString([], { weekday: 'short' }) : d.toLocaleDateString([], { month: 'short', day: 'numeric' });
}
function drawList(el, filter = '', dlg = null) {
  el.innerHTML = '';
  const f = filter.trim().toLowerCase();
  const items = chatList.filter(ch => !f || (ch.title || '').toLowerCase().includes(f));
  if (!chatList.length) { el.append(h('p', { class: 'muted small', style: 'padding:8px' }, 'No conversations yet. Ask anything and it is saved here.')); return; }
  if (!items.length) { el.append(h('p', { class: 'muted small', style: 'padding:8px' }, `No conversation matches "${filter}".`)); return; }
  let g0 = null;
  for (const ch of items) {
    const g = groupOf(ch.updated);
    if (g !== g0) { el.append(h('div', { class: 'grp' }, g)); g0 = g; }
    const row = h('div', { class: 'crow', 'data-id': ch.id, 'aria-current': String(ch.id === c.convId) });
    const open = h('button', { class: 'open', type: 'button', title: ch.title || 'Chat', onclick: () => { dlg?.close(); loadChat(ch.id); } },
      h('span', { html: I.bubble }), h('span', { class: 'tt' }, ch.title || 'Chat'), h('span', { class: 'tm' }, whenOf(ch.updated)));
    const more = h('button', { class: 'iconbtn more', type: 'button', 'aria-label': `More for ${ch.title || 'chat'}`, 'aria-haspopup': 'menu', 'aria-expanded': 'false', html: I.dots,
      onclick: (e) => popMenu(e.currentTarget, [{ label: 'Rename', icon: I.edit, onclick: () => renameRow(row, ch) }, { label: 'Delete', icon: I.trash, danger: true, onclick: () => deleteChat(ch) }], { label: 'Conversation actions' }) });
    row.append(open, more);
    el.append(row);
  }
}
function markCurrent() { $$('.crow').forEach(r => r.setAttribute('aria-current', String(r.dataset.id === c.convId))); }
function renameRow(row, ch) {
  const inp = h('input', { value: ch.title || '', 'aria-label': 'Conversation name', maxlength: '80' });
  row.innerHTML = ''; row.append(inp); inp.focus(); inp.select();
  let done = false;
  const finish = async (save) => {
    if (done) return; done = true;
    const t = inp.value.trim();
    if (save && t && t !== ch.title) { try { const j = await post('/api/v8/chats/rename', { id: ch.id, title: t }); ch.title = j.title; toast('Conversation renamed.'); } catch (e) { toast(e.message, null, true); } }
    drawList(row.parentElement, $('.csearch input')?.value || '');
  };
  inp.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); finish(true); } else if (e.key === 'Escape') { e.preventDefault(); finish(false); } });
  inp.addEventListener('blur', () => finish(true));
}
function deleteChat(ch) {
  sheet('Delete conversation?', (b, d) => {
    b.append(h('p', { style: 'margin-bottom:16px' }, `"${ch.title || 'Chat'}" and anything Cinemack remembered from it will be deleted. This cannot be undone.`),
      h('div', { class: 'actions', style: 'margin:0' }, h('button', { class: 'btn danger', type: 'button', onclick: async () => {
        try { await api('/api/chats/' + encodeURIComponent(ch.id), { method: 'DELETE' }); d.close(); toast('Conversation deleted.');
          chatList = chatList.filter(x => x.id !== ch.id); if (ch.id === c.convId) startNew(); else loadList(); } catch (e) { toast(e.message, null, true); } } }, 'Delete'),
        h('button', { class: 'btn secondary', type: 'button', onclick: () => d.close() }, 'Cancel')));
  });
}
async function loadChat(id) {
  if (c.busy) c.ctrl?.abort();
  try {
    const j = await api('/api/chats/' + encodeURIComponent(id));
    newChat(); c.convId = id; c.title = j.title || ''; ui.msgs.innerHTML = ''; ui.pbar.hidden = true; ui.main.classList.remove('private');
    for (const t of j.turns || []) {
      ui.msgs.append(userMsg(t.question, null));
      const b = botShell(null); b.status.remove(); render(b, t.answer || '', true);
      if (t.films?.length) b.recs.append(recList(t.films));
      ui.msgs.append(b.el);
      c.history.push({ role: 'user', content: t.question }, { role: 'assistant', content: t.answer || '' });
    }
    c.history = c.history.slice(-40); c.recent = j.recent_titles || []; c.conv = j.conv || null;
    c.log = [...ui.msgs.children];
    ui.scroller.scrollTop = ui.scroller.scrollHeight;
    markCurrent();
    history.replaceState(null, '', '#/ask');
  } catch (e) { toast(e.message, null, true); }
}

// ---------------------------------------------------------------- 3c preference chips (round 21)
// A limit said in a message ("nothing over two hours", "no horror", "we only have Hulu") is offered as a chip under
// it. Nothing changes until the chip is pressed; then it joins this chat's filter bar like any other filter.
async function offerChips(after, text) {
  let j; try { j = await api(`/api/v8/chips?text=${encodeURIComponent(text.slice(0, 600))}`); } catch { return; }
  if (!j.chips?.length || !document.body.contains(after)) return;
  const row = h('div', { class: 'offer', role: 'group', 'aria-label': 'Use what you said as a filter' }, h('span', { class: 'small muted' }, 'Use as a filter for this chat?'));
  for (const ch of j.chips) {
    const b = h('button', { class: 'chip', type: 'button', 'aria-pressed': 'false', html: I.plus + `<span>${ch.label}</span>` });
    b.addEventListener('click', () => {
      const f = S.filters;
      if (ch.field === 'services') { f.services = ch.value; f.services_only = true; }
      else if (ch.field === 'never_genres') f.never_genres = [...new Set([...(f.never_genres || []), ...ch.value])];
      else f[ch.field] = ch.value;
      F.save(); b.setAttribute('aria-pressed', 'true'); b.disabled = true; b.innerHTML = I.check + `<span>${ch.label}</span>`;
      toast(`Filter on: ${ch.label}. It applies to the next answers in this chat.`);
    });
    row.append(b);
  }
  after.after(row);
}

// ---------------------------------------------------------------- filters (shared with Home through S.filters)
function drawFilters() {
  const bar = ui.fbar, f = S.filters; bar.innerHTML = '';
  const n = F.count();
  bar.append(h('button', { class: 'chip fbtn', type: 'button', html: I.filter + `<span>Filters${n ? ` (${n})` : ''}</span>`, onclick: () => filterSheet() }));
  const tag = (text, clear) => bar.append(h('button', { class: 'chip on', type: 'button', 'aria-label': `Remove filter: ${text}`, onclick: () => { clear(); F.save(); } }, text, h('span', { class: 'x', html: I.x })));
  if ((f.genres || []).length && !F.allGenres()) {          // many genres collapse into one chip; every genre = no filter
    if (f.genres.length > 3) tag(`${f.genres.length} genres (any)`, () => { f.genres = []; });
    else f.genres.forEach(g => tag(shortGenre(g), () => { f.genres = f.genres.filter(x => x !== g); }));
  }
  if (f.max_runtime) tag((FILTERS.max_runtime.find(x => x[0] === String(f.max_runtime)) || [])[1], () => { delete f.max_runtime; });
  if (f.era) tag((FILTERS.era.find(x => x[0] === f.era) || [])[1] || f.era, () => { delete f.era; });
  if (f.max_cert) tag(`${f.max_cert} or milder`, () => { delete f.max_cert; });
  if (f.services_only) tag((f.services || []).length ? `${f.services.length === 1 ? f.services[0] : `${f.services.length} services`} only` : 'My services', () => { delete f.services_only; delete f.services; });
  if ((f.never_genres || []).length) tag('No ' + f.never_genres.map(g => shortGenre(g).toLowerCase()).join(', '), () => { delete f.never_genres; });
  if (f.min_year) tag(`${f.min_year} or later`, () => { delete f.min_year; });
  const add = (label, fn) => bar.append(h('button', { class: 'chip add', type: 'button', 'aria-haspopup': 'menu', 'aria-expanded': 'false', html: I.plus + `<span>${label}</span>`, onclick: fn }));
  if (!(f.genres || []).length) add('Genre', (e) => popMenu(e.currentTarget, GENRES.map(g => ({ label: shortGenre(g), onclick: () => F.toggleGenre(g) })), { label: 'Genre', title: 'Genre' }));
  if (!f.max_runtime) add('Length', (e) => popMenu(e.currentTarget, FILTERS.max_runtime.slice(1).map(([v, l]) => ({ label: l, onclick: () => { f.max_runtime = Number(v); F.save(); } })), { label: 'Length', title: 'Length' }));
  if (!f.services_only) add('My services', () => {
    if (!(S.me?.settings?.services || []).length) { toast('Add your streaming services first (Profile, Settings).'); location.hash = '#/profile?tab=settings&sec=services'; return; }
    f.services_only = true; F.save();
  });
  if (!f.era) add('Era', (e) => popMenu(e.currentTarget, FILTERS.era.slice(1).map(([v, l]) => ({ label: l, onclick: () => { f.era = v; F.save(); } })), { label: 'Era', title: 'Era' }));
}
function filterSheet() {
  const f = S.filters;
  sheet('Filters', (b, d) => {
    const on = new Set(f.genres || []);
    const chips = h('div', { class: 'chips', role: 'group', 'aria-label': 'Genres' });
    GENRES.forEach(g => chips.append(h('button', { class: 'chip', type: 'button', 'aria-pressed': String(on.has(g)),
      onclick: (e) => { on.has(g) ? on.delete(g) : on.add(g); e.currentTarget.setAttribute('aria-pressed', String(on.has(g))); } }, shortGenre(g))));
    const draft = { era: f.era || '', max_runtime: f.max_runtime ? String(f.max_runtime) : '', max_cert: f.max_cert || '' };
    const sel = (key, labelText) => { const id = `f-${key}`; const s = h('select', { class: 'input', id }, FILTERS[key].map(([v, l]) => h('option', { value: v, selected: draft[key] === v }, l)));
      s.addEventListener('change', () => { draft[key] = s.value; }); return h('div', { class: 'field' }, h('label', { for: id }, labelText), s); };
    const svc = h('input', { type: 'checkbox', id: 'f-svc', checked: !!f.services_only });
    const long = h('input', { type: 'checkbox', id: 'f-long', checked: !!f.long });
    const hasSvc = (S.me?.settings?.services || []).length;
    b.append(h('div', { class: 'field' }, h('span', { class: 'lab' }, 'Genres (any of these)'), chips),
      h('div', { style: 'display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:0 12px' }, sel('max_runtime', 'Length'), sel('era', 'Era'), sel('max_cert', 'Age rating')),
      h('div', { class: 'field' }, h('label', { class: 'switch' }, svc, 'Only what streams on my services'),
        hasSvc ? null : h('span', { class: 'hint' }, 'You have not added services yet. ', h('a', { href: '#/profile?tab=settings&sec=services', onclick: () => d.close() }, 'Add them'))),
      h('div', { class: 'field' }, h('label', { class: 'switch' }, long, 'Longer, more detailed answers'),
        h('span', { class: 'hint' }, 'Off: answers stay short. Your own length setting wins over this.')),
      h('p', { class: 'hint', style: 'margin-bottom:16px' }, 'Filters are enforced in code for recommendations here and on Home, not just suggested to the model.'),
      h('div', { class: 'actions', style: 'margin:0' }, h('button', { class: 'btn primary', type: 'button', onclick: () => {
        const keep = { never_genres: f.never_genres, min_year: f.min_year, services: f.services };   // chips from messages stay
        S.filters = { genres: GENRES.filter(g => on.has(g)) };
        for (const [k, v] of Object.entries(keep)) if (v && (!Array.isArray(v) || v.length)) S.filters[k] = v;
        if (!svc.checked) delete S.filters.services;
        if (draft.era) S.filters.era = draft.era;
        if (draft.max_runtime) S.filters.max_runtime = Number(draft.max_runtime);
        if (draft.max_cert) S.filters.max_cert = draft.max_cert;
        if (svc.checked) S.filters.services_only = true;
        if (long.checked) S.filters.long = true;
        F.save(); d.close(); } }, 'Apply'),
        h('button', { class: 'btn ghost', type: 'button', onclick: () => { S.filters = { genres: [] }; F.save(); d.close(); } }, 'Clear all')));
  });
}

// ---------------------------------------------------------------- messages
function intro() {
  const ex = ['Find me a thriller under 90 minutes', 'Movies like Prisoners but less bleak', 'The film where a family of con artists moves into a rich house', "What's new on streaming this week?"];
  return h('div', { class: 'chat-intro' }, h('div', { html: ART.reel }), h('h2', {}, 'What are you in the mood for?'),
    h('p', { class: 'muted' }, 'Describe a film you half-remember, ask about one you know, or ask for recommendations. Answers come from Cinemack\'s film database and cite their sources.'),
    h('div', { class: 'ex' }, ex.map(t => h('button', { type: 'button', onclick: () => ask(t) }, t))));
}
function userMsg(text, time) {
  return h('div', { class: 'msg user' }, h('span', { class: 'tx' }, text), time ? h('span', { class: 'tm' }, time) : null);
}
function botShell(time) {
  const status = h('div', { class: 'status', role: 'status' }, h('span', { class: 'dot' }), h('span', { class: 'st-t' }, 'Finding the right films…'), h('span', { class: 'st-s' }));
  const body = h('div', { class: 'md' }), recs = h('div', {}), foot = h('div', { class: 'ans-foot' }), extra = h('div', { class: 'extra', style: 'display:grid;gap:10px' });
  const bd = h('div', { class: 'bd' }, h('div', { class: 'who' }, 'Cinemack', time ? h('span', { class: 'tm' }, time) : null), status, body, recs, foot, extra);
  const el = h('div', { class: 'msg bot' }, h('span', { class: 'av', html: LOGO }), bd);
  return { el, status, body, recs, foot, extra, setStatus: (t) => { status.querySelector('.st-t').textContent = t; } };
}
function render(b, answer, final = false) {
  const src = splitSources(answer);
  b.body.innerHTML = md(src.text);
  if (final) { b.foot.innerHTML = ''; const s = sourcesEl(src); if (s) b.foot.append(s); b.text = src.text; }
}
// Recommendation card: poster, title, year · genre · runtime, rating with its source, the personal reason when the
// search recorded one, and actions. The model's prose is not repeated on the card.
function recCard(f) {
  const tv = f.kind === 'tv' || (f.id != null && f.id < 0);
  const name = `${f.title || 'Untitled'}${f.year ? ` (${f.year})` : ''}`;
  const href = tv ? null : `#/film/${f.id}`;
  const g = firstGenre(f.genres).slice(0, 2).join(', ');
  const meta = h('div', { class: 'mm' }, [f.year ? String(f.year).slice(0, 9) : null, firstGenre(f.genres)[0] ? shortGenre(firstGenre(f.genres)[0]) : null, f.runtime ? rt(f.runtime) : null, tv ? 'TV series' : null].filter(Boolean).map(x => h('span', {}, x)));
  const acts = h('div', { class: 'acts' });
  if (!tv) {
    acts.append(h('a', { class: 'btn outline sm', href: `#/film/${f.id}?go=watch`, html: I.play + 'Where to watch' }));
    if (f.in_theatres) acts.append(h('button', { class: 'btn secondary sm', type: 'button', html: I.ticket + 'Showtimes', onclick: () => showtimesSheet(f) }));
    acts.append(h('a', { class: 'btn ghost sm det', href }, 'Details'));
  } else acts.append(h('button', { class: 'btn outline sm', type: 'button', html: I.tv + 'Follow or track', onclick: () => showSheet({ id: Math.abs(f.id), title: f.title, poster: f.poster, year: f.year }) }));
  return h('article', { class: 'rec', role: 'listitem', 'aria-label': name },
    h(href ? 'a' : 'span', { class: 'pp', href, tabindex: '-1', 'aria-hidden': 'true' }, f.poster ? h('img', { src: f.poster_lg || f.poster, alt: '', loading: 'lazy', width: 112, height: 168 }) : h('span', { class: 'ph' }, f.title)),
    h('div', { class: 'in' }, h('div', { class: 'hd' }, h(href ? 'a' : 'span', { class: 'tt', href }, f.title || 'Untitled'), !tv && f.id > 0 ? wantButton(f) : null),
      meta, ratingEl(f), f.why ? h('p', { class: 'why' }, f.why) : null,
      f.on_services?.length ? h('div', { class: 'on' }, 'On your ' + f.on_services.join(', ')) : null, acts));
}
function recList(films) {
  const MAX = 4;
  const list = h('div', { class: 'recs', role: 'list', 'aria-label': 'Films in this answer' }, films.slice(0, MAX).map(recCard));
  const wrap = h('div', { style: 'display:grid;gap:10px' }, list);
  if (films.length > MAX) wrap.append(h('button', { class: 'btn ghost sm recs-more', type: 'button', onclick: (e) => { list.append(...films.slice(MAX).map(recCard)); e.currentTarget.remove(); } }, `Show ${films.length - MAX} more`));
  return wrap;
}
const recSkeleton = () => h('div', { class: 'recs', 'aria-hidden': 'true' }, [0, 1].map(() => h('div', { class: 'rec' }, h('div', { class: 'skel', style: 'aspect-ratio:2/3' }),
  h('div', { style: 'display:grid;gap:8px;align-content:start' }, h('div', { class: 'skel', style: 'height:18px;width:70%' }), h('div', { class: 'skel', style: 'height:12px;width:50%' }), h('div', { class: 'skel', style: 'height:12px;width:85%' })))));

// One question. The message shows at once, a status line counts seconds, film cards arrive when the search is done
// (before the answer), the answer streams in, Stop cancels. Timings go to window.__chatMarks (tools/accept_redesign.js).
export async function ask(text, opts = {}) {
  if (c.busy) return;
  const msgs = ui.msgs, sc = ui.scroller;
  $('.chat-intro', msgs)?.remove();
  const um = userMsg(text, clock()); msgs.append(um);
  offerChips(um, text);
  const b = botShell(clock()); msgs.append(b.el);
  const toEnd = (force) => { if (force || sc.scrollHeight - sc.scrollTop - sc.clientHeight < 160) sc.scrollTop = sc.scrollHeight; };
  toEnd(true);
  c.busy = true; c.ctrl = new AbortController();
  ui.send.innerHTML = I.stop; ui.send.setAttribute('aria-label', 'Stop answering');
  let answer = '', films = [], intent = null, rid = null, shown = false, last = Date.now(), stalled = false;
  const t0 = performance.now(), marks = {};
  const skel = recSkeleton(); b.recs.append(skel);
  const tick = setInterval(() => {
    const s = Math.round((performance.now() - t0) / 1000);
    b.status.querySelector('.st-s').textContent = s >= 3 ? ` ${s} s` : '';
    if (!stalled && Date.now() - last > 90000) { stalled = true; b.setStatus('Still working. The shared GPU may be busy; you can wait or press Stop.'); }
  }, 1000);
  const showFilms = () => { skel.remove(); if (!shown && films.length) { shown = true; b.recs.append(recList(films)); toEnd(); } };
  let raf = 0;
  const paint = () => { raf = 0; render(b, answer); toEnd(); };
  const handle = (ev, d) => {
    last = Date.now();
    if (ev === 'status') b.setStatus(d.stage === 'queued' ? `Waiting for the GPU: ${d.ahead} ahead of you` : d.stage === 'answering' ? 'Writing the answer…' : 'Finding the right films…');
    else if (ev === 'sources') { marks.sources = performance.now() - t0; films = d.films || []; intent = d.intent; rid = d.id; showFilms();
      if (d.alternatives?.length) renderAlts(b, d.alternatives); if (d.choices?.length) renderDym(b, d.choices); }
    else if (ev === 'token') { if (!marks.first) marks.first = performance.now() - t0; b.status.hidden = true; answer += d.text; if (!raf) raf = requestAnimationFrame(paint); }
    else if (ev === 'replace') { answer = d.text || ''; render(b, answer); }
    else if (ev === 'done') { marks.done = performance.now() - t0; rid = d.id || rid; c.recent = d.recent_titles || c.recent; if (d.conv) c.conv = d.conv; marks.server = d.timings; marks.cached = d.cached; }
    else if (ev === 'error') throw Object.assign(new Error(d.message), { friendly: true });
  };
  try {
    const res = await fetch('/api/chat', { method: 'POST', headers: hdrs(), signal: c.ctrl.signal,
      body: JSON.stringify({ message: text, history: c.history, recent_titles: c.recent, conv: c.conv, think: false, filters: opts.filters || F.forChat() }) });
    marks.headers = performance.now() - t0;
    if (res.status === 401) { b.el.remove(); gate("That code didn't work."); return; }
    if (!res.ok) { let m = `The server answered ${res.status}.`; try { m = (await res.json()).error || m; } catch {} throw Object.assign(new Error(m), { friendly: true }); }
    const reader = res.body.getReader(), dec = new TextDecoder(); let buf = '';
    for (;;) {
      const { value, done } = await reader.read(); if (done) break;
      buf += dec.decode(value, { stream: true }); let i;
      while ((i = buf.indexOf('\n\n')) >= 0) {
        const raw = buf.slice(0, i); buf = buf.slice(i + 2); let ev = 'message', data = '';
        for (const line of raw.split('\n')) { if (line.startsWith('event:')) ev = line.slice(6).trim(); else if (line.startsWith('data:')) data += line.slice(5).trim(); }
        if (data) handle(ev, JSON.parse(data));
      }
    }
    if (!answer) throw Object.assign(new Error('The model finished without an answer. Try rephrasing.'), { friendly: true });
    c.history.push({ role: 'user', content: text }, { role: 'assistant', content: answer.trim() }); c.history = c.history.slice(-40);
  } catch (e) {
    b.status.hidden = true;
    if (e.name === 'AbortError') b.extra.append(h('p', { class: 'muted small' }, 'Stopped.'));
    else b.extra.append(h('div', { class: 'notice warn' }, e.friendly ? e.message : "Couldn't reach the Cinemack server.", ' ',
      h('button', { class: 'btn secondary sm', type: 'button', onclick: () => { b.el.previousSibling?.remove(); b.el.remove(); ask(text, opts); } }, 'Try again')));
  } finally {
    clearInterval(tick); if (raf) cancelAnimationFrame(raf);
    c.busy = false; c.ctrl = null; b.status.hidden = true; skel.remove();
    if (document.body.contains(ui.send)) { ui.send.innerHTML = I.send; ui.send.setAttribute('aria-label', 'Send'); }
    if (answer.trim()) {
      render(b, answer, true);
      showFilms();
      renderFoot(b, rid, marks);
      renderFollow(b, films, intent);
    }
    marks.painted = performance.now() - t0;
    (window.__chatMarks = window.__chatMarks || []).push(Object.assign({ q: text.slice(0, 60) }, marks));
    c.log = [...msgs.children];
    toEnd();
    loadList().then(markCurrent);
  }
}
function renderFoot(b, rid, marks) {
  const fb = h('div', { class: 'fb' });
  if (rid) {
    const vote = async (v, btn) => { try { await post('/api/feedback', { id: rid, vote: v, comment: '', reasons: [] }); $$('button', fb).forEach(x => x.setAttribute('aria-pressed', String(x === btn))); toast('Thanks, feedback saved.'); } catch (e) { toast(e.message, null, true); } };
    const up = h('button', { class: 'iconbtn', type: 'button', 'aria-label': 'Good answer', 'aria-pressed': 'false', html: I.up });
    const dn = h('button', { class: 'iconbtn', type: 'button', 'aria-label': 'Bad answer', 'aria-pressed': 'false', html: I.down });
    up.onclick = () => vote('up', up); dn.onclick = () => vote('down', dn); fb.append(up, dn);
  }
  fb.append(h('button', { class: 'iconbtn', type: 'button', 'aria-label': 'Copy answer', html: I.copy, onclick: async () => { try { await navigator.clipboard.writeText(b.text || ''); toast('Answer copied.'); } catch { toast("Couldn't copy.", null, true); } } }));
  if (marks?.done) {
    const first = marks.first ? (marks.first / 1000).toFixed(1) : null, all = (marks.done / 1000).toFixed(1);
    fb.prepend(h('span', { class: 'tiny faint', style: 'align-self:center;margin-right:6px', title: `First words after ${first ?? '?'} s; full answer after ${all} s${marks.cached ? ' (from cache)' : ''}` }, marks.cached ? 'cached' : `${all} s`));
  }
  b.foot.append(fb);
}
function renderAlts(b, alts) {
  b.extra.append(h('div', { class: 'alts' }, 'Not the one you meant?', alts.slice(0, 3).map(a => h('button', { type: 'button', onclick: () => ask(`I meant ${a.title}${a.year ? ` (${a.year})` : ''}`) }, `${a.title}${a.year ? ` (${a.year})` : ''}`))));
}
function renderDym(b, choices) {
  b.extra.append(h('div', { class: 'follow' }, h('span', { class: 'muted small', style: 'align-self:center' }, 'Did you mean'),
    choices.slice(0, 4).filter(x => x && x.label && x.question).map(x => h('button', { class: 'chip', type: 'button', onclick: () => ask(x.question) }, x.label))));
}
function renderFollow(b, films, intent) {
  const f = films[0]; const T = f ? `${f.title}${f.year ? ` (${f.year})` : ''}` : null;
  const items = [];
  if (T && (intent === 'identify' || intent === 'named_film')) items.push(['Where can I watch it?', `Where can I watch ${T}?`], ['Similar films', `Recommend films similar to ${T}.`], ['Tell me more', `Tell me more about ${T}: things you haven't told me yet.`]);
  else if (intent === 'recommend') items.push(['More like these', 'More recommendations like those, please.'], ['Something shorter', 'Something similar but under 100 minutes.'], ['Something lighter', 'Something in the same spirit but lighter in tone.']);
  else items.push(['Tell me more', T ? `Tell me more about ${T}.` : 'Tell me more about that.']);
  b.extra.append(h('div', { class: 'follow', role: 'group', 'aria-label': 'Suggested follow-ups' }, items.map(([l, q]) => h('button', { class: 'chip', type: 'button', onclick: () => ask(q) }, l))));
}
