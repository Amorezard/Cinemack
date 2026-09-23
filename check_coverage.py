#!/usr/bin/env python3
"""
Validate the test set against the real movies.db BEFORE running the eval.

The test set was written against assumptions about what's in your database:
which movies cleared the vote_count filter, and which ones fall inside the
TMDB 5000 credits overlap that carries director/cast. This script checks
those assumptions so you find out here, in ten seconds, rather than
mistaking a bad test question for a model failure.

Usage:
    cd ~/movie-buff/rag
    python check_coverage.py                 # uses ./movies.db, ./test_set.json
    python check_coverage.py --db movies.db --tests test_set.json

No ollama, no GPU, no venv beyond stdlib. Safe to run any time.
"""

import argparse
import json
import sqlite3
import sys
import unicodedata


def normalize(s):
    """Loose title match: casefold, strip accents and non-alphanumerics."""
    if s is None:
        return ""
    s = unicodedata.normalize("NFKD", str(s))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return "".join(c for c in s.casefold() if c.isalnum())


def find_title(conn, title):
    """Return every row whose title matches loosely. Exact hits sort first."""
    target = normalize(title)
    rows = conn.execute(
        "SELECT id, title, release_year, director, top_cast, vote_count "
        "FROM movies WHERE title LIKE ? COLLATE NOCASE",
        (f"%{title.strip()}%",),
    ).fetchall()

    if not rows:
        # LIKE missed it (punctuation, accents). Fall back to a normalized scan.
        allrows = conn.execute(
            "SELECT id, title, release_year, director, top_cast, vote_count FROM movies"
        ).fetchall()
        rows = [r for r in allrows if normalize(r[1]) == target]

    rows.sort(key=lambda r: (normalize(r[1]) != target, -(r[5] or 0)))
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="movies.db")
    ap.add_argument("--tests", default="test_set.json")
    args = ap.parse_args()

    try:
        conn = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
        total = conn.execute("SELECT COUNT(*) FROM movies").fetchone()[0]
    except sqlite3.Error as e:
        print(f"Could not open {args.db}: {e}")
        print("Run this from ~/movie-buff/rag, where movies.db lives.")
        return 1

    with_credits = conn.execute(
        "SELECT COUNT(*) FROM movies WHERE director IS NOT NULL OR top_cast IS NOT NULL"
    ).fetchone()[0]

    print(f"Database: {args.db}")
    print(f"  {total:,} movies, {with_credits:,} with director/cast "
          f"({with_credits / total * 100:.1f}%)\n" if total else "\n")

    with open(args.tests) as f:
        tests = json.load(f)

    problems = []

    for q in tests["questions"]:
        expected = q.get("expect_retrieved") or q.get("expect_retrieved_final_turn") or []
        category = q["category"]

        # Questions with no expected title have nothing to verify here.
        if not expected:
            if category == "no_match":
                print(f"{q['id']}  {category:<16} (expects NO match - nothing to verify)")
            elif "expect_rows_match" in q:
                pred = q["expect_rows_match"]
                lo, hi = pred.get("release_year_between", [0, 9999])
                genre = pred.get("genres_contains", "")
                n = conn.execute(
                    "SELECT COUNT(*) FROM movies WHERE CAST(release_year AS INTEGER) "
                    "BETWEEN ? AND ? AND genres LIKE ?",
                    (lo, hi, f"%{genre}%"),
                ).fetchone()[0]
                flag = "OK" if n >= 10 else "THIN"
                print(f"{q['id']}  {category:<16} {n:,} candidate rows "
                      f"({genre}, {lo}-{hi})  [{flag}]")
                if n < 10:
                    problems.append(
                        f"{q['id']}: only {n} movies match {genre} {lo}-{hi}. "
                        f"Weak test - widen the range or pick another genre."
                    )
            else:
                print(f"{q['id']}  {category:<16} (no retrieval target)")
            continue

        for title in expected:
            rows = find_title(conn, title)
            if not rows:
                print(f"{q['id']}  {category:<16} MISSING: '{title}' is not in the database")
                problems.append(
                    f"{q['id']}: '{title}' absent. It didn't clear the vote_count "
                    f"filter, or the title differs. Swap in another film."
                )
                continue

            mid, found, year, director, cast, votes = rows[0]
            has_credits = bool(director or cast)
            extra = f"  (+{len(rows) - 1} other title matches)" if len(rows) > 1 else ""
            creditstr = "credits: YES" if has_credits else "credits: no"
            print(f"{q['id']}  {category:<16} '{found}' ({year})  "
                  f"{votes:,} votes  {creditstr}{extra}")

            # The two categories that depend on credits being present or absent.
            if category == "credits_present" and not has_credits:
                problems.append(
                    f"{q['id']}: '{found}' has NO credits data, but the question "
                    f"assumes it does. This test would fail for the wrong reason - "
                    f"replace it with a film that shows 'credits: YES' above."
                )
            if category == "credits_missing" and has_credits:
                problems.append(
                    f"{q['id']}: '{found}' DOES have credits data, but the question "
                    f"assumes it doesn't. Pick a more recent film instead - "
                    f"the credits file is pre-2017 only."
                )
            want_year = q.get("expect_year")
            if want_year:
                # The test set pins a year, so confirm that exact row exists -
                # otherwise every run grades this question WRONG_VERSION and
                # you spend an afternoon chasing a retrieval bug that isn't one.
                exact = [r for r in rows if str(r[2]) == str(want_year)]
                if not exact:
                    have = ", ".join(f"{r[1]} ({r[2]})" for r in rows[:4])
                    problems.append(
                        f"{q['id']}: no '{title}' row with year {want_year}. "
                        f"Database has: {have}. Fix expect_year in test_set.json."
                    )
                elif len(rows) > 1:
                    others = ", ".join(f"{r[1]} ({r[2]})" for r in rows[1:4])
                    print(f"     ^ {len(rows)} title matches ({others}) - "
                          f"pinned to {want_year}, handled")
            elif len(rows) > 1 and category in ("exact_title", "credits_missing"):
                others = ", ".join(f"{r[1]} ({r[2]})" for r in rows[1:4])
                problems.append(
                    f"{q['id']}: '{title}' matches {len(rows)} rows - {found} ({year}), "
                    f"{others}. Add an expect_year to disambiguate."
                )

    print()
    if problems:
        print(f"{len(problems)} thing(s) to fix before trusting a run:\n")
        for p in problems:
            print(f"  - {p}")
        print("\nFix these in test_set.json, then re-run this script.")
    else:
        print("All assumptions hold. The test set is valid against this database.")
        print("Next: python run_eval.py --label baseline-q8_0")

    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
