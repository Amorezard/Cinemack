"""v8.1 round 46 (16 Nov 2026): the Guardian allowance probe / top-up, the YouTube match rules, the fresh-user
ranking test's user draw, the review coverage inventory and the banner-dot edit. No network, no user data."""
import io
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
import urllib.error

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))
import v81_guardian_quota as G  # noqa: E402
import v81_review_coverage as RC  # noqa: E402
import v81_yt_match as M  # noqa: E402

KEY = "SECRET-KEY-123"


class Reply:
    def __init__(self, status, headers):
        self.status, self.headers = status, headers

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def opener(remaining, limit=500, status=200, seen=None):
    def go(req, timeout=0):
        if seen is not None:
            seen.append(req.full_url)
        h = {"X-RateLimit-Remaining-day": str(remaining), "X-RateLimit-Limit-day": str(limit), "Content-Type": "application/json"}
        if status != 200:
            raise urllib.error.HTTPError(req.full_url, status, "Too Many", h, io.BytesIO(b""))
        return Reply(status, h)
    return go


class TestGuardianQuota(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.log = os.path.join(self.tmp, "logs", "q.jsonl")

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_probe_records_the_allowance_and_never_the_key(self):
        seen = []
        rec = G.probe(KEY, self.log, opener(137, seen=seen), at="2026-11-16T22:47:00Z")
        self.assertEqual((rec["status"], rec["remaining_day"], rec["limit_day"]), (200, 137, 500))
        self.assertEqual(len(seen), 1)
        self.assertIn("api-key=" + KEY, seen[0])                       # the key goes to the Guardian ...
        text = open(self.log).read()
        self.assertNotIn(KEY, text)                                    # ... and nowhere else
        self.assertEqual(set(json.loads(text)), {"at", "status", "remaining_day", "limit_day", "headers"})
        self.assertEqual(set(json.loads(text)["headers"]), {"x-ratelimit-remaining-day", "x-ratelimit-limit-day"})

    def test_a_refused_call_and_a_dead_network_are_results_not_crashes(self):
        rec = G.probe(KEY, self.log, opener(0, status=429))
        self.assertEqual((rec["status"], rec["remaining_day"]), (429, 0))

        def dead(req, timeout=0):
            raise OSError("no route")
        rec = G.probe(KEY, self.log, dead)
        self.assertEqual((rec["status"], rec["remaining_day"]), (None, None))
        self.assertNotIn(KEY, open(self.log).read())

    def test_probe_stops_by_itself(self):
        for _ in range(3):
            G.probe(KEY, self.log, opener(10), cap=3)
        seen = []
        self.assertIn("skipped", G.probe(KEY, self.log, opener(10, seen=seen), cap=3))
        self.assertEqual(seen, [])                                     # no call once enough is recorded
        self.assertEqual(len(G.read_log(self.log)), 3)

    def test_report_finds_when_the_count_goes_back_up(self):
        days = [("2026-11-16T20:47:00Z", 96), ("2026-11-16T22:47:00Z", 95), ("2026-11-17T00:47:00Z", 499),
                ("2026-11-17T08:47:00Z", 498), ("2026-11-17T10:47:00Z", 60), ("2026-11-17T22:47:00Z", 55),
                ("2026-11-18T00:47:00Z", 499)]
        for at, left in days:
            G.probe(KEY, self.log, opener(left), at=at)
        rows = G.read_log(self.log)
        self.assertEqual(G.resets(rows), [("2026-11-16T22:47:00Z", "2026-11-17T00:47:00Z"), ("2026-11-17T22:47:00Z", "2026-11-18T00:47:00Z")])
        text = G.report(rows)
        self.assertIn("went back up 2 time(s), between (UTC): 22:47 and 00:47", text)
        self.assertIn("lowest seen: 55", text)
        self.assertIn("reset time unknown", G.report(rows[:2]))
        self.assertIn("no reply carried", G.report([]))

    def test_how_much_a_top_up_spends(self):
        self.assertEqual(G.plan_topup(100), 60)                         # 40 always stay unspent
        self.assertEqual(G.plan_topup(100, reserve=10), 90)
        self.assertEqual(G.plan_topup(59), 0)                           # under 20 to spend: not worth a run
        self.assertEqual(G.plan_topup(0), 0)
        self.assertEqual(G.plan_topup(None), 0)

    def test_top_up_is_off_by_default_and_makes_no_call_while_off(self):
        seen, ran = [], []
        msg = G.topup(KEY, opener=opener(300, seen=seen), ps="", runner=lambda *a, **k: ran.append(a), path=self.log)
        self.assertIn("switched off", msg)
        self.assertEqual((seen, ran), ([], []))
        self.assertFalse(os.path.exists(self.log))

    def test_top_up_never_overlaps_another_job(self):
        for line in ("bash nightly.sh", "python pf5_data.py press --max 400", "python eval_v3.py --tag x", "bash run_final_v8.sh"):
            seen, ran = [], []
            msg = G.topup(KEY, enabled=True, opener=opener(300, seen=seen), ps="init\n" + line + "\n",
                          runner=lambda *a, **k: ran.append(a), path=self.log)
            self.assertIn("another job is running", msg, line)
            self.assertEqual((seen, ran), ([], []))
        self.assertIsNone(G.busy("init\npython tools/v81_guardian_quota.py topup\nsshd\n"))   # itself does not count

    def test_top_up_runs_the_nightly_command_with_the_spare_calls(self):
        ran = []

        class Done:
            returncode, stdout = 0, "press: 600 films done\n  reached 260 calls"

        def runner(cmd, **kw):
            ran.append((cmd, kw.get("cwd")))
            return Done()
        msg = G.topup(KEY, enabled=True, opener=opener(300), ps="", runner=runner, path=self.log)
        self.assertEqual(ran, [(["python", "pf5_data.py", "press", "--max", "260"], G.LIVE)])
        self.assertIn("ran press --max 260 (exit 0)", msg)
        self.assertNotIn(KEY, msg)
        ran.clear()
        self.assertIn("would run", G.topup(KEY, enabled=True, dry=True, opener=opener(300), ps="", runner=runner, path=self.log))
        self.assertIn("nothing to spend", G.topup(KEY, enabled=True, opener=opener(45), ps="", runner=runner, path=self.log))
        self.assertEqual(ran, [])


# normalised title -> [(id, year, votes)]
FILMS = [(1, "Star Wars", 1977, 20000), (2, "Rogue One: A Star Wars Story", 2016, 15000), (3, "Star Wars: The Force Awakens", 2015, 18000),
         (4, "Halloween", 1978, 6000), (5, "Halloween", 2018, 4000), (6, "Transformers", 2007, 11000),
         (7, "Transformers: Age of Extinction", 2014, 8000), (8, "Transformers: Revenge of the Fallen", 2009, 8000),
         (9, "Wicked", 2024, 3000), (10, "Children of Men", 2006, 7000), (11, "Uncharted", 2022, 5000),
         (12, "Spider-Man 2", 2004, 15000), (13, "Spider-Man", 2002, 18000), (14, "Spider-Man: Homecoming", 2017, 20000),
         (15, "Scream", 1996, 6000), (16, "Scream 2", 1997, 4000), (17, "Scream 3", 2000, 3000), (18, "Looper", 2012, 10000),
         (19, "Justice League", 2017, 12000), (20, "Justice League Dark", 2017, 1500), (21, "Zack Snyder's Justice League", 2021, 9000),
         (22, "The Lord of the Rings", 1978, 900), (23, "The Lord of the Rings: The Two Towers", 2002, 21000),
         (24, "The Lord of the Rings: The Return of the King", 2003, 23000), (25, "Batman", 1989, 7000), (26, "Batman", 1966, 900),
         (27, "Batman Begins", 2005, 20000), (28, "Batman Returns", 1992, 6000)]


class TestYouTubeRules(unittest.TestCase):
    def setUp(self):
        self.idx = {}
        for fid, t, y, v in FILMS:
            self.idx.setdefault(M.norm(t), []).append((fid, y, v))
        self.rev = {c[0]: n for n, cs in self.idx.items() for c in cs}

    def d(self, title, fid, year=None, how="full title in the video title"):
        return M.decide(title, year, fid, how, self.idx, self.rev)[:3]

    def test_games_and_television_are_dropped(self):
        self.assertEqual(self.d("Uncharted 4: A Thief's End - Game Review", 11), ("drop", None, "G"))
        self.assertEqual(self.d("Spider-Man 2 (PS5) - Game Review", 12), ("drop", None, "G"))
        self.assertEqual(self.d("The Lord of the Rings: The Rings of Power - Episodes 1 & 2 - Review", 22), ("drop", None, "G"))
        self.assertEqual(self.d("Scream Season 2 Review", 15), ("drop", None, "G"))
        self.assertEqual(self.d("Spider-Man 2 - Movie Review", 12), ("keep", 12, "-"))          # the film, not the game

    def test_sequel_numbers(self):
        self.assertEqual(self.d("Transformers 4 — How a Franchise Destroyed a Filmmaker", 6), ("drop", None, "N"))
        self.assertEqual(self.d("Scream 3 - Movie Review", 15), ("switch", 17, "N"))
        self.assertEqual(self.d("Scream - Movie Review", 15), ("keep", 15, "-"))
        self.assertEqual(self.d("Scream: 25 Years Later", 15), ("keep", 15, "-"))                # a number after a break is not a sequel

    def test_one_word_title_running_into_another_word(self):
        self.assertEqual(self.d("Best of the Worst: Wicked World", 9), ("drop", None, "W"))
        self.assertEqual(self.d("Wicked - Movie Review", 9), ("keep", 9, "-"))
        self.assertEqual(self.d("Looper — Time Travel Done Right", 18), ("keep", 18, "-"))      # a break before the next phrase
        self.assertEqual(self.d("Looper reviewed by Mark Kermode", 18), ("keep", 18, "-"))
        self.assertEqual(self.d("Why Looper Works", 18), ("keep", 18, "-"))                      # not at the start: left alone
        self.assertEqual(self.d("Looper Changed Everything", 18), ("drop", None, "W"))           # the known cost of this rule
        self.assertEqual(self.d("Looper Is Better Than You Remember", 18), ("keep", 18, "-"))

    def test_series_names(self):
        self.assertEqual(self.d("Star Wars Standalone Details Emerge!! - CineFix Now", 1), ("drop", None, "F"))
        self.assertEqual(self.d("The Nerd Crew: D23, Star Wars, D23, Disney+ and Streaming Services", 1), ("drop", None, "F"))
        self.assertEqual(self.d("Drew Goddard to Take on Spider Man?!? - CineFix Now", 13), ("drop", None, "F"))
        self.assertEqual(self.d("Justice League - Movie Review", 19), ("keep", 19, "-"))
        self.assertEqual(self.d("Half in the Bag: Justice League", 19), ("keep", 19, "-"))       # after a ":" it is the subject
        self.assertEqual(self.d("Children of Men: Don't Ignore The Background", 10), ("keep", 10, "-"))   # not a series name

    def test_subtitles_name_the_right_film_of_a_series(self):
        self.assertEqual(self.d("Star Wars: Rogue One - Trailer 3 Thoughts", 1, 2016), ("switch", 2, "S"))
        self.assertEqual(self.d("Star Wars: The Force Awakens - Movie Review", 1, 2015), ("switch", 3, "S"))
        self.assertEqual(self.d("Star Wars: The Mandalorian and Grogu - Movie Review", 1, 2026), ("drop", None, "S"))
        self.assertEqual(self.d("Star Wars - Movie Review", 1), ("keep", 1, "-"))

    def test_remakes_follow_the_upload_year(self):
        self.assertEqual(self.d("Halloween - Trailer Review", 4, 2018), ("switch", 5, "Y"))
        self.assertEqual(self.d("Halloween - Trailer Review", 4, 1979), ("keep", 4, "-"))
        self.assertEqual(self.d("Halloween - Trailer Review", 4, 2012), ("keep", 4, "-"))        # no film near 2012: most-voted stays
        self.assertEqual(self.d("Halloween - Trailer Review", 4, None), ("keep", 4, "-"))

    def test_a_title_with_its_year_or_in_quotes_is_trusted(self):
        how = "quoted or with its year in the title"
        self.assertEqual(self.d("7 Things You Didn't Know About Batman (1989)!", 25, 2019, how), ("keep", 25, "-"))
        self.assertEqual(self.d("Batman (1989) Game Review", 25, 2019, how), ("drop", None, "G"))  # only rule G still applies

    def test_rematch_over_the_first_pass(self):
        import youtube_analysis as Y
        vids = [("a", "Star Wars: Rogue One - Trailer 3 Thoughts", "", "2016-10-13T00:00:00Z"),
                ("b", "Children of Men: Don't Ignore The Background", "", "2015-01-01T00:00:00Z"),
                ("c", "Uncharted 4: A Thief's End - Game Review", "", "2016-05-10T00:00:00Z"),
                ("d", "A video about nothing in the index", "", None)]
        idx = {}
        for fid, t, y, v in FILMS:
            idx.setdefault(Y._norm(t), []).append((fid, y, v))
        out = {r["video"]: r for r in M.rematch(vids, idx, Y.match_films)}
        self.assertEqual((out["a"]["v1"], out["a"]["action"], out["a"]["v2"]), (1, "switch", 2))
        self.assertEqual((out["b"]["action"], out["b"]["v2"]), ("keep", 10))
        self.assertEqual(out["c"]["action"], "drop")
        self.assertNotIn("d", out)
        self.assertEqual(M.norm("Amélie & Co."), Y._norm("Amélie & Co."))                        # same normalising as the first pass


class TestFreshUsers(unittest.TestCase):
    def test_fresh_users_are_outside_the_earlier_sample_and_repeatable(self):
        try:
            import v81_recs_fresh as RF
        except ImportError as e:
            self.skipTest("benchmark libraries not installed: %s" % e)
        import random
        elig = list(range(1, 10001))
        random.Random(1).shuffle(elig)
        users, n_seen, n_rest = RF.fresh_users(elig, 3000)
        seen = set(random.Random(13).sample(sorted(elig), 3000))        # the 14 / 15 Nov draw
        self.assertEqual((len(users), n_seen, n_rest), (3000, 3000, 7000))
        self.assertEqual(set(users) & seen, set())
        self.assertEqual(users, RF.fresh_users(sorted(elig), 3000)[0])   # order of the input does not matter
        self.assertEqual(RF.fresh_users(list(range(1, 3001)), 3000)[0], [])   # nobody left: empty, and the tool says UNRUN
        self.assertEqual(RF.W, (2.0, 1.0, 0.0))
        self.assertGreaterEqual(RF.MIN_USERS, 500)


class TestReviewCoverage(unittest.TestCase):
    def test_counts_per_source_bucket_and_gap(self):
        tmp = tempfile.mkdtemp()
        try:
            c = sqlite3.connect(os.path.join(tmp, "f.db"))
            c.execute("create table films(tmdb_id integer primary key, imdb_id text, vote_count integer)")
            c.executemany("insert into films values (?,?,?)", [(1, "tt1", 9000), (2, "tt2", 2000), (3, "tt3", 500), (4, None, 400), (5, "tt5", 10)])
            c.execute("create table critic_statements(tmdb_id integer, outlet text)")
            c.executemany("insert into critic_statements values (?,?)", [(1, "Variety"), (1, ""), (2, None), (99, "X")])
            c.execute("create table wiki_reception(tmdb_id integer)")
            c.executemany("insert into wiki_reception values (?)", [(1,), (2,), (3,)])
            c.execute("create table wiki_reception_done(tmdb_id integer)")
            c.executemany("insert into wiki_reception_done values (?)", [(1,), (2,), (3,)])
            c.execute("create table critic_reviews(source text, tmdb_id integer)")
            c.executemany("insert into critic_reviews values (?,?)", [("guardian", 1), ("guardian", None), ("guardian", None), ("nyt", 2)])
            c.execute("create table omdb(tmdb_id integer, rt_pct real, mc_score real)")
            c.executemany("insert into omdb values (?,?,?)", [(1, 90, None), (2, None, None)])
            c.commit()
            r = RC.inventory(c)
            self.assertEqual(r["films"], 5)
            self.assertEqual(r["buckets"], {"5,000+ votes": 1, "1,000-4,999": 1, "300-999": 2, "under 300": 1})
            self.assertEqual(r["sources"]["Wikipedia critic statements"]["films"], 2)            # film 99 is not a film here
            self.assertEqual(r["sources"]["  ... with a named outlet"]["films"], 1)
            self.assertEqual(r["sources"]["OMDb scores (RT or Metacritic)"]["films"], 1)
            self.assertIsNone(r["sources"]["TMDB audience reviews"])                             # table absent: said so, no crash
            self.assertEqual(r["any_review_material"]["films"], 3)
            self.assertEqual(r["gaps"]["a_reception_never_requested"], 1)                        # film 4
            self.assertEqual(r["gaps"]["b_reception_text_but_no_statement"], 1)                  # film 3
            self.assertEqual(r["gaps"]["c_guardian_reviews_unmatched"], {"unmatched": 2, "fetched": 3})
            self.assertEqual(r["gaps"]["d_no_omdb_row"], 1)                                      # film 3 (film 4 has no IMDb id)
            text = RC.report(r, "f.db")
            self.assertIn("table not in this database", text)
            self.assertIn("guardian reviews matched to no film: 2 of 3 fetched", text)
            c.close()
        finally:
            shutil.rmtree(tmp)


if __name__ == "__main__":
    unittest.main()
