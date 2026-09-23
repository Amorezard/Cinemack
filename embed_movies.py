import sqlite3
import sqlite_vec
import ollama
import time

DB_FILE = "movies.db"
EMBED_MODEL = "mxbai-embed-large"
EMBED_DIM = 1024
BATCH_SIZE = 64

conn = sqlite3.connect(DB_FILE)
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

rows = conn.execute("SELECT id, embed_text FROM movies").fetchall()
print(f"Embedding {len(rows):,} movies with {EMBED_MODEL} in batches of {BATCH_SIZE}...")

start = time.time()
done = 0

for i in range(0, len(rows), BATCH_SIZE):
    batch = rows[i:i + BATCH_SIZE]
    ids = [r[0] for r in batch]
    texts = [r[1] for r in batch]

    result = ollama.embed(model=EMBED_MODEL, input=texts)
    vectors = result["embeddings"]

    conn.executemany(
        "INSERT INTO movie_vectors (movie_id, embedding) VALUES (?, ?)",
        [
            (movie_id, sqlite_vec.serialize_float32(vec))
            for movie_id, vec in zip(ids, vectors)
        ],
    )
    conn.commit()

    done += len(batch)
    elapsed = time.time() - start
    rate = done / elapsed if elapsed > 0 else 0
    remaining = (len(rows) - done) / rate if rate > 0 else 0
    print(f"  {done:,}/{len(rows):,} embedded  ({rate:.1f}/sec, ~{remaining/60:.1f} min left)", end="\r")

print()
print(f"Done in {(time.time() - start)/60:.1f} minutes.")
conn.close()
