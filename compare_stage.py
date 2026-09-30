"""Compare two stage_scores.py runs: passes, stage scores, speed, and which questions changed.

    python compare_stage.py results/stage_scores_..._v1base.json results/stage_scores_..._wiki.json
"""
import json
import sys


def load(p):
    d = json.load(open(p))
    return d, {r["id"]: r for r in d["rows"]}


def avg(rows, key):
    xs = [r["result"]["trace"].get(key) for r in rows if r["result"]["trace"].get(key) is not None]
    return sum(xs) / len(xs) if xs else 0


def main(a_path, b_path):
    a, ra = load(a_path)
    b, rb = load(b_path)
    print(f"A = {a_path}\nB = {b_path}\n")
    groups = list(dict.fromkeys(r["group"] for r in a["rows"]))
    print(f"{'group':10s} {'A pass':>8s} {'B pass':>8s}")
    for g in groups:
        pa = sum(r["result"]["pass"] for r in a["rows"] if r["group"] == g)
        pb = sum(r["result"]["pass"] for r in b["rows"] if r["group"] == g)
        n = sum(1 for r in a["rows"] if r["group"] == g)
        print(f"{g:10s} {pa:>5d}/{n:<3d}{pb:>5d}/{n:<3d}")
    print(f"\n{'stage':13s} {'A found':>8s} {'B found':>8s} {'A s@1':>7s} {'B s@1':>7s} {'A s@10':>7s} {'B s@10':>7s}")
    for s in ("pool", "rerank_in", "rerank_out", "after_cutoff", "final"):
        sa, sb = a["summary"].get(s), b["summary"].get(s)
        if sa and sb:
            print(f"{s:13s} {sa['found']:8.2f} {sb['found']:8.2f} {sa['s@1']:7.2f} {sb['s@1']:7.2f} "
                  f"{sa['s@10']:7.2f} {sb['s@10']:7.2f}")
    print(f"\nAverage ms   {'A':>8s} {'B':>8s}")
    for key in ("plan_ms", "search_ms", "rerank_ms", "total_ms"):
        print(f"{key:12s} {avg(a['rows'], key):8.0f} {avg(b['rows'], key):8.0f}")
    ca, cb = avg(a["rows"], "rerank_chars"), avg(b["rows"], "rerank_chars")
    if ca or cb:
        print(f"{'rerank_chars':12s} {ca:8.0f} {cb:8.0f}   (re-ranker prompt size; ~4 chars per token)")
    gained = [i for i in ra if i in rb and not ra[i]["result"]["pass"] and rb[i]["result"]["pass"]]
    lost = [i for i in ra if i in rb and ra[i]["result"]["pass"] and not rb[i]["result"]["pass"]]
    print(f"\nGained ({len(gained)}): {', '.join(gained) or '-'}")
    print(f"Lost   ({len(lost)}): {', '.join(lost) or '-'}")
    for i in lost:
        print(f"  {i}: {rb[i]['result']['lost_at'][:140]}")
    pool_a = [i for i in ra if isinstance(ra[i]["result"]["ranks"].get("pool"), int)]
    pool_b = [i for i in rb if isinstance(rb[i]["result"]["ranks"].get("pool"), int)]
    print(f"\nExpected film in the search pool: A {len(pool_a)}  B {len(pool_b)}  "
          f"(newly found by B: {len(set(pool_b) - set(pool_a))})")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(sys.argv[1], sys.argv[2])
