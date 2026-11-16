// v8.1 slice 8.1 (15 Nov 2026): the text layer. Words come from web/v8/locales/<lang>.json; dates, numbers and money
// come from the browser's Intl with the SAME locale, so a page never mixes two languages' formats.
// Nothing imports this yet except the v8.1 pieces (why.js, forecast_panel.js); the existing pages keep their English
// text until they are converted one at a time. Switch I18N (server side, off): while off the language is always "en".
//   t(key, params)            the sentence for a key; {name} placeholders filled; plural keys pick by params.count
//   missing key -> English; missing in English too -> the key itself, and it is remembered in missing()
// Titles, names and provider names are data: they are passed in as params and never translated here.
export const SUPPORTED = ['en', 'es'];
export const FALLBACK = 'en';
const state = { lang: FALLBACK, locale: 'en-US', tables: {}, missing: new Set() };

export function pickLanguage(wanted, supported = SUPPORTED) {
  for (const w of [].concat(wanted || [])) {
    const base = String(w || '').toLowerCase().split('-')[0];
    if (supported.includes(base)) return base;
  }
  return FALLBACK;
}

// tables: { en: {...}, es: {...} } already loaded (tests), or fetched by load() in the browser
export function configure(lang, tables) {
  state.tables = tables || state.tables;
  state.lang = SUPPORTED.includes(lang) && state.tables[lang] ? lang : FALLBACK;
  state.locale = state.tables[state.lang]?._meta?.locale || 'en-US';
  state.missing.clear();
  return state.lang;
}

export async function load(lang, base = '/v8/locales/') {
  const want = pickLanguage(lang);
  const tables = {};
  for (const l of new Set([FALLBACK, want])) {
    const r = await fetch(`${base}${l}.json`);
    if (r.ok) tables[l] = await r.json();
  }
  return configure(want, tables);
}

export const language = () => state.lang;
export const locale = () => state.locale;
export const missing = () => [...state.missing].sort();

function entry(key) {
  for (const l of [state.lang, FALLBACK]) {
    const v = state.tables[l]?.[key];
    if (v !== undefined && key !== '_meta') return [v, l];
  }
  return [undefined, null];
}

export function t(key, params = {}) {
  let [v, from] = entry(key);
  if (v === undefined) { state.missing.add(key); return key; }
  if (from !== state.lang) state.missing.add(key);
  if (typeof v === 'object') {                     // plural forms, chosen by the locale's own rules
    const loc = state.tables[from]?._meta?.locale || 'en-US';
    const form = new Intl.PluralRules(loc).select(Number(params.count));
    v = v[form] ?? v.other;
  }
  return String(v).replace(/\{(\w+)\}/g, (m, name) => (params[name] === undefined || params[name] === null ? m : String(params[name])));
}

export function number(n, opts = {}) { return new Intl.NumberFormat(state.locale, opts).format(n); }

// Money in the response's own currency; large amounts in the short form ("$120M" / "120 M US$").
export function money(n, currency = 'USD') {
  return new Intl.NumberFormat(state.locale, { style: 'currency', currency, notation: 'compact', maximumSignificantDigits: 3 }).format(n);
}

// An ISO day (YYYY-MM-DD) is a calendar day, not a moment: format it in UTC so no time zone moves it.
export function date(isoDay, opts = { year: 'numeric', month: 'short', day: 'numeric' }) {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(isoDay || ''));
  if (!m) return '';
  return new Intl.DateTimeFormat(state.locale, { ...opts, timeZone: 'UTC' }).format(new Date(Date.UTC(+m[1], +m[2] - 1, +m[3])));
}
