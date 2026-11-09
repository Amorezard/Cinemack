// End-to-end flows on /v8 in mock mode. Each step asserts what the person would see.
const { chromium } = require('playwright');
const BASE = 'http://127.0.0.1:8765/v8';
const results = []; const ok = (name, cond, extra = '') => results.push(`${cond ? 'PASS' : 'FAIL'} ${name}${extra ? '  ' + extra : ''}`);
const NETFLIX = 'Title,Date\n"Parasite","3/1/26"\n"Arrival","2/20/26"\n"Stranger Things: Season 1: Chapter One","1/5/26"\n"Stranger Things: Season 1: Chapter Two","1/6/26"\n"Some Unknown Film Xyz","1/1/26"\n';
async function ctxFor(b, w = 390) {
  const ctx = await b.newContext({ viewport: { width: w, height: 800 } });
  await ctx.route(/image\.tmdb\.org/, r => r.fulfill({ contentType: 'image/svg+xml', body: '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="15"/>' }));
  await require('./fonts.js')(ctx);
  const page = await ctx.newPage(); page.errors = []; page.failed = [];
  page.on('pageerror', e => page.errors.push(e.message));
  page.on('console', m => { if (m.type() === 'error' && !/ERR_FAILED/.test(m.text())) page.errors.push(m.text()); });
  page.on('response', r => { if (r.status() >= 400 && !/fonts\./.test(r.url())) page.failed.push(`${r.status()} ${r.url()}`); });
  return { ctx, page };
}
async function go(page, h) { await page.goto(BASE + h); await page.reload(); await page.waitForLoadState('networkidle'); }
async function signup(page, u) {
  await go(page, '#/profile?tab=account'); await page.getByRole('button', { name: 'Create account' }).first().click();
  await page.fill('#au-u', u); await page.fill('#au-p', 'pw-' + u + '-12345'); await page.fill('#au-n', u[0].toUpperCase() + u.slice(1));
  await page.locator('dialog').getByRole('button', { name: 'Create account' }).click(); await page.waitForTimeout(500);
}
(async () => {
  const b = await chromium.launch();
  const stamp = Date.now().toString(36).slice(-5);
  const A = await ctxFor(b), B = await ctxFor(b, 1440);
  // 1 sign up both
  await signup(A.page, 'ana' + stamp); await signup(B.page, 'ben' + stamp);
  ok('sign up shows signed-in profile', /Signed in as ana/.test(await A.page.textContent('#view')));
  // 2 dialog: Esc closes, focus returns
  await go(A.page, '#/film/496243'); await A.page.waitForSelector('h1');
  await A.page.getByRole('button', { name: 'Rate or review' }).click();
  ok('rate dialog opens', await A.page.locator('dialog[open]').count() === 1);
  await A.page.keyboard.press('Escape'); await A.page.waitForTimeout(200);
  ok('Esc closes dialog', await A.page.locator('dialog[open]').count() === 0);
  // 3 want to watch -> watchlist
  await A.page.getByRole('button', { name: 'Want to watch' }).click(); await A.page.waitForTimeout(400);
  ok('want button shows saved state', /On your watchlist/.test(await A.page.textContent('.btn.want')));
  await go(A.page, '#/watchlist'); await A.page.waitForTimeout(600);
  ok('watchlist page lists the film', /Parasite/.test(await A.page.textContent('#view')));
  // 4 rating
  await go(A.page, '#/film/329865'); await A.page.waitForSelector('h1');
  await A.page.getByRole('button', { name: 'Rate or review' }).click();
  await A.page.getByRole('radio', { name: '4 stars' }).click(); await A.page.fill('#rv-text', 'Loved the ending.');
  await A.page.getByRole('button', { name: 'Save rating' }).click(); await A.page.waitForTimeout(400);
  // 5 Netflix import (file upload)
  await go(A.page, '#/profile?tab=imports'); await A.page.waitForSelector('#up-netflix');
  await A.page.setInputFiles('#up-netflix', { name: 'NetflixViewingHistory.csv', mimeType: 'text/csv', buffer: Buffer.from(NETFLIX) });
  await A.page.waitForTimeout(1200);
  const imp = await A.page.textContent('#view');
  ok('Netflix import reports rows and unmatched', /5 rows read/.test(imp) && /not matched/.test(imp), (imp.match(/\d+ rows read[^.]*\./) || [''])[0]);
  await go(A.page, '#/profile?tab=history'); await A.page.waitForTimeout(600);
  const hist = await A.page.textContent('#view');
  ok('history folds episodes into the show', /Stranger Things/.test(hist) && /2 episodes/.test(hist));
  // 6 friends: before accepting, B can't see A
  await go(A.page, '#/profile?tab=friends'); await A.page.waitForTimeout(500);
  const code = (await A.page.textContent('.notice b')).trim();
  await go(B.page, '#/profile?tab=friends'); await B.page.waitForTimeout(500);
  await B.page.fill('#fc', code); await B.page.getByRole('button', { name: 'Send request' }).click(); await B.page.waitForTimeout(600); 
  await go(A.page, '#/profile?tab=friends'); await A.page.waitForTimeout(500);
  ok('incoming request shown', await A.page.getByRole('button', { name: 'Accept' }).count() === 1);
  await A.page.getByRole('button', { name: 'Accept' }).click(); await A.page.waitForTimeout(600);
  await go(B.page, '#/profile?tab=friends'); await B.page.waitForTimeout(500);
  await B.page.getByRole('link', { name: 'View' }).click(); await B.page.waitForTimeout(600);
  ok('friend sees nothing while all private', /hasn't shared anything/.test(await B.page.textContent('#view')));
  // 7 A shares watchlist only
  await go(A.page, '#/profile?tab=privacy'); await A.page.waitForSelector('#pv-watchlist');
  await A.page.selectOption('#pv-watchlist', 'friends'); await A.page.waitForTimeout(500);
  await B.page.reload(); await B.page.waitForTimeout(800);
  const fv = await B.page.textContent('#view');
  ok('friend sees shared watchlist, not history', /Parasite/.test(fv) && !/Recently watched/.test(fv));
  // 8 discover chat (mock model)
  await go(A.page, '#/discover'); await A.page.fill('#chat-q', 'Something like Arrival but lighter');
  await A.page.keyboard.press('Enter'); await A.page.waitForSelector('.msg.bot .fb, .msg.bot .err', { timeout: 30000 });
  ok('chat answers with feedback buttons', await A.page.locator('.msg.bot .fb').count() === 1, (await A.page.locator('.msg.bot .body').first().textContent()).slice(0, 60));
  await go(A.page, '#/'); await A.page.waitForTimeout(700);
  ok('home shows Pick up where you left off after a chat', /Pick up where you left off/.test(await A.page.textContent('#view')));
  // 9 pick tonight in 3 taps: mood, time, show
  await go(A.page, '#/pick'); await A.page.waitForSelector('[role=radio]');
  let req = null; A.page.on('request', r => { if (r.url().endsWith('/api/chat')) req = JSON.parse(r.postData()); });
  await A.page.getByRole('radio', { name: 'Funny' }).click(); await A.page.getByRole('radio', { name: 'Under 1½ hours' }).click();
  await A.page.getByRole('button', { name: 'Show my picks' }).click(); await A.page.waitForTimeout(1500);
  ok('pick sends strict filters', req && req.filters.strict === true && req.filters.max_runtime === 90 && req.filters.genre === 'Comedy', JSON.stringify(req && req.filters));
  // 10 kids -> PG cap
  await A.page.getByRole('radio', { name: 'With kids' }).click(); await A.page.getByRole('button', { name: 'Show my picks' }).click(); await A.page.waitForTimeout(1500);
  ok('kids caps age rating at PG', req && req.filters.max_cert === 'PG', JSON.stringify(req && req.filters));
  // 11 guest cannot use friends
  const G = await ctxFor(b); await go(G.page, '#/profile?tab=friends'); await G.page.waitForTimeout(500);
  ok('guest sees sign-in prompt on friends', /Sign in to add friends/.test(await G.page.textContent('#view')));
  // 12 keyboard: tab reaches skip link first
  await go(G.page, '#/'); await G.page.keyboard.press('Tab');
  ok('first Tab lands on skip link', await G.page.evaluate(() => document.activeElement.className) === 'skip');
  // 13 delete account removes friend link
  await go(A.page, '#/profile?tab=account'); await A.page.getByRole('button', { name: 'Delete my account' }).click();
  await A.page.fill('#del-p', 'pw-ana' + stamp + '-12345'); await A.page.getByRole('button', { name: 'Delete everything' }).click(); await A.page.waitForTimeout(700);
  await go(B.page, '#/profile?tab=friends'); await B.page.waitForTimeout(500);
  ok('deleted account disappears from friend list', /No friends yet/.test(await B.page.textContent('#view')));
  for (const [n, p] of [['A', A.page], ['B', B.page], ['G', G.page]]) ok(`no console errors / failed requests (${n})`, !p.errors.length && !p.failed.length, [...p.errors, ...p.failed].slice(0, 4).join(' | '));
  await b.close();
  console.log(results.join('\n'));
})().catch(e => { console.log(results.join('\n')); console.log('CRASH', e.message.split('\n')[0]); process.exit(1); });
