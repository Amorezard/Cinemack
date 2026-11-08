# Cinemack roadmap (8 Nov 2026)
Source plans: "Cinemack v8: Plan to Thursday 12 November" (6 Nov) and the master implementation prompt (8 Nov).
Estimates are planning estimates, not promises. Status: DONE / IN PROGRESS / NOT STARTED / BLOCKED / UNKNOWN (not yet confirmed on the server).

## v8 (freeze Wed 11 Nov 18:00, scorecard Thu 12 Nov)
| Item | Tier | Status 8 Nov | Evidence / next step |
|---|---|---|---|
| Judge hand check (30, agreement >= 80%, kappa >= 0.6) | Must | UNKNOWN | results/judge_check_report.txt not reported yet |
| Video (50) + outlet checks | Must | UNKNOWN | results/video_match_check.csv, critic_outlet_check.csv |
| Wikipedia headlines 2000-2026 + pf4 refit | Must | IN PROGRESS (started Fri 09:00) | I-004; `run_v8_day.sh morning` shows coverage |
| Letterboxd rating-only arm | Must | NOT STARTED | performance.py LB without lb_log_n (Mon, with pf5) |
| NEWS decision | Must | NOT STARTED | I-006 |
| pf5 A people in the news | Must | CODE READY | pf5_data.py people (after the Wikipedia re-fetch) |
| pf5 B Guardian pre-release articles | Must | CODE READY | pf5_data.py press, ~2 days of quota |
| pf5 C genre momentum, franchise fatigue, FRED | Must | PARTLY READY | pf5_data.py macro (needs FRED_API_KEY); momentum in performance.py Mon |
| pf5 D track records, awards before release | Must | NOT STARTED | performance.py prior() Mon |
| pf5 fit, contributions, 10/90 ranges | Must | NOT STARTED | Tue; rule in docs/decision_log.md |
| 4a same-name show/film (>= 18/20) | Must | NOT STARTED | Mon; I-001 |
| 4b "no match" for invented films (>= 15/20, lose <= 1) | Must | NOT STARTED | Mon; I-002 |
| 4c claim check rewrite (unsupported -0.10, <= 3 s) | Should | NOT STARTED | Tue; first to cut |
| 3a memory cards, 3b long-chat summaries, 3c preference chips | Should | NOT STARTED | Tue |
| Decade/language boosts, Netflix CSV import, persona flag | Should | NOT STARTED | Tue |
| 3d context builder v3, harder memory test, Trakt, friends | Could | MOVED to post-v8 | Not enough time after Saturday slipped |
| v8 sealed set (~110) + tuning sets (same-name 20, invented 20, phrase table 40) | Must | NOT STARTED | Sun (today), before any v8 tuning |
| Freeze, tag v8.0-lock, sealed run, ablations, judge check on 30 v8 answers | Must | NOT STARTED | Wed 18:00 / Wed night |
| docs/v8_status_nov12.md scorecard | Must | NOT STARTED | Thu |

### Revised schedule (Saturday slipped)
- **Sun 8:** morning check; start Guardian fetch (day 1); people-in-news once the Wikipedia fetch is done; FRED key + macro; judge hand check (you); v8 sealed set + tuning sets written (Claude), frozen with md5s before any v8 code is tuned.
- **Mon 9:** 4a + 4b code, unit tests, tuning runs (GPU, night); Guardian day 2; pf5 features A-D + Letterboxd rating-only in performance.py.
- **Tue 10:** memory 3a-3c, Netflix import, decade/language, persona flag, 4c if time; night: tuning runs + pf5 fit.
- **Wed 11:** repairs from Tuesday night only; 18:00 freeze + tag; sealed run overnight (site stopped, one GPU job).
- **Thu 12:** scorecard, write-up, restart site with v8_lock.env.

## After v8: separate release trains (from Thu 12 Nov)
Order: security and data foundations first, ML experiments in parallel on GPU nights. None changes the v8 blind score.

| Order | Workstream | Depends on | First slice | Gate (summary) | Estimate |
|---|---|---|---|---|---|
| 1 | A Prompt-injection guard v2 | - | Threat model + structured evidence objects + dev/sealed attack sets by category | 0 critical successes on sealed high-severity; benign false-block rate, answer retention, p50/p95 reported | 1 week |
| 2 | D Canonical media + watch history imports | - | `media` (movie/series/season/episode), `watch_events`, `import_jobs`, `import_issues`; Netflix CSV + Letterboxd re-use; provider matrix | 0 silently dropped rows; >= 98% verified matches on fixtures; re-import idempotent | 1-1.5 weeks |
| 3 | E Profiles, friends, reviews, privacy | D | Visibility enum + row-level checks + friend requests; private by default | 0 leaks in owner/friend/non-friend/blocked matrix | 1.5 weeks |
| 4 | F Want to Watch + alerts | D (E for social) | Watchlist + availability snapshots + in-app alerts; email only in sandbox | 0 duplicate or opted-out sends; no "leaving soon" without a dated source | 1.5 weeks |
| 5 | G Release calendar + showtimes fallback | F | TMDB upcoming/release dates + labelled outbound "find showtimes" link | No fabricated times; region/date fixtures pass | 1 week |
| 6 | H Pick Tonight (solo first) | D, F | 3-5 tap flow over legal services + hard filters | 100% hard-filter adherence on 30 gold scenarios | 1 week |
| parallel | B Embedder fine-tune | - | Baseline measurement + hard negatives + shadow index | Predefined Recall@5/10, MRR gains, latency budget | GPU nights, 2 weeks |
| later | C Adapter retrain | B, prompt fixes | Decide whether retraining is justified at all | Wins a new sealed set and fits the hardware | rented GPU |
