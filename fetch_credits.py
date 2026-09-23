#!/usr/bin/env python3
"""
Fill in cast and director for every movie in movies.db from the TMDB API.

Today 4,259 of 30,856 movies (13.8%) have credits - only the slice that
happened to overlap the old TMDB 5000 credits file, which is pre-2017. That
is why "who starred in Dune (2021)?" correctly answers "I don't have that".
This closes the gap.

It also stores CHARACTER names, not just actor names. The current data is
"Leonardo DiCaprio, Matt Damon" with no roles, which is what sent the model
guessing - it tried to supply character names from memory, couldn't resolve
them, and narrated the confusion into the answer. After this it will read
"Leonardo DiCaprio as Billy Costigan", and there is nothing left to guess.

Safe to stop and restart: a credits_source column marks finished rows, so a
re-run picks up exactly where it left off.

    pip install requests
    export TMDB_API_KEY=your_key_here
    python fetch_credits.py                  # all rows
    python fetch_credits.py --only-missing   # only rows with no credits yet
    python fetch_credits.py --limit 50       # smoke test first

Roughly 30k requests. At the default 8 workers expect 25-40 minutes.
Afterwards run rebuild_embeddings.py - embed_text contains the credits, so
the vectors are stale until it is rebuilt.
"""

import argparse
import re
import os
import sqlite3
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

try:
    import requests
except ImportError:
    sys.exit("pip install requests")

API = "https://api.themoviedb.org/3/movie/{id}/credits"
TOP_CAST = 5
DIRECTOR_JOBS = {"Director"}

_local = threading.local()
_lock = threading.Lock()
_stats = {"ok": 0, "missing": 0, "error": 0, "done": 0}


def session():
    """One HTTP session per worker thread - connection reuse matters a lot
    across 30,000 requests."""
    if not hasattr(_local, "s"):
        _local.s = requests.Session()
    return _local.s


def fetch(movie_id, api_key, retries=4):
    for attempt in range(retries):
        try:
            r = session().get(API.format(id=movie_id),
                              params={"api_key": api_key}, timeout=15)
        except requests.RequestException:
            time.sleep(1.5 * (attempt + 1))
            continue

        if r.status_code == 200:
            return r.json()
        if r.status_code == 404:
            return None                      # film no longer in TMDB
        if r.status_code == 401:
            raise SystemExit("401 from TMDB - check TMDB_API_KEY")
        if r.status_code == 429:             # rate limited, back off
            wait = float(r.headers.get("Retry-After", 2))
            time.sleep(wait + 0.5)
            continue
        time.sleep(1.0 * (attempt + 1))      # 5xx
    return None


def parse(payload):
    """-> (director, 'Actor as Character, ...') or (None, None)."""
    if not payload:
        return None, None

    directors = [c.get("name") for c in payload.get("crew", [])
                 if c.get("job") in DIRECTOR_JOBS and c.get("name")]
    director = ", ".join(dict.fromkeys(directors)) or None

    cast = sorted(payload.get("cast", []), key=lambda c: c.get("order", 9999))
    parts = []
    for c in cast[:TOP_CAST]:
        name = c.get("name")
        if not name:
            continue
        parts.append(f"{name} as {role}" if (role := clean_role(c.get("character")))
                     else name)
    return director, (", ".join(parts) or None)


# TMDB roles carry production annotations in a few shapes: "(uncredited)"
# standing alone, "Billy Costigan (uncredited)" trailing a real name, and
# "(voice)". Strip the annotation, keep whatever real role is left.
_ANNOTATION = re.compile(
    r"\(\s*(uncredited|voice|archive footage|cameo|credit only|as \w+)[^)]*\)",
    re.I)
_NOT_A_ROLE = {"self", "himself", "herself", "themselves", ""}


def clean_role(raw):
    role = _ANNOTATION.sub("", raw or "").strip(" -–—/,")
    return "" if role.casefold() in _NOT_A_ROLE else role


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="movies.db")
    ap.add_argument("--workers", type=int, default=8,
                    help="parallel requests; lower this if you see 429s")
    ap.add_argument("--only-missing", action="store_true",
                    help="skip rows that already have any credits at all")
    ap.add_argument("--limit", type=int, default=None, help="smoke test")
    args = ap.parse_args()

    api_key = os.environ.get("TMDB_API_KEY")
    if not api_key:
        sys.exit("Set TMDB_API_KEY first:  export TMDB_API_KEY=...")

    conn = sqlite3.connect(args.db)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(movies)")}
    if "credits_source" not in cols:
        conn.execute("ALTER TABLE movies ADD COLUMN credits_source TEXT")
        conn.commit()
        print("added credits_source column")

    sql = ("SELECT id FROM movies WHERE (credits_source IS NULL "
           "OR credits_source != 'tmdb_api')")
    if args.only_missing:
        sql += " AND director IS NULL AND top_cast IS NULL"
    sql += " ORDER BY vote_count DESC"        # best-known films first
    if args.limit:
        sql += f" LIMIT {args.limit}"

    ids = [r[0] for r in conn.execute(sql)]
    total_rows = conn.execute("SELECT COUNT(*) FROM movies").fetchone()[0]
    already = conn.execute(
        "SELECT COUNT(*) FROM movies WHERE credits_source = 'tmdb_api'"
    ).fetchone()[0]

    print(f"{total_rows:,} movies in {args.db}; {already:,} already fetched")
    print(f"fetching {len(ids):,} with {args.workers} workers "
          f"(most-voted first, so a partial run still covers what people ask about)\n")
    if not ids:
        print("nothing to do.")
        return 0

    start = time.time()
    pending = []

    def work(mid):
        director, cast = parse(fetch(mid, api_key))
        with _lock:
            _stats["done"] += 1
            if director or cast:
                _stats["ok"] += 1
            else:
                _stats["missing"] += 1
            pending.append((director, cast, mid))
            n = _stats["done"]
            if n % 25 == 0 or n == len(ids):
                el = time.time() - start
                rate = n / el if el else 0
                eta = (len(ids) - n) / rate / 60 if rate else 0
                print(f"  {n:,}/{len(ids):,}  ok={_stats['ok']:,} "
                      f"none={_stats['missing']:,}  {rate:.1f}/s  "
                      f"~{eta:.0f} min left", end="\r", flush=True)

    try:
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            for i in range(0, len(ids), 500):
                batch = ids[i:i + 500]
                list(pool.map(work, batch))
                # Commit in blocks so a kill -9 costs at most 500 lookups.
                with _lock:
                    rows, pending[:] = list(pending), []
                conn.executemany(
                    "UPDATE movies SET director = COALESCE(?, director), "
                    "top_cast = COALESCE(?, top_cast), "
                    "credits_source = 'tmdb_api' WHERE id = ?", rows)
                conn.commit()
    except KeyboardInterrupt:
        print("\n\ninterrupted - committing what we have")
        with _lock:
            rows, pending[:] = list(pending), []
        if rows:
            conn.executemany(
                "UPDATE movies SET director = COALESCE(?, director), "
                "top_cast = COALESCE(?, top_cast), "
                "credits_source = 'tmdb_api' WHERE id = ?", rows)
            conn.commit()
        print("re-run the same command to resume.")

    have = conn.execute(
        "SELECT COUNT(*) FROM movies WHERE director IS NOT NULL "
        "OR top_cast IS NOT NULL").fetchone()[0]
    print(f"\n\n{'=' * 58}")
    print(f"  {have:,} / {total_rows:,} movies now have credits "
          f"({have / total_rows * 100:.1f}%)")
    print(f"  took {(time.time() - start) / 60:.1f} min")
    print(f"{'=' * 58}")
    print("\n  embed_text still holds the OLD credits - the vectors are stale.")
    print("  Next: python rebuild_embeddings.py")

    for t, d, c in conn.execute(
            "SELECT title, director, top_cast FROM movies "
            "WHERE credits_source='tmdb_api' AND top_cast IS NOT NULL "
            "ORDER BY vote_count DESC LIMIT 3"):
        print(f"\n  {t}\n    dir: {d}\n    cast: {c}")

    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
