#!/usr/bin/env python3
"""
v7 decision rules, written before the results (plan doc "Tests and rules", 23 Oct). Each command reads result files
and prints the numbers the rule uses and PASS / FAIL. Nothing here changes a switch: the switch is set by hand in
config.yaml when its rule passes.

    python v7_rules.py reception results/v3eval_rec_off.json results/v3eval_rec_on.json
        RECEPTION on if judge passes >= off + 6, unsupported rate (claimcheck) not higher, grounding not lower
    python v7_rules.py regression results/v3eval_<off>.json results/v3eval_<on>.json [--max-lost 1]
        a switch may lose at most 1 judge pass (analytic / multiturn sets: grounding not lower)
    python v7_rules.py stage results/stage_<off>.json results/stage_<on>.json [--max-lost 1]
        tune / check (stage_scores.py output): each may lose at most 1 found film
    python v7_rules.py tv results/v3eval_tv_on.json [--off results/v3eval_tv_off.json]
        TV on if the TV set passes >= 20 / 30 (and film sets hold: run `stage` / `regression` too)
    python v7_rules.py shorts results/v3eval_sh_on.json results/v3eval_sh_off.json
        (1 Nov) SHORTS on if the 15-question shorts set passes >= off + 4 (and tune / check / analytic hold)
    python v7_rules.py recall results/recall_off.json results/recall_on.json
        embedding recall on if hit@1 is higher and false recall is not higher
  3 Nov replacements (reasons in the code, written before the 3 Nov re-tests):
    python v7_rules.py reception2 <off.json> <on.json> results/pairwise_<tag>.json
        RECEPTION on if on wins >= off wins + 4 head to head, passes not lower, unsupported not higher, grounding not lower
    python v7_rules.py shorts2 <on.json> <off.json>
        SHORTS on if passes on >= off and every recommended film is a short
    python v7_rules.py recall2 results/recall_off.json results/recall_on.json
        MEM_EMBED on if hit@1 higher, false recall not higher, and the check half (c9-c15) not worse
  4 Nov (new switches):
    python v7_rules.py h2h <off.json> <on.json> results/pairwise_<tag>.json <margin>
    python v7_rules.py notworse <off.json> <on.json>
    python v7_rules.py attack results/attack_<tag>.json
        defended >= 36 / 40
    python v7_rules.py blocks results/moderation_<tag>.json
        <= 1 false block across the normal sets
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def load(p):
    return json.load(open(p))


def judge_stats(rows):
    js = [r.get("judge") or {} for r in rows if r.get("answer")]
    passes = sum(1 for j in js if j.get("verdict") == "pass")
    g = [j.get("grounded") for j in js if isinstance(j.get("grounded"), (int, float)) and j.get("grounded") > 0]
    return passes, len(js), (sum(g) / len(g) if g else None)


def claim_totals(eval_path):
    d = load(eval_path)
    tag = d.get("tag") or os.path.basename(eval_path)
    p = os.path.join(HERE, "results", f"claimcheck_{tag}.json")
    if not os.path.exists(p):
        return None
    return load(p)["totals"]


def cmd_reception(off, on):
    a, b = load(off), load(on)
    pa, na, ga = judge_stats(a["rows"])
    pb, nb, gb = judge_stats(b["rows"])
    ca, cb = claim_totals(off), claim_totals(on)
    print(f"judge passes: off {pa}/{na}, on {pb}/{nb} (need on >= off + 6 = {pa + 6})")
    print(f"grounding: off {ga if ga is None else round(ga, 2)}, on {gb if gb is None else round(gb, 2)}")
    if ca and cb:
        print(f"unsupported rate: off {ca['unsupported_rate']}, on {cb['unsupported_rate']}; citation precision on "
              f"{cb['citation_precision']}, recall on {cb['citation_recall']}, sources used per answer on "
              f"{cb['mean_sources_used']}")
    else:
        print("claimcheck results missing: run `python claimcheck.py eval <file>` on both first")
    by = {}
    for r in b["rows"]:
        by.setdefault(r["category"], [0, 0])
        by[r["category"]][1] += 1
        by[r["category"]][0] += (r.get("judge") or {}).get("verdict") == "pass"
    print("on, by type: " + ", ".join(f"{k} {v[0]}/{v[1]}" for k, v in by.items()))
    ok = (pb >= pa + 6 and (ga is None or gb is None or gb >= ga - 1e-9)
          and (not ca or not cb or ca["unsupported_rate"] is None or cb["unsupported_rate"] is None
               or cb["unsupported_rate"] <= ca["unsupported_rate"]))
    print(f"RULE RECEPTION: {'PASS -> set RECEPTION: 1 in config.yaml' if ok else 'FAIL -> RECEPTION stays 0'}")
    return ok


def cmd_regression(off, on, max_lost=1):
    a = {r["id"]: r for r in load(off)["rows"]}
    b = {r["id"]: r for r in load(on)["rows"]}
    lost = [i for i in a if i in b and (a[i].get("judge") or {}).get("verdict") == "pass"
            and (b[i].get("judge") or {}).get("verdict") != "pass"]
    gained = [i for i in a if i in b and (a[i].get("judge") or {}).get("verdict") != "pass"
              and (b[i].get("judge") or {}).get("verdict") == "pass"]
    _pa, _na, ga = judge_stats(list(a.values()))
    _pb, _nb, gb = judge_stats(list(b.values()))
    print(f"lost {len(lost)} {lost}, gained {len(gained)} {gained}; grounding {ga} -> {gb}")
    ok = len(lost) <= max_lost and (ga is None or gb is None or gb >= ga - 1e-9)
    print(f"RULE regression (lose <= {max_lost}, grounding not lower): {'PASS' if ok else 'FAIL'}")
    return ok


def _found(rows):
    """stage_scores.py rows: {"group", "id", "result": {"pass": bool, "ranks": {...}}} -> {group/id: passed}"""
    return {f"{r.get('group', '')}/{r['id']}": bool((r.get("result") or {}).get("pass")) for r in rows}


def cmd_stage(off, on, max_lost=1):
    a, b = load(off), load(on)
    ra = _found(a.get("rows") or a.get("results") or [])
    rb = _found(b.get("rows") or b.get("results") or [])
    lost = [q for q in ra if ra[q] and not rb.get(q)]
    gained = [q for q in rb if rb[q] and not ra.get(q)]
    print(f"found: off {sum(ra.values())}, on {sum(rb.values())}; lost {lost}, gained {gained}")
    ok = len(lost) <= max_lost
    print(f"RULE stage (lose <= {max_lost}): {'PASS' if ok else 'FAIL'}")
    return ok


def cmd_tv(on, off=None):
    p, n, _g = judge_stats(load(on)["rows"])
    print(f"TV set: {p}/{n} judge passes (need >= 20 of 30)")
    if off:
        po, no, _ = judge_stats(load(off)["rows"])
        print(f"  with TV=0: {po}/{no}")
    ok = p >= 20
    print(f"RULE TV (set part): {'PASS' if ok else 'FAIL'} - also check tune / check / analytic with `stage` / `regression`")
    return ok


def cmd_shorts(on, off):
    p, n, _g = judge_stats(load(on)["rows"])
    po, no, _ = judge_stats(load(off)["rows"])
    print(f"shorts set: on {p}/{n}, off {po}/{no} judge passes (need on >= off + 4 = {po + 4})")
    ok = p >= po + 4
    print(f"RULE SHORTS (set part): {'PASS' if ok else 'FAIL'} - also check tune / check / analytic with `stage` / "
          f"`regression`")
    return ok


def cmd_recall(off, on):
    a, b = load(off), load(on)
    print(f"hit@1: off {a['hit1']:.2f}, on {b['hit1']:.2f}; false recall: off {a['false_recall']:.2f}, on "
          f"{b['false_recall']:.2f}")
    ok = b["hit1"] > a["hit1"] and b["false_recall"] <= a["false_recall"]
    print(f"RULE recall: {'PASS -> MEM_EMBED=1' if ok else 'FAIL'}")
    return ok


def cmd_attack(p):
    d = load(p)
    print(f"defended {d['defended']}/{d['n']} (need >= 36 of 40); by kind: {d.get('by_kind')}")
    ok = d["defended"] >= 36
    print(f"RULE attacks: {'PASS' if ok else 'FAIL'}")
    return ok


def cmd_blocks(p):
    d = load(p)
    print(f"false blocks on normal questions: {d['false_blocks']} of {d['n']} (need <= 1): {d.get('examples', [])[:5]}")
    ok = d["false_blocks"] <= 1
    print(f"RULE moderation: {'PASS -> MODERATION=1' if ok else 'FAIL'}")
    return ok


# ---------------------------------------------------------------- 3 Nov rules (written before the 3 Nov re-tests)
# Three of the 23 Oct rules could not be met by any answer because the off arm was already good: reception needed
# 42 of 40 (off 36/40), shorts needed off + 4 with off already 10/15. They are replaced below, with the reason, before
# the re-test results exist. The old commands stay for the record. The sealed run is the blind check of all of them.
def cmd_reception2(off, on, pair):
    """RECEPTION on if, on the reception set: on wins >= off wins + 4 head to head (pairwise.py, both orders), judge
    passes not lower, unsupported rate (claimcheck) not higher, grounding not lower. (+ analytic `regression`.)"""
    a, b, p = load(off), load(on), load(pair)
    pa, na, ga = judge_stats(a["rows"])
    pb, nb, gb = judge_stats(b["rows"])
    ca, cb = claim_totals(off), claim_totals(on)
    wa, wb = p["wins_a"], p["wins_b"]
    print(f"head to head: off wins {wa}, on wins {wb}, ties {p['ties']} (need on >= off + 4 = {wa + 4})")
    print(f"judge passes: off {pa}/{na}, on {pb}/{nb} (need on >= off); grounding {ga} -> {gb}")
    ur_ok = True
    if ca and cb and ca.get("unsupported_rate") is not None and cb.get("unsupported_rate") is not None:
        ur_ok = cb["unsupported_rate"] <= ca["unsupported_rate"]
        print(f"unsupported rate: off {ca['unsupported_rate']}, on {cb['unsupported_rate']}; citation precision on "
              f"{cb.get('citation_precision')}")
    else:
        print("claimcheck results missing: run `python claimcheck.py eval <file>` on both")
    ok = wb >= wa + 4 and pb >= pa and ur_ok and (ga is None or gb is None or gb >= ga - 1e-9)
    print(f"RULE RECEPTION (3 Nov): {'PASS' if ok else 'FAIL'} - also analytic `regression` (lose <= 1)")
    return ok


def cmd_shorts2(on, off):
    """SHORTS on if the shorts set passes on >= off, and every film the recommendation questions list is a short
    (its card has the "Format: short film" line). (+ tune / check `stage`, analytic `regression`.)"""
    p, n, _g = judge_stats(load(on)["rows"])
    po, no, _ = judge_stats(load(off)["rows"])
    cards = shorts = 0
    for r in load(on)["rows"]:
        if r.get("category") != "recommend":
            continue
        ctx = r.get("context") or ""
        if "relevant to the question:" in ctx:
            ctx = ctx.split("relevant to the question:", 1)[1]
        ctx = ctx.split("Structured data from Movie Buff", 1)[0]
        for c in [c for c in ctx.split("\n\n---\n\n") if c.strip()]:
            cards += 1
            shorts += "Format: short film" in c
    print(f"shorts set: on {p}/{n}, off {po}/{no} (need on >= off); recommendation cards that are shorts: "
          f"{shorts}/{cards} (need all)")
    ok = p >= po and cards > 0 and shorts == cards
    print(f"RULE SHORTS (3 Nov): {'PASS' if ok else 'FAIL'} - also tune / check `stage` and analytic `regression`")
    return ok


def cmd_recall2(off, on):
    """MEM_EMBED on if hit@1 is higher and false recall not higher on all 45, AND on the check half (chats c9-c15,
    not used to pick MEM_BACK_COS) refer-backs right not lower and unrelated recalls not higher."""
    import memory_eval as M
    a, b = load(off), load(on)
    sa, sb = M.split(a), M.split(b)
    print(f"all: hit@1 off {a['hit1']:.2f}, on {b['hit1']:.2f}; false recall off {a['false_recall']:.2f}, on "
          f"{b['false_recall']:.2f}")
    print(f"check half (c9-c15): right off {sa['B']['hit']}/{sa['B']['n']}, on {sb['B']['hit']}/{sb['B']['n']}; "
          f"unrelated recalled off {sa['B']['false']}/{sa['B']['n_un']}, on {sb['B']['false']}/{sb['B']['n_un']}")
    ok = (b["hit1"] > a["hit1"] and b["false_recall"] <= a["false_recall"]
          and sb["B"]["hit"] >= sa["B"]["hit"] and sb["B"]["false"] <= sa["B"]["false"])
    print(f"RULE recall (3 Nov): {'PASS -> MEM_EMBED=1' if ok else 'FAIL'}")
    return ok


# ---------------------------------------------------------------- 4 Nov rules (written before the 4 Nov re-tests)
def cmd_h2h(off, on, pair, margin=0):
    """Head to head on a set (pairwise.py, both orders): on wins >= off wins + margin, judge passes not lower,
    grounding not lower. Used for ISSUES (issues set, margin 3), YT_COMMENTS (reception set, margin 0) and PERF
    (success set, margin 2); each also needs its analytic `regression` (lose <= 1, grounding not lower)."""
    a, b, p = load(off), load(on), load(pair)
    pa, na, ga = judge_stats(a["rows"])
    pb, nb, gb = judge_stats(b["rows"])
    wa, wb = p["wins_a"], p["wins_b"]
    print(f"head to head: off wins {wa}, on wins {wb}, ties {p['ties']} (need on >= off + {margin} = {wa + margin})")
    print(f"judge passes: off {pa}/{na}, on {pb}/{nb} (need on >= off); grounding {ga} -> {gb}")
    ok = wb >= wa + margin and pb >= pa and (ga is None or gb is None or gb >= ga - 1e-9)
    print(f"RULE head to head (margin {margin}): {'PASS' if ok else 'FAIL'}")
    return ok


def cmd_notworse(off, on):
    """TV_REVIEWS on the TV set: passes not lower, lose <= 1, grounding not lower."""
    pa, _na, _ga = judge_stats(load(off)["rows"])
    pb, _nb, _gb = judge_stats(load(on)["rows"])
    ok1 = cmd_regression(off, on, max_lost=1)
    print(f"passes: off {pa}, on {pb} (need on >= off)")
    ok = ok1 and pb >= pa
    print(f"RULE not worse: {'PASS' if ok else 'FAIL'}")
    return ok


def main():
    a = sys.argv[1:]
    if not a:
        sys.exit(__doc__)
    cmd, rest = a[0], a[1:]
    ml = 1
    if "--max-lost" in rest:
        i = rest.index("--max-lost")
        ml = int(rest[i + 1])
        rest = rest[:i] + rest[i + 2:]
    if cmd == "reception":
        cmd_reception(*rest[:2])
    elif cmd == "regression":
        cmd_regression(*rest[:2], max_lost=ml)
    elif cmd == "stage":
        cmd_stage(*rest[:2], max_lost=ml)
    elif cmd == "tv":
        off = rest[rest.index("--off") + 1] if "--off" in rest else None
        cmd_tv(rest[0], off)
    elif cmd == "shorts":
        cmd_shorts(*rest[:2])
    elif cmd == "recall":
        cmd_recall(*rest[:2])
    elif cmd == "h2h":
        cmd_h2h(*rest[:3], margin=int(rest[3]) if len(rest) > 3 else 0)
    elif cmd == "notworse":
        cmd_notworse(*rest[:2])
    elif cmd == "reception2":
        cmd_reception2(*rest[:3])
    elif cmd == "shorts2":
        cmd_shorts2(*rest[:2])
    elif cmd == "recall2":
        cmd_recall2(*rest[:2])
    elif cmd == "attack":
        cmd_attack(rest[0])
    elif cmd == "blocks":
        cmd_blocks(rest[0])
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
