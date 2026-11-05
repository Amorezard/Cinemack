#!/usr/bin/env python3
"""
1 Nov: why did one question change between two eval_v3 runs? Prints, for each id, what each run retrieved and
routed, the judge's verdict and reason, and a line diff of the context the answer model saw.

    python evaldiff.py results/v3eval_an_off2.json results/v3eval_an_alias2.json a11
    python evaldiff.py results/v3eval_an_off2.json results/v3eval_an_alias2.json a11,a24 --answers
"""
import difflib
import json
import sys


def main():
    a = sys.argv[1:]
    if len(a) < 3:
        sys.exit(__doc__)
    pa, pb, ids = a[0], a[1], a[2].split(",")
    show_answers = "--answers" in a
    ra = {r["id"]: r for r in json.load(open(pa))["rows"]}
    rb = {r["id"]: r for r in json.load(open(pb))["rows"]}
    for i in ids:
        x, y = ra.get(i), rb.get(i)
        if not x or not y:
            print(f"{i}: not in both files")
            continue
        print(f"===== {i}: {x['question']}")
        for name, r in (("A", x), ("B", y)):
            j = r.get("judge") or {}
            rt = r.get("route") or {}
            print(f"  {name} verdict {j.get('verdict', '-')}, grounded {j.get('grounded', '-')}; hits {r.get('hits')}; "
                  f"tasks {rt.get('tasks')}; films {rt.get('films')}; blocks {r.get('blocks')}; "
                  f"prompt {r.get('prompt_chars')} chars")
            print(f"    judge: {str(j.get('reason', ''))[:200]}")
            if j.get("unsupported_claims"):
                print(f"    unsupported: {j['unsupported_claims'][:4]}")
        same = (x.get("prompt") or x.get("context")) == (y.get("prompt") or y.get("context"))
        print(f"  prompt identical: {same}" + ("  (so the answer must be the same model call; a different verdict "
                                               "is the judge)" if same else ""))
        if not same:
            d = list(difflib.unified_diff((x.get("prompt") or x.get("context") or "").splitlines(),
                                          (y.get("prompt") or y.get("context") or "").splitlines(),
                                          "A", "B", n=0, lineterm=""))
            print("\n".join("    " + l[:220] for l in d[:60]))
            if len(d) > 60:
                print(f"    ... {len(d) - 60} more diff lines")
        if show_answers or x.get("answer") != y.get("answer"):
            print(f"  answer identical: {x.get('answer') == y.get('answer')}")
            if show_answers:
                print("  A answer: " + (x.get("answer") or "")[:700])
                print("  B answer: " + (y.get("answer") or "")[:700])


if __name__ == "__main__":
    main()
