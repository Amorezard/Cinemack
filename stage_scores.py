"""
Change 1 of the Improvement Plan: stage-by-stage search scores.

For every question, records where the expected film ranks at each stage of
retrieve() and, when it goes missing, which stage lost it.

    cd ~/movie-buff/rag && source ~/venvs/movie-rag/bin/activate
    python stage_scores.py                 # all questions, LLM plan on (~5 min)
    python stage_scores.py --fresh         # only the fresh n1-n10
    python stage_scores.py --old           # old regex path, for comparison
    python stage_scores.py --only q38,n10  # just these ids
    python stage_scores.py --tag capped    # name this run: ..._capped.json
    python stage_scores.py --questions trec_tot_2023.json --split dev   # TREC
    python stage_scores.py --questions trec_tot_2023.json --split dev --limit 5
    python stage_scores.py --report results/<file>.json   # re-read a saved run

Stages, in pipeline order:
  pool          vector search (full record + plot-only), closest first.
                20 films normally, 400 when there's a filter or a recommendation.
  subject       the film the question names, after the database checks
  guesses       the model's title guesses that passed the distance check
  rerank_in     the candidates shown to the LLM re-ranker (descriptions only)
  rerank_out    the re-ranker's picks, best first (at most 3)
  after_cutoff  after year/genre filters and the 0.90 distance cutoff
  final         what the answer model receives (top 3, or 10 for recommendations)

Scores per stage, over questions that expect a film and where that stage ran:
  found  the film is anywhere in the stage's list
  s@1, s@3, s@10  the film is in the top 1 / 3 / 10
  MRR    mean of 1/rank (0 when missing)
A question expecting several films counts the worst rank among them.
Honesty cases (expect nothing) are reported separately.

The answer stage (does the answer text name the film?) comes with change 3,
since it needs answers generated and graded.

Uses the same questions and pass rule as test_understand.py.
Writes results/stage_scores_<date>[_old][_only][_<tag>].json, so partial
and tagged runs never overwrite a full run.
"""

import contextlib
import datetime
import io
import json
import os
import sys

import retrieve as R
import test_understand as T

STAGES = ["pool", "subject", "guesses", "rerank_in", "rerank_out", "after_cutoff", "final"]
SCORED = ["pool", "rerank_in", "rerank_out", "after_cutoff", "final"]


def rank_of(stage_list, expect, expect_year):
    """1-based rank of the expected film(s) in a stage's list; worst rank when
    several are expected; None if any is missing."""
    if not stage_list or not expect:          # honesty cases have no rank
        return None
    worst = 0
    for e in expect:
        ne = T.norm(e)
        r = next((i for i, row in enumerate(stage_list, 1)
                  if T.norm(row[1]) == ne
                  and (not expect_year or str(row[2] or "")[:4] == str(expect_year))),
                 None)
        if r is None:
            return None
        worst = max(worst, r)
    return worst


def traced(question, recent):
    trace = {}
    with contextlib.redirect_stdout(io.StringIO()):
        hits = R.retrieve(question, k=3, recent_titles=recent, trace=trace)
    return [h["title"] for h in hits], trace


def run_case(turns, expect, expect_year):
    recent = None
    for q in turns[:-1]:                      # earlier turns set the conversation
        titles, _ = traced(q, recent)
        recent = titles or recent
    titles, trace = traced(turns[-1], recent)
    trace["filters"] = R.parse_filters(turns[-1])     # the question's year/genre filter
    ranks = {s: (rank_of(trace[s], expect, expect_year) if s in trace else "n/a")
             for s in STAGES}
    years = [str(r[2] or "")[:4] for r in trace.get("final", [])]
    passed = T.graded(titles, years, expect, expect_year)
    return {"pass": passed, "hits": titles, "ranks": ranks,
            "lost_at": None if passed else diagnose(trace, ranks, expect, expect_year),
            "trace": trace}


def fmt(row):
    d = row[3]
    return f"{row[1]} ({str(row[2] or '')[:4]}{', %.3f' % d if isinstance(d, float) else ''})"


def diagnose(trace, ranks, expect, expect_year):
    """Which stage lost the film, and why: "<cause>: <detail>". The cause part
    is a fixed phrase, so failures can be counted by cause."""
    filt = trace.get("filters") or {}
    film = f"film is {expect_year or '?'}"
    if not expect:
        top = trace.get("final", [])
        why = f"named {trace['subject'][0][1]}" if trace.get("subject") else "from the vector pool"
        return f"honesty - returned films: {len(top)} ({why}): " + \
               "; ".join(fmt(r) for r in top[:3])

    subj = trace.get("subject") or []
    if subj and ranks["subject"] is None:
        return f"locked onto the wrong named film: {fmt(subj[0])}"

    found_early = [s for s in ("pool", "subject", "guesses", "rerank_in")
                   if isinstance(ranks.get(s), int)]
    if not found_early:
        return (f"never found (not in the pool): {trace.get('pool_size', '?')}-film pool, "
                f"not named, not among the accepted guesses")

    p_rank = ranks.get("pool")
    if "rerank_out" in trace:                 # the re-ranker ran
        r_in = ranks.get("rerank_in")
        if not isinstance(r_in, int):
            if filt and isinstance(p_rank, int) and not R.passes(
                    {"release_year": expect_year, "genres": _genres(trace, expect)}, filt):
                return f"excluded by the question's year/genre filter: pool #{p_rank}, {filt}, {film}"
            if isinstance(p_rank, int) and p_rank > R.RERANK_POOL:
                return (f"in the pool but beyond what the re-ranker sees: pool #{p_rank}, "
                        f"re-ranker shown the closest {len(trace.get('rerank_in', []))}")
            return f"in the pool but not shown to the re-ranker (no plot text?): pool #{p_rank}"
        if not isinstance(ranks.get("rerank_out"), int):
            n = len(trace.get("rerank_in", []))
            picks = ", ".join(fmt(r) for r in trace["rerank_out"]) or "nothing"
            return f"re-ranker saw it but chose others: #{r_in} of {n}, picked {picks}"

    if not isinstance(ranks.get("after_cutoff"), int):
        where = f"pool #{p_rank}" if isinstance(p_rank, int) else "earlier"
        row = next((r for r in trace.get("pool", [])
                    if any(T.norm(r[1]) == T.norm(e) for e in expect)), None)
        dist = f" at distance {row[3]:.3f}" if row and isinstance(row[3], float) else ""
        if filt:
            return f"removed by the year/genre filter or the 0.90 cutoff: {where}{dist}, {filt}, {film}"
        return f"removed by the 0.90 distance cutoff: {where}{dist}"

    return (f"ranked too low to be sent: #{ranks['after_cutoff']} after the cutoff, only "
            f"{len(trace.get('final', []))} sent (or merged as a same-title version)")


_GENRES = {}


def _genres(trace, expect):
    """Genres of the expected film, from movies.db (for the filter check)."""
    key = tuple(expect)
    if key not in _GENRES:
        try:
            conn = R.open_db("movies.db")
            row = conn.execute("SELECT genres FROM movies WHERE title = ? ORDER BY vote_count DESC",
                               (expect[0],)).fetchone()
            conn.close()
            _GENRES[key] = row[0] if row else ""
        except Exception:
            _GENRES[key] = ""
    return _GENRES[key]


def score(rows, stage):
    ran = [r for r in rows if r["expect"] and r["result"]["ranks"][stage] != "n/a"]
    if not ran:
        return None
    ranks = [r["result"]["ranks"][stage] for r in ran]
    n = len(ranks)
    hit = lambda k: sum(1 for x in ranks if x and x <= k) / n
    return {"n": n, "found": sum(1 for x in ranks if x) / n,
            "s@1": hit(1), "s@3": hit(3), "s@10": hit(10),
            "mrr": sum(1 / x for x in ranks if x) / n}


def report(rows):
    """Stage table, pass counts, timing and failures by cause. Returns the table."""
    print("\n" + "-" * 72)
    print(f"{'stage':13s} {'n':>4s} {'found':>6s} {'s@1':>6s} {'s@3':>6s} {'s@10':>6s} {'MRR':>6s}")
    summary = {}
    for s in SCORED:
        sc = score(rows, s)
        if not sc:
            continue
        summary[s] = sc
        print(f"{s:13s} {sc['n']:4d} {sc['found']:6.2f} {sc['s@1']:6.2f} "
              f"{sc['s@3']:6.2f} {sc['s@10']:6.2f} {sc['mrr']:6.2f}")

    print()
    for group in dict.fromkeys(r["group"] for r in rows):
        g = [r for r in rows if r["group"] == group]
        if g:
            print(f"{group:10s} pass {sum(r['result']['pass'] for r in g)}/{len(g)}")
    honest = [r for r in rows if not r["expect"]]
    if honest:
        print(f"Honesty    pass {sum(r['result']['pass'] for r in honest)}/{len(honest)}")

    tr = [r["result"]["trace"] for r in rows]
    avg = lambda key: sum(t.get(key, 0) for t in tr) / len(tr)
    rr = [t["rerank_ms"] for t in tr if "rerank_ms" in t]
    sizes = [len(t["rerank_in"]) for t in tr if "rerank_in" in t]
    print(f"\nAverage ms: plan {avg('plan_ms'):.0f}  search {avg('search_ms'):.0f}  "
          f"re-rank {sum(rr) / len(rr) if rr else 0:.0f} (when it ran)  total {avg('total_ms'):.0f}")
    if sizes:
        print(f"Re-ranker candidates: mean {sum(sizes) / len(sizes):.0f}, max {max(sizes)}")

    lost = {}
    for r in rows:
        if r["result"]["lost_at"]:
            key = r["result"]["lost_at"].split(":")[0]
            lost.setdefault(key, []).append(r["id"])
    if lost:
        print("\nFailures by cause:")
        for k, ids in sorted(lost.items(), key=lambda x: -len(x[1])):
            print(f"  {len(ids):2d}  {k}: {', '.join(ids)}")
    pool_ranks = [r["result"]["ranks"].get("pool") for r in rows
                  if r["result"]["lost_at"] and isinstance(r["result"]["ranks"].get("pool"), int)]
    if pool_ranks:
        print(f"\nPool ranks of failed films that were in the pool: {sorted(pool_ranks)}")
    return summary


def rescore(path):
    """Re-read a saved run and redo the diagnosis and report - no retrieval."""
    d = json.load(open(path))
    rows = d["rows"]
    for r in rows:
        tr = r["result"]["trace"]
        ex, ey = r["expect"], r.get("expect_year")
        ranks = {s: (rank_of(tr[s], ex, ey) if s in tr else "n/a") for s in STAGES}
        r["result"]["ranks"] = ranks
        r["result"]["lost_at"] = None if r["result"]["pass"] else diagnose(tr, ranks, ex, ey)
    print(f"{path}: {len(rows)} questions\n")
    for r in rows:
        if r["result"]["lost_at"]:
            print(f"{r['id']:14s} {r['result']['lost_at'][:150]}")
    report(rows)


def main():
    args = sys.argv[1:]
    if "--report" in args:
        return rescore(args[args.index("--report") + 1])
    fresh_only = "--fresh" in args
    old = "--old" in args
    only = None
    if "--only" in args:
        only = set(args[args.index("--only") + 1].split(","))
    tag = args[args.index("--tag") + 1] if "--tag" in args else ""
    qfile = args[args.index("--questions") + 1] if "--questions" in args else None
    split = args[args.index("--split") + 1] if "--split" in args else None
    limit = int(args[args.index("--limit") + 1]) if "--limit" in args else None
    R.USE_QUERY_LLM = not old

    if qfile:
        # Another question file in the test_set.json format, e.g. trec_tot_2023.json.
        d = json.load(open(qfile))
        todo = [((q.get("source", "file") + "-" + q.get("split", "")).upper().strip("-"),
                 q["id"], q["turns"], q.get("expect_retrieved") or [], q.get("expect_year"))
                for q in (d if isinstance(d, list) else d["questions"])
                if not split or q.get("split") == split]
    else:
        todo = T.cases(fresh_only)
    if only:
        todo = [c for c in todo if c[1] in only]
    if limit:
        todo = todo[:limit]
    print(f"{len(todo)} questions, {'old regex path' if old else 'LLM plan'}.\n")

    with contextlib.redirect_stdout(io.StringIO()):     # load models before timing
        R.retrieve("warm up", k=3)

    rows = []
    for group, qid, turns, expect, ey in todo:
        res = run_case(turns, expect, ey)
        rows.append({"group": group, "id": qid, "question": turns[-1],
                     "expect": expect, "expect_year": ey, "result": res})
        rk = res["ranks"]
        cells = "  ".join(f"{s}={rk[s] if rk[s] is not None else '-'}" for s in SCORED
                          if rk[s] != "n/a") if expect else \
            f"returned {len(res['trace'].get('final', []))} film(s)"
        print(f"{group:9s} {qid:4s} {'PASS' if res['pass'] else 'FAIL'}  {cells}")
        if not res["pass"]:
            print(f"      Q:       {turns[-1][:90]}")
            print(f"      expect:  {expect or 'nothing (honesty case)'}")
            print(f"      got:     {', '.join(res['hits'])[:90] or '(none)'}")
            print(f"      LOST AT: {res['lost_at']}")

    summary = report(rows)

    os.makedirs("results", exist_ok=True)
    out = (f"results/stage_scores_{datetime.date.today()}{'_old' if old else ''}"
           f"{'_' + os.path.splitext(os.path.basename(qfile))[0] if qfile else ''}"
           f"{'_' + split if split else ''}"
           f"{'_only' if only or limit else ''}{'_' + tag if tag else ''}.json")
    with open(out, "w") as f:
        json.dump({"summary": summary, "path": "old" if old else "llm_plan", "rows": rows},
                  f, indent=1, default=str)
    print(f"\nFull results: {out}")


if __name__ == "__main__":
    main()
