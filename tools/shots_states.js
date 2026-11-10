// Screenshots of filled-in states (a chat answer with cards and sources, Pick Tonight results, a calendar day,
// month view) by replaying fixture SSE for /api/chat. OUT=dir WIDTHS=1440,390 SCALE=1
const { chromium } = require('playwright');
const { setup } = require('./shots_redesign.js');
const BASE = 'http://127.0.0.1:8765', OUT = process.env.OUT, SCALE = Number(process.env.SCALE || 1);
const WIDTHS = (process.env.WIDTHS || '1440,390').split(',').map(Number);
const sse = (ev) => ev.map(([e, d]) => `event: ${e}\ndata: ${JSON.stringify(d)}\n\n`).join('');
(async () => {
  const H = { 'X-Access-Code': 'movies', 'X-MB-User': 'demo-wes' };
  const cal = await (await fetch(`${BASE}/api/v8/calendar?start=2026-06-01&days=180`, { headers: H })).json();
  const films = [...new Map(cal.events.filter(e => e.film).map(e => [e.film.id, e.film])).values()].slice(0, 6)
    .map((f, i) => Object.assign({}, f, { runtime: 82 + i * 2, genres: 'Thriller, Mystery', cert: 'PG-13', on_services: i < 2 ? ['Netflix'] : [] }));
  const b = await chromium.launch();
  for (const w of WIDTHS) {
    const ctx = await b.newContext({ viewport: { width: w, height: w < 768 ? 844 : 900 }, deviceScaleFactor: SCALE });
    await setup(ctx);
    const page = await ctx.newPage();
    let mode = 'chat';
    await page.route('**/api/chat', r => r.fulfill({ status: 200, contentType: 'text/event-stream', body: sse(mode === 'chat'
      ? [['sources', { films: films.slice(0, 2), intent: 'recommend', id: 's1' }], ['token', { text: `Here are two tense picks under 90 minutes. **${films[0].title}** is the tighter of the two; **${films[1].title}** leans into mystery.\n\n*Sources: [Thriller film](https://en.wikipedia.org/wiki/Thriller_film); [${films[0].title}](https://www.themoviedb.org/movie/${films[0].id}) (Wikipedia, CC BY-SA). Film data from TMDB.*` }], ['done', { id: 's1', timings: {} }]]
      : [['sources', { films, intent: 'recommend', id: 's2' }], ['token', { text: films.slice(0, 3).map((f, i) => `${i + 1}. **${f.title}**: ${['a lean, nerve-shredding night in.', 'a slow-burn puzzle that pays off.', 'smart and quick, with a great final act.'][i]}`).join('\n') }], ['done', { id: 's2' }]]) }));
    await page.goto(`${BASE}/v8#/ask`, { waitUntil: 'networkidle' });
    await page.fill('#chat-q', 'Find me a thriller under 90 minutes'); await page.keyboard.press('Enter');
    await page.waitForSelector('.ans-foot .fb'); await page.waitForTimeout(500);
    await page.screenshot({ path: `${OUT}/state_chat_${w}.png` });
    mode = 'pick';
    await page.goto(`${BASE}/v8#/pick`, { waitUntil: 'networkidle' }); await page.waitForTimeout(300);
    await page.click('.pref[data-k="mood"]'); await page.click('.pop [role=menuitemradio]:has-text("Thrilling")');
    await page.click('.pref[data-k="time"]'); await page.click('.pop [role=menuitemradio]:has-text("Under 1½ hours")');
    await page.click('.prefs .go'); await page.waitForSelector('.rk .syn'); await page.waitForTimeout(900);
    await page.screenshot({ path: `${OUT}/state_pick_${w}.png`, fullPage: true });
    await page.goto(`${BASE}/v8#/calendar?view=week&date=2026-11-16`, { waitUntil: 'networkidle' }); await page.waitForTimeout(500);
    await page.screenshot({ path: `${OUT}/state_week_${w}.png`, fullPage: w < 768 });
    await page.goto(`${BASE}/v8#/calendar?view=month&date=2026-11-16`, { waitUntil: 'networkidle' }); await page.waitForTimeout(500);
    await page.screenshot({ path: `${OUT}/state_month_${w}.png`, fullPage: w < 768 });
    await ctx.close();
  }
  await b.close();
})();
