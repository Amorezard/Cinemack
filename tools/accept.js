// Acceptance scenarios A-H from the 9 Nov refinement request, run against the local mock site.
const { chromium } = require('playwright');
const BASE = 'http://127.0.0.1:8765/v8';
const res = []; const ok = (n, c, x = '') => res.push(`${c ? 'PASS' : 'FAIL'} ${n}${x ? '  [' + x + ']' : ''}`);
const tile = r => r.fulfill({ contentType: 'image/svg+xml', body: '<svg xmlns="http://www.w3.org/2000/svg" width="342" height="513"><rect width="100%" height="100%" fill="#456"/></svg>' });
const FILM = { id: 496243, title: 'Parasite', year: 2019, rating: 8.5, runtime: 132, genres: 'Comedy, Thriller, Drama', poster: 'https://image.tmdb.org/t/p/w185/x.jpg', in_theatres: false };
const ANSWER = '**11:14** (2003) is the one that fits: five storylines collide at the same minute, so it moves fast and stays tense.\n\n*Sources: [11:14](https://en.wikipedia.org/?curid=2763171) (Wikipedia, CC BY-SA). Film data from TMDB.*';
const sse = (ev, d) => `event: ${ev}\ndata: ${JSON.stringify(d)}\n\n`;
async function mk(b, w, h, opts = {}) {
  const c = await b.newContext(Object.assign({ viewport: { width: w, height: h } }, opts));
  await require('./fonts.js')(c); await c.route(/image\.tmdb\.org/, tile);
  const p = await c.newPage(); p.errs = [];
  p.on('pageerror', e => p.errs.push(e.message)); p.on('console', m => { if (m.type() === 'error' && !/ERR_FAILED|404/.test(m.text())) p.errs.push(m.text()); });
  return p;
}
const overlap = (a, b) => !(a.right <= b.left + 0.5 || b.right <= a.left + 0.5 || a.bottom <= b.top + 0.5 || b.bottom <= a.top + 0.5);
(async () => {
  const b = await chromium.launch();
  // ---------- A: genres on Home
  let p = await mk(b, 1440, 900);
  await p.goto(BASE + '#/', { waitUntil: 'networkidle' });
  ok('A label "Explore by genre" is shown', await p.getByRole('heading', { name: 'Explore by genre' }).count() === 1);
  const thr = p.locator('.genres .chip', { hasText: 'Thriller' });
  const bg0 = await thr.evaluate(e => getComputedStyle(e).backgroundColor);
  await thr.click(); await p.waitForTimeout(900); await p.mouse.move(2, 2); await p.waitForTimeout(300);
  ok('A Thriller chip is pressed and looks different', await thr.getAttribute('aria-pressed') === 'true' && bg0 !== await thr.evaluate(e => getComputedStyle(e).backgroundColor));
  ok('A still on Home', /#\/$|v8$|v8#?$/.test(p.url()) && await p.locator('.hero').count() === 1, p.url());
  const titles = await p.locator('#home-rails .section h2').allTextContents();
  ok('A rows are filtered (Top-rated Thriller row present)', titles.some(t => /Top-rated Thriller/.test(t)), titles.join(' | '));
  const bad = await p.evaluate(async () => { const j = await (await fetch('/api/v8/home?genres=Thriller', { headers: { 'X-MB-User': localStorage.getItem('mb.user') } })).json();
    return j.rails.flatMap(r => r.items).filter(c => !/Thriller/.test(c.genres || '')).map(c => c.title); });
  ok('A no non-thriller film in any filtered row', bad.length === 0, bad.join(','));
  await p.locator('.genres .chip', { hasText: 'Mystery' }).click(); await p.waitForTimeout(500);
  await p.goto(BASE + '#/discover'); await p.waitForTimeout(500);
  const tags = await p.locator('#active-filters .ftag').allTextContents();
  ok('A chat opens with Thriller and Mystery already active', tags.join(',').includes('Thriller') && tags.join(',').includes('Mystery'), tags.join(','));
  let sent = null;
  await p.route('**/api/chat', async r => { sent = JSON.parse(r.request().postData()); await r.fulfill({ status: 200, contentType: 'text/event-stream',
    body: sse('status', { stage: 'searching' }) + sse('sources', { id: 'x1', films: [FILM], intent: 'recommend', alternatives: [] }) + sse('status', { stage: 'answering' }) +
      ANSWER.match(/.{1,12}/gs).map(t => sse('token', { text: t })).join('') + sse('done', { id: 'x1', answer: ANSWER, recent_titles: [], timings: {}, cached: false }) }); });
  await p.fill('#chat-q', 'A thriller under 90 minutes'); await p.keyboard.press('Enter'); await p.waitForSelector('.msg.bot .fb');
  ok('A chat request carries the shared genres', JSON.stringify(sent.filters.genres) === '["Mystery","Thriller"]' && sent.filters.brief === true, JSON.stringify(sent.filters));
  await p.locator('#active-filters .ftag').first().click(); await p.waitForTimeout(200);
  await p.goto(BASE + '#/'); await p.waitForTimeout(700);
  ok('A removing a filter in chat un-presses it on Home', await p.locator('.genres .chip[aria-pressed="true"]').count() === 1);
  await p.getByRole('button', { name: 'Clear' }).click(); await p.waitForTimeout(600);
  ok('A Clear removes every genre', await p.locator('.genres .chip[aria-pressed="true"]').count() === 0);
  // ---------- B: hover never clips or shifts
  const card = p.locator('#home-rails .card').first(); await card.scrollIntoViewIfNeeded();
  const before = await p.evaluate(() => [...document.querySelectorAll('#home-rails .card')].slice(0, 6).map(c => { const r = c.getBoundingClientRect(); return [Math.round(r.left), Math.round(r.top), Math.round(r.width), Math.round(r.height)]; }));
  await card.hover(); await p.waitForTimeout(400);
  const hov = await p.evaluate(() => { const c = document.querySelector('#home-rails .card'), pl = c.querySelector('.pl').getBoundingClientRect(), rail = c.closest('.rail').getBoundingClientRect();
    return { top: pl.top, railTop: rail.top, tf: getComputedStyle(c.querySelector('.pl')).transform, tr: getComputedStyle(c.querySelector('.pl')).transitionDuration }; });
  const after = await p.evaluate(() => [...document.querySelectorAll('#home-rails .card')].slice(0, 6).map(c => { const r = c.getBoundingClientRect(); return [Math.round(r.left), Math.round(r.top), Math.round(r.width), Math.round(r.height)]; }));
  ok('B hovered poster (and its ring) stays inside the row: no clipping', hov.top - 2 >= hov.railTop, `poster top ${hov.top.toFixed(1)} vs row top ${hov.railTop.toFixed(1)}`);
  ok('B hover is animated and lifts', hov.tf !== 'none' && parseFloat(hov.tr) > 0.1, `${hov.tf} ${hov.tr}`);
  ok('B no card moved or resized on hover', JSON.stringify(before) === JSON.stringify(after));
  // ---------- C: In theatres carousel
  await p.evaluate(() => scrollTo(0, 0)); await p.mouse.move(700, 880);
  const cur = () => p.evaluate(() => [...document.querySelectorAll('#hero-car .dots button')].findIndex(d => d.getAttribute('aria-current') === 'true'));
  await p.reload({ waitUntil: 'networkidle' }); await p.mouse.move(700, 880);
  const c0 = await cur(); const t0 = Date.now(); let moved = 0;
  while (Date.now() - t0 < 8000) { if (await cur() !== c0) { moved = Date.now() - t0; break; } await p.waitForTimeout(100); }
  ok('C advances by itself after about 5 s', moved > 3800 && moved < 6500, `${moved} ms`);
  const c1 = await cur(); await p.click('#hero-car .arrow.next'); await p.waitForTimeout(200);
  ok('C next arrow moves one film on', await cur() === (c1 + 1) % await p.locator('#hero-car .dots button').count());
  await p.click('#hero-car .arrow.prev'); await p.waitForTimeout(200); ok('C previous arrow moves back', await cur() === c1);
  await p.hover('#hero-car .viewp'); const c2 = await cur(); await p.waitForTimeout(6200);
  ok('C pauses while hovered', await cur() === c2);
  await p.focus('#hero-car .viewp'); await p.keyboard.press('ArrowRight'); await p.waitForTimeout(200); ok('C arrow keys work', await cur() !== c2);
  const tf = await p.evaluate(() => getComputedStyle(document.querySelector('#hero-car .track')).transitionProperty);
  ok('C slides sideways (transform transition)', /transform/.test(tf), tf);
  const railArrows = await p.evaluate(() => [...document.querySelectorAll('#home-rails .railbox')].map(b => !!b.querySelector('.arrow.next')));
  ok('C every poster row has arrows', railArrows.length > 0 && railArrows.every(Boolean), String(railArrows.length));
  const rb = p.locator('#home-rails .railbox:not(.no-arrows)').first();
  if (await rb.count()) { const s0 = await rb.locator('.rail').evaluate(e => e.scrollLeft); await rb.locator('.arrow.next').click(); await p.waitForTimeout(700);
    ok('C row arrow scrolls the row', await rb.locator('.rail').evaluate(e => e.scrollLeft) > s0); }
  ok('C no Continue row on Home', !/Continue:|Pick up where you left off|Continue exploring/.test(await p.textContent('#view')));
  const rm = await mk(b, 1440, 900, { reducedMotion: 'reduce' }); await rm.goto(BASE + '#/', { waitUntil: 'networkidle' });
  const r0 = await rm.evaluate(() => [...document.querySelectorAll('#hero-car .dots button')].findIndex(d => d.getAttribute('aria-current') === 'true')); await rm.waitForTimeout(6000);
  ok('C reduced motion: no autoplay', r0 === await rm.evaluate(() => [...document.querySelectorAll('#hero-car .dots button')].findIndex(d => d.getAttribute('aria-current') === 'true')));
  const ph = await mk(b, 390, 844, { hasTouch: true, isMobile: true }); await ph.goto(BASE + '#/', { waitUntil: 'networkidle' });
  const m0 = await ph.evaluate(() => [...document.querySelectorAll('#hero-car .dots button')].findIndex(d => d.getAttribute('aria-current') === 'true'));
  await ph.evaluate(() => { const v = document.querySelector('#hero-car .viewp'); const r = v.getBoundingClientRect(); const t = (x) => new Touch({ identifier: 1, target: v, clientX: x, clientY: r.top + 20 });
    v.dispatchEvent(new TouchEvent('touchstart', { touches: [t(r.right - 30)], changedTouches: [t(r.right - 30)], bubbles: true }));
    v.dispatchEvent(new TouchEvent('touchend', { touches: [], changedTouches: [t(r.left + 30)], bubbles: true })); });
  await ph.waitForTimeout(200);
  ok('C swipe moves the carousel on a phone', m0 !== await ph.evaluate(() => [...document.querySelectorAll('#hero-car .dots button')].findIndex(d => d.getAttribute('aria-current') === 'true')));
  ok('C phone shows arrows too', await ph.locator('#hero-car .arrow.next').isVisible());
  // ---------- D + E: short answer and chat layout, several screen sizes
  for (const [w, hgt] of [[1440, 900], [1280, 720], [1024, 600], [390, 844], [360, 640]]) {
    const q = await mk(b, w, hgt);
    await q.route('**/api/chat', r => r.fulfill({ status: 200, contentType: 'text/event-stream',
      body: sse('status', { stage: 'searching' }) + sse('sources', { id: 'x1', films: [FILM], intent: 'recommend', alternatives: [] }) + ANSWER.match(/.{1,12}/gs).map(t => sse('token', { text: t })).join('') + sse('done', { id: 'x1', answer: ANSWER, recent_titles: [], timings: {}, cached: false }) }));
    await q.goto(BASE + '#/discover', { waitUntil: 'networkidle' });
    await q.fill('#chat-q', 'Recommend one thriller under 90 minutes.'); await q.keyboard.press('Enter'); await q.waitForSelector('.msg.bot .fb'); await q.waitForTimeout(300);
    const g = await q.evaluate(() => { const R = s => { const e = document.querySelector(s); return e ? e.getBoundingClientRect().toJSON() : null; };
      const sc = document.querySelector('#msgs-wrap');
      return { bar: R('.chat-bar'), wrap: R('#msgs-wrap'), comp: R('.composer'), filters: R('#chat-filters'), saved: R('#chat-saved'), nw: R('#chat-new'), send: R('#chat-send'), tab: R('.tabbar'),
        fb: R('.msg.bot .fb'), rec: R('.rec'), pageScroll: document.documentElement.scrollHeight - innerHeight, inner: sc.scrollHeight - sc.clientHeight, vh: innerHeight,
        raw: /\]\(http|\[11:14\]/.test(document.querySelector('.msg.bot').innerText), srcs: document.querySelector('.srcs summary')?.innerText || '', body: document.querySelector('.msg.bot .body').innerText }; });
    const tag = `${w}x${hgt}`;
    ok(`E ${tag} toolbar, messages and composer do not overlap`, !overlap(g.bar, g.wrap) && !overlap(g.wrap, g.comp) && !overlap(g.bar, g.comp));
    ok(`E ${tag} Filters, Chats, New chat and Send are all on screen`, [g.filters, g.saved, g.nw, g.send].every(r => r && r.top >= 0 && r.bottom <= g.vh && r.width > 0));
    const tabVisible = g.tab && g.tab.height > 0 && w < 1024;
    ok(`E ${tag} composer sits above the tab bar, page itself does not scroll`, (!tabVisible || g.comp.bottom <= g.tab.top + 1) && g.pageScroll <= 1, `page scroll ${g.pageScroll}`);
    ok(`D ${tag} no raw markdown or URL in the answer`, !g.raw && /Sources \(1\)/.test(g.srcs) && /CC BY-SA/.test(g.srcs), g.srcs.replace(/\n/g, ' '));
    if (hgt >= 720) ok(`D ${tag} question, answer, film card and actions fit without scrolling`, g.inner <= 1 && g.fb.bottom <= g.comp.top, `inner scroll ${g.inner}`);
    ok(`D ${tag} film card is compact`, g.rec && g.rec.height <= 150, g.rec ? `${Math.round(g.rec.width)}x${Math.round(g.rec.height)}` : 'none');
    if (w === 1440) {
      await q.click('.srcs summary'); const href = await q.locator('.srcs a').first().getAttribute('href'); const rel = await q.locator('.srcs a').first().getAttribute('rel');
      ok('D source link is a real, safe link', href === 'https://en.wikipedia.org/?curid=2763171' && /noopener/.test(rel));
      ok('D Want to watch works from the chat card', await (async () => { await q.locator('.rec .mini').first().click(); await q.waitForTimeout(400); return (await q.locator('.rec .mini').first().getAttribute('aria-pressed')) === 'true'; })());
      ok('D no console errors', q.errs.length === 0, q.errs.join(' | '));
    }
    await q.context().close();
  }
  // ---------- F: calendar
  await p.goto(BASE + '#/calendar', { waitUntil: 'networkidle' }); await p.waitForTimeout(400);
  const wk0 = await p.textContent('.wk-title');
  ok('F seven day columns with today marked', await p.locator('.wk-grid .col').count() === 7 && await p.locator('.wk-grid .col.today').count() === 1, wk0);
  await p.getByRole('button', { name: 'Next week' }).click(); await p.waitForTimeout(600);
  const wk1 = await p.textContent('.wk-title'); ok('F next week changes the dates', wk1 !== wk0, wk1);
  await p.getByRole('button', { name: 'Previous week' }).click(); await p.waitForTimeout(500); await p.getByRole('button', { name: 'Previous week' }).click(); await p.waitForTimeout(500);
  ok('F previous week goes back past this week', (await p.textContent('.wk-title')) !== wk0);
  await p.getByRole('button', { name: 'This week' }).click(); await p.waitForTimeout(500); ok('F This week returns', (await p.textContent('.wk-title')) === wk0);
  let found = false; for (let i = 0; i < 8 && !found; i++) { if (await p.locator('.wk-grid a.ev').count()) found = true; else { await p.getByRole('button', { name: 'Next week' }).click(); await p.waitForTimeout(500); } }
  ok('F a dated release card with a type label is shown', found && await p.locator('.wk-grid a.ev .kd').count() > 0);
  if (found) { await p.locator('.wk-grid a.ev').first().click(); await p.waitForTimeout(600); ok('F clicking a release opens its film page', /#\/film\/\d+/.test(p.url())); }
  await ph.goto(BASE + '#/calendar', { waitUntil: 'networkidle' }); await ph.waitForTimeout(400);
  ok('F phone: day strip of 7, one selected, list for that day', await ph.locator('.wk-strip button').count() === 7 && await ph.locator('.wk-strip [aria-selected="true"]').count() === 1 && await ph.locator('.wk-day h2').count() === 1);
  await ph.locator('.wk-strip button').nth(5).click(); ok('F phone: selecting a day changes the list heading', /Friday/.test(await ph.textContent('.wk-day h2')));
  // ---------- G: showtimes
  await p.goto(BASE + '#/film/693134', { waitUntil: 'networkidle' }); await p.waitForTimeout(500);
  ok('G film in theatres shows a Showtimes action by Where to watch', await p.locator('#showtimes-btn').isVisible());
  await p.click('#showtimes-btn'); await p.waitForSelector('.stlinks a'); await p.fill('#st-zip', '01845'); await p.getByRole('button', { name: 'Update' }).click(); await p.waitForTimeout(500);
  const links = await p.locator('.stlinks a').evaluateAll(a => a.map(x => x.href));
  ok('G links are film-specific searches near the ZIP', links.length >= 2 && /Dune/.test(decodeURIComponent(links[0])) && /01845/.test(links[0]) && links.every(l => l.startsWith('https://')), links[0]);
  await p.keyboard.press('Escape');
  await p.goto(BASE + '#/film/496243', { waitUntil: 'networkidle' }); await p.waitForTimeout(500);
  ok('G film not in theatres has no Showtimes action', await p.locator('#showtimes-btn').count() === 0);
  // ---------- H: TV watchlist
  await p.goto(BASE + '#/watchlist', { waitUntil: 'networkidle' }); await p.waitForTimeout(400);
  await p.fill('#tv-q', 'Stranger'); await p.waitForSelector('.tv-res .wl-row'); await p.locator('.tv-res button', { hasText: 'Follow' }).first().click(); await p.waitForTimeout(600);
  const tv = await p.textContent('.wl-row.tv');
  ok('H followed show appears with its next dated season', /Stranger Things/.test(tv) && /Season 6 starts/.test(tv) && /Upcoming season/.test(tv), tv.slice(0, 90));
  ok('H alerts for the show can be switched', await p.locator('.wl-row.tv input[type=checkbox]').isChecked());
  const al = await p.evaluate(async () => (await (await fetch('/api/v8/alerts', { headers: { 'X-MB-User': localStorage.getItem('mb.user') } })).json()).alerts.filter(a => a.kind.startsWith('tv_')).map(a => a.text));
  ok('H one alert for the dated season', al.length === 1 && /season 6 has a date/.test(al[0]), al.join(' | '));
  await p.locator('.wl-row.tv input[type=checkbox]').uncheck(); await p.waitForTimeout(300); await p.reload({ waitUntil: 'networkidle' }); await p.waitForTimeout(400);
  ok('H the alert choice persists', !(await p.locator('.wl-row.tv input[type=checkbox]').isChecked()));
  ok('no console errors (desktop session)', p.errs.length === 0, p.errs.slice(0, 3).join(' | '));
  ok('no console errors (phone session)', ph.errs.length === 0, ph.errs.slice(0, 3).join(' | '));
  await b.close();
  console.log(res.join('\n')); console.log(`${res.filter(r => r.startsWith('PASS')).length} pass, ${res.filter(r => r.startsWith('FAIL')).length} fail`);
})().catch(e => { console.log(res.join('\n')); console.log('CRASH', e.message.split('\n').slice(0, 3).join(' / ')); process.exit(1); });
