"""E11: keyword index over single Wikipedia passages, for DETAILS (dkw list) in retrieve.py.

wiki_fts (wiki_fts.py) indexes each film's whole intro + plot as one document, so the words of
one remembered detail can match anywhere in the article. This index has one row per passage
(the same plot and intro chunks the vector search uses), so a detail's words have to occur
together in one passage. It goes in its own file, wiki_pfts.db, next to wiki.db: wiki.db is
only read, and the vector cache stays valid.

    python wiki_pfts.py              # about a minute; safe to rerun (rebuilds the file)
"""
import os
import sqlite3
import sys
import time

WIKI = sys.argv[1] if len(sys.argv) > 1 else "wiki.db"
OUT = os.path.join(os.path.dirname(os.path.abspath(WIKI)), "wiki_pfts.db")

t0 = time.time()
tmp = OUT + ".new"
if os.path.exists(tmp):
    os.remove(tmp)
src = sqlite3.connect(f"file:{WIKI}?mode=ro", uri=True)
dst = sqlite3.connect(tmp)
dst.execute("CREATE VIRTUAL TABLE pfts USING fts5(body, film_id UNINDEXED, kind UNINDEXED, "
            "tokenize = 'porter unicode61')")
rows = src.execute("SELECT chunk_id, text, film_id, kind FROM chunks WHERE kind IN ('plot', 'lead')")
n = 0
while True:
    batch = rows.fetchmany(20000)
    if not batch:
        break
    dst.executemany("INSERT INTO pfts (rowid, body, film_id, kind) VALUES (?, ?, ?, ?)",
                    [(c, t or "", int(f), k) for c, t, f, k in batch])
    n += len(batch)
dst.commit()
test = dst.execute("SELECT film_id, substr(body, 1, 60) FROM pfts WHERE pfts MATCH ? ORDER BY bm25(pfts) LIMIT 3",
                   ('"lighthouse" OR "storm" OR "dog"',)).fetchall()
dst.close()
os.replace(tmp, OUT)
print(f"wiki_pfts.db: {n:,} passages indexed in {time.time() - t0:.0f} s. "
      f"Test 'lighthouse storm dog' -> {test}")
