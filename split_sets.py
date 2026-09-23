"""
Change 2, part B: divide every test question into sets, by film.

    cd ~/movie-buff/rag && source ~/venvs/movie-rag/bin/activate
    python split_sets.py

Sets
  tune   what you look at and change code for
  check  run after a change to confirm it generalises; don't debug on it
  train  TREC train questions kept for Phase 5 training (never tested on)
  final  sealed: run once, at the very end, for the head-to-head

Rules (decided 23 Sep 2026)
  TREC dev (84 with film in movies.db)   2/3 tune, 1/3 check
  TREC train (88)                        1/2 train, 1/2 final (sealed)
  Your own questions already in use      3/4 tune, 1/4 check - they've all been
    (test_set.json, follow-ups f1-f4,    seen and tuned on, so none can be a
     fresh n1-n10)                        fair final question
  Your own NEW questions (added later)   60% tune, 20% check, 20% final

Every film gets a fixed bucket 0-99 (hash of its TMDB ID), so rerunning after
adding questions never moves an existing question to another set.
Leak check: a film in tune or check never appears in final or train, and a
film in final never appears in train; the final/train question is dropped.

Writes sets/tune.json, sets/check.json, sets/train.json, sets/final.json in
the test_set.json format (stage_scores.py --questions sets/tune.json), plus
sets/assignments.json (id -> set) and sets/own_seen.json (created on the first
run: the own questions that count as already seen).
"""

import hashlib
import json
import os
import sqlite3
import unicodedata

import test_understand as T

DB = "movies.db"
OUT = "sets"


def norm(s):
    s = unicodedata.normalize("NFKD", str(s or ""))
    return "".join(c for c in s.casefold() if c.isalnum() and not unicodedata.combining(c))


def bucket(key):
    return int(hashlib.sha1(str(key).encode()).hexdigest(), 16) % 100


def film_id(conn, title, year):
    """TMDB ID for an expected title (+year), best-known version first."""
    rows = conn.execute("SELECT id, title, release_year, vote_count FROM movies").fetchall() \
        if not hasattr(film_id, "_rows") else film_id._rows
    film_id._rows = rows
    nt = norm(title)
    hits = [r for r in rows if norm(r[1]) == nt]
    if year:
        hits = [r for r in hits if str(r[2] or "").startswith(str(year))] or hits
    return max(hits, key=lambda r: r[3] or 0)[0] if hits else None


def own_questions():
    d = json.load(open("test_set.json"))
    out = []
    for q in (d if isinstance(d, list) else d["questions"]):
        q = dict(q)
        q["source"] = "own"
        if q.get("expect_retrieved") and len(q["turns"]) == 1:
            q["retrieval_graded"] = True
        out.append(q)
    for f in T.FOLLOWUPS:
        out.append({"id": f["id"], "source": "own", "category": "followup",
                    "turns": f["turns"], "expect_retrieved": f["expect"],
                    "retrieval_graded": True})
    for f in T.FRESH:
        out.append({"id": f["id"], "source": "own", "category": "fresh",
                    "turns": [f["q"]], "expect_retrieved": f["expect"],
                    "retrieval_graded": True})
    return out


def main():
    os.makedirs(OUT, exist_ok=True)
    conn = sqlite3.connect(DB)

    own = own_questions()
    seen_path = os.path.join(OUT, "own_seen.json")
    if not os.path.exists(seen_path):
        json.dump(sorted(q["id"] for q in own), open(seen_path, "w"), indent=1)
        print(f"First run: {len(own)} own questions recorded as already seen ({seen_path}).")
    seen = set(json.load(open(seen_path)))

    trec = json.load(open("trec_tot_2023.json"))["questions"]
    for q in trec:
        q["retrieval_graded"] = True
        q["trec_split"] = q.pop("split")

    everything = []
    for q in own + trec:
        exp = q.get("expect_retrieved") or []
        if q["source"] == "trec":
            fid = q["expect_tmdb"]
        elif exp:
            fid = film_id(conn, exp[0], q.get("expect_year"))
        else:
            fid = None
        key = fid if fid is not None else f"q:{q['id']}"     # no film: split by question
        b = bucket(key)
        if q["source"] == "trec":
            s = ("tune" if b < 67 else "check") if q["trec_split"] == "dev" else \
                ("train" if b < 50 else "final")
        elif q["id"] in seen:
            s = "tune" if b < 75 else "check"
        else:
            s = "tune" if b < 60 else ("check" if b < 80 else "final")
        q.update({"film_key": str(key), "bucket": b, "split": s})
        everything.append(q)

    # Leak check, by film.
    films = {}
    for q in everything:
        if not q["film_key"].startswith("q:"):
            films.setdefault(q["film_key"], set()).add(q["split"])
    # Tune/check assignments never move (the own questions there are already
    # seen). A final or train question whose film is also in tune/check - or a
    # train question whose film is in final - is dropped instead.
    moved = []
    for q in everything:
        sets_for_film = films.get(q["film_key"], set())
        if q["split"] == "final" and sets_for_film & {"tune", "check"}:
            moved.append((q["id"], "final", "dropped (film is in tune/check)"))
            q["split"] = "dropped"
        elif q["split"] == "train" and sets_for_film & {"tune", "check", "final"}:
            moved.append((q["id"], "train", "dropped (film is in a test set)"))
            q["split"] = "dropped"

    for s in ("tune", "check", "train", "final"):
        qs = [q for q in everything if q["split"] == s]
        json.dump({"questions": qs}, open(os.path.join(OUT, f"{s}.json"), "w"),
                  indent=1, ensure_ascii=False)
    json.dump({q["id"]: q["split"] for q in everything},
              open(os.path.join(OUT, "assignments.json"), "w"), indent=1)

    print(f"\n{'set':7s} {'own':>5s} {'TREC':>5s} {'films':>6s}   (own = retrieval-graded / all)")
    for s in ("tune", "check", "train", "final"):
        qs = [q for q in everything if q["split"] == s]
        o = [q for q in qs if q["source"] == "own"]
        o_r = sum(1 for q in o if q.get("retrieval_graded"))
        t = sum(1 for q in qs if q["source"] == "trec")
        f = len({q["film_key"] for q in qs if not q["film_key"].startswith("q:")})
        print(f"{s:7s} {f'{o_r}/{len(o)}':>5s} {t:5d} {f:6d}")
    if moved:
        print(f"\nLeak check dropped {len(moved)} question(s):")
        for i, a, b in moved:
            print(f"  {i}: {a} -> {b}")
    else:
        print("\nLeak check: no film appears in two sets.")
    print(f"\nWrote {OUT}/tune.json, check.json, train.json, final.json.")
    print("Measure on:  python stage_scores.py --questions sets/tune.json --tag <name>")
    print("final.json stays sealed: stage_scores.py refuses it without --final.")


if __name__ == "__main__":
    main()
