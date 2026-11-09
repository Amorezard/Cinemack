// Hover and hero checks: every control type changes on hover; the hero shows one poster at a time, stacked exactly,
// and cross-fades on a timer; reduced motion holds it still.
const { chromium } = require('playwright');
const BASE = 'http://127.0.0.1:8765/v8';
const res = []; const ok = (n, c, x = '') => res.push(`${c ? 'PASS' : 'FAIL'} ${n}${x ? '  ' + x : ''}`);
(async () => {
  const b = await chromium.launch();
  const ctx = await b.newContext({ viewport: { width: 1440, height: 900 } });
  await require('./fonts.js')(ctx);
  await ctx.route(/image\.tmdb\.org/, r => r.fulfill({ contentType: 'image/svg+xml', body: '<svg xmlns="http://www.w3.org/2000/svg" width="342" height="513"><rect width="100%" height="100%" fill="#567"/></svg>' }));
  const p = await ctx.newPage();
  await p.goto(BASE + '#/', { waitUntil: 'networkidle' }); await p.waitForTimeout(600);
  const st = await p.evaluate(() => { const a = [...document.querySelectorAll('#hero-stage > a')];
    const r = a.map(x => x.getBoundingClientRect()); return { n: a.length, on: a.filter(x => x.classList.contains('on')).length,
      same: r.every(q => Math.abs(q.left - r[0].left) < 1 && Math.abs(q.top - r[0].top) < 1 && Math.abs(q.width - r[0].width) < 1) }; });
  ok('hero: several posters, exactly one shown, all stacked on the same spot', st.n > 1 && st.on === 1 && st.same, JSON.stringify(st));
  const first = await p.evaluate(() => document.querySelector('#hero-stage > a.on').getAttribute('href'));
  await p.waitForTimeout(7600);
  const second = await p.evaluate(() => document.querySelector('#hero-stage > a.on').getAttribute('href'));
  ok('hero advances on its own after ~7 s', first !== second, `${first} -> ${second}`);
  const tr = await p.evaluate(() => getComputedStyle(document.querySelector('#hero-stage > a')).transitionDuration);
  ok('hero fade is slow (>= 1 s)', parseFloat(tr) >= 1, tr);
  await p.hover('#hero-stage'); const h1 = await p.evaluate(() => document.querySelector('#hero-stage > a.on').getAttribute('href'));
  await p.waitForTimeout(7600); const h2 = await p.evaluate(() => document.querySelector('#hero-stage > a.on').getAttribute('href'));
  ok('hero pauses while hovered', h1 === h2);
  for (const sel of ['.chips.scroll .chip', '.hero .btn.secondary', '.hero .askbox .btn.primary', '.topnav a:not([aria-current])', '#chats-top', '.card .bm']) {
    await p.mouse.move(5, 890); await p.waitForTimeout(250);
    const el = p.locator(sel).first();
    const before = await el.evaluate(e => { const c = getComputedStyle(e); return c.backgroundColor + c.borderColor + c.color + c.boxShadow; });
    await el.hover(); await p.waitForTimeout(300);
    const after = await el.evaluate(e => { const c = getComputedStyle(e); return c.backgroundColor + c.borderColor + c.color + c.boxShadow; });
    ok(`hover changes ${sel}`, before !== after);
  }
  await p.goto(BASE + '#/pick', { waitUntil: 'networkidle' });
  const chip = p.getByRole('radio', { name: 'Funny' });
  const td = await chip.evaluate(e => getComputedStyle(e).transitionDuration);
  ok('chips animate their state change (transition set)', /0\.1[0-9]*s|0\.2/.test(td), td);
  await chip.click(); await p.mouse.move(5, 890); await p.waitForTimeout(300);
  const sel = await chip.evaluate(e => ({ bg: getComputedStyle(e).backgroundColor, color: getComputedStyle(e).color }));
  ok('selected chip is blue with white text, not yellow', sel.bg === 'rgb(47, 100, 163)' && sel.color === 'rgb(255, 255, 255)', JSON.stringify(sel));
  const rm = await b.newContext({ viewport: { width: 1440, height: 900 }, reducedMotion: 'reduce' });
  await require('./fonts.js')(rm);
  const q = await rm.newPage(); await q.goto(BASE + '#/', { waitUntil: 'networkidle' });
  const r1 = await q.evaluate(() => document.querySelector('#hero-stage > a.on')?.getAttribute('href')); await q.waitForTimeout(7600);
  const r2 = await q.evaluate(() => document.querySelector('#hero-stage > a.on')?.getAttribute('href'));
  ok('reduced motion: hero holds still', r1 && r1 === r2);
  await b.close();
  console.log(res.join('\n'));
})();
