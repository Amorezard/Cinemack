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
