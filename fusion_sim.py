"""Find better weights for the search-list fusion (RRF_WEIGHTS) from saved runs - no GPU.

    python fusion_sim.py results/<tune run>.json results/<check run>.json
    python fusion_sim.py --best results/<tune run>.json results/<check run>.json   # just the weights

Uses each description question's saved search lists (top 50 of: question vectors incl.
Wikipedia passages = q, guessed plot = hyde, generated descriptions = wq, TMDB keywords =
kw, Wikipedia keywords = wkw), fuses them with every weight combination, and measures
where the expected film lands. Ranked on tune by top-3 then top-1; a combination is only
recommended if check does at least as well as plain RRF. Lists are cut at 50 in the saved
traces, so absolute numbers differ a little from a live run; comparisons are fair.
"""
import itertools
import json
import sys

CHANNELS = {"q": "pool_q", "hyde": "pool_hyde", "wq": "pool_wiki_q", "kw": "pool_keyword", "wkw": "pool_wiki_keyword"}
LEVELS = (0.0, 0.5, 1.0, 2.0)
KS = (20, 60)


def norm(s):
    return "".join(c for c in str(s).casefold() if c.isalnum())


def cases(path):
    """[(expected_id, {channel: ranking})] for description questions with an expected film."""
    out = []
    for r in json.load(open(path))["rows"]:
        tr = r["result"]["trace"]
        if not r["expect"] or "pool_q" not in tr:
            continue
        want, year = norm(r["expect"][0]), str(r.get("expect_year") or "")
        mid = None
        for item in tr.get("pool") or []:
            if norm(item[1]) == want and (not year or str(item[2] or "")[:4] == year):
                mid = item[0]
                break
        if mid is None:
            continue                    # never in the pool: no weighting can find it
        out.append((mid, {c: tr.get(key) or [] for c, key in CHANNELS.items()}))
    return out


def score(cs, weights, k):
    s1 = s3 = s10 = 0
    for mid, lists in cs:
        sc = {}
        for c, ranking in lists.items():
            w = weights.get(c, 1.0)
            if not w:
                continue
            for rank, m in enumerate(ranking, 1):
                sc[m] = sc.get(m, 0.0) + w / (k + rank)
        order = sorted(sc, key=lambda m: -sc[m])
        rank = order.index(mid) + 1 if mid in sc else 999
        s1 += rank <= 1
        s3 += rank <= 3
        s10 += rank <= 10
    return s1, s3, s10


def fmt(w):
    return ",".join(f"{c}={v:g}" for c, v in w.items())


def main(args):
    best_only = args[0] == "--best"
    if best_only:
        args = args[1:]
    tune, check = cases(args[0]), cases(args[1])
    base_w = {c: 1.0 for c in CHANNELS}
    bt, bc = score(tune, base_w, 60), score(check, base_w, 60)
    results = []
    for combo in itertools.product(LEVELS, repeat=len(CHANNELS)):
        if not any(combo):
            continue
        w = dict(zip(CHANNELS, combo))
        for k in KS:
            results.append((score(tune, w, k), score(check, w, k), w, k))
    results.sort(key=lambda r: (-r[0][1], -r[0][0], -r[0][2]))
    ok = [r for r in results if r[1][1] >= bc[1] and r[1][0] >= bc[0]]
    pick = ok[0] if ok else None
    if best_only:
        if pick and (pick[0][1], pick[0][0]) > (bt[1], bt[0]):
            print(fmt(pick[2]) + f";{pick[3]}")
        return
    print(f"description questions with the film in the pool: tune {len(tune)}, check {len(check)}")
    print(f"plain RRF (all 1, k=60): tune top1/top3/top10 {bt}, check {bc}\n")
    print(f"{'weights':38s} {'k':>3s} | {'tune 1/3/10':>12s} | {'check 1/3/10':>12s}")
    for t, c, w, k in results[:15]:
        print(f"{fmt(w):38s} {k:>3d} | {'/'.join(map(str, t)):>12s} | {'/'.join(map(str, c)):>12s}")
    if pick:
        print(f"\nRecommended (best on tune, check no worse than plain RRF): RRF_WEIGHTS={fmt(pick[2])} RRF_K={pick[3]}")
        print(f"  tune {pick[0]} vs {bt}, check {pick[1]} vs {bc}")
    else:
        print("\nNo combination beats plain RRF on tune without losing on check.")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    main(sys.argv[1:])
