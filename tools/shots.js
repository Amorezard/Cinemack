// Screenshots with a signed-in person who has a list, a friend (Sam) who shares theirs, and their services set.
const { chromium } = require('playwright');
const BASE = 'http://127.0.0.1:8765/v8', OUT = process.argv[2];
async function call(page, user, path, body) {
  return page.evaluate(async ([user, path, body]) => {
    const r = await fetch(path, { method: body ? 'POST' : 'GET', headers: { 'Content-Type': 'application/json', 'X-MB-User': user },
      body: body ? JSON.stringify(body) : undefined });
    try { return await r.json(); } catch { return {}; }
  }, [user, path, body]);
}
(async () => {
  const b = await chromium.launch();
  const tile = r => { let h = 0; for (const ch of r.request().url()) h = (h * 31 + ch.charCodeAt(0)) % 360;
    r.fulfill({ contentType: 'image/svg+xml', body: `<svg xmlns="http://www.w3.org/2000/svg" width="342" height="513"><defs><linearGradient id="g" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="hsl(${h},32%,42%)"/><stop offset="1" stop-color="hsl(${(h + 40) % 360},36%,20%)"/></linearGradient></defs><rect width="100%" height="100%" fill="url(#g)"/></svg>` }); };
  const mk = async (w, theme, state) => {
    const c = await b.newContext({ viewport: { width: w, height: w < 768 ? 844 : 900 }, deviceScaleFactor: 2, storageState: state });
    await require('./fonts.js')(c); await c.route(/image\.tmdb\.org/, tile);
    await c.addInitScript(([t]) => { localStorage.setItem('mb.user', 'shots-me'); if (t) localStorage.setItem('cm.theme', t); }, [theme]);
    return c;
  };
  const stamp = Date.now().toString(36).slice(-5);
  const fcx = await mk(1440, ''); const fp = await fcx.newPage(); await fp.goto(BASE + '#/');
  const mcx = await mk(1440, ''); const mp = await mcx.newPage(); await mp.goto(BASE + '#/');
  await call(fp, 'shots-f', '/api/signup', { username: 'sam' + stamp, password: 'pw-sam-' + stamp + '-1', display_name: 'Sam' });
  await call(mp, 'shots-me', '/api/signup', { username: 'wes' + stamp, password: 'pw-wes-' + stamp + '-1', display_name: 'Wes' });
  const samCode = (await call(fp, 'shots-f', '/api/v8/profile')).code;
  console.log('request', JSON.stringify(await call(mp, 'shots-me', '/api/v8/friends/request', { code: samCode })));
  const inc = (await call(fp, 'shots-f', '/api/v8/profile')).friends.incoming;
  await call(fp, 'shots-f', '/api/v8/friends/respond', { key: inc[0].key, accept: true });
  for (const [id, ch] of [[496243, 'want'], [329865, 'want'], [27205, 'liked'], [603, 'liked'], [949, 'liked'], [680, 'not'], [157336, 'liked']])
    await call(fp, 'shots-f', '/api/film', { id, title: '', choice: ch });
  await call(fp, 'shots-f', '/api/v8/review', { id: 940721, rating: 5, text: '' });
  await call(fp, 'shots-f', '/api/v8/privacy', { changes: { watchlist: 'friends', ratings: 'friends' } });
  for (const [id, ch] of [[496243, 'want'], [693134, 'want'], [940721, 'want'], [120467, 'want'], [329865, 'want'], [550, 'want'],
                          [27205, 'liked'], [603, 'liked'], [949, 'liked'], [680, 'liked'], [157336, 'liked']])
    await call(mp, 'shots-me', '/api/film', { id, title: '', choice: ch });
  await call(mp, 'shots-me', '/api/settings', { settings: { services: ['netflix'] } });
  const state = await mcx.storageState(); await fcx.close(); await mcx.close();
  for (const [w, theme] of [[390, ''], [1440, ''], [390, 'light'], [1440, 'light']]) {
    const c = await mk(w, theme, state); const p = await c.newPage();
    const tag = `${theme ? theme + '_' : ''}${w}`;
    for (const r of ['#/', '#/watchlist', '#/pick', '#/film/496243']) {
      await p.goto(BASE + r); await p.reload({ waitUntil: 'networkidle' }); await p.waitForTimeout(800);
      await p.mouse.move(1, 1);
      await p.screenshot({ path: `${OUT}/${(r.replace(/[#\/]+/g, '_').replace(/^_|_$/g, '') || 'home')}_${tag}.png`, fullPage: r === '#/watchlist' });
    }
    if (w === 1440 && !theme) { await p.goto(BASE + '#/'); await p.reload({ waitUntil: 'networkidle' }); await p.click('#chats-top'); await p.waitForTimeout(700);
      await p.screenshot({ path: `${OUT}/chats_panel_${tag}.png` }); }
    await c.close();
  }
  await b.close();
})().catch(e => { console.log('ERR', e.message); process.exit(1); });
