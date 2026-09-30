"""Keyword index over the Wikipedia text, for WIKI_KW in retrieve.py.

    cd ~/movie-buff/rag && source ~/venvs/movie-rag/bin/activate
    python wiki_fts.py                 # about a minute; safe to rerun

Adds one table to wiki.db, wiki_fts (SQLite FTS5): each linked film's title and its
Wikipedia intro + plot. rowid = the film's TMDB id (same as movies_fts). Porter
stemming, so "lighthouse keepers" matches "lighthouse keeper".
movies.db is not touched.
"""
import json
import sqlite3
import sys
import time

DB = sys.argv[1] if len(sys.argv) > 1 else "wiki.db"

conn = sqlite3.connect(DB)
t0 = time.time()
conn.execute("DROP TABLE IF EXISTS wiki_fts")
conn.execute("CREATE VIRTUAL TABLE wiki_fts USING fts5(title, body, tokenize = 'porter unicode61')")
rows = []
for film_id, title, sections in conn.execute("SELECT film_id, title, sections_json FROM pages"):
    s = json.loads(sections or "{}")
    body = " ".join(x for x in (s.get("abstract", ""), s.get("plot", "")) if x)
    if body:
        rows.append((int(film_id), title, body))
conn.executemany("INSERT OR REPLACE INTO wiki_fts (rowid, title, body) VALUES (?, ?, ?)", rows)
conn.commit()
test = conn.execute("SELECT title FROM wiki_fts WHERE wiki_fts MATCH ? "
                    "ORDER BY bm25(wiki_fts, 3.0, 1.0) LIMIT 3",
                    ('"lighthouse" OR "keepers" OR "island" OR "mad"',)).fetchall()
print(f"wiki_fts: {len(rows):,} films indexed in {time.time() - t0:.0f}s. "
      f"Test 'lighthouse keepers island mad' -> {[t for (t,) in test]}")
