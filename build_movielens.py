"""
Change 18: "people who rated X highly also rated Y highly", from MovieLens.

    cd ~/movie-buff/rag && source ~/venvs/movie-rag/bin/activate
    python build_movielens.py              # downloads ml-latest-small (~1 MB), a minute on CPU
    python build_movielens.py --zip FILE   # use a zip you downloaded yourself

Uses the small MovieLens set (100,000 ratings from 610 people, 9,700 films).
The 32-million-rating set covers far more films; it's the next step, not
tonight's. Films are linked to movies.db through links.csv's TMDB ids.

For every film with at least MIN_RATINGS ratings: item-to-item cosine
similarity on ratings centred on each person's average, shrunk when few people
rated both films (score * n / (n + SHRINK)). Keeps each film's top 50 in a new
table, ml_neighbours(tmdb_id, neighbour_id, score, rank). No GPU; nothing else
in movies.db changes.

MovieLens data: F. Maxwell Harper and Joseph A. Konstan. 2015. The MovieLens
Datasets: History and Context. ACM TiiS 5(4). Used under the GroupLens license,
which requires this citation.
"""

import argparse
import csv
import io
import os
import sqlite3
import time
import urllib.request
import zipfile

import numpy as np

URL = "https://files.grouplens.org/datasets/movielens/ml-latest-small.zip"
MIN_RATINGS = 15
SHRINK = 10
TOP = 50

ap = argparse.ArgumentParser()
ap.add_argument("--zip")
ap.add_argument("--db", default="movies.db")
args = ap.parse_args()
t0 = time.time()

if args.zip:
    blob = open(args.zip, "rb").read()
else:
    os.makedirs("data/movielens", exist_ok=True)
    path = "data/movielens/ml-latest-small.zip"
    if not os.path.exists(path):
        print("downloading MovieLens (small) ...")
        req = urllib.request.Request(URL, headers={"User-Agent": "MovieBuff-student-research/0.1"})
        with urllib.request.urlopen(req, timeout=120) as r, open(path, "wb") as f:
            f.write(r.read())
    blob = open(path, "rb").read()
z = zipfile.ZipFile(io.BytesIO(blob))


def read(name):
    member = next(n for n in z.namelist() if n.endswith("/" + name) or n == name)
    return csv.DictReader(io.TextIOWrapper(z.open(member), encoding="utf-8"))


conn = sqlite3.connect(args.db)
in_db = {r[0] for r in conn.execute("SELECT id FROM movies")}
to_tmdb = {}
for row in read("links.csv"):
    if row.get("tmdbId", "").isdigit() and int(row["tmdbId"]) in in_db:
        to_tmdb[row["movieId"]] = int(row["tmdbId"])

ratings = [(r["userId"], to_tmdb[r["movieId"]], float(r["rating"]))
           for r in read("ratings.csv") if r["movieId"] in to_tmdb]
count = {}
for _, m, _ in ratings:
    count[m] = count.get(m, 0) + 1
films = sorted(m for m, n in count.items() if n >= MIN_RATINGS)
users = sorted({u for u, _, _ in ratings})
fi = {m: i for i, m in enumerate(films)}
ui = {u: i for i, u in enumerate(users)}

R = np.zeros((len(films), len(users)), dtype=np.float32)
seen = np.zeros_like(R)
user_sum, user_n = {}, {}
for u, m, r in ratings:
    user_sum[u] = user_sum.get(u, 0) + r
    user_n[u] = user_n.get(u, 0) + 1
for u, m, r in ratings:
    if m in fi:
        R[fi[m], ui[u]] = r - user_sum[u] / user_n[u]
        seen[fi[m], ui[u]] = 1

norms = np.linalg.norm(R, axis=1, keepdims=True)
norms[norms == 0] = 1
Rn = R / norms
sim = Rn @ Rn.T
overlap = seen @ seen.T
sim = sim * (overlap / (overlap + SHRINK))
np.fill_diagonal(sim, -1)

conn.execute("DROP TABLE IF EXISTS ml_neighbours")
conn.execute("CREATE TABLE ml_neighbours (tmdb_id INTEGER, neighbour_id INTEGER, "
             "score REAL, rank INTEGER)")
rows = []
for i, m in enumerate(films):
    order = np.argsort(-sim[i])[:TOP]
    rows += [(m, films[j], float(sim[i, j]), r + 1)
             for r, j in enumerate(order) if sim[i, j] > 0]
conn.executemany("INSERT INTO ml_neighbours VALUES (?, ?, ?, ?)", rows)
conn.execute("CREATE INDEX IF NOT EXISTS idx_ml_nb ON ml_neighbours(tmdb_id, rank)")
conn.commit()

print(f"{len(ratings):,} ratings on {len(to_tmdb):,} films in movies.db; "
      f"{len(films):,} films with >= {MIN_RATINGS} ratings got neighbours "
      f"({len(rows):,} rows) in {time.time() - t0:.0f}s")
for title in ("Inception", "Toy Story", "Heat"):
    row = conn.execute("SELECT id FROM movies WHERE title = ? ORDER BY vote_count DESC",
                       (title,)).fetchone()
    if row:
        nb = [t for (t,) in conn.execute(
            "SELECT m.title FROM ml_neighbours n JOIN movies m ON m.id = n.neighbour_id "
            "WHERE n.tmdb_id = ? ORDER BY n.rank LIMIT 5", (row[0],))]
        print(f"  people who liked {title} also liked: {', '.join(nb) or '(too few ratings)'}")
