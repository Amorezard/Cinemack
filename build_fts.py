"""
Change 11: build the keyword index that HYBRID search uses.

    cd ~/movie-buff/rag && source ~/venvs/movie-rag/bin/activate
    python build_fts.py            # about a minute; safe to rerun

Adds one table to movies.db, movies_fts (SQLite FTS5), holding each film's
title and its full record text (genres, director, cast, tagline, keywords,
plot). Nothing else in the database changes. rowid = the film's TMDB id.
Porter stemming, so "balloon" matches "balloons".
"""

import sqlite3
import sys
import time

DB = sys.argv[1] if len(sys.argv) > 1 else "movies.db"

conn = sqlite3.connect(DB)
t0 = time.time()
try:
    conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS movies_fts USING fts5("
                 "title, body, tokenize = 'porter unicode61')")
except sqlite3.OperationalError as e:
    sys.exit(f"This SQLite build has no FTS5 ({e}). Tell Claude; HYBRID=0 turns keyword search off.")
conn.execute("DELETE FROM movies_fts")
conn.execute("INSERT INTO movies_fts (rowid, title, body) "
             "SELECT id, title, COALESCE(embed_text, overview, '') FROM movies")
conn.commit()
n = conn.execute("SELECT count(*) FROM movies_fts").fetchone()[0]
test = conn.execute("SELECT m.title FROM movies_fts f JOIN movies m ON m.id = f.rowid "
                    "WHERE movies_fts MATCH ? ORDER BY bm25(movies_fts, 3.0, 1.0) LIMIT 3",
                    ('"balloons" OR "house" OR "old"',)).fetchall()
print(f"movies_fts: {n:,} films indexed in {time.time() - t0:.0f}s. "
      f"Test query 'balloons house old' -> {[t for (t,) in test]}")
