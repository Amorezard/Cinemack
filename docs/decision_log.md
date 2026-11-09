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
| 8 Nov | 4a5: a show's IMDb primary/original title as a second name (SAME_NAME only) | sealed --db check: "Daredevil" missing = TMDB "Marvel's Daredevil" (named_show returned None); Highlander = "Highlander: The Series" (covered by 4a3); Uncle Buck series really absent; Il Postino found by a Wikidata alias | no - after the sealed check (those items were swapped out, so the sealed set does not test this) |
| 8 Nov | 4a4: TV_SEASONS season line only for a named show (not description searches or recommendations) | TV set (30) TV_SEASONS 0 vs 1: 24 -> 25/30, grounding 4.47 -> 4.57, but tv05 (The Office description) pass -> fail with "tighter and shorter at just 12 episodes" built from the new line; tv09, tv11 flips not about seasons (re-generation noise ~3/30) | no - written after seeing tv05 |
| 8 Nov | 4a3 (after tuning tn17): "X: The TV Series" also matched as "X" (SAME_NAME only); TV_SEASONS switch (episodes per season, default off, needs its own TV-set check before shipping); samename re-run with TV_SEASONS=1 in both arms (tag tune8_sn3) | tn17 on-arm: right show but no named match -> 3-show description search, film answer paragraph, no per-season data; the off-arm pass was "not in context" for the wrong title | no - written after seeing tn17 |
| 8 Nov | SAME_NAME + TV_SEASONS (named show only) proposed for the v8 lock | samename tuning 20/20 both arms, routing 17 -> 20/20; TV set 24 -> 25/30 (tv11 = noise), grounding 4.47 -> 4.53 | rule yes (TV_SEASONS, before on arm); fixes 4a2-4a5 not blind |
| 9 Nov | 4b IDENT_HONEST rule (written ~11:15, before any on-arm run): R1 invented answers presenting a film <= 2/20 (off 5: ti03 ti04 ti06 ti08 ti11); R2 real descriptions from tune+check named first on >= off - 1; R3 TV identify the same; scored by ident_score.py, not the judge | tune8_inv_v7: judge passed 20/20 (honest 5) though 5 answers present a wrong film -> judge cannot score 4b; retrieval returned 3 films for 20/20 invented (scores up to 0.99, no usable cut-off) | not blind (line written after seeing the baseline answers) |
