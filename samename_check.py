#!/usr/bin/env python3
"""
8 Nov (v8 4a): routing-only check for same-name titles, no model calls.

    python samename_check.py sets/tune_v8_samename.json            # score: routed type vs expect_type, off and on
    python samename_check.py --regress sets/tune.json sets/check.json sets/tv.json sets/multiturn.json
                                                                    # every question whose TV routing changes

A question counts as routed to TV when tv.wants_tv() says so (named / words / followup); otherwise it goes to the
film path. Rule (written 6 Nov): SAME_NAME=1 routes >= 18/20 of the tuning set right, and tune, check and
multi-turn each lose at most 1 (checked with stage_scores; --regress shows which questions could move).
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import tv  # noqa: E402


def qs(p):
    d = json.load(open(p, encoding="utf-8"))
    return d if isinstance(d, list) else d["questions"]


def routed(q, same):
    tv.TV, tv.SAME_NAME = True, same
    text, recent = tv._last_turn(q), tv._scripted_recent(q)
    why = tv.wants_tv(text, recent)
    ns = tv.named_show(text, recent_titles=recent) if same else tv.named_show(text)
    return ("tv" if why else "film"), why, (ns[2] if ns and why == "named" else None)


def score(path):
    rows = qs(path)
    ok = {False: 0, True: 0}
    for q in rows:
        want = q.get("expect_type")
        out = []
        for same in (False, True):
            typ, why, show = routed(q, same)
            ok[same] += typ == want
            out.append(f"{typ}{'*' if typ != want else ' '}{('(' + (show or why) + ')') if why else '':22s}")
        print(f"  {q.get('id', '?'):6s} want {want:4s} | off {out[0]} | on {out[1]} | {tv._last_turn(q)[:70]}")
    n = len(rows)
    print(f"{os.path.basename(path)}: right off {ok[False]}/{n}, on {ok[True]}/{n}  (* = wrong)")
    return ok


def regress(paths):
    for p in paths:
        if not os.path.exists(p):
            print(f"{p}: missing")
            continue
        rows = qs(p)
        moved = []
        for q in rows:
            a, b = routed(q, False), routed(q, True)
            if a[0] != b[0]:
                moved.append(f"  {q.get('id', '?'):10s} {a[0]} -> {b[0]} {('(' + str(b[2] or b[1]) + ')') if b[1] else ''}  {tv._last_turn(q)[:80]!r}")
        print(f"{p}: {len(rows)} questions; routing changed for {len(moved)}")
        print("\n".join(moved))


if __name__ == "__main__":
    a = sys.argv[1:]
    if not a:
        sys.exit(__doc__)
    if a[0] == "--regress":
        regress(a[1:])
    else:
        for p in a:
            score(p)
