# Cinemack v8.1 - baseline results (package 0)

Status words: PASS, FAIL, BLOCKED, UNRUN. Nothing is marked PASS unless it ran. Server: cs.merrimack.edu, commit 929bbbf, Thu 12 Nov 2026 19:35 EST (round 40, `audit_raw/todo_r40.sh` is the exact script).

| Kit test | What | Command | Result | Status |
|---|---|---|---|---|
| A01 | live site answers | `curl localhost:49151/v8/app.css`, `/api/v8/build`, `/classic` | 200 / 200 / 200, 3-15 ms | PASS (pages only; no chat sent) |
| A02 | mock site + browser checks | `MOVIE_BUFF_MOCK=1 PORT=8765 python web/server.py`, `node tools/accept_redesign.js` | - | UNRUN (no node_modules on the server; to run in a workspace) |
| A03 | `/classic` still served | as A01 | 200, 150,514 bytes | PASS |
| A04 | test copy on 49152 | `PORT=49152 bash start_site.sh` | - | UNRUN (shares the GPU and the live databases) |
| A05 | `nightly.sh --dry` (from the v8.1 worktree) | `bash nightly.sh --dry` | 13 steps listed, none run; fetch steps absent because the worktree has no API keys (expected) | PASS |
| A07 | database definitions | read-only `sqlite_master` + counts | 18 databases, `audit_raw/db_schema.txt` | PASS |
| A08 | switch defaults | lock files + site.env | `audit_raw/switches.txt` | PASS (recorded) |
| A09 | pending experiments | decision log | MEM_CARDS on (site), GUARD2 on (site; sealed gate FAILED by rule), CLAIM_REWRITE unrun, persona FAILED, context v3 FAILED on TV, embedder fine-tune unrun | recorded |
| A10 | sealed sets untouched | `md5sum -c results/sealed_v8_sets.md5` | 7 of 7 OK | PASS |
| - | unit tests | each `tests/test_*.py` with `python -m unittest` | live: 27 modules OK, 255 tests; `test_v7_oct30_fixes` ran 0 tests. v8.1 branch (e82c88e): 28 of 28 modules OK, the six tests now run (6/6) | PASS on the branch |
| F01 | pf4 reproduction (reference 1.200) | `python pf5_model.py`, `python performance.py fit` on the worktree's facts.db copy (12 Nov 19:47) | pf4 before+trailers test MAE **1.196** (rho +0.561, late 1.432) on **8,740** train / 1,487 test / 787 late films; committed: 1.200 on 8,742 / 1,487 / 787. All other arms moved by <= 0.006. pf5 rules unchanged (B still no, 15.9% Guardian coverage; conformal 80% range 78.8% test, 74.0% late) | **DEVIATION** by the rule written before the run (exact 1.200 and 8,742 required). Cause: two training films left the universe since 6 Nov (money repairs / nightly updates); pf5's own check (within 0.01) passes. Treated as reproduced within drift, not as an exact PASS. |
| C01 | 1/2/4/8 users latency | `tools/chat_bench.py live` | - | BLOCKED (GPU held by another job; needs the user's yes) |
| S15 | retrieval baseline | `stage_scores.py` on tuning sets | - | UNRUN (needs the GPU) |
| U17 | browser regression | `tools/accept_redesign.js` | last recorded 164/164 on 10 Nov (project notes, not re-run) | UNRUN |

Numbers quoted from earlier work and NOT re-measured here: pf4 test MAE 1.200; 80% range coverage 78.1%; sealed v7 68/77; first token ~38 s under GPU contention (10 Nov).

## Added 13 Nov (round 41 night 1, read from sync\v81_r41)

**F02 data inventory (slice 4.2, `results/v81_boxoffice_inventory.txt`)** - facts.db copy, model universe = budget >= $100k and gross > 0:

| | <= 1999 | 2000-2015 | 2016-2019 | 2020-2023 | 2024 on |
|---|---|---|---|---|---|
| films | 3,543 | 5,197 | 1,487 | 787 | 534 |
| US release date | 97% | 86% | 71% | 75% | 92% |
| certificate (any US) | 95% | 83% | 79% | 88% | 95% |
| trailer / video | 96% | 92% | 91% | 95% | 97% |
| awards row | 37% | 28% | 24% | 26% | 13% |
| Wikipedia pageviews | 0% | 4% | 98% | 89% | 96% |
| Guardian pre-release fetch | 0% | 27% | 100% | 100% | 100% |
| franchise (collection) | 23% | 21% | 22% | 25% | 24% |
Director, cast, writer, producer credits, companies, IMDb and Wikidata ids: 97-100% in every period.

Findings: (1) money figures: worldwide box office from the Wikipedia infobox (13,107 rows), Wikidata (2,210), TMDB revenue (13,870); budgets from TMDB, Wikipedia, Wikidata; NO domestic / international field anywhere (confirms audit F1). (2) Wikipedia pageviews exist almost only from 2016 (the API starts July 2015), so a pageview feature cannot be trained on <= 2015 films: it can only be tested by cross-validation inside 2016+, as pf3 did. (3) Guardian pre-release coverage of the training years is 27% (2000-2015) and 0% before 2000. (4) For 29% of 2016-2019 films the model's release date is the world date because no US date is stored. (5) Missing values differ by period (certificate 79-95%, awards 13-37%), so any feature built on them must carry a missing flag and its period shift must be reported.

## Added 15 Nov (rounds 42-44, read from sync\v81_r42 and sync\v81_r44)
- **1.0 identity audit (round 42):** 33,041 films, 8,043 shows; 1,934 films without an IMDb id, 3,399 without Wikidata; 519 films whose IMDb id is a TV title; 22 stale IMDb ids; 8 film-show pairs share an IMDb id; 1,072 TMDB numbers used by both a film and a show.
- **1.1 identity build (round 44, on the copy):** 41,084 identities (33,041 films + 8,043 shows), 125,484 outside ids. Rule R1-R5 PASS; the audit's 519 / 22 / 8 all match.
- **2.2 filter conformance (round 42):** plan = reference on all 30 cases over 33,041 films: PASS. Home genre chips and chat drop the 555 films with no genre data even in normal mode; chat also drops 14 films with no year (reported, not changed).
- **4.4 factor study (night 2):** dropping trailers +0.060 [0.046, 0.074], genre / film type +0.052, budget +0.017, cast +0.015, franchise +0.014 (all Holm p < .05); adding US rating +0.031 [0.020, 0.042], producer track record +0.022 [0.007, 0.039]; director, writer, timing, competition, source, companies: no clear value. Trailers and genre do not clearly help on 2020-2023. Associations only.
- **4.5 forecast (night 3, v81-pf4bt-cqr-1, worldwide only):** d90 MAE 1.218, 50% range 47.6%, 80% range 78.2% (median width x41.6): PASS. d7 MAE 1.202, 47.3%, 79.0%: PASS. Late 2020-2023 under-covers (d90 50% 43.5%, 80% 75.7%; bias +0.82). 2016-2019 is NOT blind.
- **5.2a MovieLens benchmark (night 3; MovieLens users, not Cinemack users):** uniform negatives v2 NDCG@10 0.653 vs current 0.627 (+0.026 [0.021, 0.031]), coverage 0.256 vs 0.198; popular negatives 0.442 vs 0.276 (+0.165 [0.153, 0.178]), coverage 0.255 vs 0.118. Candidate under both. Variety lower (0.748 vs 0.780; 0.670 vs 0.776). The two protocols picked different weights -> one set is fixed in round 45.
