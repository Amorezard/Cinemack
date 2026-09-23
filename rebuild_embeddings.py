#!/usr/bin/env python3
"""
Rebuild embed_text with the new credits, then re-embed every movie.

fetch_credits.py updated the director and top_cast COLUMNS, but retrieval
searches the VECTORS, which were built from the old embed_text. Until this
runs, none of those 26,000 newly-credited films are findable by cast or
director - the database knows, and the search does not.

Three changes to embed_text:

  1. The new credits, with character names. "Leonardo DiCaprio as Billy
     Costigan" rather than "Leonardo DiCaprio". This makes questions like
     "the one where Damon plays a mole in the Boston police" answerable,
     which they simply are not today.

  2. The ratings line is dropped. "Rating: 8.2/10 from 13792 votes" is
     tokens of pure noise in a vector whose job is semantic matching - the
     numbers carry no meaning that helps match a plot description. Pass
     --keep-ratings to leave it in, which is how you'd A/B this change on
     its own.

  3. Tagline and Keywords are preserved. build_db.py never stored them as
     columns - they exist only inside the old embed_text - so they are
     parsed back out rather than re-read from the 660MB CSV.

    cp movies.db movies.db.precredits     # do this first
    python rebuild_embeddings.py --dry-run     # show 3 before/after samples
    python rebuild_embeddings.py

Phase 1 is seconds. Phase 2 re-embeds ~30k rows; the embedder is 100% GPU
now (it was 97% CPU when this database was first built), so expect minutes
rather than the original run's much longer haul.
"""

import argparse
import sqlite3
import sys
import time

import ollama
import sqlite_vec

EMBED_MODEL = "mxbai-embed-large"
EMBED_DIM = 1024
BATCH = 64

# Lines in the old embed_text, in the order build_db.py wrote them.
PREFIXES = ("Genres:", "Director:", "Starring:", "Tagline:", "Rating:",
            "Keywords:", "Plot:", "Summary:")


def parse_old(text):
    """Pull the fields back out of an embed_text that build_db.py wrote."""
    out = {}
    if not text:
        return out
    current, buf = None, []
    for line in text.split("\n"):
        hit = next((p for p in PREFIXES if line.startswith(p)), None)
        if hit:
            if current:
                out[current] = "\n".join(buf).strip()
            current, buf = hit.rstrip(":").lower(), [line[len(hit):].strip()]
        elif current:
            buf.append(line)
    if current:
        out[current] = "\n".join(buf).strip()
    return out


from embed_layout import clean_keywords
import sqlite3 as _sqlite3
EMBED_MAX_CHARS = 1900   # ~475 tokens; mxbai-embed-large reads 512
_WIKI = None

def wiki_summary(mid):
    """Wikipedia lead summary for a movie, if fetch_wiki.py found one."""
    global _WIKI
    if _WIKI is None:
        c = _sqlite3.connect("movies.db")
        try:
            _WIKI = dict(c.execute("SELECT id, wiki_summary FROM movies WHERE wiki_summary IS NOT NULL"))
        except _sqlite3.OperationalError:
            _WIKI = {}
        c.close()
    return _WIKI.get(mid)


def build(row, keep_ratings):
    mid, title, year, genres, director, cast, overview, old, va, vc = row
    old_fields = parse_old(old)

    # Plot right after the title, then cleaned keywords: tested on q07 as the
    # layout that moves a plot question closest to the right film.
    parts = [f"{title} ({year or 'unknown year'})"]
    plot = overview or old_fields.get("plot", "")
    if plot:
        parts.append(f"Plot: {plot}")
    summary = wiki_summary(mid)
    if summary:
        parts.append(f"Summary: {summary}")
    keywords = clean_keywords(old_fields.get("keywords", ""))
    if keywords:
        parts.append(f"Keywords: {keywords}")
    if genres:
        parts.append(f"Genres: {genres}")
    if director:
        parts.append(f"Director: {director}")
    if cast:
        parts.append(f"Starring: {cast}")
    tagline = old_fields.get("tagline", "")
    if tagline:
        parts.append(f"Tagline: {tagline}")
    if keep_ratings and va is not None:
        parts.append(f"Rating: {va}/10 from {int(vc or 0)} votes")

    return "\n".join(parts)[:EMBED_MAX_CHARS]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="movies.db")
    ap.add_argument("--keep-ratings", action="store_true",
                    help="leave the Rating line in embed_text (for A/B)")
    ap.add_argument("--dry-run", action="store_true",
                    help="print 3 before/after samples and stop")
    ap.add_argument("--embed-only", action="store_true",
                    help="skip rebuilding embed_text, just re-embed")
    ap.add_argument("--batch", type=int, default=BATCH)
    args = ap.parse_args()

    conn = sqlite3.connect(args.db)

    # ---- phase 1: rebuild embed_text ---------------------------------
    if not args.embed_only:
        rows = conn.execute(
            "SELECT id, title, release_year, genres, director, top_cast, "
            "overview, embed_text, vote_average, vote_count FROM movies"
        ).fetchall()
        print(f"rebuilding embed_text for {len(rows):,} movies "
              f"(ratings line: {'kept' if args.keep_ratings else 'dropped'})")

        updates, changed = [], 0
        for r in rows:
            new = build(r, args.keep_ratings)
            if new != (r[7] or ""):
                changed += 1
            updates.append((new, r[0]))

        if args.dry_run:
            print(f"\n{changed:,} of {len(rows):,} rows would change. Samples:\n")
            for r in rows[:3]:
                print("=" * 62)
                print("BEFORE:\n" + (r[7] or ""))
                print("\nAFTER:\n" + build(r, args.keep_ratings) + "\n")
            print("Nothing written. Drop --dry-run to apply.")
            return 0

        conn.executemany("UPDATE movies SET embed_text = ? WHERE id = ?", updates)
        conn.commit()
        print(f"  {changed:,} rows changed\n")

    # ---- phase 2: re-embed -------------------------------------------
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)

    conn.execute("DROP TABLE IF EXISTS movie_vectors")
    conn.execute(f"""
        CREATE VIRTUAL TABLE movie_vectors USING vec0(
            movie_id INTEGER PRIMARY KEY,
            embedding FLOAT[{EMBED_DIM}]
        )
    """)
    conn.commit()

    rows = conn.execute("SELECT id, embed_text FROM movies "
                        "WHERE embed_text IS NOT NULL AND embed_text != ''").fetchall()
    print(f"embedding {len(rows):,} movies with {EMBED_MODEL}, "
          f"batches of {args.batch}...")

    start, done = time.time(), 0
    for i in range(0, len(rows), args.batch):
        batch = rows[i:i + args.batch]
        try:
            result = ollama.embed(model=EMBED_MODEL,
                                  input=[r[1] for r in batch],
                                  keep_alive="30m")
        except Exception as e:
            print(f"\n  batch at row {i} failed ({e}); retrying once...")
            time.sleep(3)
            result = ollama.embed(model=EMBED_MODEL,
                                  input=[r[1] for r in batch],
                                  keep_alive="30m")

        vectors = result["embeddings"] if isinstance(result, dict) else result.embeddings
        conn.executemany(
            "INSERT INTO movie_vectors (movie_id, embedding) VALUES (?, ?)",
            [(r[0], sqlite_vec.serialize_float32(v))
             for r, v in zip(batch, vectors)])
        conn.commit()

        done += len(batch)
        el = time.time() - start
        rate = done / el if el else 0
        eta = (len(rows) - done) / rate / 60 if rate else 0
        print(f"  {done:,}/{len(rows):,}  {rate:.0f}/sec  ~{eta:.1f} min left",
              end="\r", flush=True)

    n = conn.execute("SELECT COUNT(*) FROM movie_vectors").fetchone()[0]
    print(f"\n\n{'=' * 58}")
    print(f"  {n:,} vectors written in {(time.time() - start) / 60:.1f} min")
    print(f"{'=' * 58}")
    print("\n  Sanity check, then re-run the eval:")
    print('    python retrieve.py "the one where Matt Damon plays a mole in the Boston police"')
    print("    python run_eval.py --label q4-credits --no-think")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
