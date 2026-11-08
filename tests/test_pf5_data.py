"""8 Nov: pf5_data.py on a tiny synthetic facts.db, no network:  python -m unittest tests.test_pf5_data"""
import datetime
import os
import sqlite3
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp()
DB = os.path.join(TMP, "facts.db")
os.environ["FACTS_DB"] = DB
sys.path.insert(0, HERE)
import facts_db as F  # noqa: E402
import pf5_data as P  # noqa: E402


def build():
    c = F.connect(DB)
    c.execute("create table if not exists money(tmdb_id integer primary key, budget real, gross real)")
    c.execute("create table if not exists news_items(news_id integer primary key, source text, date text, month text, "
              "headline text, abstract text, section text, url text, key text unique, fetched_at text)")
    films = [(1, "Big Film", "2017-06-01"), (2, "Old Film", "2010-03-01"), (3, "No Money", "2017-01-01"),
             (4, "Early Film", "1995-05-05")]
    for fid, t, d in films:
        c.execute("insert into films(tmdb_id, title, original_title, release_date, us_release_date, year) values (?,?,?,?,?,?)",
                  (fid, t, t, d, d, int(d[:4])))
        if fid != 3:
            c.execute("insert into money values (?,?,?)", (fid, 1e7, 3e7))
    people = [(10, "Ana Star"), (11, "Bo Director"), (12, "Cher"), (13, "Same Name"), (14, "Same Name")]
    c.executemany("insert into people(person_id, name) values (?,?)", people)
    c.executemany("insert into credits values (?,?,?,?,?,?,?)", [
        (1, 10, "cast", None, None, None, 0), (1, 11, "crew", "Director", "Directing", None, None),
        (1, 12, "cast", None, None, None, 1), (1, 13, "cast", None, None, None, 2),
        (1, 99, "cast", None, None, None, 7), (2, 10, "cast", None, None, None, 0)])
    heads = [("2017-05-20", "Ana Star arrested after party"), ("2017-05-25", "Ana Star wins Golden Globe"),
             ("2017-06-02", "Ana Star after release"), ("2016-01-10", "Ana Star at a gala"),
             ("2015-06-01", "Bo Director plans new film"), ("2017-05-01", "Cher sings"),
             ("2017-05-03", "Same Name in the news"), ("2009-12-25", "Ana Star holiday")]
    for i, (d, h) in enumerate(heads):
        c.execute("insert into news_items(news_id, source, date, month, headline, key) values (?,?,?,?,?,?)",
                  (i + 1, "nyt", d, d[:7], h, f"k{i}"))
    c.commit()
    c.close()


build()


class People(unittest.TestCase):
    def test_counts_before_release_only(self):
        P.cmd_people(None)
        c = sqlite3.connect(DB)
        r = dict(((pid, role), (n, b, s, w)) for _f, pid, role, n, b, s, w, _v in
                 c.execute("select * from people_news where tmdb_id = 1"))
        self.assertEqual(r[(10, "cast")][0], 2)                 # two in the 90 days before; the day after is out
        self.assertEqual(r[(10, "cast")][2:], (1, 1))           # one scandal, one award headline
        self.assertGreater(r[(10, "cast")][1], 0)               # 2016 gala counts in the 3-year base
        self.assertEqual(r[(11, "director")][0], 0)
        self.assertNotIn((12, "cast"), r)                       # one-word name skipped
        self.assertNotIn((13, "cast"), r)                       # name shared by two people skipped
        self.assertNotIn((99, "cast"), r)                       # billed 8th: not top 5
        f = c.execute("select pre_total, scandal, award from film_people_news where tmdb_id = 1").fetchone()
        self.assertEqual(f, (2, 1, 1))
        self.assertIsNone(c.execute("select 1 from film_people_news where tmdb_id = 3").fetchone())   # no money


class Press(unittest.TestCase):
    def test_window_order_and_cap(self):
        seen = []

        def fake(url, label):
            seen.append(url)
            return {"response": {"total": 3, "results": [
                {"id": "film/2017/may/01/a", "webPublicationDate": "2017-05-01T10:00:00Z", "webTitle": "Big Film trailer",
                 "sectionId": "film", "fields": {"trailText": "<p>First look</p>"}},
                {"id": "film/2017/jun/01/b", "webPublicationDate": "2017-06-01T10:00:00Z", "webTitle": "Big Film review",
                 "sectionId": "film"}]}}
        P.api = fake
        os.environ["GUARDIAN_API_KEY"] = "test"
        P.cmd_press(type("A", (), {"max": 1, "retry_bad": False})())
        self.assertEqual(len(seen), 1)                          # cap respected
        self.assertIn("from-date=2017-02-01", seen[0])           # 120 days before 1 June 2017
        self.assertIn("to-date=2017-05-31", seen[0])
        self.assertIn("section=film", seen[0])
        c = sqlite3.connect(DB)
        self.assertEqual(c.execute("select tmdb_id, n_total from film_press_done").fetchall(), [(1, 3)])  # test year first
        self.assertEqual([r[0] for r in c.execute("select headline from film_press")], ["Big Film trailer"])  # release day dropped
        P.cmd_press(type("A", (), {"max": 5, "retry_bad": False})())
        done = {r[0] for r in c.execute("select tmdb_id from film_press_done")}
        self.assertEqual(done, {1, 2})                          # 1995 is before the Guardian archive, not asked

    def test_bad_query_skips_quota_stops(self):
        c = sqlite3.connect(DB)
        c.execute("delete from film_press_done"); c.execute("delete from film_press"); c.commit()
        os.environ["GUARDIAN_API_KEY"] = "test"
        answers = iter([("bad", 400, "bad title"), ("quota", 429, "limit")])
        P.api = lambda url, label: next(answers)
        P.cmd_press(type("A", (), {"max": 10, "retry_bad": False})())
        got = dict(c.execute("select tmdb_id, status from film_press_done").fetchall())
        self.assertEqual(got, {1: "bad_query"})                # 400 skips that film; 429 stops the run, film 2 untouched
        c.execute("delete from film_press_done"); c.commit()

    def test_phrase(self):
        self.assertEqual(P.phrase('Fast & "Furious" (2009)'), '"Fast and Furious 2009"')
        self.assertEqual(P.phrase("Mission: Impossible \u2013 Dead Reckoning Part One"), '"Mission Impossible Dead Reckoning Part One"')
        self.assertEqual(P.phrase("Love AND Other Drugs?!"), '"Love and Other Drugs"')
        self.assertEqual(P.phrase("Am\u00e9lie"), '"Am\u00e9lie"')


class Macro(unittest.TestCase):
    def test_series_stored(self):
        P.api = lambda url, label: {"observations": [{"date": "2017-01-01", "value": "243.6"}, {"date": "2017-02-01", "value": "."}]}
        os.environ["FRED_API_KEY"] = "test"
        P.cmd_macro(None)
        c = sqlite3.connect(DB)
        self.assertEqual(c.execute("select count(*) from macro").fetchone()[0], 3)   # 3 series x 1 valid month


if __name__ == "__main__":
    unittest.main()
