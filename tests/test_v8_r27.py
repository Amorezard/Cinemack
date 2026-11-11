"""11 Nov (round 27): fixes and the TVmaze / banner / Any-All features. Temp databases only; no server, no network.
    python -m unittest tests.test_v8_r27
"""
import base64
import importlib
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest

T = tempfile.mkdtemp()
for k, f in (("SOCIAL_DB", "social.db"), ("HISTORY_DB", "history.db"), ("TRAKT_DB", "trakt.db"), ("ALERTS_DB", "alerts.db"),
             ("USERS_DB", "users.db"), ("FACTS_DB", "facts.db"), ("MOVIES_DB", "movies.db"), ("CHATS_DB", "chats.db"),
             ("MEMORY_DB", "memory.db"), ("TMDB_EXTRA_DB", "tmdb_extra.db"), ("TVMAZE_DB", "tvmaze.db")):
    os.environ[k] = os.path.join(T, f)
os.environ["BANNER_DIR"] = os.path.join(T, "banners")
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
sys.path[:0] = [os.path.join(ROOT, "web"), ROOT]

c = sqlite3.connect(os.environ["FACTS_DB"])
c.executescript("""
create table films(tmdb_id integer primary key, title text, original_title text, year integer, release_date text, us_release_date text,
  vote_count integer, vote_average real, runtime integer, genres_json text, imdb_id text, popularity real, overview text);
create table certifications(tmdb_id integer, region text, certification text, release_type integer, release_date text);
create table watch_providers(tmdb_id integer, region text, kind text, provider_id integer, provider_name text, priority integer);
create table lists(list_name text, region text, tmdb_id integer, rank integer, fetched_at text);
create table film_images(tmdb_id integer primary key, poster_path text, backdrop_path text);
""")
c.commit()
c.close()
subprocess.run([sys.executable, os.path.join(ROOT, "tools", "make_demo_tv.py"), os.environ["FACTS_DB"]], check=True, capture_output=True)


class StripWhen(unittest.TestCase):
    """'tonight' in a recommendation request no longer pulls films titled '...Night' (3b tuning, round 25)."""
    def setUp(self):
        try:
            os.environ["STRIP_WHEN"] = "1"
            import retrieve
            self.R = importlib.reload(retrieve)
        except Exception as e:  # noqa: BLE001 - the laptop / CI may lack ollama
            self.skipTest(f"retrieve not importable here: {e!r}")

    def test_viewing_time_removed(self):
        s = self.R.strip_when
        self.assertEqual(s("OK, last one: what's one more film we could put on tonight?"), "what's one more film we could put?")
        self.assertEqual(s("Right, give me one film to watch tonight."), "Right, give me one film")
        self.assertEqual(s("A PG-rated or milder family-friendly film suitable for a 7-year-old to watch tonight."),
                         "A PG-rated or milder family-friendly film suitable for a 7-year-old")
        self.assertEqual(s("tonight"), "a good film")

    def test_titles_and_moods_kept(self):
        s = self.R.strip_when
        for t in ("something like Last Night in Soho", "a romantic film for date night", "Night of the Living Dead style zombie film",
                  "films like Tonight and Every Night"):
            self.assertIn("night", s(t).lower(), t)


class Money(unittest.TestCase):
    def test_decimal_comma(self):
        from wiki_sections import parse_money
        self.assertEqual(parse_money("$136,6 million <ref>x</ref>")[0], 136_600_000)
        self.assertEqual(parse_money("$1,034,800,131")[0], 1_034_800_131)
        self.assertEqual(parse_money("£3,5 million")[:2], (3_500_000, "GBP"))
        self.assertEqual(parse_money("$12,345 million")[0], 12_345_000_000)        # a thousands comma stays one

    def test_decimal_slip(self):
        import tools
        self.assertTrue(tools._decimal_slip(1_366_000_000, 136_579_287))
        self.assertTrue(tools._decimal_slip(5e6, 5e8))
        self.assertFalse(tools._decimal_slip(1_034_999_999, 1_034_800_131))
        self.assertFalse(tools._decimal_slip(30e6, 60e6))

    def test_genre_agreement(self):
        import tools
        db = sqlite3.connect(":memory:")
        db.executescript(r"""create table films(tmdb_id int, imdb_id text, title text, genres_json text);
            create table imdb_titles(tconst text primary key, genres text);
            insert into films values (1,'tt1','Despicable Me 3','["Action", "Animation"]'),(2,'tt2','Die Hard','["Action"]'),
              (3,null,'No IMDb','["Action"]'),(4,'tt4','Empty IMDb','["Action"]'),(5,'tt5','Alien','["Science Fiction"]');
            insert into imdb_titles values ('tt1','Adventure,Animation,Comedy'),('tt2','Action,Thriller'),('tt4','\N'),('tt5','Horror,Sci-Fi');""")
        old = tools.GENRE_AGREE
        try:
            tools.GENRE_AGREE = True
            for g, want in (("Action", ["Die Hard", "No IMDb", "Empty IMDb"]), ("Science Fiction", ["Alien"])):
                cl, a = tools.genre_clause(g)
                self.assertEqual([r[0] for r in db.execute("select title from films f where " + cl, a)], want)
            tools.GENRE_AGREE = False
            cl, a = tools.genre_clause("Action")
            self.assertEqual(len(db.execute("select title from films f where " + cl, a).fetchall()), 4)
        finally:
            tools.GENRE_AGREE = old


class Moderation(unittest.TestCase):
    def _run(self, cat, toxic=0.01, on=True, answer="Paddington 2 (2017): a bear is framed for a theft and sent to prison."):
        import moderation as M
        M.ANSWER_PLOT_OK = on
        M._guard = lambda msgs: (False, cat)
        M._detox = lambda text: {"toxicity": toxic}
        return M.check("OK, last one: what's one more film we could put on tonight?", answer)

    def test_plot_crime_in_answer_passes(self):
        self.assertTrue(self._run("S1")["ok"])
        self.assertTrue(self._run("S2")["ok"])

    def test_other_categories_and_switch_off_still_replace(self):
        for cat in ("S3", "S4", "S9", "S10", "S12"):
            self.assertFalse(self._run(cat)["ok"], cat)
        self.assertFalse(self._run("S1", toxic=0.9)["ok"])
        self.assertFalse(self._run("S1", on=False)["ok"])
        self.assertEqual(self._run("S11")["reply"][:24], "I can't help with that, ")


class ChatGenresAll(unittest.TestCase):
    def test_all_mode(self):
        import user_prefs as U
        c = U.clean_chat({"genres": ["Comedy", "Horror"], "genres_mode": "all"})
        self.assertEqual(c["genres_mode"], "all")
        self.assertNotIn("genres_mode", U.clean_chat({"genres": ["Comedy"], "genres_mode": "all"}))   # one genre: no mode
        s = U.merge_chat(dict(U.DEFAULTS), {"genres": ["Comedy", "Horror"], "genres_mode": "all"})
        self.assertTrue(s["genres_all"])
        self.assertNotIn("genres_all", U.merge_chat(dict(U.DEFAULTS), {"genres": ["Comedy", "Horror"]}))
        self.assertNotEqual(U.filter_tag(s), U.filter_tag(U.merge_chat(dict(U.DEFAULTS), {"genres": ["Comedy", "Horror"]})))
        self.assertIn("ALL of these genres", U.prompt_block(s, "recommend", "something fun"))


class TVmazeOnSite(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import tvdata
        F = sqlite3.connect(os.environ["FACTS_DB"])
        cls.sid, cls.season = F.execute("select tmdb_id, season_number from tv_episodes where season_number > 0 limit 1").fetchone()
        cls.n = F.execute("select min(episode_number) from tv_episodes where tmdb_id = ? and season_number = ?", (cls.sid, cls.season)).fetchone()[0]
        F.execute("update tv_episodes set overview = '' where tmdb_id = ? and season_number = ? and episode_number = ?", (cls.sid, cls.season, cls.n))
        F.commit()
        t = sqlite3.connect(os.environ["TVMAZE_DB"])
        t.executescript("""create table show(tvmaze_id integer primary key, tmdb_id integer, imdb_id text, name text, status text,
              premiered text, ended text, network text, web_channel text, country text, timezone text, air_time text,
              air_days text, runtime integer, rating real, image text, summary text, url text, official_site text, updated integer, fetched_at text);
            create table episode(ep_id integer primary key, tvmaze_id integer, season integer, number integer, name text, type text,
              airdate text, airtime text, airstamp text, runtime integer, rating real, image text, summary text, url text);
            create table schedule(ep_id integer primary key, tvmaze_id integer, season integer, number integer, name text,
              airdate text, airtime text, airstamp text, runtime integer, web integer);""")
        t.execute("insert into show values (1, ?, null, 'Demo', 'Running', null, null, 'AMC', null, 'US', 'America/New_York', '21:00', 'Sunday', 60, 8, null, 's', 'https://www.tvmaze.com/shows/1', null, 1, 'x')", (cls.sid,))
        t.execute("insert into episode values (10, 1, ?, ?, 'Ep', 'regular', '2020-01-01', '21:00', '2020-01-02T02:00:00+00:00', 60, null, null, 'From TVmaze.', 'u')", (cls.season, cls.n))
        t.execute("insert into schedule values (20, 1, 99, 1, 'Future', '2099-01-04', '21:00', '2099-01-05T02:00:00+00:00', 60, 0)")
        t.commit()
        cls.T = tvdata

    def test_airs_text(self):
        import tvmaze
        self.assertEqual(tvmaze.airs_text({"network": "AMC", "air_days": ["Sunday"], "air_time": "21:00", "timezone": "America/New_York",
                                           "status": "Running"}), "Sundays at 9:00 pm ET on AMC")
        self.assertEqual(tvmaze.airs_text({"web_channel": "Netflix", "status": "Running"}), "Streams on Netflix")
        self.assertEqual(tvmaze.airs_text({"network": "HBO", "status": "Ended"}), "Aired on HBO")
        self.assertEqual(tvmaze.airs_text({}), "")

    def test_show_page_and_episode_summary(self):
        d = self.T.show(self.sid)
        self.assertEqual(d["tvmaze"]["airs"], "Sundays at 9:00 pm ET on AMC")
        self.assertIn("tvmaze.com", d["tvmaze"]["url"])
        self.assertIn("TVmaze", d["tvmaze"]["credit"]["text"])
        eps = {e["episode"]: e for e in self.T.episodes(self.sid, self.season)}
        self.assertEqual(eps[self.n]["overview"], "From TVmaze.")
        self.assertEqual(eps[self.n]["overview_src"], "TVmaze")

    def test_missing_db_changes_nothing(self):
        import tvmaze
        old = tvmaze.DB
        try:
            tvmaze.DB = os.path.join(T, "nope.db")
            d = self.T.show(self.sid)
            self.assertIsNone(d["tvmaze"])
        finally:
            tvmaze.DB = old


class Banner(unittest.TestCase):
    PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64

    def test_upload_read_delete_move(self):
        import v8extra as V
        ok, _m = V.banner_save("guest-a", "data:image/png;base64," + base64.b64encode(self.PNG).decode())
        self.assertTrue(ok)
        p, t = V.banner_file("guest-a")
        self.assertEqual((p.suffix, t), (".png", "image/png"))
        self.assertNotIn("guest-a", p.name)                       # no user key on disk
        self.assertEqual(V.summary("guest-a")["banner"]["custom"], True)
        V.move_user("guest-a", "acct9")
        self.assertIsNone(V.banner_file("guest-a")[0])
        self.assertIsNotNone(V.banner_file("acct9")[0])
        V.delete_user("acct9")
        self.assertIsNone(V.banner_file("acct9")[0])

    def test_rejects(self):
        import v8extra as V
        self.assertFalse(V.banner_save("x", "data:text/html;base64," + base64.b64encode(b"<script>").decode())[0])
        self.assertFalse(V.banner_save("x", "not base64 !!")[0])
        self.assertFalse(V.banner_save("x", base64.b64encode(b"\xff\xd8\xff" + b"0" * (V.BANNER_MAX + 1)).decode())[0])


if __name__ == "__main__":
    unittest.main()
