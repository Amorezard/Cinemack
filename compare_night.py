"""
One table from several stage_scores.py runs (and judge.py runs).

    python compare_night.py results/stage_scores_*_night_base.json results/stage_scores_*_night_all.json ...
    python compare_night.py --judge results/judge-night_base.json results/judge-night_all.json

The first file is the reference: every other row shows its difference, and
which questions it gained or lost against the reference.
"""

import json
import os
import re
import sys


def label(path):
    m = re.search(r"((?:night|final)_[a-z0-9_]+)", os.path.basename(path))
    return m.group(1) if m else os.path.basename(path)


def groups(rows):
    out = {}
    for r in rows:
        g = "HONESTY" if not r["expect"] else r["group"]
        out.setdefault(g, [0, 0])
        out[g][0] += bool(r["result"]["pass"])
        out[g][1] += 1
    return out


def retrieval_table(paths):
    runs = [(label(p), json.load(open(p))) for p in paths]
    names = sorted({g for _, d in runs for g in groups(d["rows"])})
    head = f"{'run':18s} " + " ".join(f"{g:>11s}" for g in names) + \
        f" {'s@1':>6s} {'MRR':>6s} {'ms/q':>6s}  switches"
    print(head)
    print("-" * len(head))
    ref = None
    for name, d in runs:
        g = groups(d["rows"])
        fin = d["summary"].get("final", {})
        ms = sum(r["result"]["trace"].get("total_ms", 0) for r in d["rows"]) / max(1, len(d["rows"]))
        sw = " ".join(f"{k}={int(v) if isinstance(v, bool) else v}"
                      for k, v in d.get("switches", {}).items()
                      if k in ("UNKNOWN_FILM", "MULTI_FILM", "HYDE", "HYBRID", "RERANK_N",
                               "RERANK_CHARS", "GUESS_WIDE"))
        cells = " ".join(f"{g.get(n, [0, 0])[0]:>5d}/{g.get(n, [0, 0])[1]:<5d}" for n in names)
        print(f"{name:18s} {cells} {fin.get('s@1', 0):6.2f} {fin.get('mrr', 0):6.2f} {ms:6.0f}  {sw}")
        passed = {r["id"]: bool(r["result"]["pass"]) for r in d["rows"]}
        if ref is None:
            ref = (name, passed)
            continue
        gained = [i for i, p in passed.items() if p and not ref[1].get(i, False)]
        lost = [i for i, p in passed.items() if not p and ref[1].get(i, False)]
        print(f"{'':18s} vs {ref[0]}: +{len(gained)} {' '.join(gained)[:90]}")
        print(f"{'':18s} {' ' * (len(ref[0]) + 4)}-{len(lost)} {' '.join(lost)[:90]}")


def judge_table(paths):
    keys = ("grounded", "direct", "specific", "honest", "concise")
    print(f"{'run':18s} {'pass':>7s} " + " ".join(f"{k[:8]:>8s}" for k in keys))
    for p in paths:
        if not os.path.exists(p):
            print(f"{label(p):18s} (missing: {p})")
            continue
        rows = json.load(open(p))
        js = [r["judge"] for r in rows if r.get("judge")]
        n = len(js)
        ok = sum(j.get("verdict") == "pass" for j in js)
        avg = {k: [j[k] for j in js if isinstance(j.get(k), (int, float))] for k in keys}
        print(f"{label(p):18s} {ok:>3d}/{n:<3d} " +
              " ".join(f"{(sum(v) / len(v)) if v else 0:8.2f}" for v in avg.values()))


if __name__ == "__main__":
    args = sys.argv[1:]
    if args and args[0] == "--judge":
        judge_table(args[1:])
    else:
        retrieval_table(args)
