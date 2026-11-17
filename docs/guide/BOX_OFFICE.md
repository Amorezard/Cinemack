# Box-office forecasting

**In one sentence:** the forecaster predicts worldwide gross as a multiple of budget, using only what was known
before release, with 50% and 80% ranges that are checked to hold as often as they claim.

## What it predicts

The log of worldwide gross divided by budget. On a log scale "three times the budget" and "a third of it" are equal-
sized errors, which matches how hits and flops behave. Domestic and international splits are not predicted: the data
has worldwide totals only (decided 13 Nov).

## Inputs, with a timestamp rule (`boxoffice_asof.py`)

Every input is registered with when it becomes known: *planned* (genre, budget, cast, producer), *trailers* (only
those published before the cutoff), *release*, *after*. A forecast made 90 days (`d90`) or 7 days (`d7`) before
release may use only inputs known by then; anything later raises an error. A test plants a deliberate leak and checks
that it is caught. Leaks are the most common way box-office models look better than they are.

## Model and ranges

- **Point estimate:** LightGBM, the mean of 5 fits (seeds 7, 11, 13, 17, 19), trained on films up to 2015.
- **Ranges: conformalized quantile regression (CQR).** Quantile models trained on films up to 2011 give a low and a
  high edge; films from 2012-2015 measure how often those edges miss, and the ranges are widened until the stated
  coverage holds on that held-out period.

## Which inputs matter (factor study, 13 Nov)

LightGBM, 5 seeds, 2,000 bootstrap resamples, Holm correction. Change in test error when a group is removed or added:

| Group | Effect | Verdict |
| --- | --- | --- |
| trailers | removing raises error +0.060 [0.046, 0.074] | carries value |
| genre / film type | +0.052 [0.038, 0.067] | carries value |
| budget, cast track record, franchise | +0.014 to +0.017 | carries value |
| US rating (added) | adding lowers error by 0.031 [0.020, 0.042] | helps |
| producer track record (added) | adding lowers error by 0.022 [0.007, 0.039] | helps |
| director, writer, release timing, competition, source, companies | CI includes 0 | no clear value |

Associations only; no causal claim.

## Results

The candidate with producer track record and US rating (the US rating at `d7` only, since a rating is usually
assigned in the last weeks) passed its pre-registered rule at both horizons and is the stored model,
`v81-pf4bt-pr-cqr-1`:

| Horizon | Typical miss (test 2016-19) | 50% range holds | 80% range holds | 80% range width |
| --- | --- | --- | --- | --- |
| 90 days before | x3.3 (MAE 1.191) | 48.4% | 76.9% | x35 |
| 7 days before | x3.2 (MAE 1.148) | 48.0% | 78.6% | x35 |

- For 2020-2023 films it guesses too high and its 80% range holds only 74-75% of the time; every forecast for those
  years carries that warning.
- 2016-2019 is **not blind**: earlier versions were tuned on it.
- 2,951 films have stored forecasts, every one re-checked against the response schema; 0 failed.

## What it never says

"Profit" or "ROI": gross over budget leaves out marketing and the cinemas' share. The endpoint refuses to serve any
stored forecast containing those words, and the panel shows the range before the middle estimate so a single number
never looks certain.
