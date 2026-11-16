// v8.1 slice 4.6 (15 Nov 2026): the box-office forecast panel. HIDDEN: nothing loads this file until FORECAST_PANEL
// is on and the film page is told to ask for it (a later, approved round).
// It shows what /api/v8/forecast/<media id> returned and nothing else: the RANGE first, then the middle estimate,
// the "experimental" label and every warning. It never computes a new number and never says profit.
import { t, money, number } from './i18n.js';

// The lines of the panel as plain data (so the wording and the order can be tested without a page).
export function panelModel(r) {
  if (!r || r.schema_version !== 1) return null;
  const cur = r.currency || 'USD';
  const range = (est, cov) => (est?.intervals || []).find(i => i.nominal_coverage === cov && i.lower != null && i.upper != null);
  const block = (est, fmt) => {
    const rows = [];
    const r80 = range(est, 0.8), r50 = range(est, 0.5);
    if (r80) rows.push({ kind: 'range80', text: t('forecast.range80', { low: fmt(r80.lower), high: fmt(r80.upper) }) });
    if (r50) rows.push({ kind: 'range50', text: t('forecast.range50', { low: fmt(r50.lower), high: fmt(r50.upper) }) });
    if (est?.point != null && rows.length) rows.push({ kind: 'middle', text: t('forecast.middle', { value: fmt(est.point) }) });
    return rows;                                    // no range -> no number at all: a lone point would look certain
  };
  const m = { title: t('forecast.title'), badge: r.status === 'experimental' ? t('forecast.experimental') : null,
              horizon: r.horizon === 'd90' || r.horizon === 'd7' ? t(`forecast.horizon.${r.horizon}`) : null,
              sections: [], notes: [], warnings: [...(r.warnings || [])] };
  const ww = block(r.worldwide_gross, v => money(v, cur));
  if (ww.length) m.sections.push({ heading: t('forecast.worldwide'), rows: ww });
  const mult = block(r.gross_to_budget_multiple, v => t('forecast.times', { value: number(v, { maximumSignificantDigits: 2 }) }));
  if (mult.length) m.sections.push({ heading: t('forecast.multiple'), rows: mult });
  if (!m.sections.length) return null;
  if (r.budget?.amount != null) m.notes.push(t('forecast.budget', { value: money(r.budget.amount, cur) }));
  if (r.domestic_gross?.point == null && r.international_gross?.point == null) m.notes.push(t('forecast.territories'));
  return m;
}

export function renderPanel(doc, r) {
  const m = panelModel(r);
  if (!m) return null;
  const el = (tag, cls, text) => { const e = doc.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };
  const box = el('section', 'fcast');
  box.setAttribute('aria-label', m.title);
  const head = el('h3', null, m.title);
  if (m.badge) head.append(' ', el('span', 'fcast-badge', m.badge));
  box.append(head);
  if (m.horizon) box.append(el('p', 'fcast-h', m.horizon));
  for (const s of m.sections) {
    box.append(el('h4', null, s.heading));
    const ul = el('ul');
    for (const row of s.rows) ul.append(el('li', `fcast-${row.kind}`, row.text));
    box.append(ul);
  }
  for (const n of m.notes) box.append(el('p', 'fcast-note', n));
  if (m.warnings.length) {
    const ul = el('ul', 'fcast-warn');
    for (const w of m.warnings) ul.append(el('li', null, w));
    box.append(ul);
  }
  return box;
}
