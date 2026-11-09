# Cinemack v8 website: results against the success criteria (Mon 9 Nov 2026, ~15:30)

The new page is at `/v8`. The classic page stays at `/` until the user approves the switch; after that, `/` serves v8
and the old page moves to `/classic`. Nothing in the answer pipeline changed.

## How it was tested
- In the work container, mock mode (`MOVIE_BUFF_MOCK=1`, fake model), against a 24-film fixture database
  (`tools/make_demo.py`; layout testing only, never shipped). TMDB images and Google Fonts are not reachable from the
  container, so posters were replaced with flat colour tiles and the system font was used. The server's own smoke
  check (round 9) repeats the endpoint checks against the real data.
- `tools/check.js`: 7 pages x 7 widths (320, 375, 390, 430, 768, 1024, 1440): overflow, tap targets, axe-core
  (WCAG 2.0 A/AA, 2.1 AA, 2.2 AA rules), console errors, layout shift.
- `tools/flows.js`: 20 end-to-end steps with two accounts and a guest.
- `tools/perf.js`: lab timing with 4x CPU slowdown, 1.6 Mbps, 150 ms latency.

## Results
| Area | Result | Pass |
|---|---|---|
| Layout | 0 px overflow on 49 / 49 page-width pairs | yes |
| Touch | every control >= 44 x 44 px below 1024 px wide; >= 24 px on desktop (WCAG 2.5.8). Inline links inside sentences are exempt | yes |
| Accessibility | 0 axe-core violations of any severity on 49 / 49; Esc closes dialogs; first Tab reaches "Skip to content"; focus moves to the page on navigation | yes |
| Contrast | lowest pair 4.94:1 (faint text on raised surface, both themes) after raising --faint; body text 15.6:1 dark / 16.4:1 light | yes |
| Errors | 0 console errors, 0 failed requests in all flows | yes |
| Performance | home LCP 0.48 s, detail 0.73 s; CLS 0.018 / 0; TBT 0 / 14 ms; JS 18.0 KB + CSS 4.8 KB gzipped | yes (lab, local server) |
| Effort | Pick tonight: home, Pick tonight tab, Show my picks = 2 taps with defaults, 4 with mood and time | yes |
| Honesty | every rail is an endpoint; empty rails say how to fill them; no control for an unbuilt feature (no "leaving soon", no showtimes, no public profiles) | review |

Flow steps that passed (20 / 20): sign up; rating dialog opens and closes on Esc; Want to watch shows its saved state
and the film appears on the Watchlist page; rating saved; Netflix CSV import reports 5 rows read, 4 added, 1 not matched
(nothing dropped silently); episodes fold into one show line; friend request by code, accept; friend sees nothing while
everything is private; after sharing only the watchlist the friend sees the watchlist and not the history; chat answer
streams with feedback buttons; Home then shows "Continue exploring"; Pick tonight sends strict filters (Comedy, 90 min);
With kids caps the rating at PG; a guest gets a sign-in prompt on Friends; deleting an account removes it from the
friend's list; no console errors for either account or the guest.

## Problems found by the checks and fixed
1. `pick.json` returned 404: the asset route did not serve `.json`.
2. Pick tonight's choice chips used both `role=radio` and `aria-pressed` (critical axe error); now `aria-checked` only.
3. Small buttons were 36 px tall on phones; composer bar overflowed by 14 px at 375 px (icon-only "Saved chats" and
   "New chat" on narrow phones, names kept for screen readers).
4. The alerts badge showed "0" because `.badge{display:grid}` beat the `hidden` attribute.
5. Pressing Show my picks again while an answer was streaming was silently ignored; it now stops the first answer.
6. The page focused `<main>` on first load, which skipped the skip link.
7. "Coming soon" lost TMDB's order (SQL `in (...)` order); now kept.
8. Card details wrapped onto a clipped third line on phones; runtime shortened to "2h 47m" and the line truncates.
9. "Want to watch" looked the same saved or not; it now reads "On your watchlist" when saved.

## Not done yet / limits
- Lighthouse itself was not run (not installed); the lab numbers above come from the browser's own performance entries.
  Real numbers depend on TMDB image speed and the server. The work container cannot reach cs.merrimack.edu, so the
  live check is round 9's smoke test on the server plus opening /v8 on a phone.
- "Because you liked" rails need MovieLens neighbours in movies.db; they appear only for people with liked films.
- Answer length, spoilers and memory settings are still on the classic page (linked from Settings).
- Screenshots: `docs/audit/` (classic page, before) and `docs/audit/after/` (v8, fixture data, colour tiles for posters).
