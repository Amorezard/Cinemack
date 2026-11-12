"""Round 39 (12 Nov): GENRE_MAIN variant 4 - the MovieLens tag genome as a third vote for broad genres.
No real data:  python -m unittest tests.test_r39_genres"""
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest

T = tempfile.mkdtemp()
os.environ["FACTS_DB"] = os.path.join(T, "facts.db")
os.environ["GENRE_VOTES_DB"] = os.path.join(T, "genre_votes.db")
os.environ["GENOME_NPZ"] = os.path.join(T, "genome.npz")
os.environ["GENRE_MAIN"] = "4"
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(HERE, "web"), HERE]

import numpy as np  # noqa: E402

TAGS = ["comedy", "drama", "thriller", "dark"]
FILMS = [(i, ["Comedy"], "Comedy", [0.8, 0.2, 0.1, 0.1]) for i in range(1, 41)]          # agreed comedies
FILMS += [(i, ["Thriller"], "Thriller", [0.1, 0.3, 0.7, 0.5]) for i in range(41, 81)]     # agreed thrillers
FILMS += [
    (101, ["Animation", "Adventure", "Family", "Comedy"], "Action,Adventure,Animation", [0.95, 0.1, 0.05, 0.1]),  # Zootopia-like
    (102, ["Comedy", "Drama", "Romance"], "Drama,Romance", [0.3, 0.9, 0.05, 0.2]),                               # Forrest Gump-like
    (103, ["Crime", "Mystery", "Thriller"], "Crime,Drama,Mystery", [0.05, 0.6, 0.9, 0.9]),                        # Se7en-like
    (104, ["Thriller", "Crime", "Comedy"], "Crime,Drama", [0.5, 0.4, 0.3, 0.8]),                                  # Pulp Fiction-like
    (105, ["Drama", "Comedy"], "Comedy", None),                                                                   # not in the genome
]
c = sqlite3.connect(os.environ["FACTS_DB"])
c.executescript("create table films(tmdb_id integer primary key, title text, year integer, genres_json text, imdb_id text, "
                "vote_count integer, in_catalog integer); create table imdb_titles(tconst text primary key, genres text);")
for fid, tg, ig, _r in FILMS:
    c.execute("insert into films values (?,?,2010,?,?,5000,1)", (fid, f"F{fid}", json.dumps(tg), f"tt{fid}"))
    c.execute("insert into imdb_titles values (?,?)", (f"tt{fid}", ig))
c.commit()
c.close()
g = [(fid, r) for fid, _t, _i, r in FILMS if r]
np.savez_compressed(os.environ["GENOME_NPZ"], tmdb_ids=np.array([x[0] for x in g], dtype=np.int64), tags=np.array(TAGS),
                    relevance=np.array([x[1] for x in g], dtype=np.float32))
BUILD = subprocess.run([sys.executable, os.path.join(HERE, "tools", "build_genre_votes.py")], capture_output=True, text=True,
                       env=dict(os.environ))
import genres  # noqa: E402


def main(fid, v="4"):
    tg, ig = next((t, i) for f, t, i, _r in FILMS if f == fid)
    gv = genres.genome_votes([fid]).get(fid)
    return genres.film_split(tg, ig, v, votes=gv, cuts=genres.genome_cuts())[0]


class GenomeVote(unittest.TestCase):
    def test_build_and_cuts(self):
        self.assertEqual(BUILD.returncode, 0, BUILD.stdout + BUILD.stderr)
        cuts = genres.genome_cuts()
        self.assertAlmostEqual(cuts["Comedy"], 0.8, places=3)
        self.assertAlmostEqual(cuts["Thriller"], 0.7, places=3)
        self.assertNotIn("Romance", cuts)                     # no genome tag for it in this fake genome

    def test_variant4_restores_strong_genres_only(self):
        self.assertIn("Comedy", main(101))                    # genome says strongly comedy
        self.assertNotIn("Comedy", main(101, "2"))
        self.assertNotIn("Comedy", main(102))                 # weak comedy vote: still out
        self.assertIn("Thriller", main(103))                  # strong thriller vote
        self.assertNotIn("Thriller", main(104))               # 0.3 < 0.7
        self.assertEqual(main(105), ["Comedy"])               # no genome row: as variant 2

    def test_lookup_uses_votes(self):
        g = genres.films([101, 102, 103])
        self.assertIn("Comedy", g[101][0])
        self.assertNotIn("Comedy", g[102][0])
        self.assertIn("Thriller", g[103][0])


if __name__ == "__main__":
    unittest.main()
