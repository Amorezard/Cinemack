const { chromium } = require('playwright');
(async () => {
  const b = await chromium.launch();
  for (const r of ['#/', '#/film/496243']) {
    const ctx = await b.newContext({ viewport: { width: 390, height: 844 } });
    await ctx.route(/image\.tmdb\.org/, async x => { await new Promise(s => setTimeout(s, 150)); x.fulfill({ contentType: 'image/svg+xml', body: '<svg xmlns="http://www.w3.org/2000/svg" width="342" height="513"><rect width="100%" height="100%" fill="#345"/></svg>' }); });
    await ctx.route(/fonts\.(googleapis|gstatic)\.com/, x => x.abort());
    const page = await ctx.newPage();
    const cdp = await ctx.newCDPSession(page);
    await cdp.send('Emulation.setCPUThrottlingRate', { rate: 4 });
    await cdp.send('Network.emulateNetworkConditions', { offline: false, latency: 150, downloadThroughput: 1.6e6 / 8 * 1024 / 1000 * 1000, uploadThroughput: 750e3 / 8 });
    await page.addInitScript(() => { window.__lcp = 0; window.__cls = 0; window.__lt = 0;
      new PerformanceObserver(l => { for (const e of l.getEntries()) window.__lcp = e.startTime; }).observe({ type: 'largest-contentful-paint', buffered: true });
      new PerformanceObserver(l => { for (const e of l.getEntries()) if (!e.hadRecentInput) window.__cls += e.value; }).observe({ type: 'layout-shift', buffered: true });
      new PerformanceObserver(l => { for (const e of l.getEntries()) window.__lt += Math.max(0, e.duration - 50); }).observe({ type: 'longtask', buffered: true }); });
    await page.goto('http://127.0.0.1:8765/v8' + r, { waitUntil: 'networkidle' }); await page.waitForTimeout(1500);
    const m = await page.evaluate(() => ({ lcp: Math.round(window.__lcp), cls: +window.__cls.toFixed(3), tbt: Math.round(window.__lt) }));
    console.log(r, JSON.stringify(m), '(4x CPU slowdown, 1.6 Mbps, 150 ms latency)');
    await ctx.close();
  }
  await b.close();
})();
