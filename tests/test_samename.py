"""8 Nov (+ night 4a3): SAME_NAME routing in tv.py on a tiny synthetic facts.db:  python -m unittest tests.test_samename"""
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp()
DB = os.path.join(TMP, "facts.db")
os.environ["FACTS_DB"] = DB
sys.path.insert(0, HERE)
import facts_db as F  # noqa: E402

c = F.connect(DB)
c.execute("alter table films add column collection_name text") if False else None
c.execute("create table if not exists tv_shows(tmdb_id integer primary key, name text, original_name text, first_year integer, vote_count integer)")
films = [(1, "Twin Peaks", 1989, 400, ["TV Movie", "Mystery"]), (2, "Hannibal", 2001, 5000, ["Thriller"]),
         (3, "Friday Night Lights", 2004, 900, ["Drama"]), (4, "Westworld", 1973, 1500, ["Science Fiction"]),
         (5, "Clueless", 1995, 4000, ["Comedy"]), (6, "The Wire Cutter", 2001, 10, ["Drama"])]
for fid, t, y, v, g in films:
    c.execute("insert into films(tmdb_id, title, original_title, year, vote_count, genres_json) values (?,?,?,?,?,?)",
              (fid, t, t, y, v, json.dumps(g)))
c.execute("insert into films(tmdb_id, title, original_title, year, vote_count, genres_json, collection_name) values "
          "(7, 'Scream', 'Scream', 1996, 6000, '[\"Horror\"]', 'Scream Collection')")
for sid, n, y, v in [(11, "Twin Peaks", 1990, 3000), (12, "Hannibal", 2013, 4000), (13, "Friday Night Lights", 2006, 1200),
                     (14, "Westworld", 2016, 6000), (15, "Clueless", 1996, 300), (16, "Breaking Bad", 2008, 15000)]:
    c.execute("insert into tv_shows values (?,?,?,?,?)", (sid, n, n, y, v))
# 8 Nov night (4a3): the Scream show's TMDB name has the ": The TV Series" suffix; its seasons for season_line()
c.execute("insert into tv_shows values (17, 'Scream: The TV Series', 'Scream: The TV Series', 2015, 1197)")
c.execute("create table if not exists tv_seasons(tmdb_id integer, season_number integer, name text, air_date text, "
          "episode_count integer, overview text, vote_average real, poster_path text, primary key (tmdb_id, season_number))")
for sn, d, n in [(0, "2016-10-18", 2), (1, "2015-06-30", 10), (2, "2016-05-30", 12), (3, "2019-07-08", 6)]:
    c.execute("insert into tv_seasons values (17, ?, ?, ?, ?, '', 0, '')", (sn, f"Season {sn}", d, n))
c.commit()
c.close()
import tv  # noqa: E402

tv.TV = True


def route(q, same, recent=None):
    tv.SAME_NAME = same
    return "tv" if tv.wants_tv(q, recent) else "film"


class SameName(unittest.TestCase):
    def test_off_is_v7(self):
        self.assertEqual(route("When did Twin Peaks first air?", False), "film")        # the v7 failure (I-001)
        self.assertEqual(route("What network aired Hannibal?", False), "film")

    def test_tv_words(self):
        for q in ("When did Twin Peaks first air?", "What network aired Hannibal?", "When did Friday Night Lights premiere?",
                  "How many seasons of Westworld are there?", "Did Hannibal win any Emmys?"):
            self.assertEqual(route(q, True), "tv", q)

    def test_film_words(self):
        for q in ("Who directed Hannibal?", "How much did Westworld gross at the box office?",
                  "Who directed the original Westworld movie?", "How long is Clueless?", "Did Clueless win an Oscar?"):
            self.assertEqual(route(q, True), "film", q)

    def test_conversation_decides_when_unclear(self):
        self.assertEqual(route("Who stars in Clueless?", True), "film")
        self.assertEqual(route("Who stars in Clueless?", True, recent=["Breaking Bad"]), "tv")

    def test_pilot_never_blocks_the_show(self):
        self.assertEqual(route("Who is in Twin Peaks?", True), "tv")                 # 1989 TV Movie = the pilot
        self.assertEqual(route("Who is in Hannibal?", True), "film")                 # a real film: v7 default

    def test_episode_words_beat_the_franchise_guard(self):
        q = "How many episodes are in the first season of the Scream series?"
        self.assertEqual(route(q, False), "film")
        self.assertEqual(route(q, True), "tv")
        self.assertEqual(route("How many films are in the Scream series?", True), "film")

    def test_only_same_name_titles_change(self):
        for same in (False, True):
            self.assertEqual(route("Who created Breaking Bad?", same), "tv")
            self.assertEqual(route("the movie where a radio host is on the air when a killer calls", same), "film")


    def test_series_suffix_is_also_the_short_name(self):              # 8 Nov night (4a3, tuning tn17)
        q = "How many episodes are in the first season of the Scream series?"
        tv.SAME_NAME = True
        ns = tv.named_show(q)
        self.assertIsNotNone(ns)
        self.assertEqual((ns[1], ns[2]), (17, "Scream: The TV Series"))
        self.assertEqual(tv.wants_tv(q), "named")
        tv.SAME_NAME = False
        self.assertIsNone(tv.named_show(q))                                # off: v7 exactly
        for same in (False, True):
            self.assertEqual(route("Is Scream: The TV Series worth watching?", same), "tv")
        for q in ("Who directed Scream?", "How much did Scream gross?", "How many films are in the Scream series?"):
            self.assertEqual(route(q, True), "film", q)
        self.assertEqual(route("Who stars in Scream?", True), "film")      # unclear: the v7 default (CLARIFY asks)

    def test_season_line(self):
        self.assertEqual(tv.season_line(17), "Episodes per season: S1 (2015) 10; S2 (2016) 12; S3 (2019) 6 [TMDB]")
        self.assertEqual(tv.season_line(16), "")
        self.assertTrue(tv.season_line(17, max_seasons=2).endswith("S2 (2016) 12; and 1 more seasons [TMDB]"))

    def test_season_line_only_for_a_named_show(self):                 # 8 Nov night (4a4, TV set tv05)
        tv.TV_SEASONS = True
        self.assertTrue(tv.seasons_wanted({"_plan": {"intent": "named_film"}}))
        for h in ({"_plan": {"intent": "identify"}}, {"_plan": {"intent": "recommend"}}, {}, None):
            self.assertFalse(tv.seasons_wanted(h), h)
        tv.TV_SEASONS = False
        self.assertFalse(tv.seasons_wanted({"_plan": {"intent": "named_film"}}))

if __name__ == "__main__":
    unittest.main()
