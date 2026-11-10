// Round 21 screenshots: Home signed out, join steps, show page, Pick Tonight TV, private chat + chips, calendar episodes.
const { chromium } = require('playwright');
const { setup } = require('./shots_redesign.js');
const OUT = process.env.OUT, BASE = 'http://127.0.0.1:8765/v8';
(async () => {
  const b = await chromium.launch();
  for (const w of (process.env.WIDTHS || '1440').split(',').map(Number)) {
    const ctx = await b.newContext({ viewport: { width: w, height: w < 768 ? 844 : 900 } });
    await setup(ctx, 'r21-guest-' + w);
    const p = await ctx.newPage();
    p.on('pageerror', e => console.log('pageerror', w, e.message));
    p.on('console', m => { if (m.type() === 'error' && !/favicon/.test(m.text())) console.log('console', w, m.text()); });
    const shot = async (n, full = false) => { await p.waitForTimeout(600); await p.screenshot({ path: `${OUT}/${n}_${w}.png`, fullPage: full }); };
    await p.goto(BASE + '#/', { waitUntil: 'networkidle' }); await shot('home');
    await p.goto(BASE + '#/show/6000002', { waitUntil: 'networkidle' }); await shot('show', true);
    await p.goto(BASE + '#/pick', { waitUntil: 'networkidle' });
    await p.click('.pref[data-k="kind"]'); await p.click('text=A TV show'); await p.click('.go'); await p.waitForTimeout(1200); await shot('pick_tv', true);
    await p.goto(BASE + '#/ask', { waitUntil: 'networkidle' });
    await p.click('.cside-top [aria-label="New private chat"]').catch(() => p.click('.chead [aria-label="New chat"]'));
    await p.fill('#chat-q', "no horror please, and keep it under 90 minutes"); await p.keyboard.press('Enter'); await p.waitForTimeout(2500); await shot('chat_private');
    await p.goto(BASE + '#/calendar?view=week', { waitUntil: 'networkidle' }); await shot('cal_week', true);
    await p.goto(BASE + '#/join', { waitUntil: 'networkidle' }); await shot('join1');
    const u = 'r21u' + w + Date.now().toString(36);
    await p.fill('#j-u', u); await p.fill('#j-p', 'correct-horse-21'); await p.fill('#j-p2', 'correct-horse-21'); await p.fill('#j-n', 'Riley');
    await p.click('.join-nav .btn.primary'); await p.waitForTimeout(1200); await shot('join2', true);
    await p.click('.tg .chip >> nth=0'); await p.click('.join-nav .btn.primary'); await p.waitForTimeout(700); await shot('join3', true);
    await p.click('.join-nav .btn.primary'); await p.waitForTimeout(700); await shot('join4');
    await p.click('.join-nav .btn.primary'); await p.waitForTimeout(500); await shot('join5');
    await p.click('.join-nav .btn.primary'); await p.waitForTimeout(1200); await shot('join_done');
    await p.goto(BASE + '#/', { waitUntil: 'networkidle' }); await shot('home_signed_in');
    await ctx.close();
  }
  await b.close();
})();
