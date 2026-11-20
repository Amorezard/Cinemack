// v8.1 round 55: Pick Tonight notes. The functions are cut out of web/v8/pick.js (it imports the browser-only
// core.js), so this runs in plain Node: node tests/js/test_v81_r55_pick.mjs
import { readFileSync } from 'node:fs';
import assert from 'node:assert/strict';
const src = readFileSync(new URL('../../web/v8/pick.js', import.meta.url), 'utf8');
const a = src.indexOf('const plain = (s) =>'), b = src.indexOf('// ---------------------------------------------------------------- results');
assert.ok(a > 0 && b > a, 'note functions found in pick.js');
const body = src.slice(a, b).replace(/^export /gm, '');
const splitSources = (t) => ({ text: t });
const M = new Function('splitSources', body + '\nreturn { sentencesOf, noteFor, factLine };')(splitSources);
let n = 0; const t = (name, fn) => { fn(); n++; console.log('ok', name); };

// The answer seen on 20 Nov: one paragraph, three films, only one of them on the page.
const ans = "Three Colors: Blue (1993) on Max — Kieślowski's quiet study of grief and emotional liberty after a devastating loss. " +
  "Dogville (2003) is Lars von Trier's stark, stage-like parable about how kindness erodes under pressure, starring Nicole Kidman. " +
  "Se7en (1995) on Paramount+ is the obvious outlier — it's well known, but per TMDB it still holds an 8.4 rating.";
t('note names only its own film', () => {
  const s = M.noteFor({ title: 'Three Colors: Blue' }, ans, ['Dogville', 'Se7en']);
  assert.ok(s.startsWith('Three Colors: Blue (1993)'));
  assert.ok(!/Dogville|Se7en/.test(s));
});
t('a film the answer never discusses gets no note', () => {
  assert.equal(M.noteFor({ title: 'Three Colors: Red' }, ans, ['Dogville']), null);
});
t('a sentence naming two films is dropped', () => {
  const x = 'Blue (1993) pairs well with Red (1994) as a double bill. Blue is the quieter one.';
  assert.equal(M.noteFor({ title: 'Blue' }, x, []), 'Blue is the quieter one.');
});
t('a sentence naming a film found but not shown is dropped', () => {
  const x = 'Heat is tense. Heat beats Ronin for set pieces.';
  assert.equal(M.noteFor({ title: 'Heat' }, x, ['Ronin']), 'Heat is tense.');
});
t('list answers: one item per film, markup removed', () => {
  const x = '1. **Heat** (1995): a slick crime epic.\n2. **Ronin** (1998): car chases.';
  assert.equal(M.noteFor({ title: 'Ronin' }, x, ['Heat']), 'Ronin (1998): car chases.');
});
t('at most two sentences', () => {
  const x = 'Up is lovely. Up is short. Up is funny.';
  assert.equal(M.noteFor({ title: 'Up' }, x, []), 'Up is lovely. Up is short.');
});
t('accents and case do not matter', () => {
  assert.ok(M.noteFor({ title: 'Amélie' }, 'AMELIE is a delight.', []));
});
t('fact line from the known reasons', () => {
  assert.equal(M.factLine(['Drama', 'Runs 1h 38m', 'On your Max']), 'Drama; runs 1h 38m; on your Max.');
  assert.equal(M.factLine([]), null);
});
t('the cards carry no "Why this pick" block', () => {
  assert.ok(!src.includes("'Why this pick'"));
  assert.ok(src.includes("class: 'pick-notes'"));
});
console.log(`${n} pick tests passed`);
