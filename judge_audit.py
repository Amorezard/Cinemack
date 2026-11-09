#!/usr/bin/env python3
"""9 Nov: audit the CHECKERS, not the bot. For every eval_v3 results file given, flag rows where a checker is likely
wrong, so a switch is not turned on/off by a measuring error:
  G  judge 'unsupported' phrase that is actually in the context (word-for-word after normalising)
  H  judge passed / honest>=4 but the answer presents a film as the one described while a films check failed
  F  films check failed but the answer's first two sentences name the expected title (label/naming mismatch)
  W  one-word expected title that only matches as a lower-case common word (scorer trap)
    python judge_audit.py results/v3eval_*.json [--sets sets/a.json sets/b.json]
"""
import glob
import json
import re
import sys

PRESENTS = re.compile(r"\b(?:[Tt]hat's|[Tt]hat is|[Ii]t's|[Tt]his is|[Tt]hat sounds(?: more)? like|[Ss]ounds(?: more)? like"
                      r"|[Yy]ou're thinking of)\s+(?:actually\s+)?(?!not\b|no\b|quite\b)[A-Z0-9]")


def norm(s):
    return re.sub(r"\s+", " ", re.sub(r"[^\w]+", " ", (s or "").casefold())).strip()


def first2(a):
    return " ".join(re.split(r"(?<=[.!?])\s+(?=[A-Z\"'])", (a or "").strip())[:2])


def main():
    args = sys.argv[1:]
    sets = []
    if "--sets" in args:
        i = args.index("--sets"); sets = args[i + 1:]; args = args[:i]
    exp = {}
    for p in sets:
        d = json.load(open(p))
        for q in (d["questions"] if isinstance(d, dict) else d):
            e = q.get("expect_films") or q.get("expect_retrieved") or []
            if e:
                exp[q["id"]] = e
    files = [f for a in args for f in sorted(glob.glob(a))]
    tot = {"G": 0, "H": 0, "F": 0, "W": 0}
    for f in files:
        rows = json.load(open(f))["rows"]
        out = []
        for r in rows:
            j = r.get("judge") or {}
            ctx = norm(r.get("context")); ans = r.get("answer") or ""
            for c in j.get("unsupported_claims") or []:
                if len(norm(c)) >= 8 and norm(c) in ctx:
                    out.append(f"  G {r['id']}: judge called {c!r} unsupported, but it is in the context "
                               f"(verdict {j.get('verdict')})"); tot["G"] += 1
            g = r.get("grades") or {}
            if g.get("films") is False and ans:
                for t in exp.get(r["id"], []):
                    t0 = re.sub(r"\s*\(\d{4}\)$", "", t)
                    if norm(t0) and f" {norm(t0)} " in f" {norm(first2(ans))} " and re.search(re.escape(t0), ans):
                        out.append(f"  F {r['id']}: films check failed, answer names {t0!r} first "
                                   f"(retrieved {[h.get('title') if isinstance(h, dict) else h for h in (r.get('hits') or [])][:3]})")
                        tot["F"] += 1
            if j.get("verdict") == "pass" and PRESENTS.search(ans) and (r.get("category") == "invented" or (
                    r.get("category") in ("identify", "real_ident") and g.get("films") is False)):
                out.append(f"  H {r['id']}: judge PASS, films check failed, and the answer presents a film: "
                           f"{first2(ans)[:140]!r}"); tot["H"] += 1
            for t in exp.get(r["id"], []):
                t0 = re.sub(r"\s*\(\d{4}\)$", "", t)
                if len(norm(t0).split()) == 1 and re.search(rf"\b{re.escape(t0.lower())}\b", first2(ans)) \
                        and not re.search(rf"\b{re.escape(t0)}\b", first2(ans)):
                    out.append(f"  W {r['id']}: {t0!r} appears only as a lower-case word"); tot["W"] += 1
        print(f"{f}: {len(rows)} rows, {len(out)} flags")
        print("\n".join(out))
    print("totals:", tot)


if __name__ == "__main__":
    main()
