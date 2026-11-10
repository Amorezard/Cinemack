const { chromium } = require('playwright'); const OUT = process.argv[2];
const sse = (ev, d) => `event: ${ev}\ndata: ${JSON.stringify(d)}\n\n`;
const FILM = { id: 496243, title: '11:14', year: 2003, rating: 6.8, runtime: 86, genres: 'Thriller, Mystery', poster: 'https://image.tmdb.org/t/p/w185/x.jpg' };
const ANSWER = '**11:14** (2003) is the one that fits: five storylines collide at the same minute, so it moves fast and stays tense.\n\n*Sources: [11:14](https://en.wikipedia.org/?curid=2763171) (Wikipedia, CC BY-SA). Film data from TMDB.*';
(async () => { const b = await chromium.launch();
  for (const [w, h] of [[1440, 900], [390, 844]]) { const c = await b.newContext({ viewport: { width: w, height: h }, deviceScaleFactor: 2 }); await require('./fonts.js')(c);
    await c.route(/image\.tmdb\.org/, r => r.fulfill({ contentType: 'image/svg+xml', body: '<svg xmlns="http://www.w3.org/2000/svg" width="342" height="513"><rect width="100%" height="100%" fill="#476"/></svg>' }));
    const p = await c.newPage(); await p.addInitScript(() => localStorage.setItem('cm.filters', JSON.stringify({ genres: ['Thriller'], max_runtime: 90 })));
    await p.route('**/api/chat', r => r.fulfill({ status: 200, contentType: 'text/event-stream', body: sse('sources', { id: 'x', films: [FILM], intent: 'recommend' }) + sse('token', { text: ANSWER }) + sse('done', { id: 'x', answer: ANSWER, recent_titles: [], timings: {} }) }));
    await p.goto('http://127.0.0.1:8765/v8#/discover', { waitUntil: 'networkidle' }); await p.screenshot({ path: `${OUT}/chat_empty_${w}.png` });
    await p.fill('#chat-q', 'Recommend one thriller under 90 minutes.'); await p.keyboard.press('Enter'); await p.waitForSelector('.msg.bot .fb'); await p.waitForTimeout(300); await p.mouse.move(1, 1);
    await p.screenshot({ path: `${OUT}/chat_answer_${w}.png` }); await c.close(); }
  await b.close(); })();
