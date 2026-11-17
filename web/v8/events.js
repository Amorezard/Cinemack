// v8.1 round 50: what the page tells the server about recommendations (web/events_api.py, switch REC_EVENTS).
// Anonymous by design: a random id per browser tab (sessionStorage), never the account or the browser id, and no
// text of any kind. What is sent: a title id, where it was (home, film page, ...), what happened (shown for the
// first time, opened, saved, rated), and its position in the row. The first 404 means the switch is off: the page
// then stops sending for the rest of the visit.
const PLACE = [[/^#\/film\//, 'detail'], [/^#\/pick/, 'pick_tonight'], [/^#\/watchlist/, 'watchlist'], [/^#\/calendar/, 'calendar'], [/^#\/ask/, 'chat'], [/^(#\/?|#\/home(\?.*)?)?$/, 'home']];
const MEDIA = /^#\/(film|show)\/(\d{1,10})(?:[?/]|$)/;
let off = false, queue = [], timer = 0;

const store = { get: (k) => { try { return sessionStorage.getItem(k); } catch { return null; } }, set: (k, v) => { try { sessionStorage.setItem(k, v); } catch {} } };
const rand = (n) => { const a = new Uint8Array(n); crypto.getRandomValues(a); return [...a].map(b => b.toString(16).padStart(2, '0')).join(''); };
export const session = () => store.get('cm.ev') || (() => { const s = rand(12); store.set('cm.ev', s); return s; })();
export const placement = (hash = location.hash) => (PLACE.find(([re]) => re.test(hash || '')) || [])[1] || null;
export function mediaOf(href) {
  const m = MEDIA.exec(href || '');
  return m ? `${m[1] === 'film' ? 'movie' : 'tv'}:${m[2]}` : null;
}

function headers() {
  let code = '';
  try { code = localStorage.getItem('mb.code') || ''; } catch {}
  return { 'Content-Type': 'application/json', 'X-Access-Code': code };
}
export function flush(keepalive = false) {
  clearTimeout(timer); timer = 0;
  if (off || !queue.length) { queue = []; return; }
  const events = queue.splice(0, 40);
  fetch('/api/v8/events', { method: 'POST', headers: headers(), keepalive, cache: 'no-store', body: JSON.stringify({ session: session(), events }) })
    .then(r => { if (r.status === 404) { off = true; queue = []; } })
    .catch(() => {});
  if (queue.length) timer = setTimeout(flush, 500);
}
export function track(media_id, type, extra = {}) {
  if (off || !media_id) return;
  const place = extra.placement || placement();
  if (!place) return;
  queue.push({ media_id, placement: place, type, position: Number.isInteger(extra.position) ? extra.position : null, exposure: extra.exposure || null });
  if (queue.length >= 40) flush(); else if (!timer) timer = setTimeout(flush, 4000);
}

// A row of posters: each card counts as "shown" once, when at least half of it has been on screen.
export function watchRow(track_) {
  if (off || typeof IntersectionObserver === 'undefined' || !track_) return;
  const exposure = rand(12), place = placement();
  if (!place) return;
  track_.dataset.exp = exposure;
  const io = new IntersectionObserver((entries) => {
    for (const e of entries) {
      if (!e.isIntersecting) continue;
      io.unobserve(e.target);
      const a = e.target.querySelector('a[href]');
      const pos = [...track_.children].indexOf(e.target);
      track(mediaOf(a?.getAttribute('href')), 'impression', { position: pos, exposure, placement: place });
    }
  }, { threshold: 0.5 });
  requestAnimationFrame(() => [...track_.children].forEach(c => io.observe(c)));
}

// Opening a title from a row; saving, rating and marking watched (core.js emits these).
document.addEventListener('click', (e) => {
  const a = e.target.closest?.('a[href^="#/film/"], a[href^="#/show/"]');
  if (!a) return;
  const row = a.closest('[data-exp]');
  const card = row ? [...row.children].find(c => c.contains(a)) : null;
  track(mediaOf(a.getAttribute('href')), 'click', row ? { exposure: row.dataset.exp, position: [...row.children].indexOf(card) } : {});
}, true);
const kind = { want: 'watchlist_add', liked: 'rating_positive', not: 'rating_negative' };
document.addEventListener('cm:choice', (e) => {
  const { id, choice, prev } = e.detail || {};
  if (!(Number(id) > 0)) return;
  const type = kind[choice] || (prev === 'want' && !choice ? 'watchlist_remove' : null);
  if (type) track(`movie:${Number(id)}`, type);
});
document.addEventListener('cm:watched', (e) => { if (Number(e.detail?.id) > 0) track(`movie:${Number(e.detail.id)}`, 'already_watched'); });
document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'hidden') flush(true); });
