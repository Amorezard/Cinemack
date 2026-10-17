#!/usr/bin/env python3
"""
16 Oct: why did the nickname set score 0/15 with ALIASES off AND on (run_v5.sh, 15 Oct night)?

Re-grades the SAVED stage_scores runs - no retrieval, no model calls, a second or two:

    python diag_aliases.py                      # latest v5_alias_off and v5_alias_on runs
    python diag_aliases.py results/<file>.json  # any saved stage_scores run

For each question it prints what came back and three verdicts:
  saved    the pass flag stored in the file (what v5_summary.txt counted)
  title    the expected film is among the returned titles (year ignored)
  year     title AND year match, years compared as 4-character text on both sides (the 16 Oct grading)

Reading the result:
  saved 0, title/year > 0   -> a scoring fault (wrong year type or years paired with the wrong titles)
  saved 0, title 0          -> retrieval really failed; look at "lost at" and "returned"
  every question returned nothing -> retrieval was broken during the run (Ollama down, model missing,
                                     GPU full); check results/v5.log around the aliases step
"""
import glob
import json
import os
import sys
import unicodedata


def norm(s):
    s = unicodedata.normalize("NFKD", str(s or ""))
    return "".join(c for c in s.casefold() if c.isalnum() and not unicodedata.combining(c))


def latest(tag):
    fs = sorted(glob.glob(f"results/stage_scores_*_{tag}.json"), key=os.path.getmtime)
    return fs[-1] if fs else None


def regrade(path):
    d = json.load(open(path))
    rows = d["rows"]
    print(f"\n=== {path}  ({len(rows)} questions)")
    print(f"    switches: ALIASES={d.get('switches', {}).get('ALIASES', '?')}")
    n_saved = n_title = n_year = n_empty = 0
    types = set()
    for r in rows:
        res = r["result"]
        exp, ey = r.get("expect") or [], r.get("expect_year")
        types.add(type(ey).__name__)
        hits = res.get("hits") or []
        final = (res.get("trace") or {}).get("final") or []
        fy = {norm(row[1]): str(row[2] or "")[:4] for row in final}      # title -> year, from the trace
        title_ok = bool(exp) and all(any(norm(h) == norm(e) for h in hits) for e in exp)
        year_ok = title_ok and (not ey or all(fy.get(norm(e)) == str(ey)[:4] for e in exp))
        n_saved += bool(res.get("pass"))
        n_title += title_ok
        n_year += year_ok
        n_empty += not hits
        got = ", ".join(f"{h} ({fy.get(norm(h), '?')})" for h in hits[:3]) or "(nothing)"
        print(f"  {r['id']:5s} saved {'P' if res.get('pass') else '-'}  title {'P' if title_ok else '-'}  "
              f"year {'P' if year_ok else '-'}   expect {exp[0] if exp else '-'} ({ey!r})")
        print(f"        returned: {got[:110]}")
        if not title_ok:
            print(f"        lost at:  {(res.get('lost_at') or '')[:110]}")
        q = r.get("question", "")
        print(f"        question: {q[:110]}")
    print(f"\n  saved pass {n_saved}/{len(rows)}   title-only {n_title}/{len(rows)}   "
          f"title+year {n_year}/{len(rows)}   returned nothing {n_empty}/{len(rows)}")
    print(f"  expect_year types in the file: {', '.join(sorted(types))}")
    return n_saved, n_title, n_year, n_empty, len(rows)


def main():
    paths = sys.argv[1:] or [p for p in (latest("v5_alias_off"), latest("v5_alias_on")) if p]
    if not paths:
        sys.exit("no results/stage_scores_*_v5_alias_off.json / _on.json found - did the aliases step run?")
    out = [regrade(p) for p in paths]
    print("\n--- verdict")
    for p, (s, t, y, e, n) in zip(paths, out):
        if e == n:
            why = "every question returned nothing: retrieval was broken during this run"
        elif s == 0 and y > 0:
            why = "SCORING fault: films came back with the right year but were graded as failures"
        elif s == 0 and t > 0:
            why = "titles right but years differ: check the expect_year values against movies.db"
        elif t == 0:
            why = "retrieval failed on every question (see 'lost at')"
        else:
            why = "scored as stored"
        print(f"  {os.path.basename(p)}: saved {s}/{n}, re-graded {y}/{n} -> {why}")
    print("\nJSON on the server (should say str 15 times, not int):")
    try:
        qs = json.load(open("sets/aliases.json"))["questions"]
        print("  sets/aliases.json expect_year types:",
              {t: sum(type(q.get('expect_year')).__name__ == t for q in qs) for t in ('str', 'int')})
    except Exception as e:  # noqa: BLE001
        print("  could not read sets/aliases.json:", e)


if __name__ == "__main__":
    main()
