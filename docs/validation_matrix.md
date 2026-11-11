# Validation matrix (v8)
| Requirement | Code | Tests | Pass rule (written before results) | Status |
|---|---|---|---|---|
| pf5 A people in the news | pf5_data.py people; performance.py (Mon) | tests/test_pf5_data.py People | group alone beats pf4 + trailers, 95% CI excludes 0 | code + unit tests |
| pf5 B Guardian pre-release | pf5_data.py press | tests/test_pf5_data.py Press (window, release-day cut, cap, order) | same | code + unit tests |
| pf5 C FRED + momentum | pf5_data.py macro; performance.py (Mon) | tests/test_pf5_data.py Macro | same; values from release month - 2 only | code + unit tests |
| pf5 D track records | performance.py (Mon) | to write | same | not started |
| pf5 overall | performance.py pf5 | synthetic end-to-end fit | pf5 before beats pf4 before + trailers on 2016-19, CI excludes 0 | not started |
| 10/90 ranges | performance.py | coverage check | report coverage honestly (target ~80%) | not started |
| 4a same-name | tv.py | same-name set (20) | >= 18/20; tune, check, multi-turn lose <= 1 | not started |
| 4b invented films | retrieve.py / confidence.py | invented set (20) | >= 15/20 "no match"; tune and check lose <= 1 | not started |
| 4c claim check | chat.py + claimcheck.py | reception/analysis sets | unsupported -0.10, passes not lower, <= 3 s added | not started |
| 3a memory cards | web/memory.py | memory_eval.py card arm | right refer-backs not lower, false recalls not higher | not started |
| 3b long-chat summaries | web/pipeline.py | 6 twelve-turn chats | >= 5/6; multi-turn unchanged | not started |
| 3c preference chips | web/user_prefs.py + index.html | 40-sentence phrase table | all real limits caught, <= 1 false trigger | not started |
| Persona rename | chat.py prompts (switch) | tune, check, multi-turn | lose <= 1 each | not started |
| Netflix import | web/imports.py | fixtures: films, episodes, profiles, bad rows | 0 silently dropped rows, unmatched shown | not started |

## Round 27 (11 Nov 2026) status
| Requirement | Code | Tests | Pass rule | Status |
|---|---|---|---|---|
| 3a memory cards | web/memory.py (MEM_CARDS) | tests/test_v8_r27b.py; memory_eval.py --cards | refer-backs not lower, false recalls not higher (tuning set) | built; night run |
| 3b long-chat summaries | web/longsum.py | tune_v8_long | >= 5/6 | PASS 5/6 (in v8_lock) |
| 3c preference chips | web/pref_phrases.py | phrase tables | all caught, <= 1 trap | PASS (site) |
| 3d context builder v3 | chat.py CTX_BUILDER_V=3 | test_v8_r27b | passes, grounding not lower; prompt not longer | built; night run |
| 3d harder memory test | sets/recall_hard.json | - | reported | written |
| 4c claim rewrite | claim_rewrite.py (CLAIM_REWRITE) | test_v8_r27b | unsupported -0.10, passes not lower, <= 3 s | built; night run |
| Persona rename | persona.py (PERSONA=cinemack) | test_v8_r27b | <= 1 pass lost per set, length +-15% | built; night run |
| Guard v2 | safety2.py (GUARD2) | test_v8_r27b; attack_eval --set | 0 high-severity on sealed set; <= 1 false block; <= 1 pass lost | built; night run |
| Embedder fine-tune | tools/embed_ft.py | - | dev +0.03 R@10, +0.02 MRR | built; night run (shadow index) |
| Email alerts | web/emailer.py, tools/send_digests.py | test_v8_r27b, accept t17 | verified only, once per alert, unsubscribe works | built (sandbox) |
