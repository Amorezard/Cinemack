# Recommendations

**In one sentence:** films are scored from three signals by code, the person's hard limits are applied before
scoring, and a new ranking is switched on only if it is no worse than the current one on both of two tests - which
the first two candidates were not.

## How the current ranking works (`personal.py`)

From the films a person liked and disliked, the site builds a profile (directors, cast, genres, keywords, decades)
and mixes three signals for each candidate film:

| Signal | Meaning | Source |
| --- | --- | --- |
| content | how much the film shares with the profile | `facts.db` credits, genres, keywords |
| neighbours | "people who liked X also liked Y" | MovieLens 32M ratings (`ml_neighbours`) |
| popularity | how widely the film is rated | MovieLens / TMDB vote counts |

Current weights: content 0.25, neighbours 2, popularity 0.5.

## The new ranking (`web/recs.py`, switch `RECS_V2`, off)

One function, five fixed steps: (1) candidates = neighbours of liked films + well-known films that meet the limits;
(2) **hard limits** from the validated query plan, compiled to SQL, plus already-rated films removed - nothing removed
here can come back; (3) score with the three signals, each scaled 0-1; (4) a variety step (genre MMR, lambda 0.85)
moves near-copies down; (5) exactly one reason code per film, chosen from the numbers, never from model text:
`similar_to_liked`, `shares_director`, `shares_cast`, `shares_genre`, `shares_theme`, `popular_fits_limits`.

## How rankings are tested

There are no real Cinemack users at scale (3 accounts), so the *method* is tested on MovieLens 32M users, in time
order: train on ratings before a cut date, then see whether the films a user liked after it rank highly.

- Each user's liked films after the cut are mixed with 200 films they never rated, under two tests:
  - **uniform**: the 200 are random films (mostly obscure);
  - **popular**: the 200 are drawn in proportion to popularity (well-known films the user did not pick).
- Score: NDCG@10 (are the liked films near the top of the 10 shown?), catalogue coverage, and variety.
- **Rule (written before every run):** under **both** tests the new ranking's NDCG@10 minus the current ranking's has
  a bootstrap 95% interval that is not wholly below zero, **and** its coverage is not lower.

## What happened

| Date | Candidate | Uniform test | Popular test | Rule |
| --- | --- | --- | --- | --- |
| 14 Nov | weights chosen separately per test | +0.026 [+0.021, +0.031] | +0.165 [+0.153, +0.178] | pass, but two different weight sets - a site can run only one |
| 15 Nov | one set (content 2, neighbours 1, popularity 0) | **-0.015** [-0.026, -0.005] | +0.152 | FAIL |
| 16 Nov | same set on 2,569 fresh users | **-0.011** [-0.019, -0.003] | +0.147 | FAIL |

## Why it failed (diagnosis, 16 Nov)

NDCG@10 of each signal alone (14 Nov report half):

| Signal alone | Uniform test | Popular test |
| --- | --- | --- |
| content | 0.468 | 0.420 |
| neighbours | 0.631 | 0.278 |
| popularity | 0.387 | 0.092 |
| current ranking | 0.627 | 0.276 |

- **The two tests reward opposite things.** Against random films, neighbours and popularity are strong, because a
  random film is obscure. Against popular films, popularity is worse than useless and neighbours lose most of their
  value (popular films are everybody's neighbours). Only content keeps working on both.
- **The single weight set was chosen by the wrong yardstick.** On 15 Nov the set with the highest *average share of
  each test's best score* was picked. That favoured the popular test, where there was 0.15 of room, and landed just
  below the current ranking on the uniform test, where there was almost none. The rule never asked for a high
  average; it asked to be no worse on both.
- **The variety step was not the cause:** without it the same weights did no better (-0.013 on fresh users).

## The third try (round 48, `tools/v81_recs_v3.py`): also failed

- **Choice:** for every weight set in the grid, its margin over the current ranking on each test; pick the set whose
  *smaller* margin is largest. This asks the rule's own question directly.
- **Test on new data:** the year 2021 (train = ratings before 2021, neighbours rebuilt), on users never used in the
  2020 runs. Same rule. Fewer than 500 users = no verdict.
- **Outcome:** a PASS writes `results/v81_recs_v3.json` and `web/recs.py` reads it; `RECS_V2` still stays off until it
  is switched on. A FAIL means no single linear weight set beats the current ranking on both tests, and the current
  ranking stays - which is itself a finding worth reporting.

Caveat to say out loud: the choice uses 2020 data already seen three times; only the 2021 test is new, and MovieLens
users are not Cinemack users.

**Result (16 Nov, 2,831 new users from 2021): FAIL.** The chosen set was content 2, neighbours 4, popularity 0.

| Test | Candidate | Current | Difference (95% range) | Coverage | Verdict |
| --- | --- | --- | --- | --- | --- |
| Random wrong answers | 0.646 | 0.653 | -0.0076 (-0.0108 to -0.0042) | 0.314 vs 0.220 | not ok |
| Popular wrong answers | 0.292 | 0.252 | +0.0396 (+0.0368 to +0.0426) | 0.149 vs 0.113 | ok |

On 2020 the same set looked fine on both tests (+0.027 and +0.058); on new users a year later the smaller margin
turned negative. That is what choosing on seen data does, and why the 2021 test was the only one that counted.

**What follows.** Three candidates, three fails: no single set of three weights beats the current ranking on both
tests, so the current ranking stays and this line of work stops on MovieLens data. The next test needs what MovieLens
cannot give: what Cinemack's own users open, save and skip (`web/rec_events.py`, built, off). See
[ROADMAP.md](ROADMAP.md), idea 3.
