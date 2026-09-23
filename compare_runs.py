#!/usr/bin/env python3
"""
Diff two eval runs. This is the script that makes the roadmap measurable -
after every change, it answers "did that help, hurt, or do nothing?" with
a number instead of a feeling.

Usage:
    python compare_runs.py results/baseline-q8_0.json results/q5_K_M.json
"""

import argparse
import json
import sys


def arrow(new, old, higher_is_better=True, pct=True):
    if new is None or old is None:
        return "n/a"
    if old == 0:
        return f"{new}"
    delta = new - old
    ratio = new / old
    better = (delta > 0) if higher_is_better else (delta < 0)
    if abs(delta) < 1e-9:
        mark = "="
    else:
        mark = "up" if delta > 0 else "down"
    tail = f"  ({ratio:.2f}x)" if pct else ""
    verdict = "" if mark == "=" else ("  better" if better else "  WORSE")
    return f"{mark} {abs(delta):.2f}{tail}{verdict}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("before")
    ap.add_argument("after")
    args = ap.parse_args()

    with open(args.before) as f:
        a = json.load(f)
    with open(args.after) as f:
        b = json.load(f)

    sa, sb = a["summary"], b["summary"]

    print(f"\n  {sa['label']}  ->  {sb['label']}")
    print(f"  {sa['when']}     {sb['when']}\n")

    print(f"  {'Measure':<28} {'before':>10} {'after':>10}   change")
    print(f"  {'-' * 68}")

    rows = [
        ("Retrieval passed", "retrieval_passed", True, False),
        ("Tokens/sec (median)", "median_tokens_per_sec", True, True),
        ("Time to first token (s)", "median_time_to_first_token_s", False, True),
        ("Full response wait (s)", "median_wall_seconds", False, True),
        ("Output length (tokens)", "median_output_tokens", True, False),
    ]
    for name, key, hib, pct in rows:
        va, vb = sa.get(key), sb.get(key)
        print(f"  {name:<28} {str(va):>10} {str(vb):>10}   {arrow(vb, va, hib, pct)}")

    print(f"\n  Retrieval by category")
    print(f"  {'-' * 68}")
    cats = sorted(set(sa["by_category"]) | set(sb["by_category"]))
    for c in cats:
        ca = sa["by_category"].get(c, {"pass": 0, "total": 0})
        cb = sb["by_category"].get(c, {"pass": 0, "total": 0})
        d = cb["pass"] - ca["pass"]
        note = "" if d == 0 else ("  +%d" % d if d > 0 else "  %d  REGRESSION" % d)
        print(f"  {c:<28} {ca['pass']}/{ca['total']:<8} {cb['pass']}/{cb['total']:<8} {note}")

    # Per-question flips are where the real information is.
    va = {r["id"]: r["retrieval_verdict"] for r in a["results"]}
    vb = {r["id"]: r["retrieval_verdict"] for r in b["results"]}
    fixed = [q for q in va if va[q] in ("FAIL", "WRONG_VERSION") and vb.get(q) == "PASS"]
    broke = [q for q in va if va[q] == "PASS" and vb.get(q) in ("FAIL", "WRONG_VERSION")]

    print()
    if fixed:
        print(f"  Newly passing: {', '.join(sorted(fixed))}")
    if broke:
        print(f"  NEWLY BROKEN:  {', '.join(sorted(broke))}   <- look at these first")
    if not fixed and not broke:
        print("  No retrieval verdicts changed.")

    print("\n  Auto-grading only covers retrieval. Re-read the behavior questions")
    print(f"  in {args.after.replace('.json', '.md')} - persona and honesty")
    print("  regressions don't show up in any number above.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
