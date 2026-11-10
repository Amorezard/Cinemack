// Lab timings for the redesigned pages: phone viewport, 4x CPU slowdown, 1.6 Mbps, 150 ms latency, posters delayed
// 150 ms. Seeded user (demo-wes) so pages are full. Reports LCP, CLS, total blocking time and bytes transferred.
const { chromium } = require('playwright');
const { setup } = require('./shots_redesign.js');
(async () => {
  const b = await chromium.launch();
  for (const r of ['#/', '#/ask', '#/pick', '#/calendar', '#/calendar?view=month', '#/watchlist', '#/profile']) {
    const ctx = await b.newContext({ viewport: { width: 390, height: 844 } });
    await setup(ctx);
    await ctx.route(/image\.tmdb\.org/, async x => { await new Promise(s => setTimeout(s, 150)); x.fulfill({ contentType: 'image/svg+xml', body: '<svg xmlns="http://www.w3.org/2000/svg" width="342" height="513"><rect width="100%" height="100%" fill="#345"/></svg>' }); });
    const page = await ctx.newPage();
    let bytes = 0, imgs = 0;
    page.on('response', async res => { try { const u = res.url(); if (/image\.tmdb/.test(u)) imgs++; else bytes += (await res.body()).length; } catch {} });
    const cdp = await ctx.newCDPSession(page);
    await cdp.send('Emulation.setCPUThrottlingRate', { rate: 4 });
    await cdp.send('Network.enable');
    await cdp.send('Network.emulateNetworkConditions', { offline: false, latency: 150, downloadThroughput: 1.6e6 / 8, uploadThroughput: 750e3 / 8 });
    await page.addInitScript(() => { window.__lcp = 0; window.__cls = 0; window.__lt = 0;
      new PerformanceObserver(l => { for (const e of l.getEntries()) window.__lcp = e.startTime; }).observe({ type: 'largest-contentful-paint', buffered: true });
      new PerformanceObserver(l => { for (const e of l.getEntries()) if (!e.hadRecentInput) window.__cls += e.value; }).observe({ type: 'layout-shift', buffered: true });
      new PerformanceObserver(l => { for (const e of l.getEntries()) window.__lt += Math.max(0, e.duration - 50); }).observe({ type: 'longtask', buffered: true }); });
    await page.goto('http://127.0.0.1:8765/v8' + r, { waitUntil: 'networkidle' }); await page.waitForTimeout(1500);
    const m = await page.evaluate(() => ({ lcp_ms: Math.round(window.__lcp), cls: +window.__cls.toFixed(3), tbt_ms: Math.round(window.__lt) }));
    console.log(r.padEnd(22), JSON.stringify(Object.assign(m, { kb_uncompressed: Math.round(bytes / 1024), posters_requested: imgs })));
    await ctx.close();
  }
  await b.close();
})();
