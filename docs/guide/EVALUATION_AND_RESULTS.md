# Evaluation and results

**In one sentence:** every result here came from a rule written before the run, on data the builder had not tuned
on where possible, and the failures are listed with the successes.

## The method

1. **Tuning sets** (seen while building), e.g. 20 same-name questions, 20 invented-film questions, 40 phrase tests.
2. **Rule before run.** The decision rule is written into `planning/cinemack-v8.1/IMPLEMENTATION_PLAN_LOCKED.md` (or
   `docs/decision_log.md`) before any result exists. Example: "the forecast candidate replaces the current model only
   if test error is lower with a 95% interval excluding 0, its ranges still pass, and 2020-23 error is not worse."
3. **One switch per feature.** Arms differ by a single switch, so a gain or loss can be attributed.
4. **Sealed sets.** Written and hashed (md5) before the final run, checked before use, never used for tuning. v8 had
   128 items in 7 sets, frozen 8 Nov.
5. **Two kinds of scoring.** A judge model from another family (`gemma3:27b`) grades grounding and honesty, and
   deterministic checks run beside it ("expected title named first", "invented film presented as real"), because the
   judge was found to pass fluent answers about the wrong same-name title.
6. **Statistics.** Paired bootstrap 95% intervals (2,000 resamples); Holm correction when several groups are tested.
   Data used before is labelled "not blind".

## Results

Status words: PASS, FAIL, UNRUN (did not run), BLOCKED (needs something not available). Full detail with commits:
`planning/cinemack-v8.1/BASELINE_RESULTS.md`.

### Answers (sealed v8 test, 12 Nov, v7 -> v8, deterministic checks)

| Set | v7 | v8 |
| --- | --- | --- |
| Same-name titles, right title named first (15) | 11 | 13 |
| Invented films presented as real (10, lower is better) | 1 | 0 |
| Real but vague descriptions named first (10) | 10 | 8 |
| Memory: refer-backs recalled right (16) | 12 | 15 |
| Memory: unrelated chats recalled (8, lower is better) | 1 | 0 |

### v8.1 (12-17 Nov)

| Area | Test | Result | Status |
| --- | --- | --- | --- |
| Identity | 41,084 ids for 33,041 films + 8,043 shows; 5 build rules; audit counts 519 / 22 / 8 matched | all rules hold | PASS |
| Hard limits, films | plan = plain-Python reference, 30 cases x 33,041 films | 100% | PASS |
| Hard limits, TV | 24 cases x 8,043 shows + 3 mixed film/show plans | 100% | PASS |
| Caches | 20 negation pairs and the site's near-miss list at similarity forced to 1.0; per-person and per-limit keys | one gap found and fixed: strict mode was missing from the search-cache key | PASS after fix |
| Forecast (4.5) | ranges hold 45-55% / 75-85% on 2016-19 | d90 47.6% / 78.2%, d7 47.3% / 79.0% | PASS |
| Forecast candidate (4.6) | lower error with CI excluding 0, ranges still pass, 2020-23 not worse | d90 -0.027, d7 -0.054 | PASS |
| Ranking, one weight set | no worse than current on both tests | uniform -0.015 | FAIL |
| Ranking, fresh users | same | uniform -0.011 | FAIL |
| Ranking, third candidate | max-min choice, 2021 test | random -0.008 (CI below 0), popular +0.040, 2,831 new users | FAIL |
| Motion | kit tokens, reduced-motion block, no animated layout properties | first failed on the home banner dots; fixed | PASS |
| YouTube matches | >= 90% right on a new graded sample (AI rater) | 55 of 60 kept matches right (91.7%, range 82-96%); but 9 of 20 drops removed a right match | PASS |
| Review relinking | >= 90% right on a graded sample (AI rater) | 38 of 50 right (76%); new films sharing an old title | FAIL |
| Retrieval refresh, load tests | - | need the shared GPU | BLOCKED |
| Real deals | - | no named official source | BLOCKED |

## Honest limits to state

- The judge agreed with a second rater on only 16 of 30 checks, and that second rater was also an AI, so decisions
  rest on the deterministic checks.
- Some live-site switches were turned on despite a failed or after-the-fact rule (memory cards, guard v2, a few
  answer-text rules); the decision log lists them.
- Recommendations are measured on MovieLens users, not Cinemack users; there are 3 real accounts.
- Box-office 2016-19 is not blind, and ranges are wide (about x35) and over-optimistic for 2020-23.
- No HTTPS yet; a shared GPU (first token about 38 s under contention on 10 Nov).
