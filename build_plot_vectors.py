"""Plot-only vectors (title + TMDB plot), searched alongside the full record."""
import json, struct, time, urllib.request
import retrieve as R

def embed_batch(texts):
    req = urllib.request.Request("http://localhost:11434/api/embed",
        data=json.dumps({"model": "mxbai-embed-large", "input": texts}).encode(),
        headers={"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=300))["embeddings"]

conn = R.open_db("movies.db")
items = [(mid, f"{t} ({(y or '')[:4] or 'unknown year'})\nPlot: {o}")
         for mid, t, y, o in conn.execute("SELECT id, title, release_year, overview FROM movies")
         if o]
conn.execute("DROP TABLE IF EXISTS movie_plot_vectors")
conn.execute("CREATE VIRTUAL TABLE movie_plot_vectors USING vec0("
             "movie_id INTEGER PRIMARY KEY, embedding FLOAT[1024])")
t0 = time.time()
for i in range(0, len(items), 64):
    chunk = items[i:i + 64]
    vecs = embed_batch([t for _, t in chunk])
    conn.executemany("INSERT INTO movie_plot_vectors(movie_id, embedding) VALUES (?, ?)",
                     [(m, struct.pack(f"{len(v)}f", *v)) for (m, _), v in zip(chunk, vecs)])
    conn.commit()
    done = i + len(chunk); rate = done / max(time.time() - t0, 1e-6)
    print(f"\r  {done:,}/{len(items):,}  {rate:.0f}/sec  ~{(len(items)-done)/rate/60:.1f} min left", end="", flush=True)
print("\ndone")
