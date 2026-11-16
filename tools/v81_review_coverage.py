#!/usr/bin/env python3
"""v8.1 round 46 (16 Nov 2026): which films have review material, from which source, and where more could be had.

Read-only counts on the facts.db copy, so the next round fetches where it helps instead of guessing:
  per source   films with at least one item: Wikipedia critic statements (with / without a named outlet), Wikipedia
               reception text, Guardian reviews, NYT reviews, OMDb scores, TMDB audience reviews, YouTube review /
               essay videos (first-pass confident matches)
  by audience  the same shares for films with >= 5,000 votes, 1,000-4,999, 300-999 and fewer than 300
  gaps         a. films (>= 300 votes) whose Wikipedia reception was never requested          -> fetch target
               b. films with reception text but no critic statement extracted                 -> extractor target
               c. Guardian and NYT reviews fetched but matched to no film                     -> matcher target
               d. films (>= 300 votes) with an IMDb id and no OMDb row                         -> OMDb daily batch
No rule: this is an inventory. Nothing is fetched and nothing is written except the two result files.
    python tools/v81_review_coverage.py [--db facts.db]
"""
import argparse
import json
import os
import sqlite3
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUCKETS = [("5,000+ votes", 5000, 10 ** 12), ("1,000-4,999", 1000, 4999), ("300-999", 300, 999), ("under 300", 0, 299)]
SOURCES = [
    ("Wikipedia critic statements", "select distinct tmdb_id from critic_statements where tmdb_id is not null"),
    ("  ... with a named outlet", "select distinct tmdb_id from critic_statements where tmdb_id is not null and coalesce(outlet, '') <> ''"),
    ("Wikipedia reception text", "select distinct tmdb_id from wiki_reception where tmdb_id is not null"),
    ("Guardian review", "select distinct tmdb_id from critic_reviews where source = 'guardian' and tmdb_id is not null"),
    ("NYT review", "select distinct tmdb_id from critic_reviews where source = 'nyt' and tmdb_id is not null"),
    ("OMDb scores (RT or Metacritic)", "select distinct tmdb_id from omdb where tmdb_id is not null and (rt_pct is not null or mc_score is not null)"),
    ("TMDB audience reviews", "select distinct tmdb_id from reviews where tmdb_id is not null"),
    ("YouTube review / essay video", "select distinct tmdb_id from yt_analysis_film where score >= 0.6"),
]


def ids(c, sql):
    """The set of film ids a query returns, or None when its table does not exist."""
    try:
        return {r[0] for r in c.execute(sql)}
    except sqlite3.Error:
        return None


def one(c, sql, args=()):
    try:
        return c.execute(sql, args).fetchone()[0]
    except sqlite3.Error:
        return None


def inventory(c):
    votes = dict(c.execute("select tmdb_id, coalesce(vote_count, 0) from films"))
    groups = {name: {f for f, v in votes.items() if lo <= v <= hi} for name, lo, hi in BUCKETS}
    res = {"films": len(votes), "buckets": {k: len(v) for k, v in groups.items()}, "sources": {}, "gaps": {}}
    have = {}
    for name, sql in SOURCES:
        s = ids(c, sql)
        have[name] = s
        if s is None:
            res["sources"][name] = None
            continue
        s &= set(votes)
        res["sources"][name] = {"films": len(s), "by_bucket": {k: len(s & g) for k, g in groups.items()}}
    known = set().union(*[s for n, s in have.items() if s and not n.startswith("  ")]) & set(votes) if any(have.values()) else set()
    res["any_review_material"] = {"films": len(known), "by_bucket": {k: len(known & g) for k, g in groups.items()}}
    big = {f for f, v in votes.items() if v >= 300}
    asked = ids(c, "select tmdb_id from wiki_reception_done where tmdb_id is not null")
    if asked is not None:
        res["gaps"]["a_reception_never_requested"] = len(big - asked)
    rec, st = have.get("Wikipedia reception text"), have.get("Wikipedia critic statements")
    if rec is not None and st is not None:
        res["gaps"]["b_reception_text_but_no_statement"] = len((rec & set(votes)) - st)
    for src in ("guardian", "nyt"):
        n = one(c, "select count(*) from critic_reviews where source = ? and tmdb_id is null", (src,))
        tot = one(c, "select count(*) from critic_reviews where source = ?", (src,))
        if n is not None:
            res["gaps"][f"c_{src}_reviews_unmatched"] = {"unmatched": n, "fetched": tot}
    om = ids(c, "select tmdb_id from omdb where tmdb_id is not null")
    with_imdb = ids(c, "select tmdb_id from films where coalesce(imdb_id, '') <> ''")
    if om is not None and with_imdb is not None:
        res["gaps"]["d_no_omdb_row"] = len((big & with_imdb) - om)
    return res


def report(res, db):
    pct = lambda n, d: f"{n:,} ({n / d:.0%})" if d else f"{n:,}"
    names = [b[0] for b in BUCKETS]
    L = [f"Review material per film (v8.1 round 46), {res['films']:,} films in {db}", "",
         f"{'source':34s} {'all films':>15s} " + " ".join(f"{n:>17s}" for n in names),
         f"{'films in the group':34s} {res['films']:>15,d} " + " ".join(f"{res['buckets'][n]:>17,d}" for n in names)]
    for name, v in list(res["sources"].items()) + [("ANY of the sources above", res["any_review_material"])]:
        if v is None:
            L.append(f"{name:34s} table not in this database")
            continue
        L.append(f"{name:34s} {pct(v['films'], res['films']):>15s} " + " ".join(f"{pct(v['by_bucket'][n], res['buckets'][n]):>17s}" for n in names))
    g = res["gaps"]
    L += ["", "where more could be had:"]
    if "a_reception_never_requested" in g:
        L.append(f"  a. films with 300+ votes whose Wikipedia reception was never requested: {g['a_reception_never_requested']:,}")
    if "b_reception_text_but_no_statement" in g:
        L.append(f"  b. films with reception text but no critic statement extracted: {g['b_reception_text_but_no_statement']:,}")
    for src in ("guardian", "nyt"):
        k = f"c_{src}_reviews_unmatched"
        if k in g:
            L.append(f"  c. {src} reviews matched to no film: {g[k]['unmatched']:,} of {g[k]['fetched']:,} fetched")
    if "d_no_omdb_row" in g:
        L.append(f"  d. films with 300+ votes and an IMDb id but no OMDb row: {g['d_no_omdb_row']:,} (OMDb allows 1,000 requests a day)")
    L.append("Inventory only: nothing was fetched or changed.")
    return "\n".join(L) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=os.environ.get("FACTS_DB", os.path.join(HERE, "facts.db")))
    ap.add_argument("--out", default=os.path.join(HERE, "results", "v81_review_coverage"))
    a = ap.parse_args(argv)
    c = sqlite3.connect(f"file:{a.db}?mode=ro", uri=True)
    res = inventory(c)
    text = report(res, a.db)
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    with open(a.out + ".txt", "w") as fh:
        fh.write(text)
    with open(a.out + ".json", "w") as fh:
        json.dump(res, fh, indent=1)
    print(text, end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
