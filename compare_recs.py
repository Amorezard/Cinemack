"""
Change 18, the blind test: current recommendations vs. current + MovieLens.

    python compare_recs.py                 # writes results/recs_blind.txt + results/recs_key.json
    python compare_recs.py --score A,B,B,A,...   # after you've picked, one letter per question

For each "something like X" question, the current system's top 5 and a version
that merges in MovieLens neighbours of X (reciprocal rank fusion) are printed
as List A / List B in random order. Pick the better list for each question
without knowing which is which, then --score reveals which system won.
"""

import contextlib
import io
import json
import random
import sqlite3
import sys

import retrieve as R

QUESTIONS = [
    "Something like Inception but a bit lighter",
    "I loved Heat. What should I watch next?",
    "Movies like The Grand Budapest Hotel",
    "Something similar to Get Out",
    "If I liked Toy Story, what else would I like?",
    "Films like Mad Max: Fury Road",
    "Something like The Notebook",
    "I just watched Parasite and loved it. What next?",
    "Recommend something like The Matrix",
    "Anything similar to Superbad?",
]
SHOW = 5


def subject_of(conn, q):
    plan = R.understand(q)
    for f in (plan or {}).get("films", []):
        n = R.lookup_named(conn, f, q, None)
        if n:
            return n
    return None


def with_movielens(conn, base_ids, subject_id):
    nb = [r[0] for r in conn.execute(
        "SELECT neighbour_id FROM ml_neighbours WHERE tmdb_id = ? ORDER BY rank LIMIT 50",
        (subject_id,))]
    nb = [m for m in nb if m != subject_id]
    if not nb:
        return None
    return [m for m in R.rrf(base_ids, nb) if m != subject_id]


def titles(conn, ids):
    out = []
    for m in ids[:SHOW]:
        t, y = conn.execute("SELECT title, release_year FROM movies WHERE id = ?", (m,)).fetchone()
        out.append(f"{t} ({(y or '')[:4]})")
    return out


def main():
    if "--score" in sys.argv:
        picks = sys.argv[sys.argv.index("--score") + 1].upper().split(",")
        key = json.load(open("results/recs_key.json"))
        ml = sum(1 for p, k in zip(picks, key) if k.get(p) == "movielens")
        cur = sum(1 for p, k in zip(picks, key) if k.get(p) == "current")
        print(f"MovieLens version preferred on {ml}, current on {cur} (of {len(key)} questions).")
        return
    random.seed(24)
    conn = sqlite3.connect("movies.db")
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'ml_neighbours'").fetchone():
        sys.exit("Run python build_movielens.py first.")
    lines, key = [], []
    for q in QUESTIONS:
        with contextlib.redirect_stdout(io.StringIO()):
            base = [h["id"] for h in R.retrieve(q, k=R.RECOMMEND_K)]
        subj = subject_of(conn, q)
        if not subj or not base:
            continue
        ml = with_movielens(conn, base, subj["id"])
        if not ml or ml[:SHOW] == base[:SHOW]:
            continue
        pair = [("current", titles(conn, base)), ("movielens", titles(conn, ml))]
        random.shuffle(pair)
        key.append({"A": pair[0][0], "B": pair[1][0], "question": q})
        lines += [f"Q{len(key)}. {q}",
                  "   List A: " + "; ".join(pair[0][1]),
                  "   List B: " + "; ".join(pair[1][1]), ""]
    lines.append("Pick the better list for each question, then run:")
    lines.append("   python compare_recs.py --score " + ",".join("A" for _ in key) + "   (your letters)")
    open("results/recs_blind.txt", "w").write("\n".join(lines) + "\n")
    json.dump(key, open("results/recs_key.json", "w"), indent=1)
    print("\n".join(lines))


if __name__ == "__main__":
    main()
