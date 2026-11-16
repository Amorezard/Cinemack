"""v8.1 slice 5.2 (15 Nov 2026): the one ranking interface (web/recs.py) and the single-weight-set rule
(tools/v81_recs_single.py), on small made-up databases. No user data, no model, no MovieLens file."""
import importlib
import json
import os
import sqlite3
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "web"))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import personal  # noqa: E402
import query_plan as QP  # noqa: E402
import recs as R  # noqa: E402

# id, year, runtime, genres, votes, US cert, director id, keywords
FILMS = [
    (1, 2010, 110, ["Crime", "Thriller"], 9000, "R", 501, ["heist"]),            # liked
    (2, 2012, 105, ["Crime", "Thriller"], 800, "R", 501, ["heist"]),             # same director + neighbour of 1
    (3, 2014, 100, ["Crime", "Drama"], 700, "PG-13", 502, ["heist"]),            # neighbour of 1
    (4, 2016, 95, ["Comedy"], 20000, "PG", 503, ["wedding"]),                    # popular, unrelated
    (5, 2018, 190, ["Crime", "Thriller"], 600, "R", 501, ["heist"]),             # same director, too long for a 150 limit
    (6, 2019, 98, ["Horror"], 5000, "R", 504, ["haunting"]),                     # disliked genre
    (7, 2020, 102, ["Crime", "Thriller"], 400, None, 505, ["heist"]),            # no US rating
    (8, 2021, 99, ["Animation", "Family"], 15000, "G", 506, ["toys"]),
    (9, 2005, 120, ["Horror"], 300, "R", 504, ["haunting"]),                     # disliked
    (10, 2022, 101, ["Drama"], 12000, "PG-13", 507, ["family"]),
    (11, 2023, 97, ["Crime", "Thriller"], 350, "R", 508, ["heist"]),             # strongest neighbour of 1, watched
]
NEIGHBOURS = [(1, 11, 0.95), (1, 2, 0.90), (1, 3, 0.70), (1, 5, 0.65), (1, 7, 0.40)]


def plan(**f):
    base = {"year_min": None, "year_max": None, "runtime_min_minutes": None, "runtime_max_minutes": None, "genres_any": [],
            "genres_all": [], "genres_exclude": [], "watch_region": None, "watch_provider_ids_any": [], "watch_monetization": [],
            "exclude_watched": False, "include_media_ids": [], "exclude_media_ids": []}
    media = f.pop("media", ["movie"])
    base.update(f)
    return QP.parse({"schema_version": "1.1", "intent": "recommend", "media_types": media, "query_text": "",
                     "entity": {"internal_media_id": None, "tmdb_id": None, "imdb_id": None, "candidate_title": None},
                     "filters": base, "retrieval": {"routes": ["sql", "recommendations"], "article_mode": "none", "max_candidates": 200},
                     "response": {"language": "en", "include_explanations": True, "include_sources": False}})


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        c = self.c = sqlite3.connect(os.path.join(self.tmp.name, "facts.db"))
        c.execute("create table films(tmdb_id integer primary key, title text, year integer, runtime integer, genres_json text, vote_count integer)")
        c.execute("create table certifications(tmdb_id integer, region text, certification text, release_type integer, release_date text)")
        c.execute("create table watch_providers(tmdb_id integer, region text, kind text, provider_id integer, provider_name text, priority integer, fetched_at text)")
        c.execute("create table keywords(tmdb_id integer, keyword_id integer, name text)")
        c.execute("create table people(person_id integer primary key, name text)")
        c.execute("create table credits(tmdb_id integer, person_id integer, kind text, job text, ord integer)")
        for fid, y, rt, g, v, cert, d, kws in FILMS:
            c.execute("insert into films values (?,?,?,?,?,?)", (fid, "Film %d" % fid, y, rt, json.dumps(g), v))
            if cert:
                c.execute("insert into certifications values (?,?,?,?,?)", (fid, "US", cert, 3, None))
            for j, kw in enumerate(kws):
                c.execute("insert into keywords values (?,?,?)", (fid, j, kw))
            c.execute("insert or ignore into people values (?,?)", (d, "Director %d" % d))
            c.execute("insert into credits values (?,?,?,?,?)", (fid, d, "crew", "Director", 0))
        c.commit()
        self.movies = os.path.join(self.tmp.name, "movies.db")
        m = sqlite3.connect(self.movies)
        m.execute("create table ml_neighbours(tmdb_id integer, neighbour_id integer, score real, rank integer)")
        for i, (a, b, s) in enumerate(NEIGHBOURS):
            m.execute("insert into ml_neighbours values (?,?,?,?)", (a, b, s, i + 1))
        m.commit()
        m.close()
        personal._FEAT.clear()
        personal._DF.clear()

    def tearDown(self):
        self.c.close()
        self.tmp.cleanup()
        personal._FEAT.clear()
        personal._DF.clear()

    def rank(self, p=None, **kw):
        kw.setdefault("movies_db", self.movies)
        kw.setdefault("w", (2.0, 2.0, 0.0))
        return R.rank(self.c, p or plan(), **kw)


class TestRank(Base):
    def test_hard_limits_come_before_scoring_and_nothing_removed_returns(self):
        got = self.rank(plan(runtime_max_minutes=150, certification_max="R", unknown="fail", exclude_media_ids=["movie:11"]),
                        likes=[1], dislikes=[9], k=10)
        ids = [x["media_id"] for x in got]
        self.assertNotIn("movie:11", ids)       # watched: the strongest neighbour, still never shown
        self.assertNotIn("movie:5", ids)        # 190 minutes
        self.assertNotIn("movie:7", ids)        # no US rating, strict
        self.assertNotIn("movie:1", ids)        # already rated (liked)
        self.assertNotIn("movie:9", ids)        # already rated (disliked)
        self.assertEqual(ids[0], "movie:2")
        self.assertEqual([x["rank"] for x in got], list(range(1, len(got) + 1)))

    def test_exactly_one_reason_per_film_from_a_fixed_list(self):
        got = self.rank(likes=[1], dislikes=[9], k=10)
        self.assertGreater(len(got), 4)
        for x in got:
            self.assertEqual(set(x), {"media_id", "rank", "score", "reason", "signals", "ranking_version"})
            self.assertEqual(set(x["reason"]), {"code", "params"})
            self.assertIn(x["reason"]["code"], R.REASONS)
            json.dumps(x)
        by = {x["media_id"]: x for x in got}
        self.assertEqual(by["movie:11"]["reason"], {"code": "similar_to_liked", "params": {"liked_media_id": "movie:1"}})
        self.assertEqual(by["movie:4"]["reason"]["code"], "popular_fits_limits")

    def test_content_reasons_name_the_shared_thing(self):
        got = R.rank(self.c, plan(), likes=[1], k=10, movies_db=os.path.join(self.tmp.name, "absent.db"), w=(2.0, 2.0, 0.0))
        by = {x["media_id"]: x for x in got}
        self.assertEqual(by["movie:2"]["reason"], {"code": "shares_director", "params": {"name": "Director 501"}})
        self.assertIn(by["movie:3"]["reason"]["code"], ("shares_genre", "shares_theme"))
        self.assertTrue(all(x["signals"]["neighbours"] == 0 for x in got))

    def test_new_person_gets_popular_films_that_meet_the_limits(self):
        got = self.rank(plan(certification_max="PG", unknown="fail"), k=5)
        self.assertEqual([x["media_id"] for x in got], ["movie:4", "movie:8"])
        self.assertTrue(all(x["reason"] == {"code": "popular_fits_limits", "params": {}} for x in got))
        self.assertEqual(self.rank(plan(year_min=2090), k=5), [])

    def test_a_score_is_never_called_a_probability(self):
        text = json.dumps(self.rank(likes=[1], k=10)).lower()
        for word in ("prob", "confidence", "chance", "percent", "likelihood"):
            self.assertNotIn(word, text)
        self.assertNotIn("probab", (R.__doc__ or "").lower().replace("not a probability", ""))

    def test_variety_moves_a_near_copy_down(self):
        scores = {1: 1.0, 2: 0.99, 3: 0.95}
        genres = {1: frozenset({"Crime"}), 2: frozenset({"Crime"}), 3: frozenset({"Comedy"})}
        self.assertEqual(R.mmr(scores, genres, 3), [1, 3, 2])
        self.assertEqual(R.mmr(scores, genres, 3, lam=1.0), [1, 2, 3])
        self.assertEqual(R.mmr({}, {}, 3), [])

    def test_inputs_are_checked_and_results_repeat(self):
        with self.assertRaises(R.Unsupported):
            self.rank(plan(media=["tv"]))
        for k in (0, 51):
            with self.assertRaises(ValueError):
                self.rank(k=k)
        a = self.rank(plan(media=["movie", "tv"]), likes=[1], dislikes=[9], k=6)
        b = self.rank(likes=[1], dislikes=[9], k=6)
        self.assertEqual(a, b)                                   # a mixed plan ranks its films; same call, same answer
        self.assertTrue(all(x["media_id"].startswith("movie:") for x in a))
        only = self.rank(likes=[1], k=10, candidate_ids=[4, 11, 5, 999])
        self.assertEqual({x["media_id"] for x in only}, {"movie:4", "movie:11", "movie:5"})

    def test_limits_helper_drops_rated_and_failing_films(self):
        self.assertEqual(R.apply_limits(self.c, plan(genres_exclude=["Horror"]), [6, 4, 4, 2, 9], rated=[2]), [4])
        self.assertEqual(R.apply_limits(self.c, plan(), [], rated=[1]), [])
        self.assertEqual(R.minmax({1: 5.0, 2: 5.0}), {1: 0.0, 2: 0.0})


class TestWeights(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.file = os.path.join(self.tmp.name, "w.json")

    def tearDown(self):
        for k in ("RECS_V2", "RECS_V2_WEIGHTS"):
            os.environ.pop(k, None)
        importlib.reload(R)
        self.tmp.cleanup()

    def load(self, on, content=None):
        if content is not None:
            with open(self.file, "w") as fh:
                json.dump(content, fh)
        os.environ["RECS_V2"] = "1" if on else "0"
        os.environ["RECS_V2_WEIGHTS"] = self.file
        importlib.reload(R)

    def test_fallback_until_one_set_passed_its_rule(self):
        self.load(True)
        self.assertEqual(R.weights(), (R.FALLBACK_W, "fallback"))
        self.assertFalse(R.enabled())                             # switch on, but no benchmarked weights: stays off
        self.load(True, {"weights": [2, 1, 0], "rule": "FAIL"})
        self.assertFalse(R.enabled())
        for bad in ({"weights": [2, 1], "rule": "PASS"}, {"weights": [0, 0, 0], "rule": "PASS"},
                    {"weights": [2, -1, 0], "rule": "PASS"}, {"weights": "x", "rule": "PASS"}, []):
            self.load(True, bad)
            self.assertEqual(R.weights()[1], "fallback", bad)

    def test_passed_weights_are_used_and_the_switch_still_decides(self):
        self.load(True, {"weights": [2, 1, 0], "rule": "PASS"})
        self.assertEqual(R.weights(), ((2.0, 1.0, 0.0), "v81_recs_single"))
        self.assertTrue(R.enabled())
        self.load(False, {"weights": [2, 1, 0], "rule": "PASS"})
        self.assertFalse(R.enabled())


class TestSingleWeightRule(unittest.TestCase):
    """The choice and the rule of tools/v81_recs_single.py on hand-made rows (skipped where pandas / scipy are absent)."""

    def setUp(self):
        try:
            import v81_recs_single as S
        except ImportError as e:
            self.skipTest("benchmark libraries not installed: %s" % e)
        self.S = S

    def rows(self, good="c"):
        out = []
        for u in range(1, 41):
            cand = list(range(1, 21))
            pos = {u % 5 + 1}
            s = {"c": {f: 0.0 for f in cand}, "nt": {f: 0.0 for f in cand}, "pt": {f: (20 - f) / 20 for f in cand}}
            for f in pos:
                s[good][f] = 1.0
            out.append((u, pos, cand, s))
        return out

    def test_choice_uses_both_protocols_and_ignores_a_useless_signal(self):
        S = self.S
        w, how = S.choose({"uniform": self.rows("c"), "popular": self.rows("nt")})
        self.assertGreater(w[0], 0)
        self.assertGreater(w[1], 0)                               # one set has to serve both protocols
        self.assertGreater(how["mean_ratio"], 0.9)
        self.assertEqual(S.order([1, 2, 3], {"c": {1: 0, 2: 1, 3: 0}, "nt": {1: 0, 2: 0, 3: 0}, "pt": {1: 0, 2: 0, 3: 1}}, (1, 0, 0)), [2, 1, 3])

    def test_rule_needs_ndcg_and_coverage(self):
        S = self.S
        rows = self.rows("c")
        genres = {f: frozenset({"G%d" % (f % 4)}) for f in range(1, 21)}
        worse = {u: [20, 19, 18, 17, 16, 15, 14, 13, 12, 11] for u, _p, _c, _s in rows}
        e = S.evaluate(rows, (2, 0, 0), genres, worse)
        self.assertTrue(e["ok"])
        self.assertGreater(e["diff"][1], 0)
        better = {u: sorted(c, key=lambda f: (f not in p, f))[:10] for u, p, c, _s in rows}
        e2 = S.evaluate(rows, (0, 0, 1), genres, better)          # popularity only: misses most positives
        self.assertFalse(e2["ok"])
        self.assertLess(e2["diff"][2], 0)
        self.assertEqual(S.half(1), S.half(1))
        self.assertEqual({S.half(u) for u in range(1, 200)}, {"select", "report"})


if __name__ == "__main__":
    unittest.main()
