# v8 success criteria (written Mon 9 Nov 2026 ~14:00, before the work they judge)

Every criterion is checked by a script, not by eye, unless marked "review". A feature ships switched on only when all
of its criteria pass; otherwise it stays behind its switch and the failure is reported.

## A. Website (new UI at /v8 until it passes; then / ; old page kept at /classic)
| Area | Criterion | How it is checked |
|---|---|---|
| Layout | 0 px horizontal overflow on every page at 320, 375, 390, 430, 768, 1024, 1440 px | Playwright `scrollWidth - clientWidth` |
| Touch | every primary control (nav, actions, chips, send) at least 44 x 44 px at phone widths | Playwright bounding boxes |
| Accessibility | 0 serious / critical axe-core violations per page; dialogs trap focus and close on Esc; all actions reachable by keyboard with a visible focus ring; works at 200% text | axe-core + Playwright |
| Contrast | body text >= 4.5:1, large text and controls >= 3:1, light and dark themes | computed from the colour tokens |
| Errors | 0 console errors and 0 failed requests in every flow | Playwright |
| Performance (lab, simulated mid-range phone) | LCP <= 2.5 s, CLS <= 0.1, TBT <= 200 ms on home and detail; CSS + JS <= 150 KB gzipped (images excluded); posters lazy-loaded with fixed sizes | Lighthouse in the container against test mode |
| Clarity | at 375 px, above the fold: what Cinemack is (one line), the Ask box, the Pick tonight entry, the start of one poster rail | Playwright + review |
| Effort | from home to a recommendation in <= 3 taps (Pick tonight) | Playwright flow |
| Honesty | no invented data: every rail backed by an endpoint; empty rails show how to fill them, never placeholders posing as data; no control for an unbuilt feature | review + endpoint tests |
| No regressions | every existing API keeps its shape; smoke_site.py passes against the new page; chats, accounts, memory, filters, imports still work | tests + smoke_site.py |

## B. Features with functional tests ("does it work, does it leak")
| Feature | Criterion |
|---|---|
| Profiles and privacy | private by default; 0 leaks across owner / friend / non-friend / blocked for profile, watchlist, history, ratings, reviews |
| Friends | mutual accept only; decline, remove and block work; blocked users cannot request or see anything; per-item hide works |
| Watch history (Netflix CSV, Letterboxd, Trakt) | 0 silently dropped rows (each row matched or listed as an issue); re-import adds no duplicates; >= 98% correct matches on the fixture files |
| Watchlist and alerts | in-app only; at most one alert per title per change; no "leaving soon" without a dated source |
| Release calendar | only dated releases from TMDB / our tables; theatrical vs streaming vs TV labelled; never an invented showtime; "Find showtimes" is a labelled outbound link |
| Pick tonight | 100% of 30 fixed scenarios respect every hard filter (services, time, certificate / kids, excluded genres) |
| Decade / language preferences | a boost, never a filter: nothing disappears; preference changes ranking only |
| Persona rename (Cinemack) | tune, check and multi-turn each lose at most 1 |

## C. Answer path (unchanged method)
Each switch: rule written before its first result, tuning run, then a "v8 without X" arm in the sealed run scored by
the judge AND the judge-free checks (final_score.py, judge_audit.py).
