// Screenshots of the redesigned pages (mock server on 8765, seeded user). OUT=dir USER=browser-id ROUTES=... WIDTHS=...
// FULL=1 for full-page shots. Posters are flat colour tiles (the container cannot reach TMDB); fonts are local Inter.
const { chromium } = require('playwright');
const fs = require('fs'), path = require('path');
const OUT = process.env.OUT, BASE = process.env.BASE || 'http://127.0.0.1:8765/v8', USER = process.env.USER_ID || 'demo-wes';
const WIDTHS = (process.env.WIDTHS || '1440,390').split(',').map(Number);
const ROUTES = (process.env.ROUTES || '#/ask,#/pick,#/calendar,#/calendar?view=month,#/watchlist,#/profile').split(',');
const SCALE = Number(process.env.SCALE || 1);
function poster(p) { let h = 0; for (const ch of p) h = (h * 31 + ch.charCodeAt(0)) % 360;
  const wide = /\/(fxb|bd|b)\d+|w1280|w780/.test(p);
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${wide ? 1280 : 342}" height="${wide ? 720 : 513}"><defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="hsl(${h},42%,40%)"/><stop offset="1" stop-color="hsl(${(h + 50) % 360},45%,14%)"/></linearGradient></defs><rect width="100%" height="100%" fill="url(#g)"/><circle cx="${wide ? 900 : 240}" cy="${wide ? 200 : 140}" r="${wide ? 220 : 70}" fill="hsla(${(h + 180) % 360},60%,70%,.18)"/></svg>`; }
const INTER = path.join(__dirname, 'node_modules/@fontsource/inter/files');
const css = [400, 500, 600, 700, 800].map((w, i) => `@font-face{font-family:'Inter';font-weight:${w};font-style:normal;src:url(https://fonts.gstatic.com/local/${i}.woff2) format('woff2');}`).join('\n');
module.exports.setup = async (ctx, user = USER) => {
  await ctx.route(/image\.tmdb\.org/, r => r.fulfill({ contentType: 'image/svg+xml', body: poster(r.request().url()) }));
  await ctx.route(/fonts\.googleapis\.com/, r => r.fulfill({ contentType: 'text/css', body: css }));
  await ctx.route(/fonts\.gstatic\.com\/local\/(\d+)\.woff2/, r => { const i = +r.request().url().match(/local\/(\d+)/)[1]; r.fulfill({ contentType: 'font/woff2', body: fs.readFileSync(`${INTER}/inter-latin-${[400, 500, 600, 700, 800][i]}-normal.woff2`) }); });
  await ctx.addInitScript(([u]) => { localStorage.setItem('mb.user', u); localStorage.setItem('mb.code', 'movies'); }, [user]);
};
if (require.main === module) (async () => {
  const b = await chromium.launch();
  for (const w of WIDTHS) {
    const ctx = await b.newContext({ viewport: { width: w, height: w < 768 ? 844 : 900 }, deviceScaleFactor: SCALE });
    await module.exports.setup(ctx);
    const page = await ctx.newPage();
    page.on('pageerror', e => console.log('pageerror', w, e.message));
    page.on('console', m => { if (m.type() === 'error' && !/favicon/.test(m.text())) console.log('console', w, m.text()); });
    for (const r of ROUTES) {
      await page.goto(BASE + r, { waitUntil: 'networkidle' }); await page.waitForTimeout(700);
      const name = `${r.replace(/[#\/?=&]+/g, '_').replace(/^_|_$/g, '') || 'home'}_${w}.png`;
      await page.screenshot({ path: `${OUT}/${name}`, fullPage: !!process.env.FULL });
    }
    await ctx.close();
  }
  await b.close();
})();
