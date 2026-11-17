# Roadmap: what comes after 19 Nov 2026

Written 17 Nov 2026. A plan, not a result: nothing here has been measured. Every item keeps the project's rules
(a new off switch, the pass rule written before the run, private by default, no outside call without the owner's yes).

## What other services do (checked 16-17 Nov 2026)

| Service | What it does | Cinemack today |
| --- | --- | --- |
| Netflix | Testing "say what you feel like" by voice or text; mixes history with what is trending now; a language model as ranker | Chat and plain-words filters: yes. Reacting within one visit: no |
| Prime Video with Alexa+ | Voice requests by mood, genre, actor; follow-ups narrow the answer ("something newer"); jump to a described scene | Typed: yes. Voice: no. Narrowing: in chat only |
| Google TV with Gemini | One pick that blends two people's tastes; finds a film from a half-remembered plot | Half-remembered plot: yes. Two people: no |
| Letterboxd | Follow members, activity stream, paid watchlist alerts. API closed to this kind of project; public RSS per profile | Friends by code with privacy: yes. Stream: no. Import: export file |
| Trakt | Streaming services connected once, history arrives by itself; open API | Connect by code: yes. Sync only when the button is pressed |
| Plex Discover Together | Friends feed, "which friends watched this", send a title with a note; private until shared | Friend profiles: yes. The rest: no |
| JustWatch, Simkl | Alerts when a saved title arrives or a release nears; email or phone | In-app alerts: yes. Email digest built, sandbox only |
| Swipe-to-match apps | A room, a link, everyone taps yes or no, first film all want wins | No |

Three things Cinemack has that the list above does not: a box-office forecast with an honest range, the reasons
behind a pick, and answers with a source to open.

## The ideas, ranked (value to a person / new work needed)

| # | Idea | Switch | State | Needs the owner's yes for |
| --- | --- | --- | --- | --- |
| 1 | Follow a person: their films, what is coming, stored Guardian pieces | `FOLLOW_PEOPLE` | Built: API (round 49), film-page buttons and Home row (round 50) | Nothing (stored data only) |
| 2 | Trakt history refreshes nightly | `TRAKT_AUTOSYNC` | Tool built (round 49), dry run by default | Adding it to the nightly job |
| 3 | Suggestions that react within a visit | `REC_EVENTS`, later `RECS_SESSION` | Event log wired to the pages (round 50), anonymous per tab; owner said yes on 17 Nov | Nothing more for the log |
| 4 | Email that really sends, with a followed-people block | `EMAIL_SANDBOX=0` | Digest, verification, unsubscribe built | Real email, a provider |
| 5 | Friends feed; friends' ratings on a film page | new | Not started | Nothing |
| 6 | Pick together: a room and yes/no taps, or a two-person blend | `PICK_TOGETHER` | Not started | Nothing |
| 7 | Ask out loud; "something newer" narrows the last answer | new | Not started | Browser speech sends audio to the browser maker |
| 8 | "How much will you like it": a predicted rating with a range | new | Not started | Nothing (CPU, MovieLens) |
| 9 | Taste match with a friend | new | Not started | Nothing |
| 10 | Your year in film | new | Not started | Nothing |
| 11 | Opening-week forecast | new | Not started | The worldwide-only decision would need revisiting |
| 12 | Spoiler-safe "catch me up" for a show | new | Not started | GPU time |
| 13 | Find a scene from a description | new | Not started | More transcript data |

Not planned: scraping Letterboxd (its terms forbid it), reading streaming-service histories directly (Trakt does this
for the person, with their consent).

## Why idea 3 is also the ranking fix

Three ranking candidates failed on MovieLens (see [RECOMMENDATIONS.md](RECOMMENDATIONS.md)). MovieLens cannot say what
Cinemack's users open, save and skip. With the event log on for two weeks there is a test that can: saves per 100
picks shown, new order against current order on alternating days, the rule fixed before any event is read.

## A known trap in idea 2

The Sync button copies ratings and the watchlist by writing over what is there (a rating arrives with empty review
text; a watchlist film is set back to "want"). Pressed by the person, that is their choice. Run nightly it would undo
edits made in Cinemack, so the nightly tool copies watch history only, which is safe to repeat. Ratings and watchlist
need an "only add what is missing" rule first.

## Order

1. Week of 23 Nov: event log on, real email, nightly Trakt history, the Follow page, friends feed.
2. December: pick together, taste match, the rating prediction, reacting suggestions tested on collected events, voice.
