# Cinemack v8 refinement pass, 9 Nov 2026 (evening) — issue tracker

Request: the "UI/UX Refinement, Performance Fixes & Feature Improvements" brief (9 Nov 19:19) plus the chat screenshot.
All front-end work is in `web/v8/` (app.js, app.css, index.html); server work in `web/discovery.py`, `web/v8routes.py`,
`web/user_prefs.py`. Tests: `tests/test_v8_web.py` (26), `tools/check.js` (49 page x width), `tools/accept.js`
(scenarios A-H, 71 checks), `tools/flows.js` (end-to-end, two accounts + guest), `tools/chat_bench.py` (latency).
Local testing used mock mode and a 24-film fixture; the server re-runs the benchmark against real data.

| # | Issue | Where | Root cause | Fix | Test | Result |
|---|---|---|---|---|---|---|
| 1 | Chat buttons overlap the conversation | app.css `.composer` | Filters / Saved chats / New chat sat in the sticky composer, which floated over the last message with a fade | Chat is a fixed-height column: toolbar row (title, Filters, Chats, New chat), active-filter chips, a scrolling message area, composer row. Nothing is positioned over anything | accept.js E at 1440x900, 1280x720, 1024x600, 390x844, 360x640: no overlap, all controls on screen, page itself never scrolls | pass |
| 2 | Poster top cut off on hover | app.css `.rail` | `.rail` scrolls sideways (`overflow-x:auto`), which makes the browser clip it vertically too; the 3-4 px hover lift and its 1.5 px ring went past the top edge | 8 px of room above posters inside the rail (padding-top, cancelled by margin) | accept.js B: poster top stays inside the row, transform animates, no neighbour moves | pass |
| 3 | Genre chips: no label, no highlight, jump into chat | app.js `pageHome` | Chips were links to `#/discover?genre=` | "Explore by genre" section; chips are toggle buttons (aria-pressed, blue fill + check); several allowed; Clear; Home rows refilter in place via `/api/v8/home?genres=`; two genre rows added (top-rated, recent) | accept.js A + unit test: no film without the genre in any filtered row | pass |
| 4 | Home and chat filters out of sync | app.js | Chat kept its own filter object | One store, `S.filters` saved as `cm.filters`; Home chips, chat Filters sheet and chat filter chips all read and write it; the chat sends `genres` (server: any of these) | accept.js A: Thriller + Mystery picked on Home are active in chat and sent with the request; removing one in chat un-presses it on Home | pass |
| 5 | Raw "Sources: [x](url)" in answers | server appends it as text; app.js `md()` | The page printed the markdown | `splitSources()` takes the line off the answer and shows a compact "Sources (n)" disclosure with link names, host and the CC BY-SA / TMDB notice; only http(s) links are rendered, with rel=noopener | accept.js D | pass |
| 6 | Too much space for a short answer | app.css, app.js `recCard` | Full poster card (300 px) per film, wide margins, long answers | Compact film card (60 px poster, facts beside, Want to watch / Where to watch / Showtimes); tighter message spacing; per-chat `brief` filter asks the model for about 80 words unless the person's own length setting says otherwise | accept.js D: question + answer + card + actions fit at 1440x900 and 1280x720 without scrolling | pass |
| 7 | Continue chat on Home | app.js | Was added at 19:00 | Removed from Home; Discover shows "Pick up where you left off" (3 recent chats) when the chat is empty; the Chats panel is unchanged | flows.js, accept.js C | pass |
| 8 | In theatres carousel | app.js `heroCarousel` | Was a cross-fade with no arrows | Sideways slide every 5 s, arrows, dots, swipe, arrow keys; pauses on hover, focus, touch, open dialogs and hidden tab; manual moves restart the timer; still under reduced motion | accept.js C | pass |
| 9 | Arrows on all poster rows | app.js `railBox` | Rows only scrolled | Arrow buttons page each row; hidden when the row fits; disabled at the ends; touch scrolling still works | accept.js C | pass |
| 10 | Calendar was a text list | app.js `pageCalendar` | | Week view (Sun-Sat), previous/next/This week, kind filter, today marked; desktop 7 columns of poster cards, phone day strip + day list with swipe; type badges (theatres, re-release, streaming, TV season); films open their page, shows can be followed | accept.js F | pass |
| 11 | No Showtimes | discovery.theatres, app.js | | "Showtimes" only for films on TMDB's US now-playing list (cards, carousel, film page under Where to watch). Opens a sheet with film-specific searches (Google, Fandango, AMC, Regal, Showcase) near a ZIP the person types (kept in this browser; no GPS) | unit test + accept.js G | pass |
| 12 | TV on the watchlist | discovery.tv_* | | Follow shows (search, calendar); Watchlist "TV shows you follow": seasons out, next dated season, episodes in your history, badges; one alert per dated season / season start, opt-out per show; undated seasons never alert | unit test + accept.js H | pass, see limits |
| 13 | Header | app.css | | 56 px tall, even spacing, avatar 34 px with object-fit, Chats button, compact at 1024-1180 | check.js | pass |
| 14 | Chat feels slow | see below | | measured first | chat_bench.py | in progress |

## Chat latency (round 14a baseline, live site, 13 questions)
Median first answer word 34 s, done 80 s (short recommendations: 35 s / 82 s). The site log from earlier today showed
8 s / 15 s. Cause found in round 14b: another user's job (`vasp_std`, 16 GB, 99% GPU) has held the shared A10 since
about 14:15, so Ollama loaded movie-buff 79% on the CPU: generation fell from ~115 to ~14 characters a second. That is
outside this project's control; it is reported, nothing was touched.

Inside our own pipeline: the planner step (a movie-buff call) takes a median 6.3 s before search starts (p95 17.6 s);
search itself is 0.2 s; answers were 100-400 words. Changes so far: answers default to short in the v8 chat (fewer
tokens to generate), film cards and sources appear as soon as search finishes instead of after the answer, a live
seconds counter and a stall message after 90 s. Before/after numbers must be taken when the GPU is free, otherwise
they measure the other job, not the change.

## Limits (not done, and why)
- Episode-level dates and finales: the database has season premiere dates only (`tv_seasons.air_date`).
- Real showtimes or booking: no authorised theatre API is connected; links are searches and say so.
- "Leaving streaming": no source publishes these dates.
- Push/email notifications: alerts appear in the bell and Watchlist only.
