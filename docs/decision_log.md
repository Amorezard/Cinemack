# Decision log
Newest first. Each decision names its evidence; rules were written before results unless marked.

| Date | Decision | Evidence | Rule written before results? |
|---|---|---|---|
| 8 Nov | pf5 data fetch order: Guardian test years (2016-19) first, then 2020-23, then 2015 back to 2000 | 5,000 calls/day free key; test coverage matters most | yes (plan 6 Nov) |
| 8 Nov | Guardian, not NYT, for pre-release articles | ~5,000/day vs ~3,900/day at our NYT pace; archive from 1999 | yes |
| 8 Nov | FRED CPIAUCNS (never revised) + UNRATE + UMCSENT at release month - 2 | Time-honest: published before release; small revisions noted | yes |
| 8 Nov | New workstreams A-H (master prompt) are post-v8 trains; not in the v8 blind score | Freeze is 11 Nov 18:00; nothing in them is specified or frozen | yes |
| 6 Nov | pf5 pass rule: before-release MAE beats pf4 + trailers on 2016-19, bootstrap 95% CI excludes 0; each group alone vs pf4 | Plan doc 6 Nov | yes |
| 6 Nov | pf4: news (NYT, mixed) not used per film; Letterboxd reported only | before+news -0.004 [-0.012, +0.004]; LB -0.026 but log_n leaks audience size | yes |
| 6 Nov | Wikipedia per-source news counted as UNTESTED, not "no" | 0/1,487 test coverage (I-004) | - |
| 5 Nov | v7 locked: TV, RECEPTION, SHORTS, MEM_EMBED, CLARIFY, PROMPT_ORDER, ALIAS_IMDB, PEOPLE, REC_CLEAN, MODERATION, FAST, SEMCACHE, RECENT on | Sealed v7: 68/77 vs 62/77; TV 13/14 vs 8/14 (p = 0.06 one-sided) | yes |
| 4 Nov | ISSUES, YT_COMMENTS, TV_REVIEWS, PERF off | All four failed their rules (feat run) | yes |
