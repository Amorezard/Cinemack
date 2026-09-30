"""Why did a film enter or leave the search pool between two stage_scores runs?

    python diff_pool.py results/<run A>.json results/<run B>.json

For each question whose expected film is in one run's pool but not the other's, prints
the film's rank in every search channel of both runs, whether the planner's intent and
guessed plot (HyDE) were the same, and the pool size. The channel whose rank moved is
the one that changed the pool.
"""
import json
import sys

CHANNELS = ("pool_q", "pool_hyde", "pool_keyword", "pool_wiki", "pool_wiki_keyword", "pool_wiki_q")


def load(p):
    return {r["id"]: r for r in json.load(open(p))["rows"]}


def expected_id(row):
    """The expected film's id, from whichever stage list holds it."""
    tr = row["result"]["trace"]
    for key in ("pool", "rerank_in", "after_cutoff", "final", "guesses"):
        for item in tr.get(key) or []:
            if row["expect"] and item[1].casefold() == row["expect"][0].casefold():
                return item[0]
    return None


def rank(lst, mid):
    try:
        return lst.index(mid) + 1
    except (ValueError, AttributeError):
        return "-"


def main(a_path, b_path):
    A, B = load(a_path), load(b_path)
    shown = 0
    for qid in A:
        if qid not in B or not A[qid]["expect"]:
            continue
        ra, rb = A[qid]["result"]["ranks"].get("pool"), B[qid]["result"]["ranks"].get("pool")
        if isinstance(ra, int) == isinstance(rb, int):
            continue
        shown += 1
        mid = expected_id(A[qid]) or expected_id(B[qid])
        ta, tb = A[qid]["result"]["trace"], B[qid]["result"]["trace"]
        print(f"\n{qid}  expect {A[qid]['expect'][0]} (id {mid})   pool rank A {ra}  B {rb}")
        print(f"  path A {ta.get('path')}  B {tb.get('path')}   pool size A {ta.get('pool_size')}  B {tb.get('pool_size')}"
              f"   soft filters A {ta.get('soft_filters')}  B {tb.get('soft_filters')}")
        print(f"  guessed plot same: {ta.get('guessed_plot') == tb.get('guessed_plot')}"
              f"   (A {len(ta.get('guessed_plot') or '')} chars, B {len(tb.get('guessed_plot') or '')} chars)")
        if mid is not None:
            for c in CHANNELS:
                if c in ta or c in tb:
                    print(f"  {c:18s} A {str(rank(ta.get(c), mid)):>4s}   B {str(rank(tb.get(c), mid)):>4s}"
                          f"   (list sizes {len(ta.get(c) or [])} / {len(tb.get(c) or [])})")
    print(f"\n{shown} questions changed pool membership.")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(sys.argv[1], sys.argv[2])
