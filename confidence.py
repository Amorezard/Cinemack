#!/usr/bin/env python3
"""
v4 (15 Oct): calibrated confidence for "is the top film the right one?" (inventory item 22).

Today two fixed distance cut-offs (MAX_DISTANCE 0.90, GUESS_MAX_DISTANCE 0.87) decide whether a film is
offered at all, and nothing says how sure the answer is. This learns, from retrieval runs already on disk
(results/stage_scores_*.json: each question's trace and whether it passed), the probability that the
first film is right, and checks that the probability means what it says.

  features   distance of the #1 film, its lead over #2, whether the LLM re-ranker ran, whether the
             re-ranker's pick was also the fused search's #1, pool size, description vs named question
  model      logistic regression (numpy, standardised features, L2), fitted on TUNE runs only
  checked    on CHECK runs: Brier score, expected calibration error over 10 bins, and a table of
             "answer when p >= t": share of questions answered and how often those answers are right

    python confidence.py fit        # -> results/confidence_model.json + results/confidence_summary.txt
    python confidence.py table      # the threshold table again

Two thresholds then replace the fixed cut-offs (not switched on here): answer above the one where
answered questions are right >= 85% of the time, ask "did you mean" between, say nothing matched below.
Limitation stated in the output: tune and check questions come from the same sets the pipeline was tuned
on, so the absolute numbers are optimistic; the calibration (does 0.8 mean 80%?) is what this measures.
"""
import glob
import json
import math
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "results", "confidence_model.json")
SUMMARY = os.path.join(HERE, "results", "confidence_summary.txt")
NAMES = ["d1", "gap", "reranked", "agree", "log_pool", "identify"]


def features(trace):
    fin = trace.get("final") or []
    if not fin:
        return None
    d = [r[3] if isinstance(r[3], (int, float)) else None for r in fin]
    d1 = d[0] if d and d[0] is not None else 1.0
    d2 = d[1] if len(d) > 1 and d[1] is not None else d1 + 0.1
    ro = trace.get("rerank_out") or []
    pool = trace.get("pool") or []
    agree = float(bool(ro and pool and ro[0][0] == pool[0][0]))
    ident = float(str(trace.get("path", "")).endswith("identify"))
    return [d1, d2 - d1, float(bool(ro)), agree, math.log1p(len(pool)), ident]


def rows_from(pattern):
    X, y, seen = [], [], set()
    for f in sorted(glob.glob(os.path.join(HERE, "results", pattern)), key=os.path.getmtime, reverse=True):
        try:
            d = json.load(open(f))
        except (OSError, ValueError):
            continue
        for r in d.get("rows", []):
            if not r.get("expect"):
                continue
            key = (r.get("id"), json.dumps(d.get("switches", {}), sort_keys=True))
            if key in seen:
                continue
            seen.add(key)
            x = features(r["result"].get("trace") or {})
            if x is not None:
                X.append(x)
                y.append(float(bool(r["result"]["pass"])))
    return np.array(X, dtype=float), np.array(y, dtype=float)


def fit(X, y, l2=1.0, steps=4000, lr=0.1):
    mu, sd = X.mean(0), X.std(0) + 1e-9
    Z = np.c_[np.ones(len(X)), (X - mu) / sd]
    w = np.zeros(Z.shape[1])
    for _ in range(steps):
        p = 1 / (1 + np.exp(-Z @ w))
        g = Z.T @ (p - y) / len(y) + l2 * np.r_[0, w[1:]] / len(y)
        w -= lr * g
    return {"w": w.tolist(), "mu": mu.tolist(), "sd": sd.tolist(), "names": NAMES}


def predict(m, X):
    Z = np.c_[np.ones(len(X)), (X - np.array(m["mu"])) / np.array(m["sd"])]
    return 1 / (1 + np.exp(-Z @ np.array(m["w"])))


def report(p, y):
    bins = np.clip((p * 10).astype(int), 0, 9)
    ece, lines = 0.0, []
    for b in range(10):
        k = bins == b
        if k.any():
            ece += k.mean() * abs(p[k].mean() - y[k].mean())
            lines.append(f"  p {b / 10:.1f}-{(b + 1) / 10:.1f}: {k.sum():4d} questions, predicted {p[k].mean():.2f}, "
                         f"actually right {y[k].mean():.2f}")
    table = []
    for t in (0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9):
        k = p >= t
        table.append(f"  answer when p >= {t:.1f}: answers {k.mean():5.1%} of questions, right {y[k].mean() if k.any() else 0:5.1%}")
    return float(np.mean((p - y) ** 2)), float(ece), lines, table


def cmd_fit():
    Xt, yt = rows_from("stage_scores_*_tune*.json")
    Xc, yc = rows_from("stage_scores_*_check*.json")
    if len(yt) < 30 or len(yc) < 10:
        sys.exit(f"not enough runs on disk (tune {len(yt)}, check {len(yc)})")
    m = fit(Xt, yt)
    pc = predict(m, Xc)
    brier, ece, lines, table = report(pc, yc)
    base = float(np.mean((yc.mean() - yc) ** 2))
    m.update(n_tune=int(len(yt)), n_check=int(len(yc)), brier=brier, ece=ece, brier_base=base)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump(m, open(OUT, "w"), indent=1)
    w = dict(zip(["bias"] + NAMES, m["w"]))
    text = [f"Confidence model (logistic regression), fitted on {len(yt)} tune question-runs, checked on {len(yc)} check runs",
            "weights (standardised features; + raises confidence): " + ", ".join(f"{k} {v:+.2f}" for k, v in w.items()),
            f"check: Brier {brier:.3f} (always predicting the base rate: {base:.3f}); expected calibration error {ece:.3f}",
            "calibration on check:"] + lines + ["thresholds on check:"] + table + [
            "Limitation: tune/check questions are the sets the pipeline was tuned on; the calibration is the point, "
            "the absolute accuracy is optimistic."]
    open(SUMMARY, "w").write("\n".join(text) + "\n")
    print("\n".join(text))


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "fit":
        cmd_fit()
    elif len(sys.argv) > 1 and sys.argv[1] == "table":
        print(open(SUMMARY).read())
    else:
        print(__doc__)
