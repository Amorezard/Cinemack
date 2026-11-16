// v8.1 slice 6.2 (15 Nov 2026): exactly one "Why I picked this" per pick, written from a reason CODE.
// The code and its parameters come from web/recs.py (numbers, not model text). This file only chooses the sentence.
// Not connected to Pick Tonight yet: that happens when RECS_V2 is wired in, in a round with browser tests.
import { t } from './i18n.js';

export const REASONS = ['similar_to_liked', 'shares_director', 'shares_cast', 'shares_genre', 'shares_theme', 'popular_fits_limits'];

// reason = { code, params }; titles = { "movie:123": "Heat" } for the liked films the page already knows.
export function whyText(reason, titles = {}) {
  const code = REASONS.includes(reason?.code) ? reason.code : 'popular_fits_limits';
  const p = reason?.params || {};
  if (code === 'similar_to_liked') {
    const title = titles[p.liked_media_id];
    return title ? t('why.similar_to_liked', { title }) : t('why.similar_to_liked.untitled');
  }
  if (code === 'popular_fits_limits') return t('why.popular_fits_limits');
  const name = typeof p.name === 'string' && p.name.trim() ? p.name.trim() : null;
  return name ? t(`why.${code}`, { name }) : t('why.popular_fits_limits');
}

// One block per card, replacing any earlier one: a card can never show two.
export function whyBlock(doc, card, reason, titles = {}) {
  for (const old of card.querySelectorAll('.why1')) old.remove();
  const box = doc.createElement('p');
  box.className = 'why1';
  const label = doc.createElement('strong');
  label.textContent = t('why.title');
  box.append(label, doc.createTextNode(' ' + whyText(reason, titles)));     // text nodes only: names are never read as HTML
  card.append(box);
  return box;
}
