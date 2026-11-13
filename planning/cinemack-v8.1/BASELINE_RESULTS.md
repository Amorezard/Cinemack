# Cinemack v8.1 - baseline results (package 0)

Status words: PASS, FAIL, BLOCKED, UNRUN. Nothing is marked PASS unless it ran. Server: cs.merrimack.edu, commit 929bbbf, Thu 12 Nov 2026 19:35 EST (round 40, `audit_raw/todo_r40.sh` is the exact script).

| Kit test | What | Command | Result | Status |
|---|---|---|---|---|
| A01 | live site answers | `curl localhost:49151/v8/app.css`, `/api/v8/build`, `/classic` | 200 / 200 / 200, 3-15 ms | PASS (pages only; no chat sent) |
| A02 | mock site + browser checks | `MOVIE_BUFF_MOCK=1 PORT=8765 python web/server.py`, `node tools/accept_redesign.js` | - | UNRUN (no node_modules on the server; to run in a workspace) |
| A03 | `/classic` still served | as A01 | 200, 150,514 bytes | PASS |
| A04 | test copy on 49152 | `PORT=49152 bash start_site.sh` | - | UNRUN (shares the GPU and the live databases) |
| A05 | `nightly.sh --dry` | - | - | UNRUN (round 41) |
| A07 | database definitions | read-only `sqlite_master` + counts | 18 databases, `audit_raw/db_schema.txt` | PASS |
| A08 | switch defaults | lock files + site.env | `audit_raw/switches.txt` | PASS (recorded) |
| A09 | pending experiments | decision log | MEM_CARDS on (site), GUARD2 on (site; sealed gate FAILED by rule), CLAIM_REWRITE unrun, persona FAILED, context v3 FAILED on TV, embedder fine-tune unrun | recorded |
| A10 | sealed sets untouched | `md5sum -c results/sealed_v8_sets.md5` | 7 of 7 OK | PASS |
| - | unit tests | each `tests/test_*.py` with `python -m unittest` | 27 modules OK, 255 tests, 18 s; `test_v7_oct30_fixes` ran 0 tests (6 pytest-style tests not collected) | 27 PASS, 1 UNRUN |
| F01 | pf4 reproduction (reference 1.200) | `FACTS_DB=<copy> python pf5_model.py`, `python performance.py fit` | - | UNRUN (round 41, overnight) |
| C01 | 1/2/4/8 users latency | `tools/chat_bench.py live` | - | BLOCKED (GPU held by another job; needs the user's yes) |
| S15 | retrieval baseline | `stage_scores.py` on tuning sets | - | UNRUN (needs the GPU) |
| U17 | browser regression | `tools/accept_redesign.js` | last recorded 164/164 on 10 Nov (project notes, not re-run) | UNRUN |

Numbers quoted from earlier work and NOT re-measured here: pf4 test MAE 1.200; 80% range coverage 78.1%; sealed v7 68/77; first token ~38 s under GPU contention (10 Nov).
