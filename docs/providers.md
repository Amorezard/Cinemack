# Provider access matrix (8 Nov 2026)
VERIFIED = checked in the provider's docs or our own working code, with the date. UNKNOWN = not checked yet: nothing is promised.

| Provider | Data we use | Method | Limits / terms | Status |
|---|---|---|---|---|
| Guardian Content API | film articles, reviews | free developer key | ~5,000 calls/day, 12/s (guardianapi package docs); non-commercial; own access page not opened from here | VERIFIED in our code (25,028 reviews); limits to re-check |
| NYT APIs | front pages, reviews | key | our pace 6.2 s/call, 3,900 calls/day; 2023 terms bar AI training use | VERIFIED in our code |
| FRED | CPIAUCNS, UNRATE, UMCSENT | free key, series/observations | about 120 requests/min (fredr notes) | key NOT YET created |
| TMDB | metadata, release dates, watch providers | key | watch providers come from JustWatch: attribution required, no full deep links (to re-check) | VERIFIED in our code |
| IMDb datasets | titles, ratings, crew | non-commercial files | "Data courtesy of IMDb."; no box office | VERIFIED (21 Oct reply) |
| Letterboxd | user ratings / history | user's own export (built); API by approval only | scraping not allowed | export VERIFIED; API UNKNOWN (request) |
| Kaggle Letterboxd set | ratings ~2021 | downloaded file | licence to re-check before showing on the site | UNKNOWN licence |
| Netflix | viewing history | user-downloaded CSV | no API; not continuous sync | to verify help.netflix.com/en/node/101917 |
| Trakt | history, ratings, watchlist | user OAuth | terms and limits to read | UNKNOWN |
| Simkl | history | OAuth / import | to read | UNKNOWN |
| Fandango / Fabric Origin | showtimes | approved, paid feed | not free | BLOCKED (approval + cost) |
| YouTube Data API | trailers, comments | key | refresh/delete stored data within 30 days; captions owner-only | VERIFIED (23 Oct) |
