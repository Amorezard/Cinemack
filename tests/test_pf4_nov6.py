"""6 Nov: news by source (news.py films-src) and the Letterboxd loader, on a tiny synthetic facts.db with fake
vectors (no Ollama, no network):  python -m unittest tests.test_pf4_nov6"""
import csv
import datetime
import io
import json
import os
import sqlite3
import sys
import tempfile
import unittest
import zipfile

import numpy as np

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp()
DB = os.path.join(TMP, "facts.db")
os.environ["FACTS_DB"] = DB
sys.path.insert(0, HERE)
import facts_db as F  # noqa: E402
import news as N  # noqa: E402
import letterboxd_data as L  # noqa: E402

RNG = np.random.default_rng(0)


def build():
    c = F.connect(DB)
    c.executescript(N.DDL)
    c.execute("create table if not exists money(tmdb_id integer primary key, budget real, gross real)")
    for i in range(1, 41):
        d = datetime.date(2000 + i % 10, 1 + i % 12, 15)
        c.execute("insert into films(tmdb_id, imdb_id, title, original_title, release_date, year, genres_json, overview, "
                  "vote_count) values (?,?,?,?,?,?,?,?,?)", (i, f"tt{1000000 + i}", f"Film {i}", f"Film {i}", d.isoformat(),
                                                           d.year, json.dumps(["Drama"]), "x", 100))
        c.execute("insert into money values (?,?,?)", (i, 1e7, 2e7))
    nid = 0
    for y in range(1999, 2011):
        for m in range(1, 13):
            for src in ("wikipedia", "nyt"):
                for k in range(12):
                    nid += 1
                    c.execute("insert into news_items(news_id, source, date, month, headline, key) values (?,?,?,?,?,?)",
                              (nid, src, f"{y}-{m:02d}-{k + 1:02d}", f"{y}-{m:02d}", f"{src} item {nid}", f"{src}|{nid}"))
                    v = RNG.normal(size=8).astype(np.float32)
                    c.execute("insert into news_vec values (?,?)", (nid, (v / np.linalg.norm(v)).tobytes()))
    c.commit()
    c.close()


build()
N._embed = lambda texts, prefix="": [list(RNG.normal(size=8)) for _ in texts]


class NewsBySource(unittest.TestCase):
    def test_one_row_per_film_and_source(self):
        N.cmd_films_src(type("A", (), {"limit": 0})())
        c = sqlite3.connect(DB)
        got = dict(c.execute("select source, count(resonance) from news_film_src group by source").fetchall())
        self.assertEqual(got, {"nyt": 40, "wikipedia": 40})
        N.cmd_films_src(type("A", (), {"limit": 0})())          # a second run has nothing left to do
        self.assertEqual(c.execute("select count(*) from news_film_src").fetchone()[0], 80)

    def test_source_window_only_sees_its_source(self):
        c = N.connect()
        rows, _m = N.Window(c, "nyt").range(datetime.date(2005, 1, 1), datetime.date(2005, 1, 31))
        self.assertTrue(rows and all(r[3] == "nyt" for r in rows))


class Letterboxd(unittest.TestCase):
    def test_kaggle_zip_ids_and_scale(self):
        zb = io.BytesIO()
        with zipfile.ZipFile(zb, "w") as z:
            s = io.StringIO()
            w = csv.writer(s)
            w.writerow(["_id", "movie_id", "rating_val", "user_id"])
            for fid in (1, 2, 3):
                for u in range(30):
                    w.writerow([f"{fid}-{u}", f"film-{fid}", 10 if fid == 1 else 4, f"u{u}"])
            z.writestr("ratings_export.csv", s.getvalue())
            s = io.StringIO()
            w = csv.writer(s)
            w.writerow(["_id", "display_name", "num_ratings_pages", "username"])
            z.writestr("users_export.csv", s.getvalue())
            s = io.StringIO()
            w = csv.writer(s)
            w.writerow(["movie_id", "movie_title", "imdb_id", "tmdb_id", "year_released"])
            w.writerow(["film-1", "Film 1", "", "1", "2001"])
            w.writerow(["film-2", "Film 2", "tt1000002", "", ""])
            w.writerow(["film-3", "Film 3", "", "", "2003"])
            z.writestr("movie_data.csv", s.getvalue())
        p = os.path.join(TMP, "lb.zip")
        open(p, "wb").write(zb.getvalue())
        L.cmd_load(type("A", (), {"kaggle": p, "hf": False, "file": None})())
        c = sqlite3.connect(DB)
        got = {f: (n, m, mt) for f, n, m, mt in c.execute("select tmdb_id, n, mean, match from letterboxd where source = 'kaggle'")}
        self.assertEqual(got[1], (30, 5.0, 1.0))
        self.assertEqual(got[2], (30, 2.0, 1.0))
        self.assertEqual(got[3][2], 1.0)                       # title + year

    def test_hf_title_only(self):
        p = os.path.join(TMP, "hf.csv")
        with open(p, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["user", "movie_id", "title", "rating"])
            for u in range(25):
                w.writerow([u, 7, "Film 7", 3.5])
                w.writerow([u, 999, "No Such Film", 4])
        L.cmd_load(type("A", (), {"kaggle": None, "hf": True, "file": p})())
        c = sqlite3.connect(DB)
        self.assertEqual(c.execute("select tmdb_id, n, mean, match from letterboxd where source = 'hf'").fetchall(),
                         [(7, 25, 3.5, 0.5)])


if __name__ == "__main__":
    unittest.main()
