// v8.1 round 45: the text layer (8.1), the one-reason sentence (6.2) and the forecast panel (4.6), without a browser.
//   node --test tests/js/test_v81_frontend.mjs
// The page modules are copied to a temporary folder marked as ES modules, so this works on any Node 18+.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const v8 = path.join(here, '..', '..', 'web', 'v8');
const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'v81fe-'));
fs.writeFileSync(path.join(tmp, 'package.json'), '{"type":"module"}');
for (const f of ['i18n.js', 'why.js', 'forecast_panel.js']) fs.copyFileSync(path.join(v8, f), path.join(tmp, f));
const I = await import(pathToFileURL(path.join(tmp, 'i18n.js')));
const W = await import(pathToFileURL(path.join(tmp, 'why.js')));
const F = await import(pathToFileURL(path.join(tmp, 'forecast_panel.js')));
const tables = { en: JSON.parse(fs.readFileSync(path.join(v8, 'locales', 'en.json'))), es: JSON.parse(fs.readFileSync(path.join(v8, 'locales', 'es.json'))) };
process.on('exit', () => fs.rmSync(tmp, { recursive: true, force: true }));

// the smallest document that why.js / forecast_panel.js need
function fakeDoc() {
  const mk = (tag) => ({ tag, className: '', textContent: null, children: [], attrs: {},
    append(...xs) { for (const x of xs) this.children.push(typeof x === 'string' ? { text: x } : x); },
    setAttribute(k, v) { this.attrs[k] = v; },
    remove() { this.parent.children = this.parent.children.filter(c => c !== this); },
    querySelectorAll(sel) { const cls = sel.slice(1); const out = []; const walk = (n) => { for (const c of n.children || []) { if (c.className === cls) { c.parent = n; out.push(c); } walk(c); } }; walk(this); return out; } });
  return { createElement: mk, createTextNode: (text) => ({ text }) };
}
const textOf = (n) => (n.text ?? '') + (n.textContent ?? '') + (n.children || []).map(textOf).join('');

test('language choice falls back to English', () => {
  assert.equal(I.pickLanguage(['es-MX', 'en']), 'es');
  assert.equal(I.pickLanguage('fr-FR'), 'en');
  assert.equal(I.pickLanguage(undefined), 'en');
  assert.equal(I.configure('es', tables), 'es');
  assert.equal(I.configure('fr', tables), 'en');
  assert.equal(I.configure('es', { en: tables.en }), 'en');           // Spanish file missing: English, not half a page
});

test('sentences, placeholders and plurals', () => {
  I.configure('en', tables);
  assert.equal(I.t('why.shares_director', { name: 'Ana Díaz' }), 'From Ana Díaz, who directed a film you liked.');
  assert.equal(I.t('count.films', { count: 1 }), '1 film');
  assert.equal(I.t('count.films', { count: 3 }), '3 films');
  assert.equal(I.t('why.shares_director', {}), 'From {name}, who directed a film you liked.');   // a missing value stays visible
  assert.equal(I.t('no.such.key'), 'no.such.key');
  assert.deepEqual(I.missing(), ['no.such.key']);
  I.configure('es', tables);
  assert.equal(I.t('count.films', { count: 1 }), '1 película');
  assert.equal(I.t('count.films', { count: 2 }), '2 películas');
  assert.deepEqual(I.missing(), []);
});

test('a key missing in Spanish is shown in English and reported', () => {
  const es = { ...tables.es };
  delete es['forecast.title'];
  I.configure('es', { en: tables.en, es });
  assert.equal(I.t('forecast.title'), 'Box-office forecast');
  assert.deepEqual(I.missing(), ['forecast.title']);
});

test('dates, numbers and money follow the page language', () => {
  I.configure('en', tables);
  assert.equal(I.date('2026-11-15'), 'Nov 15, 2026');
  assert.equal(I.date('2026-01-01T23:59:00-08:00'), 'Jan 1, 2026');   // a day is a day: no time zone moves it
  assert.equal(I.date('soon'), '');
  assert.equal(I.money(120000000, 'USD'), '$120M');
  assert.equal(I.number(1234.5), '1,234.5');
  I.configure('es', tables);
  assert.match(I.date('2026-11-15'), /15 nov 2026/);
  assert.match(I.money(120000000, 'USD'), /120\s?M/);
  assert.notEqual(I.money(120000000, 'USD'), '$120M');                // not the English form inside a Spanish page
});

test('one reason sentence for every code, and unknown codes are safe', () => {
  I.configure('en', tables);
  const seen = new Set();
  for (const code of W.REASONS) {
    const s = W.whyText({ code, params: { name: 'Crime', liked_media_id: 'movie:1' } }, { 'movie:1': 'Heat' });
    assert.ok(s && !s.includes('{') && !s.startsWith('why.'), code);
    seen.add(s);
  }
  assert.equal(seen.size, W.REASONS.length);
  assert.equal(W.whyText({ code: 'similar_to_liked', params: { liked_media_id: 'movie:9' } }, {}), 'People who liked a film you rated highly often liked this too.');
  const fallback = I.t('why.popular_fits_limits');
  assert.equal(W.whyText({ code: 'made_up', params: {} }), fallback);
  assert.equal(W.whyText(null), fallback);
  assert.equal(W.whyText({ code: 'shares_director', params: {} }), fallback);     // no name: no half sentence
  assert.deepEqual(I.missing(), []);
});

test('a card shows exactly one why block, and names are text, not HTML', () => {
  I.configure('en', tables);
  const doc = fakeDoc();
  const card = doc.createElement('article');
  W.whyBlock(doc, card, { code: 'popular_fits_limits', params: {} });
  W.whyBlock(doc, card, { code: 'shares_cast', params: { name: '<img src=x onerror=alert(1)>' } });
  const blocks = card.querySelectorAll('.why1');
  assert.equal(blocks.length, 1);
  assert.equal(textOf(blocks[0]), 'Why I picked this With <img src=x onerror=alert(1)>, who is in a film you liked.');
  assert.ok(!('innerHTML' in blocks[0]));
});

const response = () => ({ schema_version: 1, prediction_id: 'p', media_id: 'movie:7', model_version: 'v', status: 'experimental', horizon: 'd7',
  as_of: '2019-05-01T00:00:00Z', currency: 'USD', budget: { amount: 4e7, known_as_of: null, source_id: 'tmdb' },
  domestic_gross: { point: null, intervals: [] }, international_gross: { point: null, intervals: [] },
  worldwide_gross: { point: 1.2e8, intervals: [{ nominal_coverage: 0.5, lower: 6e7, upper: 2.4e8 }, { nominal_coverage: 0.8, lower: 2e7, upper: 6e8 }] },
  gross_to_budget_multiple: { point: 3, intervals: [{ nominal_coverage: 0.5, lower: 1.5, upper: 6 }, { nominal_coverage: 0.8, lower: 0.5, upper: 15 }] },
  source_notes: [], warnings: ['Experimental forecast.', 'Gross / budget is a ratio of two reported figures, not profit: marketing and cinema shares are not included.'] });

test('the forecast panel shows the range before the middle estimate, with every warning', () => {
  I.configure('en', tables);
  const m = F.panelModel(response());
  assert.equal(m.badge, 'Experimental');
  assert.deepEqual(m.sections.map(s => s.heading), ['Worldwide gross', 'Gross compared with budget']);
  assert.deepEqual(m.sections[0].rows.map(r => r.kind), ['range80', 'range50', 'middle']);
  assert.equal(m.sections[0].rows[0].text, 'Likely range (8 in 10): $20M to $600M');
  assert.equal(m.sections[0].rows[2].text, 'Middle estimate: $120M');
  assert.equal(m.sections[1].rows[0].text, 'Likely range (8 in 10): 0.5 times the budget to 15 times the budget');
  assert.equal(m.warnings.length, 2);
  assert.ok(m.notes.some(n => n.includes('worldwide totals only')));
  const all = JSON.stringify(m).toLowerCase().replace('not profit', '');
  for (const w of ['profit', 'roi', 'break even', 'guarantee', 'will earn']) assert.ok(!all.includes(w), w);
  assert.deepEqual(I.missing(), []);
});

test('no range means no number, and bad responses show nothing', () => {
  I.configure('en', tables);
  const r = response();
  r.worldwide_gross.intervals = [];
  r.gross_to_budget_multiple.intervals = [];
  assert.equal(F.panelModel(r), null);                 // a lone middle estimate is never shown
  assert.equal(F.panelModel(null), null);
  assert.equal(F.panelModel({ schema_version: 2 }), null);
  const r2 = response();
  r2.worldwide_gross.intervals[1].lower = null;
  assert.deepEqual(F.panelModel(r2).sections[0].rows.map(x => x.kind), ['range50', 'middle']);
});

test('the panel renders as text in Spanish too', () => {
  I.configure('es', tables);
  const box = F.renderPanel(fakeDoc(), response());
  const text = textOf(box);
  assert.ok(text.startsWith('Pronóstico de taquilla'));
  assert.ok(text.includes('Rango probable (8 de cada 10)'));
  assert.ok(text.includes('Experimental forecast.'));    // warnings are stored text and stay as stored
  assert.equal(box.attrs['aria-label'], 'Pronóstico de taquilla');
  assert.deepEqual(I.missing(), []);
  assert.equal(F.renderPanel(fakeDoc(), null), null);
});
