"""Simulate a confidence gate for the re-ranker from two saved stage_scores runs - no GPU.

    python gate_sim.py TUNE_WITH_RERANK TUNE_NO_RERANK [CHECK_WITH_RERANK CHECK_NO_RERANK]

Both runs of a pair must use the same questions and the same search settings, differing
only in RERANK. For every question where the re-ranker ran, the gate looks at the
no-re-ranker run's fused search list and decides "confident" when
  - the top film is in the top 3 of at least `agree` search channels
    (question vector, HyDE vector, TMDB keywords, Wikipedia vector, Wikipedia keywords,
     generated descriptions), and
  - its lead over #2 in distance to the question is at least `gap`.
Confident questions take the no-re-ranker result; the others keep the re-ranker's.
Rules are ranked on the first pair (tune); the best ones are then applied unchanged to the
second pair (check). A rule is only worth using if it holds up on both.
"""
import json
import sys

CHANNELS = ("pool_q", "pool_hyde", "pool_keyword", "pool_wiki", "pool_wiki_keyword", "pool_wiki_q")
AGREES = (1, 2, 3, 4)
GAPS = (None, 0.0, 0.01, 0.02, 0.03, 0.05, 0.08)


def load(p):
    d = json.load(open(p))
    return {r["id"]: r for r in d["rows"]}


def features(trace):
    pool = trace.get("pool") or []
    if not pool:
        return None
    top = pool[0][0]
    agree = sum(1 for c in CHANNELS if top in (trace.get(c) or [])[:3])
    d1 = pool[0][3]
    d2 = pool[1][3] if len(pool) > 1 else None
    gap = (d2 - d1) if isinstance(d1, float) and isinstance(d2, float) else 0.0
    return agree, gap


def simulate(with_rr, no_rr, agree, gap):
    """(passes, n, skipped, reranked, saved_ms) for one rule."""
    passes = skipped = reranked = 0
    saved = 0.0
    for qid, a in with_rr.items():
        b = no_rr.get(qid)
        ran = "rerank_ms" in a["result"]["trace"]
        if not ran or b is None:
            passes += a["result"]["pass"]
            continue
        reranked += 1
        f = features(b["result"]["trace"])
        confident = f is not None and f[0] >= agree and (gap is None or f[1] >= gap)
        if confident:
            skipped += 1
            saved += a["result"]["trace"].get("rerank_ms", 0)
            passes += b["result"]["pass"]
        else:
            passes += a["result"]["pass"]
    return passes, len(with_rr), skipped, reranked, saved


def base(rows):
    return sum(r["result"]["pass"] for r in rows.values())


def main(paths):
    pairs = [(load(paths[i]), load(paths[i + 1])) for i in range(0, len(paths) - 1, 2)]
    names = ["tune", "check"][:len(pairs)]
    for name, (w, n) in zip(names, pairs):
        print(f"{name}: always re-rank {base(w)}/{len(w)}, never re-rank {base(n)}/{len(n)}")
    results = []
    for ag in AGREES:
        for gp in GAPS:
            results.append((ag, gp, [simulate(w, n, ag, gp) for w, n in pairs]))
    w0 = base(pairs[0][0])
    # best first: most passes on tune, then most skipped
    results.sort(key=lambda r: (-r[2][0][0], -r[2][0][2]))
    print(f"\n{'agree':>5s} {'gap':>6s} | " + " | ".join(f"{nm + ' pass':>11s} {'skip':>9s}" for nm in names))
    for ag, gp, sims in results[:14]:
        cells = []
        for (p, tot, sk, rr, _ms) in sims:
            cells.append(f"{p:>5d}/{tot:<5d} {sk:>3d}/{rr:<4d}")
        print(f"{ag:>5d} {('-' if gp is None else f'{gp:.2f}'):>6s} | " + " | ".join(cells))
    ok = [r for r in results if r[2][0][0] >= w0 - 1]
    if ok:
        ag, gp, sims = max(ok, key=lambda r: r[2][0][2])
        print(f"\nMost skipping within 1 tune question of always re-ranking: agree >= {ag}, "
              f"gap >= {'-' if gp is None else gp}")
        for nm, (p, tot, sk, rr, ms) in zip(names, sims):
            print(f"  {nm}: {p}/{tot} passes, re-ranker skipped on {sk} of {rr} questions, "
                  f"~{ms / max(rr, 1) / 1000:.1f} s saved per re-ranked question on average")


if __name__ == "__main__":
    if len(sys.argv) not in (3, 5):
        sys.exit(__doc__)
    main(sys.argv[1:])
