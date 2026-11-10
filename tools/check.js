// v8 page checks against docs/success_criteria_v8.md: overflow, tap targets, axe, console errors, CLS, screenshots.
const { chromium } = require('playwright');
const fs = require('fs');
const AXE = fs.readFileSync(__dirname + '/node_modules/axe-core/axe.min.js', 'utf8');
const BASE = 'http://127.0.0.1:8765/v8';
const WIDTHS = (process.env.WIDTHS || '320,375,390,430,768,1024,1440').split(',').map(Number);
const ROUTES = (process.env.ROUTES || '#/,#/discover,#/pick,#/watchlist,#/calendar,#/profile,#/film/496243').split(',');
const SHOT = process.env.SHOT || '';
function poster(path) {
  let h = 0; for (const ch of path) h = (h * 31 + ch.charCodeAt(0)) % 360;
  return `<svg xmlns="http://www.w3.org/2000/svg" width="342" height="513"><defs><linearGradient id="g" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="hsl(${h},35%,38%)"/><stop offset="1" stop-color="hsl(${(h+40)%360},40%,16%)"/></linearGradient></defs><rect width="100%" height="100%" fill="url(#g)"/></svg>`;
}
(async () => {
  const b = await chromium.launch();
  const report = [];
  for (const w of WIDTHS) {
    const ctx = await b.newContext({ viewport: { width: w, height: w < 768 ? 800 : 900 }, deviceScaleFactor: SHOT ? 2 : 1 });
    await ctx.route(/image\.tmdb\.org/, r => r.fulfill({ contentType: 'image/svg+xml', body: poster(r.request().url()) }));
    await require('./fonts.js')(ctx);
    const page = await ctx.newPage();
    const errors = [];
    page.on('console', m => { if (m.type() === 'error' && !/fonts\.g|ERR_FAILED/.test(m.text())) errors.push(m.text()); });
    page.on('pageerror', e => errors.push('pageerror: ' + e.message));
    await page.addInitScript(() => {
      window.__cls = 0;
      new PerformanceObserver(l => { for (const e of l.getEntries()) if (!e.hadRecentInput) window.__cls += e.value; }).observe({ type: 'layout-shift', buffered: true });
    });
    for (const r of ROUTES) {
      await page.goto(BASE + r, { waitUntil: 'networkidle' });
      await page.evaluate(() => { window.__cls = 0; }); await page.reload({ waitUntil: 'networkidle' });
      await page.waitForTimeout(400);
      const m = await page.evaluate(() => {
        const sw = document.documentElement.scrollWidth, cw = document.documentElement.clientWidth;
        const small = [];
        for (const el of document.querySelectorAll('a,button,input,select,textarea,[role=tab],[role=radio]')) {
          const rc = el.getBoundingClientRect(); const cs = getComputedStyle(el);
          if (!rc.width || cs.visibility === 'hidden' || el.closest('[hidden]') || el.closest('footer.credit')) continue;
          if (el.matches('.skip') || el.getAttribute('aria-hidden') === 'true') continue;
          if (el.tagName === 'A' && el.closest('p, .feat, .hint, .fine, .notice, .why')) continue;   // inline links in a sentence (WCAG 2.5.8 exception)
          if (el.closest('.pl') || el.closest('.ti')) continue;   // poster/title links in cards are large via the poster
          const min = innerWidth < 1024 ? 44 : 24;
          if (rc.height < min || rc.width < min) small.push(`${el.tagName.toLowerCase()}.${el.className}:${(el.textContent||el.getAttribute('aria-label')||'').trim().slice(0,20)} ${Math.round(rc.width)}x${Math.round(rc.height)}`);
          
        }
        return { overflow: sw - cw, small: [...new Set(small)].slice(0, 12), cls: window.__cls };
      });
      await page.addScriptTag({ content: AXE });
      const axe = await page.evaluate(async () => { const r = await axe.run(document, { runOnly: ['wcag2a', 'wcag2aa', 'wcag21aa', 'wcag22aa'] });
        return r.violations.map(v => `${v.id}(${v.impact}) x${v.nodes.length}: ${v.nodes.slice(0,2).map(n => n.target.join(' ')).join(' | ')}`); });
      report.push({ w, r, overflow: m.overflow, cls: +m.cls.toFixed(3), small: m.small, axe, errors: errors.splice(0) });
      if (SHOT) await page.screenshot({ path: `${SHOT}/${r.replace(/[#\/?=]+/g, '_').replace(/^_|_$/g, '') || 'home'}_${w}.png`, fullPage: false });
    }
    await ctx.close();
  }
  await b.close();
  for (const x of report) {
    const bad = x.overflow > 0 || x.axe.length || x.errors.length || x.small.length || x.cls > 0.1;
    console.log(`${bad ? 'FAIL' : 'ok  '} ${String(x.w).padStart(4)} ${x.r.padEnd(16)} overflow=${x.overflow} cls=${x.cls}` +
      (x.small.length ? `\n      small: ${x.small.join('; ')}` : '') + (x.axe.length ? `\n      axe: ${x.axe.join('\n           ')}` : '') + (x.errors.length ? `\n      console: ${x.errors.join(' | ')}` : ''));
  }
})();
