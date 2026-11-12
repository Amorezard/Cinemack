#!/usr/bin/env python3
"""Judge check, three raters (10 Nov 2026): the gemma3:27b judge (results/judge_check_key.json), Claude graded blind to
the key (results/judge_check_claude.csv), and you (the judge_check.html export or a filled-in judge_check.csv).

    python tools/judge_compare.py [your_grades.csv]
Prints agreement on pass/fail for each pair, every row where any two disagree, and the judge's false passes / false
fails against you (or against Claude when your file is not given). Your file: any CSV with an `id` column and a
`your verdict (pass/fail)` (or `verdict`) column.
"""
import csv
import json
import sys

key = json.load(open("results/judge_check_key.json"))
cl = {r["id"]: r for r in csv.DictReader(open("results/judge_check_claude.csv"))}
you = {}
if len(sys.argv) > 1:
    for r in csv.DictReader(open(sys.argv[1])):
        v = (r.get("your verdict (pass/fail)") or r.get("verdict") or "").strip().lower()
        if v in ("pass", "fail"):
            you[r["id"]] = v
J = {i: ("pass" if str(k.get("verdict", "")).lower().startswith("pass") else "fail") for i, k in key.items()}
C = {i: r["claude verdict"] for i, r in cl.items()}
ids = [i for i in cl if i in J]


def agree(a, b):
    both = [i for i in ids if i in a and i in b]
    return sum(a[i] == b[i] for i in both), len(both)


def kappa(a, b):
    """Cohen's kappa on pass/fail (round 35: the plan's rule is >= 80% agreement AND kappa >= 0.6)."""
    both = [i for i in ids if i in a and i in b]
    if not both:
        return None
    po = sum(a[i] == b[i] for i in both) / len(both)
    pa, pb = sum(a[i] == "pass" for i in both) / len(both), sum(b[i] == "pass" for i in both) / len(both)
    pe = pa * pb + (1 - pa) * (1 - pb)
    return (po - pe) / (1 - pe) if pe < 1 else 1.0


for name, a, b in (("judge vs Claude", J, C), ("judge vs you", J, you), ("Claude vs you", C, you)):
    n, m = agree(a, b)
    if m:
        k = kappa(a, b)
        print(f"{name}: {n}/{m} agree ({n / m:.0%}), kappa {k:+.2f}  rule (>= 80% and kappa >= 0.6): "
              f"{'PASS' if n / m >= 0.8 and k >= 0.6 else 'FAIL'}")
print(f"passes: judge {sum(J[i] == 'pass' for i in ids)}/{len(ids)}, Claude {sum(C[i] == 'pass' for i in ids)}/{len(ids)}"
      + (f", you {sum(v == 'pass' for v in you.values())}/{len(you)}" if you else ""))
ref, refname = (you, "you") if you else (C, "Claude")
fp = [i for i in ids if i in ref and J[i] == "pass" and ref[i] == "fail"]
fn = [i for i in ids if i in ref and J[i] == "fail" and ref[i] == "pass"]
print(f"judge false passes (judge pass, {refname} fail): {len(fp)}  {fp}")
print(f"judge false fails  (judge fail, {refname} pass): {len(fn)}  {fn}")
print("\nrows where raters differ:")
for i in ids:
    vs = {"judge": J[i], "claude": C[i], **({"you": you[i]} if i in you else {})}
    if len(set(vs.values())) > 1:
        print(f"  {cl[i]['n']:>2} {i:<18} {vs}  | judge: {str(key[i].get('reason', ''))[:110]}  | claude: {cl[i]['claude note'][:110]}")
