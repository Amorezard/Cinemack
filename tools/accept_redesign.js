// Acceptance checks for the 10 Nov 2026 redesign (master prompt section 11, tests 1-12), against the local mock
// server (MOVIE_BUFF_MOCK=1, port 8765, fixture facts.db from make_demo.py + make_demo2.py, user seeded by
// seed_demo.py). Chat and Pick Tonight answers are replayed as SSE with fixture films where a check needs known films;
// everything else goes through the real routes. Prints one line per check and a summary; exit code 1 on any failure.
//   node tools/accept_redesign.js            ONLY=t2,t6 to run a subset
const { chromium, request } = require('playwright');
const fs = require('fs');
const { setup } = require('./shots_redesign.js');
const BASE = process.env.BASE || 'http://127.0.0.1:8765';
const AXE = fs.readFileSync(__dirname + '/node_modules/axe-core/axe.min.js', 'utf8');
const ONLY = (process.env.ONLY || '').split(',').filter(Boolean);
const R = [];
const ok = (t, name, pass, detail = '') => { R.push({ t, name, pass: !!pass, detail }); console.log(`${pass ? 'PASS' : 'FAIL'} ${t} ${name}${detail ? ` - ${detail}` : ''}`); };
const H = (user) => ({ 'Content-Type': 'application/json', 'X-Access-Code': 'movies', 'X-MB-User': user });
async function apiJ(user, path, body) {
  const r = await fetch(BASE + path, { method: body ? 'POST' : 'GET', headers: H(user), body: body ? JSON.stringify(body) : undefined });
  return r.json();
}
function sse(events) { return events.map(([e, d]) => `event: ${e}\ndata: ${JSON.stringify(d)}\n\n`).join(''); }
async function fixtureFilms() {
  const j = await apiJ('fx', '/api/v8/calendar?start=2024-01-01&days=180');
  const j2 = await apiJ('fx', '/api/v8/calendar?start=2026-06-01&days=180');
  const by = {};
  for (const e of [...(j.events || []), ...(j2.events || [])]) if (e.film) by[e.film.id] = e.film;
  return Object.values(by);
}
async function newPage(b, user, vp = { width: 1440, height: 900 }) {
  const ctx = await b.newContext({ viewport: vp });
  await setup(ctx, user);
  const page = await ctx.newPage();
  page.errors = [];
  page.on('pageerror', e => page.errors.push(e.message));
  page.on('console', m => { if (m.type() === 'error' && !/favicon|ERR_FAILED|status of 40[134]/.test(m.text())) page.errors.push(m.text()); });
  return { ctx, page };
}
const go = async (page, hash) => { await page.goto(`${BASE}/v8${hash}`, { waitUntil: 'networkidle' }); await page.waitForTimeout(350); };
const run = (k) => !ONLY.length || ONLY.includes(k);

(async () => {
  const b = await chromium.launch();
  const films = await fixtureFilms();
  const W = 'demo-wes';

  // ------------------------------------------------------------ Test 1: chat layout
  if (run('t1')) for (const vp of [{ width: 1440, height: 900 }, { width: 390, height: 844 }]) {
    const tag = `t1@${vp.width}`;
    const { ctx, page } = await newPage(b, `chat-${vp.width}`, vp);
    const answer = 'Two tight thrillers under 90 minutes; both on your services.\n\n*Sources: [Example article](https://en.wikipedia.org/wiki/Thriller_film); reviews link to their pages (Wikipedia, CC BY-SA). Film data from TMDB.*';
    const pick = films.filter(f => f.runtime && f.runtime <= 90).slice(0, 2);
    await page.route('**/api/chat', r => r.fulfill({ status: 200, contentType: 'text/event-stream', body: sse([['status', { stage: 'searching' }], ['sources', { films: pick, intent: 'recommend', id: 'm1' }],
      ...answer.match(/[\s\S]{1,24}/g).map(t => ['token', { text: t }]), ['done', { id: 'm1', timings: {} }]]) }));
    await go(page, '#/ask');
    const desk = vp.width >= 1024;
    ok(tag, 'history reachable', desk ? await page.isVisible('.cside .btn.primary') : await page.isVisible('.chead .tog.mob'));
    if (!desk) { await page.click('.chead .tog.mob'); ok(tag, 'history drawer opens', await page.isVisible('dialog.sheet.side')); await page.keyboard.press('Escape'); }
    const comp = await page.locator('.composer').boundingBox();
    ok(tag, 'composer on screen', comp && comp.y + comp.height <= vp.height + 1 && comp.y > vp.height * 0.5, comp ? `bottom at ${Math.round(comp.y + comp.height)}` : 'missing');
    await page.fill('#chat-q', 'Find me a thriller under 90 minutes'); await page.keyboard.press('Enter');
    await page.waitForSelector('.rec'); await page.waitForTimeout(300);
    ok(tag, 'film cards', (await page.locator('.rec').count()) === pick.length && await page.isVisible('.rec .bm'));
    const raw = await page.locator('.msg.bot .md').last().innerText();
    ok(tag, 'citations rendered, no raw markdown', !/\]\(http/.test(raw) && await page.isVisible('details.srcs summary'), raw.slice(0, 50));
    await page.click('details.srcs summary');
    ok(tag, 'sources open with licence note', (await page.locator('details.srcs a[href^="https://en.wikipedia.org"]').count()) === 1 && /CC BY-SA/.test(await page.locator('details.srcs .lic').innerText()));
    const sc = await page.locator('.cscroll').boundingBox(), bot = await page.locator('.cbottom').boundingBox();
    ok(tag, 'filters/composer below messages, no overlap', bot.y >= sc.y + sc.height - 1, `msgs end ${Math.round(sc.y + sc.height)}, bottom bar ${Math.round(bot.y)}`);
    const pageScroll = await page.evaluate(() => document.documentElement.scrollHeight - innerHeight);
    ok(tag, 'page itself does not scroll (chat column scrolls)', pageScroll <= 1, `${pageScroll}px`);
    if (desk) {
      const lastCard = await page.locator('.rec').last().boundingBox();
      ok(tag, 'short answer fits without scrolling', lastCard.y + lastCard.height <= sc.y + sc.height + 1);
    }
    ok(tag, 'no page errors', !page.errors.length, page.errors.join(' | '));
    await ctx.close();
  }
  if (run('t1')) {   // existing conversations: real (mock) stream saves the chat; it is listed and reopens after reload
    const { ctx, page } = await newPage(b, 'chat-persist');
    await go(page, '#/ask');
    await page.fill('#chat-q', 'recommend something like parasite'); await page.keyboard.press('Enter');
    await page.waitForFunction(() => !document.querySelector('.status:not([hidden])') && document.querySelector('.ans-foot .fb'), null, { timeout: 30000 });
    await page.waitForSelector('.crow');
    await page.reload({ waitUntil: 'networkidle' }); await page.waitForSelector('.crow');
    ok('t1', 'conversation listed after reload', (await page.locator('.crow').count()) >= 1);
    await page.click('.crow .more'); await page.click('text=Rename'); await page.fill('.crow input', 'Parasite night'); await page.keyboard.press('Enter'); await page.waitForTimeout(400);
    ok('t1', 'rename persists', (await (await fetch(BASE + '/api/chats', { headers: H('chat-persist') })).json()).chats[0].title === 'Parasite night');
    await page.click('.crow .open'); await page.waitForSelector('.msg.user');
    ok('t1', 'reopened chat shows its turns', (await page.locator('.msg.user').count()) === 1 && (await page.locator('.msg.bot .md').innerText()).length > 20);
    await page.fill('.csearch input', 'zzz'); ok('t1', 'search filters list', /No conversation matches/.test(await page.locator('.clist').innerText()));
    await ctx.close();
  }

  // ------------------------------------------------------------ Test 2: Pick Tonight
  if (run('t2')) {
    const me = await apiJ(W, '/api/me');
    const svc = me.settings.services || [];
    const thr = films.filter(f => (f.genres || '').includes('Thriller'));
    const good = films.filter(f => f.poster).slice(0, 4).map((f, i) => Object.assign({}, f, { runtime: 84 + i, genres: 'Thriller, Drama', cert: 'PG-13', on_services: ['Netflix'] }));
    const long = Object.assign({}, thr.find(f => f.runtime > 90) || films.find(f => f.runtime > 90), { on_services: ['Netflix'], genres: 'Thriller' });
    const unknown = Object.assign({}, good[0], { id: 999991, title: 'Unknown Runtime', runtime: null });
    const offSvc = Object.assign({}, good[1], { id: 999992, title: 'Not On Services', on_services: [] });
    let sent = null, mode = 'full';
    const { ctx, page } = await newPage(b, W);
    await page.route('**/api/chat', async r => {
      sent = JSON.parse(r.request().postData());
      const list = mode === 'full' ? [long, unknown, offSvc, ...good] : [long, unknown, good[0], good[1]];
      await r.fulfill({ status: 200, contentType: 'text/event-stream', body: sse([['sources', { films: list, intent: 'recommend', id: 'p1' }],
        ['token', { text: `1. **${good[0].title}**: tense and short, a perfect fit.\n2. **${good[1].title}**: a twisty one.\n` }], ['done', { id: 'p1' }]]) });
    });
    await page.route(/\/api\/film\/999\d+/, r => r.fulfill({ status: 404, contentType: 'application/json', body: '{"error":"x"}' }));
    await go(page, '#/pick');
    const choose = async (k, label) => { await page.click(`.pref[data-k="${k}"]`); await page.click(`.pop [role=menuitemradio]:has-text("${label}")`); };
    await choose('mood', 'Thrilling'); await choose('time', 'Under 1½ hours');
    if (svc.length) await choose('services', 'Only my services');
    await page.click('.prefs .go'); await page.waitForSelector('.rk .syn', { timeout: 15000 }); await page.waitForTimeout(800);
    ok('t2', 'request carries hard limits', sent && sent.filters.strict === true && sent.filters.max_runtime === 90 && sent.filters.genre === 'Thriller' && (!svc.length || sent.filters.services_only === true), JSON.stringify(sent?.filters));
    const n = await page.locator('.rk').count();
    ok('t2', 'three ranked results', n === 3, `${n}`);
    ok('t2', 'best match highlighted', await page.isVisible('.rk1 .best') && (await page.locator('.rk1 .num').innerText()) === '1');
    const titles = await page.locator('.rk .tt').allInnerTexts();
    ok('t2', 'hard limits respected (no long, unknown-runtime or off-service film)', !titles.some(t => t.includes(long.title) || t.includes('Unknown Runtime') || t.includes('Not On Services')), titles.join(' | '));
    const reasons = await page.$$eval('.rk', els => els.map(e => e.querySelectorAll('.reasons .tag').length));
    ok('t2', 'every pick has reasons tied to the choices', reasons.every(x => x >= 2), reasons.join(','));
    ok('t2', 'reasons mention the runtime limit', /under your 90-minute limit/.test(await page.locator('.rk1 .reasons').innerText()));
    ok('t2', 'model note attached to its film', /perfect fit/.test(await page.locator('.rk1').innerText()));
    ok('t2', 'no chat transcript as the result', !(await page.locator('.pick-out .msg').count()));
    await page.click('.rk1 [data-want]'); await page.waitForTimeout(500);
    const wl = await apiJ(W, '/api/v8/watchlist/overview');
    ok('t2', 'saving updates the watchlist', wl.items.some(x => x.id === good[0].id));
    mode = 'short'; await page.click('.prefs .go'); await page.waitForFunction(() => document.querySelectorAll('.rk').length === 2 && document.querySelector('.pick-limits'), null, { timeout: 15000 }); await page.waitForTimeout(600);
    ok('t2', 'fewer than three: says why and offers to loosen', (await page.locator('.rk').count()) === 2 && /Only 2 films fit every limit/.test(await page.locator('.pick-limits').innerText()) && (await page.locator('.pick-limits .chip').count()) >= 1);
    ok('t2', 'no page errors', !page.errors.length, page.errors.join(' | '));
    await apiJ(W, '/api/film', { id: good[0].id, title: good[0].title, choice: '' });
    await ctx.close();
  }

  // ------------------------------------------------------------ Tests 3 + 4: calendar
  if (run('t3') || run('t4')) {
    const { ctx, page } = await newPage(b, W);
    const d0 = '2026-11-13';
    await go(page, `#/calendar?view=week&date=${d0}`);
    // 10 Nov, round 18: every release is listed in its column (tiles or rows), nothing clipped, no "+N more"
    const days = await page.$$eval('.wday', els => els.map(e => { const b = e.getBoundingClientRect(); const items = [...e.querySelectorAll('.wt, .wr')];
      return { h: b.height, label: e.querySelector('.dh').getAttribute('aria-label'), n: items.length, more: e.querySelectorAll('.more').length,
        clipped: items.filter(x => { const r = x.getBoundingClientRect(); return r.bottom > b.bottom + 1 || r.right > b.right + 1 || r.width < 20; }).length,
        imgs: [...e.querySelectorAll('img')].filter(i => { const r = i.getBoundingClientRect(), p = i.closest('.wt, .pp').getBoundingClientRect(); return r.width > p.width + 1 || r.height > p.height + 1; }).length }; }));
    ok('t3', 'seven days', days.length === 7);
    ok('t3', 'dates correct (Sun Nov 8 to Sat Nov 14)', /^Sunday, November 8/.test(days[0].label) && /^Saturday, November 14/.test(days[6].label), `${days[0].label} .. ${days[6].label}`);
    ok('t3', 'columns share one height (the busiest day sets it)', new Set(days.map(d => Math.round(d.h))).size === 1, `${Math.round(days[0].h)}px`);
    const acc = days.every(d => d.n === +(d.label.match(/: (\d+) release/) || [0, 0])[1]);
    ok('t3', 'every release of each day is listed', acc, days.map(d => d.n).join(' '));
    ok('t3', 'no "+N more", nothing clipped, no image overflow', days.every(d => !d.more && !d.clipped && !d.imgs), days.map(d => `${d.more}/${d.clipped}/${d.imgs}`).join(' '));
    ok('t3', 'busy day (14 releases) fully shown', days.some(d => d.n >= 14), String(Math.max(...days.map(d => d.n))));
    ok('t3', 'quiet groups get wide tiles, TV gets rows', (await page.locator('.wday .grp.tiles .wt').count()) > 0 && (await page.locator('.wday .grp .wr .mm').filter({ hasText: /premiere|S\d+ E\d+/ }).count()) > 0);
    const busy = days.findIndex(d => !/: 0 release/.test(d.label));
    await page.locator('.wday .dh').nth(busy).click(); await page.waitForTimeout(300);
    const groups = await page.$$eval('.dpanel .dgroup', els => els.map(e => e.dataset.g));
    ok('t3', 'selected day grouped by type', groups.length >= 1 && groups.every(g => ['theaters', 'streaming', 'tv'].includes(g)), groups.join(','));
    await page.click('.chip[data-k="theaters"]'); await page.waitForTimeout(300);
    const g2 = await page.$$eval('.dpanel .dgroup', els => els.map(e => e.dataset.g));
    ok('t3', 'filter updates results', g2.every(g => g === 'theaters'), g2.join(',') || 'empty');
    await page.click('.cal-nav [aria-label="Next"]'); await page.waitForTimeout(300);
    ok('t3', 'next week', /Nov 15 – 21, 2026/.test(await page.locator('.cal-nav .lbl').innerText()), await page.locator('.cal-nav .lbl').innerText());
    await page.click('.cal-nav [aria-label="Previous"]'); await page.click('.chip[data-k="all"]'); await page.waitForTimeout(300);
    const link = page.locator('.dpanel .ev a.tt').first();
    if (await link.count()) { await link.click(); await page.waitForTimeout(400); ok('t3', 'film opens', /#\/film\/\d+/.test(page.url())); await page.goBack(); await page.waitForTimeout(400); }
    // month
    await go(page, `#/calendar?view=week&date=${d0}&f=theaters`);
    await page.click('.seg button:has-text("Month")'); await page.waitForTimeout(400);
    const cells = await page.$$eval('.mcell', els => els.map(e => ({ d: e.dataset.d, h: Math.round(e.getBoundingClientRect().height), sel: e.getAttribute('aria-selected') })));
    ok('t4', 'month grid', cells.length === 35 || cells.length === 42, `${cells.length} cells`);
    ok('t4', 'date kept when switching', cells.find(c => c.sel === 'true')?.d === d0);
    ok('t4', 'filter kept when switching', (await page.getAttribute('.chip[data-k="theaters"]', 'aria-pressed')) === 'true');
    ok('t4', 'cells same height (stable)', new Set(cells.map(c => c.h)).size === 1, [...new Set(cells.map(c => c.h))].join(','));
    ok('t4', 'release indicators', (await page.locator('.mcell .dots i').count()) > 0);
    ok('t4', 'cells large enough to read (>= 120px at 1440)', cells[0].h >= 120, `${cells[0].h}px`);
    const mp = await page.$$eval('.mcell', els => els.map(e => { const b = e.getBoundingClientRect(); return [...e.querySelectorAll('.mp .pp')].filter(p => { const r = p.getBoundingClientRect(); return r.right > b.right + 1 || r.bottom > b.bottom + 1; }).length; }));
    ok('t4', 'cell posters fit their cell', mp.every(x => !x), mp.join(''));
    ok('t4', 'day panel beside the month at 1440', await page.evaluate(() => { const g = document.querySelector('.month').getBoundingClientRect(), p = document.querySelector('.dpanel').getBoundingClientRect(); return p.left >= g.right; }));
    const dated = cells.find(c => c.d.startsWith('2026-11') && c.d !== d0);
    await page.click(`.mcell[data-d="${dated.d}"]`); await page.waitForTimeout(300);
    ok('t4', 'selecting a date opens its panel', new RegExp(new Date(dated.d + 'T12:00').getDate()).test(await page.locator('.dpanel h2').innerText()));
    for (let i = 0; i < 2; i++) { await page.click('.cal-nav [aria-label="Next"]'); await page.waitForTimeout(250); }
    ok('t4', 'year boundary', /January 2027/.test(await page.locator('.cal-nav .lbl').innerText()), await page.locator('.cal-nav .lbl').innerText());
    await page.click('.cal-nav [aria-label="Previous"]'); await page.waitForTimeout(250);
    ok('t4', 'back across the boundary', /December 2026/.test(await page.locator('.cal-nav .lbl').innerText()));
    await page.click('.seg button:has-text("Week")'); await page.waitForTimeout(300);
    ok('t4', 'week again keeps date + filter', (await page.locator('.wday .dh[aria-pressed="true"]').getAttribute('data-d')).startsWith('2026-12') && (await page.getAttribute('.chip[data-k="theaters"]', 'aria-pressed')) === 'true');
    const cls = await page.evaluate(() => new Promise(res => { let v = 0; new PerformanceObserver(l => { for (const e of l.getEntries()) if (!e.hadRecentInput) v += e.value; }).observe({ type: 'layout-shift', buffered: true }); setTimeout(() => res(v), 300); }));
    ok('t4', 'no major layout shift', cls < 0.1, cls.toFixed(3));
    ok('t3', 'no page errors', !page.errors.length, page.errors.join(' | '));
    await ctx.close();
  }

  // ------------------------------------------------------------ Test 5: watchlist density
  if (run('t5')) {
    const { ctx, page } = await newPage(b, W);
    await go(page, '#/watchlist');
    ok('t5', 'featured section', await page.isVisible('.feat h3'));
    const sh = page.locator('.section .shelf').first();
    const vis = await sh.evaluate(s => { const t = s.querySelector('.track'), r = t.getBoundingClientRect(); return [...t.children].filter(c => { const b = c.getBoundingClientRect(); return b.left >= r.left - 1 && b.right <= r.right + 1; }).length; });
    ok('t5', '5 to 7 posters per shelf at 1440', vis >= 5 && vis <= 7, `${vis}`);
    const fill = await sh.evaluate(s => { const t = s.querySelector('.track'); return t.scrollWidth > t.clientWidth ? 1 : t.children[t.children.length - 1].getBoundingClientRect().right / t.getBoundingClientRect().right; });
    ok('t5', 'shelves use the width', fill > 0.6, fill.toFixed(2));
    const before = await sh.evaluate(s => s.querySelector('.track').scrollLeft);
    const next = sh.locator('.arrow.next');
    if (await next.isEnabled()) { await next.click(); await page.waitForTimeout(700); ok('t5', 'shelf arrows scroll', (await sh.evaluate(s => s.querySelector('.track').scrollLeft)) > before); }
    else ok('t5', 'shelf arrows scroll', true, 'shelf fits, arrows hidden');
    const clipped = await page.$$eval('.shelf .track', ts => ts.flatMap(t => { const r = t.getBoundingClientRect(); return [...t.querySelectorAll('.pl')].filter(p => { const b = p.getBoundingClientRect(); return b.top < r.top - 0.5 || b.bottom > r.bottom + 0.5; }).length ? [1] : []; }).length);
    ok('t5', 'no clipped posters', clipped === 0, `${clipped} shelves clip`);
    const showAll = page.locator('button:has-text("Show all")'); if (await showAll.isVisible()) await showAll.click();
    const allN = await page.locator('.grid .pc').count(), api0 = (await apiJ(W, '/api/v8/watchlist/overview')).items.length;
    ok('t5', 'all movies grid shows the whole list', allN === api0, `${allN} of ${api0}`);
    const card = page.locator('.grid .pc').first(); const title = await card.locator('.ti').innerText();
    await card.locator('[data-want]').click(); await page.waitForTimeout(500);
    ok('t5', 'remove gives feedback with undo', /Removed/.test(await page.locator('.toast').innerText()) && await page.isVisible('.toast button:has-text("Undo")'));
    const after = (await apiJ(W, '/api/v8/watchlist/overview')).items.length;
    ok('t5', 'removal persisted', after === api0 - 1, `${api0} -> ${after}`);
    await page.click('.toast button:has-text("Undo")'); await page.waitForTimeout(500);
    ok('t5', 'undo restores', (await apiJ(W, '/api/v8/watchlist/overview')).items.length === api0, title);
    ok('t5', 'no page errors', !page.errors.length, page.errors.join(' | '));
    await ctx.close();
  }

  // ------------------------------------------------------------ Tests 6 + 8: continue watching, profile
  if (run('t6') || run('t8')) {
    const { ctx, page } = await newPage(b, W);
    await apiJ(W, '/api/v8/tv/progress', { id: 4000001, season: 3, episode: 6 });
    await go(page, '#/watchlist');
    const cw = page.locator('#wl-cw .cw:has-text("The Long Shift")');
    ok('t6', 'series appears', await cw.count() === 1);
    ok('t6', 'last + next episode', /S3 E6/.test(await cw.innerText()) && /S3 E7/.test(await cw.innerText()));
    ok('t6', 'no invented percentage', !/%/.test(await page.locator('#wl-cw').innerText()));
    const hl = page.locator('#wl-cw .cw:has-text("Harbor Lights")');
    ok('t6', 'import-only show: honest, no next episode or bar', (await hl.count()) === 1 && /Next episode unknown/.test(await hl.innerText()) && !(await hl.locator('.sbar').count()));
    await cw.locator('button:has-text("Watched S3 E7")').click(); await page.waitForTimeout(600);
    ok('t6', 'update in place', /S3 E7/.test(await page.locator('#wl-cw .cw:has-text("The Long Shift") .l1').innerText()) && /S3 E8/.test(await page.locator('#wl-cw .cw:has-text("The Long Shift")').innerText()));
    await page.reload({ waitUntil: 'networkidle' }); await page.waitForTimeout(300);
    ok('t6', 'persists after refresh', /S3 E7/.test(await page.locator('#wl-cw .cw:has-text("The Long Shift") .l1').innerText()));
    await go(page, '#/profile');
    ok('t6', 'profile shows the same progress', /S3 E7/.test(await page.locator('#pf-cw .cw:has-text("The Long Shift")').innerText()));
    await page.locator('#pf-cw .cw:has-text("The Long Shift") button:has-text("Watched S3 E8")').click(); await page.waitForTimeout(600);
    await go(page, '#/watchlist');
    ok('t6', 'profile update shows on watchlist', /S3 E8/.test(await page.locator('#wl-cw .cw:has-text("The Long Shift") .l1').innerText()));
    // profile with history
    await go(page, '#/profile');
    const sum = await apiJ(W, '/api/v8/me/summary');
    const tiles = await page.$$eval('.stat b', els => els.map(e => e.textContent));
    const want = [sum.stats.films, sum.stats.shows, sum.stats.ratings, sum.stats.reviews, sum.stats.watchlist, sum.stats.avg_rating?.toFixed(1) ?? '—'].map(String);
    ok('t8', 'statistics match stored data', JSON.stringify(tiles) === JSON.stringify(want), `${tiles} vs ${want}`);
    ok('t8', 'banner + avatar', await page.isVisible('.pf-banner') && await page.isVisible('.pf-av'));
    ok('t8', 'favorite films', (await page.locator('.panel:has-text("Favorite Films") .pc').count()) === sum.favorites.items.length && sum.favorites.items.length > 0);
    ok('t8', 'taste bars from data', (await page.locator('.taste .bar').count()) === Math.min(5, sum.taste.inferred.length) && sum.taste.inferred.length > 0);
    ok('t8', 'taste separates chosen from inferred', /You chose|Never recommend/i.test(await page.locator('.taste').innerText()) && /Inferred/i.test(await page.locator('.taste').innerText()));
    ok('t8', 'recent activity', (await page.locator('.act-row').count()) === Math.min(6, sum.activity.length));
    await page.click('.pf-tabs [data-k="ratings"]'); await page.waitForTimeout(200);
    ok('t8', 'ratings tab', (await page.locator('.grid .pc').count()) === sum.stats.ratings);
    await page.click('.pf-tabs [data-k="reviews"]'); await page.waitForTimeout(200);
    ok('t8', 'reviews tab', (await page.locator('.rv-card').count()) === sum.stats.reviews);
    ok('t8', 'no page errors', !page.errors.length, page.errors.join(' | '));
    await ctx.close();
  }

  // ------------------------------------------------------------ Test 7: upcoming
  if (run('t7')) {
    const { ctx, page } = await newPage(b, W);
    await apiJ(W, '/api/v8/tv/follow', { id: 4000005, on: true }); await apiJ(W, '/api/v8/tv/follow', { id: 4000005, on: true });
    const u = (await apiJ(W, '/api/v8/watchlist/upcoming')).items;
    ok('t7', 'one entry per title (no duplicates)', u.length === new Set(u.map(x => x.media + x.id)).size && u.filter(x => x.id === 4000005).length === 1);
    await go(page, '#/watchlist?tab=coming');
    const row = page.locator('.upc .row-i:has-text("Field Notes")');
    ok('t7', 'listed as a new season with its date', /New season/.test(await row.innerText()) && /Season 2/.test(await row.innerText()) && /2026/.test(await row.innerText()));
    ok('t7', 'unknown date not invented', /Date not announced/.test(await page.locator('.upc .row-i:has-text("Second Orbit")').innerText()));
    await row.locator('.bell').click(); await page.waitForTimeout(400);
    let s = (await apiJ(W, '/api/v8/watchlist/overview')).shows.find(x => x.id === 4000005);
    ok('t7', 'reminder can be turned off', s.notify === false);
    await row.locator('.bell').click(); await page.waitForTimeout(400);
    s = (await apiJ(W, '/api/v8/watchlist/overview')).shows.find(x => x.id === 4000005);
    ok('t7', 'and back on', s.notify === true);
    const film = u.find(x => x.media === 'movie');
    if (film) { const fr = page.locator(`.upc .row-i:has-text("${film.film.title}") .bell`).first(); await fr.click(); await page.waitForTimeout(400);
      ok('t7', 'film alerts mute', (await apiJ(W, '/api/v8/watchlist/upcoming')).items.find(x => x.id === film.id).notify === false); await fr.click(); await page.waitForTimeout(300); }
    ok('t7', 'no page errors', !page.errors.length, page.errors.join(' | '));
    await ctx.close();
  }

  // ------------------------------------------------------------ Test 9: new user / guest
  if (run('t9')) {
    const user = 'fresh-' + Date.now();
    const { ctx, page } = await newPage(b, user);
    await go(page, '#/profile');
    const tiles = await page.$$eval('.stat b', els => els.map(e => e.textContent));
    ok('t9', 'no fabricated statistics', JSON.stringify(tiles) === JSON.stringify(['0', '0', '0', '0', '0', '—']), tiles.join(','));
    ok('t9', 'default banner, no photo', await page.isVisible('.pf-banner.def') && !(await page.locator('.pf-head img').count()));
    ok('t9', 'onboarding actions', await page.isVisible('button:has-text("Rate your first movie")') && await page.isVisible('text=Follow a TV show'));
    ok('t9', 'no fabricated progress or favourites', !(await page.locator('.cw').count()) && !(await page.locator('.panel:has-text("Favorite Films") .pc').count()));
    ok('t9', 'account prompt present, not blocking', await page.isVisible('.pf-id button:has-text("Create account")') && !(await page.locator('dialog[open]').count()));
    await go(page, '#/watchlist');
    ok('t9', 'watchlist onboarding', await page.isVisible('text=Start your watchlist') && (await page.locator('.onboard > *').count()) === 4);
    await apiJ(user, '/api/film', { id: 496243, title: 'Parasite', choice: 'want' });
    await page.reload({ waitUntil: 'networkidle' }); await page.waitForTimeout(400);
    ok('t9', 'guest data kept', (await page.locator('.feat h3').innerText()).includes('Parasite'));
    ok('t9', 'no page errors', !page.errors.length, page.errors.join(' | '));
    await ctx.close();
  }

  // ------------------------------------------------------------ Test 10: privacy (two accounts, direct API too)
  if (run('t10')) {
    const mk = async (name) => { const c = await request.newContext({ baseURL: BASE, extraHTTPHeaders: { 'X-Access-Code': 'movies', 'X-MB-User': 'b-' + name } });
      const r = await c.post('/api/signup', { data: { username: name, password: 'correct horse battery 9', display_name: name, keep_history: true } }); return { c, ok: r.ok(), j: await r.json() }; };
    const stamp = Date.now() % 1e6;
    const A = await mk('ann' + stamp), B = await mk('bob' + stamp);
    ok('t10', 'two accounts', A.ok && B.ok, JSON.stringify(A.j).slice(0, 80));
    await A.c.post('/api/film', { data: { id: 496243, title: 'Parasite', choice: 'want' } });
    await A.c.post('/api/v8/watched', { data: { id: 27205 } });
    const pa = await (await A.c.get('/api/v8/profile')).json();
    const fr = await (await B.c.post('/api/v8/friends/request', { data: { code: pa.code } })).json();
    const pb = await (await B.c.get('/api/v8/profile')).json();
    const bk = (await (await A.c.get('/api/v8/profile')).json()).friends.incoming[0]?.key;
    await A.c.post('/api/v8/friends/respond', { data: { key: bk, accept: true } });
    const ak = (await (await B.c.get('/api/v8/profile')).json()).friends.friends[0]?.key;
    let v = await (await B.c.get('/api/v8/friend/' + encodeURIComponent(ak))).json();
    ok('t10', 'private by default (direct API)', !v.watchlist && !v.history && !v.ratings, JSON.stringify(v).slice(0, 90));
    await A.c.post('/api/v8/privacy', { data: { changes: { watchlist: 'friends' } } });
    v = await (await B.c.get('/api/v8/friend/' + encodeURIComponent(ak))).json();
    ok('t10', 'friend sees only what is shared', v.watchlist?.length === 1 && !v.history);
    await A.c.post('/api/v8/privacy', { data: { changes: { watchlist: 'private' } } });
    v = await (await B.c.get('/api/v8/friend/' + encodeURIComponent(ak))).json();
    ok('t10', 'privacy change applies at once', !v.watchlist);
    const C = await mk('cat' + stamp);
    const r3 = await C.c.get('/api/v8/friend/' + encodeURIComponent(ak));
    ok('t10', 'non-friend gets nothing', r3.status() === 404);
    const sumB = await (await B.c.get('/api/v8/me/summary')).json();
    ok('t10', 'summary is per-person only', sumB.stats.watchlist === 0 && sumB.stats.films === 0);
    for (const x of [A, B, C]) await x.c.dispose();
  }

  // ------------------------------------------------------------ Test 13: Home genre filtering (homepage brief tests A-L)
  if (run('t13')) {
    const U = 'home-' + Date.now();
    const { ctx, page } = await newPage(b, U);
    const railIds = async (title) => page.$$eval(`section[aria-label="${title}"] .pc`, els => els.map(e => e.querySelector('.ti, .cap')?.textContent || ''));
    const api = async (gs, mode = 'any') => apiJ(U, '/api/v8/home' + (gs.length ? `?genres=${encodeURIComponent(gs.join(','))}&mode=${mode}` : ''));
    const apiTitles = (d, key) => (d.rails.find(r => r.key === key)?.items || []).map(x => x.title);
    const settle = async () => { await page.waitForFunction(() => !document.querySelector('#home-rails[aria-busy]')); await page.waitForTimeout(150); };
    await go(page, '#/'); await settle();
    const chip = async (g) => { if (!(await page.locator(`.gx-chips [data-g="${g}"]`).count())) await page.click('.gx-chips .chip.add'); await page.click(`.gx-chips [data-g="${g}"]`); };
    const base = await api([]);
    ok('t13', 'A: no genre = server order', JSON.stringify(await railIds('In Theaters Now')) === JSON.stringify(apiTitles(base, 'now_playing').slice(0, 20)));
    await chip('Thriller'); await settle();
    const th = await api(['Thriller']);
    ok('t13', 'B: one genre = server result', JSON.stringify(await railIds('Top Rated')) === JSON.stringify(apiTitles(th, 'top_rated')) && apiTitles(th, 'top_rated').length > 0);
    ok('t13', 'J: stays on Home', /#\/$|#\/?$/.test(new URL(page.url()).hash || '#/'), page.url());
    await chip('Action'); await settle();
    const anyR = await api(['Action', 'Thriller']);
    ok('t13', 'C: Match Any', JSON.stringify(await railIds('Top Rated')) === JSON.stringify(apiTitles(anyR, 'top_rated')) && /Action or Thriller/.test(await page.locator('.gx-sum').innerText()));
    await page.click('.gx .seg button:has-text("All")'); await settle();
    const allR = await api(['Action', 'Thriller'], 'all');
    ok('t13', 'D: Match All', JSON.stringify(await railIds('Top Rated')) === JSON.stringify(apiTitles(allR, 'top_rated')) && /Action and Thriller/.test(await page.locator('.gx-sum').innerText()));
    ok('t13', 'D: every Match All film has both genres', allR.rails.every(r => r.items.every(x => /Action/.test(x.genres || '') && /Thriller/.test(x.genres || ''))) && allR.rails.some(r => r.items.length));
    await page.reload({ waitUntil: 'networkidle' }); await settle();
    ok('t13', 'L: selection + mode persist after reload', (await page.$$eval('.gx-chips [aria-pressed="true"]', els => els.map(e => e.dataset.g)).then(x => x.sort().join())) === 'Action,Thriller'
      && (await page.getAttribute('.gx .seg button:has-text("All")', 'aria-pressed')) === 'true');
    // G: impossible combination under Match All
    for (const g of ['Western', 'Animation', 'Documentary']) await chip(g);
    await settle();
    ok('t13', 'G: no match = empty state, no stray films', (await page.locator('#home-rails .pc').count()) === 0 && await page.isVisible('#home-rails .empty') && await page.isVisible('#home-rails .empty button:has-text("Clear filters")'));
    await page.click('#home-rails .empty button:has-text("Match any")'); await settle();
    ok('t13', 'G: Match any relaxes it', (await page.locator('#home-rails .pc').count()) > 0 && (await page.getAttribute('.gx .seg button:has-text("Any")', 'aria-pressed')) === 'true');
    // E: every genre = no filter
    if ((await page.getAttribute('.gx-chips .chip.add', 'aria-expanded')) !== 'true') await page.click('.gx-chips .chip.add');
    for (const g of await page.$$eval('.gx-chips [data-g]', els => els.filter(e => e.getAttribute('aria-pressed') !== 'true').map(e => e.dataset.g))) await page.click(`.gx-chips [data-g="${g}"]`);
    await settle();
    ok('t13', 'E: all 18 selected = unfiltered rows, same order', (await page.locator('.gx-chips [aria-pressed="true"]').count()) === base.genre_options.length
      && JSON.stringify(await railIds('In Theaters Now')) === JSON.stringify(apiTitles(base, 'now_playing').slice(0, 20))
      && JSON.stringify(await railIds('Top Rated')) === JSON.stringify(apiTitles(base, 'top_rated')) && /same as no filter/.test(await page.locator('.gx-sum').innerText()));
    // H: fast switching: the page ends on the newest selection, never on a stale answer
    await page.click('.gx .btn.ghost:has-text("Clear all")'); await settle();
    if ((await page.getAttribute('.gx-chips .chip.add', 'aria-expanded')) !== 'true') await page.click('.gx-chips .chip.add');
    for (const g of ['Comedy', 'Drama', 'Comedy', 'Horror', 'Crime', 'Drama', 'Thriller', 'Horror']) { await page.click(`.gx-chips [data-g="${g}"]`); await page.waitForTimeout(25); }
    await settle();
    const fin = await page.$$eval('.gx-chips [aria-pressed="true"]', els => els.map(e => e.dataset.g));
    const stored = await page.evaluate(() => JSON.parse(localStorage.getItem('cm.filters')).genres);
    const want = await api(fin, 'any');
    ok('t13', 'H: rapid clicks end on the final state', fin.sort().join() === 'Crime,Thriller' && stored.slice().sort().join() === 'Crime,Thriller'
      && JSON.stringify(await railIds('Top Rated')) === JSON.stringify(apiTitles(want, 'top_rated')), `${fin} / ${stored}`);
    // F: stable
    const a1 = JSON.stringify((await api(['Crime', 'Thriller'])).rails.map(r => r.items.map(x => x.id))); let same = true;
    for (let i = 0; i < 4; i++) same = same && JSON.stringify((await api(['Crime', 'Thriller'])).rails.map(r => r.items.map(x => x.id))) === a1;
    ok('t13', 'F: same request, same order', same);
    // J: Chat gets the genres when opened, Home did not navigate
    let sent = null;
    await page.route('**/api/chat', r => { sent = JSON.parse(r.request().postData()); r.fulfill({ status: 200, contentType: 'text/event-stream', body: 'event: token\ndata: {"text":"ok"}\n\nevent: done\ndata: {}\n\n' }); });
    await go(page, '#/ask'); await page.waitForTimeout(300);
    ok('t13', 'J: chat filter bar shows the Home genres', /Crime/.test(await page.locator('.cfilters').innerText()) && /Thriller/.test(await page.locator('.cfilters').innerText()));
    await page.fill('#chat-q', 'something good'); await page.keyboard.press('Enter'); await page.waitForTimeout(600);
    ok('t13', 'J: chat request carries them', JSON.stringify((sent?.filters?.genres || []).slice().sort()) === '["Crime","Thriller"]', JSON.stringify(sent?.filters));
    await go(page, '#/'); await settle();
    await page.click('.gx .btn.ghost:has-text("Clear all")'); await settle();
    // K: shelves fully visible, 5-7 posters on a wide desktop, only the hero moves by itself
    const vis = await page.$$eval('#home-rails .shelf .track', ts => ts.map(t => { const r = t.getBoundingClientRect(); return [...t.children].filter(c => { const b = c.getBoundingClientRect(); return b.left >= r.left - 1 && b.right <= r.right + 1; }).length; }));
    ok('t13', 'K: 5-7 posters per full shelf at 1440', vis.length > 0 && vis.every(n => n >= 5 && n <= 7), vis.join(','));
    const clipped = await page.$$eval('#home-rails .shelf .track', ts => ts.filter(t => { const r = t.getBoundingClientRect(); return [...t.querySelectorAll('.pl')].some(p => { const b = p.getBoundingClientRect(); return b.top < r.top - .5 || b.bottom > r.bottom + .5; }); }).length);
    ok('t13', 'K: no clipped posters', clipped === 0, `${clipped}`);
    const s0 = await page.$$eval('#home-rails .shelf .track', ts => ts.map(t => t.scrollLeft));
    const h0 = await page.locator('.hh-t').innerText();
    await page.mouse.move(5, 890); await page.waitForTimeout(5600);
    const s1 = await page.$$eval('#home-rails .shelf .track', ts => ts.map(t => t.scrollLeft));
    ok('t13', 'only the hero advances by itself', JSON.stringify(s0) === JSON.stringify(s1) && (await page.locator('.hh-t').innerText()) !== h0);
    await page.click('.hh-pause'); const h1 = await page.locator('.hh-t').innerText(); await page.mouse.move(5, 890); await page.waitForTimeout(5600);
    ok('t13', 'pause stops the hero', (await page.locator('.hh-t').innerText()) === h1 && (await page.getAttribute('.hh-pause', 'aria-label')) === 'Play slideshow');
    await page.focus('.hh-arrow.next'); await page.keyboard.press('Enter'); await page.waitForTimeout(200);
    ok('t13', 'hero arrows work', (await page.locator('.hh-t').innerText()) !== h1);
    ok('t13', 'no page errors', !page.errors.length, page.errors.join(' | '));
    await ctx.close();
    // missing artwork: no poster path, and a poster that fails to load
    const m = await newPage(b, U);
    const first = (await api([])).rails.find(r => r.key === 'trending').items;
    await m.page.route('**/api/v8/home*', async r => { const res = await r.fetch(); const j = await res.json(); const tr = j.rails.find(x => x.key === 'trending'); if (tr.items[0]) { tr.items[0].poster = null; tr.items[0].poster_lg = null; }
      r.fulfill({ response: res, body: JSON.stringify(j) }); });
    const broken = first[1]; if (broken) await m.page.route(new RegExp((broken.poster_lg || broken.poster || 'x').replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + '$'), r => r.fulfill({ status: 404, body: '' }));
    await go(m.page, '#/'); await m.page.waitForTimeout(800);
    const cardsTr = m.page.locator('section[aria-label="Trending Now"] .pc');
    ok('t13', 'no artwork: styled title card', /\S/.test(await cardsTr.nth(0).locator('.pl .ph .ph-t').innerText()) && !(await cardsTr.nth(0).locator('.pl img').count()));
    if (broken) ok('t13', 'failed image: same title card, no broken icon', (await cardsTr.nth(1).locator('.pl .ph').count()) === 1 && !(await cardsTr.nth(1).locator('.pl img').count()));
    await m.ctx.close();
  }

  // ------------------------------------------------------------ Test 11: responsive sweep + accessibility
  if (run('t11')) {
    const widths = (process.env.WIDTHS || '320,375,390,430,768,1024,1440,1920').split(',').map(Number);
    const routes = (process.env.ROUTES || '#/,#/ask,#/pick,#/calendar,#/calendar?view=month,#/watchlist,#/watchlist?tab=tv,#/profile,#/profile?tab=settings,#/film/496243,#/show/6000002,#/join').split(',');
    let bad = 0;
    for (const w of widths) {
      const { ctx, page } = await newPage(b, W, { width: w, height: w < 768 ? 800 : 900 });
      for (const r of routes) {
        await go(page, r); await page.waitForTimeout(250);
        const m = await page.evaluate(() => {
          const sw = document.documentElement.scrollWidth - document.documentElement.clientWidth;
          const small = [];
          for (const el of document.querySelectorAll('a,button,input,select,textarea,[role=tab]')) {
            const rc = el.getBoundingClientRect(), cs = getComputedStyle(el);
            if (!rc.width || cs.visibility === 'hidden' || el.closest('[hidden]') || el.closest('footer.credit') || el.matches('.skip') || el.getAttribute('aria-hidden') === 'true') continue;
            if (el.tagName === 'A' && el.closest('p, .hint, .cfine, .sub, .notice')) continue;     // inline links in text (WCAG 2.5.8 exception)
            if (el.matches('input[type=checkbox]') && el.closest('label')) continue;               // the label is the target
            if (el.closest('.pl, .pp') || el.matches('.ti, .tt, .t')) continue;                    // titles duplicate the poster link
            // Seven day buttons / month cells cannot be 44 px wide on a 320-360 px phone; they must still clear
            // WCAG 2.5.8's 24 px minimum (documented exception in docs/redesign_2026-11-10.md).
            const min = innerWidth < 1024 ? (el.matches('.wstrip button, .mcell') && innerWidth < 400 ? 24 : 44) : 24;
            if (rc.height < min - 0.5 || rc.width < min - 0.5) small.push(`${el.tagName.toLowerCase()}.${String(el.className).split(' ')[0]}:${(el.textContent || el.getAttribute('aria-label') || '').trim().slice(0, 18)} ${Math.round(rc.width)}x${Math.round(rc.height)}`);
          }
          const clip = [...document.querySelectorAll('h1,h2,h3,.btn,.chip')].filter(e => e.scrollWidth > e.clientWidth + 2 && getComputedStyle(e).overflow !== 'visible' && !e.closest('.shelf,.cfilters,.chips.scroll,.tabs,.pills')).length;
          return { sw, small: [...new Set(small)].slice(0, 6), clip };
        });
        await page.addScriptTag({ content: AXE });
        const axe = await page.evaluate(async () => (await axe.run(document, { runOnly: ['wcag2a', 'wcag2aa', 'wcag21aa', 'wcag22aa'] })).violations.map(v => `${v.id} x${v.nodes.length} ${v.nodes[0].target.join(' ')}`));
        const fail = m.sw > 0 || m.small.length || axe.length || page.errors.length || m.clip;
        if (fail) { bad++; console.log(`  ${w} ${r}: overflow=${m.sw} clip=${m.clip}${m.small.length ? ' small=' + m.small.join('; ') : ''}${axe.length ? ' axe=' + axe.join('; ') : ''}${page.errors.length ? ' errors=' + page.errors.splice(0).join(' | ') : ''}`); }
      }
      await ctx.close();
    }
    ok('t11', `responsive + a11y sweep (${widths.length} widths x ${routes.length} pages)`, bad === 0, `${bad} page-width pairs with problems`);
  }

  // ------------------------------------------------------------ Test 12: existing functionality still works
  if (run('t12')) {
    const { ctx, page } = await newPage(b, 'legacy-check');
    await go(page, '#/');
    ok('t12', 'home rails load', (await page.locator('#home-rails .shelf').count()) >= 1);
    await go(page, '#/film/496243');
    ok('t12', 'film page loads', /Parasite/.test(await page.locator('h1').innerText()));
    const csv = 'Title,Date\n"Inception",10/30/26\n"Not A Real Film Title Zzz",10/29/26\n';
    const imp = await apiJ('import-' + Date.now(), '/api/v8/import', { kind: 'netflix', text: csv });
    const again = await apiJ('legacy-check', '/api/v8/import', { kind: 'netflix', text: csv }); await apiJ('legacy-check', '/api/v8/import', { kind: 'netflix', text: csv });
    ok('t12', 'importing the same file twice adds nothing twice', (await apiJ('legacy-check', '/api/v8/import', { kind: 'netflix', text: csv })).added === 0);
    ok('t12', 'Netflix import (1 added, 1 unmatched listed)', imp.added === 1 && imp.issues.length === 1);
    const st = await apiJ('legacy-check', '/api/settings', { settings: { services: ['netflix'], never_genres: ['Horror'] } });
    ok('t12', 'settings save', st.settings?.services?.includes('netflix') && st.settings.never_genres.includes('Horror'));
    const classic = await fetch(BASE + '/classic'); ok('t12', 'classic page still served at /classic', classic.status === 200 && (await classic.text()).includes('brand'));
    const root = await (await fetch(BASE + '/')).text(); ok('t12', '/ serves the v8 site', root.includes('/v8/app.css'));
    await go(page, '#/discover'); ok('t12', 'old #/discover link opens Ask Cinemack', await page.isVisible('#chat-q'));
    ok('t12', 'no page errors', !page.errors.length, page.errors.join(' | '));
    await ctx.close();
  }
  // ------------------------------------------------------------ Test 14 (10 Nov, round 18): TV rows, short films
  if (run('t14')) {
    const { ctx, page } = await newPage(b, W);
    await go(page, '#/'); await page.waitForTimeout(600);
    const api = await apiJ(W, '/api/v8/home');
    const rows = await page.$$eval('section.section', els => els.map(e => e.querySelector('h2')?.textContent));
    ok('t14', 'Home has New Seasons and Top Rated Shows', rows.includes('New Seasons') && rows.includes('Top Rated Shows'), rows.join(' | '));
    const ns = api.rails.find(r => r.key === 'new_seasons'), ts = api.rails.find(r => r.key === 'top_shows');
    const shown = await page.$$eval('section[aria-label="New Seasons"] .pc .ti', els => els.map(e => e.textContent));
    ok('t14', 'New Seasons in the server order', JSON.stringify(shown) === JSON.stringify(ns.items.map(x => x.title)), shown.slice(0, 3).join(', '));
    const today = new Date(); const cut = new Date(today); cut.setDate(cut.getDate() - 30);
    ok('t14', 'every new season premiered in the last 30 days', ns.items.every(x => x.season && new Date(x.season.date + 'T12:00') >= cut && new Date(x.season.date + 'T00:00') <= today));
    ok('t14', 'Top Rated Shows carry a rating', ts.items.length > 0 && ts.items.every(x => x.rating != null));
    await page.locator('section[aria-label="Top Rated Shows"] .pc a.pl').first().click(); await page.waitForTimeout(900);
    ok('t14', 'show poster opens the show page with Follow (round 21)', /#\/show\/\d+/.test(page.url()) && await page.locator('.sh-hero button:has-text("Follow show"), .sh-hero button:has-text("Following")').count() > 0, page.url());
    await go(page, '#/');
    const bell = page.locator('section[aria-label="New Seasons"] .pc .bm').first();
    await bell.click(); await page.waitForTimeout(500);
    const followed = await bell.getAttribute('aria-pressed');
    const ov = await apiJ(W, '/api/v8/watchlist/overview');
    ok('t14', 'bell follows the show (it is on the Watchlist)', followed === 'true' && (ov.shows || []).some(x => x.id === ns.items[0].id));
    await bell.click(); await page.waitForTimeout(400);
    await page.click('.gx button.chip:has-text("Crime")'); await page.waitForTimeout(800);
    const f = await apiJ(W, '/api/v8/home?genres=Crime');
    const shownF = await page.$$eval('section[aria-label="Top Rated Shows"] .pc .ti', els => els.map(e => e.textContent));
    ok('t14', 'genre chips filter the TV rows too', JSON.stringify(shownF) === JSON.stringify(f.rails.find(r => r.key === 'top_shows').items.map(x => x.title)) && shownF.length < ts.items.length, `${shownF.length} of ${ts.items.length}`);
    await page.click('.gx .btn.ghost'); await page.waitForTimeout(500);
    const titles = await page.$$eval('.pc .ti', els => els.map(e => e.textContent));
    ok('t14', 'the 7-minute short is on no Home row', !titles.includes('ee'));
    const cal = await apiJ(W, '/api/v8/calendar?start=2026-11-01&days=41');
    ok('t14', 'nor in the calendar', !(cal.events || []).some(e => e.film && e.film.title === 'ee'));
    await go(page, '#/film/3900001'); await page.waitForTimeout(500);
    const facts = await page.locator('.detail-head .facts').first().innerText();
    ok('t14', 'film page: "7 min" and "Short film" (was "0 h 7 min")', /\b7 min\b/.test(facts) && /Short film/.test(facts) && !/0 h/.test(facts), facts.replace(/\s+/g, ' '));
    ok('t14', 'no page errors', !page.errors.length, page.errors.join(' | '));
    await ctx.close();
  }

  // ------------------------------------------------------------ Test 15 (10 Nov, round 21): private chat, chips, sign-up, TV picks, show page
  if (run('t15')) {
    const G = 'r21-acc-' + Date.now();
    const { ctx, page } = await newPage(b, G);
    await go(page, '#/'); await page.waitForTimeout(600);
    ok('t15', 'signed out: header offers Sign in + Create account', await page.isVisible('#hdr-signin') && await page.isVisible('#hdr-join'));
    ok('t15', 'signed out: Home invites to create an account', await page.locator('.acct:has-text("Make Cinemack yours")').count() === 1);
    // private chat: request carries X-MB-Private: 1 and the chat is not listed afterwards
    await go(page, '#/ask');
    let privHeader = null;
    page.on('request', r => { if (r.url().endsWith('/api/chat')) privHeader = r.headers()['x-mb-private']; });
    await page.click('.cside-top [aria-label="New private chat"]');
    ok('t15', 'private chat banner', await page.isVisible('.pbar'));
    await page.fill('#chat-q', 'keep it under 90 minutes and no horror please'); await page.keyboard.press('Enter');
    await page.waitForSelector('.offer .chip', { timeout: 8000 }).catch(() => {});
    ok('t15', 'private chat request marked private', privHeader === '1', String(privHeader));
    const chipLabels = await page.$$eval('.offer .chip', els => els.map(e => e.textContent.trim()));
    ok('t15', '3c: limits in the message offered as chips', chipLabels.includes('Under 90 min') && chipLabels.includes('No horror'), chipLabels.join(' | '));
    await page.click('.offer .chip:has-text("Under 90 min")'); await page.waitForTimeout(300);
    ok('t15', '3c: accepted chip joins the filter bar', await page.locator('.cfilters .chip.on:has-text("1½")').count() + await page.locator('.cfilters .chip.on:has-text("90")').count() > 0);
    await page.waitForFunction(() => !document.querySelector('.msg.bot .status:not([hidden])'), null, { timeout: 30000 }).catch(() => {});
    const chats = await apiJ(G, '/api/chats');
    ok('t15', 'private chat not saved to the list', !(chats.chats || []).length, `${(chats.chats || []).length} saved`);
    // sign-up flow
    await go(page, '#/join');
    const u = 'acc' + Date.now().toString(36);
    await page.fill('#j-u', u); await page.fill('#j-p', 'a-long-pass-21'); await page.fill('#j-p2', 'a-long-pass-21x');
    await page.click('.join-nav .btn.primary'); await page.waitForTimeout(400);
    ok('t15', 'sign-up: mismatched passwords stopped', /different/.test(await page.locator('.join-body .err').innerText()));
    await page.fill('#j-p2', 'a-long-pass-21'); await page.fill('#j-n', 'Sam'); await page.click('.join-nav .btn.primary'); await page.waitForTimeout(1200);
    ok('t15', 'sign-up: account made, step 2', /step=taste/.test(page.url()) && await page.isVisible('#me-btn'));
    await page.click('.tg .chip:has-text("Love it") >> nth=0'); await page.click('.join-nav .btn.primary'); await page.waitForTimeout(600);
    await page.click('.join-body .chips button >> nth=0');                       // first service
    await page.selectOption('#j-cert', 'PG-13'); await page.click('.join-nav .btn.primary'); await page.waitForTimeout(600);
    await page.click('.join-nav .btn.ghost:has-text("Skip")'); await page.waitForTimeout(400);
    await page.selectOption('#j-pv-watchlist', 'friends'); await page.click('.join-nav .btn.primary:has-text("Finish")'); await page.waitForTimeout(1200);
    const me = await page.evaluate(async () => (await fetch('/api/me', { headers: { 'X-Access-Code': 'movies', 'X-MB-User': localStorage.getItem('mb.user') } })).json());
    ok('t15', 'sign-up: settings, taste and onboarding saved', me.account?.username === u && me.onboarded && me.settings.max_cert === 'PG-13' && me.settings.services.length === 1 && me.groups.length === 1 && me.name === 'Sam',
      JSON.stringify({ u: me.account?.username, ob: me.onboarded, cert: me.settings?.max_cert, svc: me.settings?.services, g: me.groups, n: me.name }));
    const prof = await page.evaluate(async () => (await fetch('/api/v8/profile', { headers: { 'X-Access-Code': 'movies', 'X-MB-User': localStorage.getItem('mb.user') } })).json());
    ok('t15', 'sign-up: privacy choice saved', prof.privacy?.watchlist === 'friends', JSON.stringify(prof.privacy));
    await go(page, '#/'); await page.waitForTimeout(500);
    ok('t15', 'signed in + set up: no invitation, no header buttons', !(await page.locator('.acct').count()) && !(await page.isVisible('#hdr-auth')));
    // Pick Tonight: TV
    await go(page, '#/pick'); await page.click('.pref[data-k="kind"]'); await page.click('text=A TV show'); await page.click('.go'); await page.waitForTimeout(1500);
    const tvp = await page.$$eval('.rk .tt', els => els.map(e => e.textContent));
    ok('t15', 'Pick Tonight: TV shows ranked', tvp.length >= 1 && (await page.locator('.rk .tag.tvk').count()) === tvp.length, tvp.join(' | '));
    const certs = await page.$$eval('.rk .mm', els => els.map(e => e.textContent));
    // show page: season switch loads episodes with ratings
    await go(page, '#/show/6000002'); await page.waitForTimeout(800);
    const n1 = await page.locator('.ep').count();
    await page.click('.sh-seasons button[data-n="1"]'); await page.waitForTimeout(600);
    ok('t15', 'show page: episodes per season with IMDb ratings', n1 > 0 && await page.locator('.ep .ep-r:has-text("IMDb")').count() > 0 && /Season 1/.test(await page.locator('.eps-h').innerText()));
    await page.click('.ep .btn:has-text("Watched up to here") >> nth=2'); await page.waitForTimeout(600);
    const cw = await page.evaluate(async () => (await fetch('/api/v8/continue', { headers: { 'X-Access-Code': 'movies', 'X-MB-User': localStorage.getItem('mb.user') } })).json());
    const it = (cw.items || []).find(x => x.id === 6000002);
    ok('t15', 'show page: "Watched up to here" records progress; next episode named', it && it.last.label === 'S1 E3' && it.next?.label?.startsWith('S1 E4'), JSON.stringify(it?.next));
    ok('t15', 'no page errors', !page.errors.length, page.errors.join(' | '));
    await ctx.close();
  }

  // ------------------------------------------------------------ Test 16 (round 27): / is v8, TVmaze, chat Any/All, banner
  if (run('t16')) {
    const G = 'r27-acc-' + Date.now();
    const { ctx, page } = await newPage(b, G);
    await page.goto(`${BASE}/#/`, { waitUntil: 'networkidle' }); await page.waitForTimeout(500);
    ok('t16', '/ opens the v8 site', await page.isVisible('header .logo') && await page.locator('footer a[href="/classic"]').count() === 1);
    ok('t16', 'footer credits TVmaze', await page.locator('footer a[href="https://www.tvmaze.com"]').count() === 1);
    // show page: where and when it airs, with the TVmaze credit (fixture show 4000002 airs on AMC)
    await go(page, '#/show/4000002'); await page.waitForTimeout(700);
    ok('t16', 'show page: schedule line from TVmaze', /Sundays at 9:00 pm ET on AMC/.test(await page.locator('.sh-airs').innerText().catch(() => '')));
    ok('t16', 'show page: TVmaze credit + licence links', await page.locator('a[href*="tvmaze.com/shows/"]').count() >= 1 && await page.locator('a[href*="creativecommons.org/licenses/by-sa/4.0"]').count() >= 1);
    // calendar: TVmaze credit in the note once a followed show has dated air times
    await apiJ(G, '/api/v8/tv/follow', { id: 4000002, on: true });
    await go(page, '#/calendar?view=month'); await page.waitForTimeout(700);
    ok('t16', 'calendar: TVmaze credited when air times are shown', await page.locator('.cal-note a.tvm').count() >= 1, await page.locator('.cal-note').innerText());
    // chat: "All" from the shared filters reaches the request as genres_mode
    await page.evaluate(() => { localStorage.setItem('cm.filters', JSON.stringify({ genres: ['Comedy', 'Horror'], genre_mode: 'all' })); });
    await go(page, '#/ask'); await page.reload({ waitUntil: 'networkidle' }); await page.waitForTimeout(400);
    ok('t16', 'chat: All shown on the filter chip', await page.locator('.cfilters .chip:has-text("All of:")').count() === 1);
    let sent = null;
    page.on('request', r => { if (r.url().endsWith('/api/chat')) { try { sent = JSON.parse(r.postData() || '{}').filters; } catch { /* */ } } });
    await page.fill('#chat-q', 'something fun for tonight'); await page.keyboard.press('Enter'); await page.waitForTimeout(1200);
    ok('t16', 'chat: genres_mode all sent', sent && sent.genres_mode === 'all' && sent.genres.length === 2, JSON.stringify(sent));
    await page.click('.cfilters .fbtn'); await page.waitForTimeout(300);
    ok('t16', 'filter sheet: Any / All switch', await page.locator('dialog [aria-label="Match genres"] button[aria-pressed="true"]:has-text("All of these")').count() === 1);
    await page.keyboard.press('Escape');
    // banner upload: a real image through the Edit profile sheet, then shown; "Use a film backdrop" removes it
    await go(page, '#/profile'); await page.waitForTimeout(500);
    await page.click('.pf-head .btn:has-text("Edit profile")'); await page.waitForTimeout(300);
    const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==', 'base64');
    await Promise.all([page.waitForEvent('load').catch(() => {}), page.setInputFiles('#pf-banner', { name: 'b.png', mimeType: 'image/png', buffer: png })]);
    await page.waitForTimeout(1200);
    ok('t16', 'banner: uploaded image shown', /blob:/.test(await page.locator('.pf-banner').getAttribute('style') || ''));
    const other = await apiJ('r27-someone-else', '/api/v8/me/summary');
    ok('t16', 'banner: not shown to another person', !other.banner || !other.banner.custom);
    ok('t16', 'no page errors', !page.errors.length, page.errors.join(' | '));
    await ctx.close();
  }

  await b.close();
  const f = R.filter(x => !x.pass);
  console.log(`\n${R.length - f.length}/${R.length} checks passed${f.length ? `; failed: ${f.map(x => `${x.t} ${x.name}`).join('; ')}` : ''}`);
  process.exit(f.length ? 1 : 0);
})();
