"""10 Nov (round 21): TV data for the site (top shows by IMDb, Pick Tonight for TV, episodes with IMDb ratings, next
episode, calendar episodes), 3c preference phrases, per-chat limits. Temp databases with the server's TV schema built
by tools/make_demo_tv.py (invented shows); no server, no network.
    python -m unittest tests.test_v8_r21
"""
import datetime as dt
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
             ("MEMORY_DB", "memory.db"), ("TMDB_EXTRA_DB", "tmdb_extra.db")):
    os.environ[k] = os.path.join(T, f)
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

import discovery  # noqa: E402
import pref_phrases  # noqa: E402
import tvdata  # noqa: E402
import user_prefs  # noqa: E402
import v8extra  # noqa: E402

F = sqlite3.connect(os.environ["FACTS_DB"])
TODAY = dt.date.today().isoformat()


def q(sql, p=()):
    return F.execute(sql, p).fetchall()


class TopShows(unittest.TestCase):
    def test_imdb_weighted_with_vote_floor_and_skips(self):
        top = tvdata.top_rated()
        self.assertTrue(top)
        meta = {r[0]: (r[1], r[2], json.loads(r[3])) for r in q("select t.tmdb_id, r.rating, r.votes, t.genres_json from tv_shows t join imdb_ratings r on r.imdb_id = t.imdb_id")}
        for sid in top:
            self.assertGreaterEqual(meta[sid][1], tvdata.TOP_MIN_IMDB_VOTES)
            self.assertFalse(set(meta[sid][2]) & tvdata.SKIP_TV)          # no talk / reality shows
        w = lambda s: meta[s][1] / (meta[s][1] + 5000) * meta[s][0] + 5000 / (meta[s][1] + 5000) * 7.2   # noqa: E731
        self.assertEqual(top, sorted(top, key=lambda s: (-w(s), s)))

    def test_genre_filter_maps_tv_genres(self):
        crime = tvdata.top_rated(["Crime"])
        for sid in crime:
            self.assertIn("Crime", tvdata.site_genres(json.loads(q("select genres_json from tv_shows where tmdb_id = ?", (sid,))[0][0])))
        scifi = tvdata.top_rated(["Science Fiction"])        # "Sci-Fi & Fantasy" on TMDB
        self.assertTrue(all("Sci-Fi & Fantasy" in json.loads(q("select genres_json from tv_shows where tmdb_id = ?", (s,))[0][0]) for s in scifi))


class PickTv(unittest.TestCase):
    def test_hard_limits(self):
        r = tvdata.pick("", None, "PG", 30, None, [], "familiar")
        for x in r["picks"] + r["candidates"]:
            self.assertLessEqual(x["episode_runtime"], 30)
            self.assertIn(x["cert"], tvdata.TV_CERT_OK["PG"])                # unknown rating fails too
        self.assertIn("US TV rating too high or unknown", r["removed"])

    def test_services_and_never(self):
        r = tvdata.pick("", None, None, None, ["netflix"], ["Comedy"], "familiar")
        for x in r["picks"] + r["candidates"]:
            self.assertEqual(x["providers"], ["Netflix"])
            self.assertNotIn("Comedy", x["genres"].split(", "))

    def test_followed_shows_left_out_and_stable(self):
        a = tvdata.pick("u-pick")
        first = a["picks"][0]["id"]
        discovery.tv_follow("u-pick", first, True)
        b = tvdata.pick("u-pick")
        self.assertNotIn(first, [x["id"] for x in b["picks"] + b["candidates"]])
        self.assertEqual(tvdata.pick("u-pick")["picks"], b["picks"])        # deterministic
        discovery.tv_follow("u-pick", first, False)

    def test_different_means_less_voted(self):
        for x in tvdata.pick("", novelty="different")["picks"]:
            self.assertTrue((x["rating_src"] == "IMDb" and x["votes"] < 50000) or (x["rating_src"] == "TMDB" and x["votes"] < 1500))


class Episodes(unittest.TestCase):
    def setUp(self):
        self.sid = q("select tmdb_id from tv_shows where status = 'Returning Series' and tmdb_id >= 6000000 order by tmdb_id limit 1")[0][0]

    def test_season_with_imdb_ratings(self):
        eps = tvdata.episodes(self.sid, 1)
        self.assertEqual([e["episode"] for e in eps], sorted(e["episode"] for e in eps))
        aired = [e for e in eps if e["aired"]]
        self.assertTrue(aired and all(e["imdb"] for e in aired))             # IMDb joined by show id + numbers
        self.assertTrue(any(e["type"] == "finale" for e in eps))

    def test_next_after_and_title_match(self):
        nx = tvdata.next_after(self.sid, 1, 3)
        self.assertEqual((nx["season"], nx["episode"]), (1, 4))
        last_s = q("select max(season_number) from tv_episodes where tmdb_id = ?", (self.sid,))[0][0]
        self.assertIsNone(tvdata.next_after(self.sid, last_s, 99))
        t = q("select name from tv_episodes where tmdb_id = ? and season_number = 1 and episode_number = 5", (self.sid,))[0][0]
        self.assertEqual(tvdata.episode_by_title(self.sid, 1, t.upper()), (1, 5))

    def test_continue_watching_uses_real_next_episode(self):
        # the running show's latest season has episodes in the future: progress one before the first future one
        fut = q("select season_number, episode_number, air_date from tv_episodes where tmdb_id = ? and air_date > ? order by air_date limit 1", (self.sid, TODAY))
        s, e, d = fut[0]
        v8extra.set_progress("u-cw", self.sid, s, e - 1)
        it = [x for x in v8extra.continue_watching("u-cw") if x["id"] == self.sid][0]
        self.assertEqual((it["next"]["season"], it["next"]["episode"]), (s, e))
        self.assertTrue(it["next"]["upcoming"])
        self.assertEqual(it["next"]["date"], d)
        self.assertIn("airs", it["next"]["label"])

    def test_show_page(self):
        d = tvdata.show(self.sid)
        self.assertEqual(d["n_seasons"], len(d["seasons"]))
        self.assertTrue(d["imdb"] and d["trailer"] and d["cast"])
        self.assertTrue(d["next_episode"] is None or d["next_episode"]["date"] > TODAY)
        self.assertTrue(all(b["imdb"]["votes"] >= 300 for b in d["best_episodes"]))


class Calendar(unittest.TestCase):
    def test_followed_show_episodes_and_no_double_premiere(self):
        sid = q("select tmdb_id from tv_shows where status = 'Returning Series' and tmdb_id >= 6000000 order by tmdb_id limit 1")[0][0]
        discovery.tv_follow("u-cal", sid, True)
        start = (dt.date.today() - dt.timedelta(days=30)).isoformat()
        cal = discovery.calendar("u-cal", days=60, start=start)
        eps = [e for e in cal["events"] if e["kind"] == "tv_episode" and e["show"]["id"] == sid]
        self.assertTrue(eps)
        prem = {(e["show"]["id"], e["show"]["season"]) for e in cal["events"] if e["kind"] == "tv_season"}
        self.assertFalse([e for e in eps if e["show"]["episode"] == 1 and (sid, e["show"]["season"]) in prem])
        keys = [(e["show"]["id"], e["show"]["season"], e["show"].get("episode")) for e in cal["events"] if e["kind"] == "tv_episode"]
        self.assertEqual(len(keys), len(set(keys)))
        other = discovery.calendar("u-none", days=60, start=start)
        self.assertTrue(all(e.get("finale") for e in other["events"] if e["kind"] == "tv_episode"))   # strangers: finales only


class Phrases(unittest.TestCase):
    def score(self, path):
        d = json.load(open(path))
        norm = lambda x: {k: sorted(v) if isinstance(v, list) else v for k, v in x.items()}   # noqa: E731
        real = [norm(pref_phrases.detect(t["text"])) == norm(t["expect"]) for t in d["questions"] if t["expect"]]
        traps = [bool(pref_phrases.detect(t["text"])) for t in d["questions"] if not t["expect"]]
        return sum(real), len(real), sum(traps), len(traps)

    def test_tuning_set_rule(self):
        p = os.path.join(ROOT, "sets", "tune_v8_phrases.json")
        if not os.path.exists(p):
            self.skipTest("tuning set not on this machine")
        ok, n, false, _t = self.score(p)
        self.assertEqual(ok, n)
        self.assertLessEqual(false, 1)

    def test_check_set(self):
        ok, n, false, _t = self.score(os.path.join(ROOT, "sets", "check_v8_phrases.json"))
        self.assertEqual((ok, false), (n, 0))

    def test_chips_labels(self):
        self.assertEqual([c["label"] for c in pref_phrases.chips("keep it under 90 minutes, no horror")], ["Under 90 min", "No horror"])
        self.assertEqual(pref_phrases.chips("is The Irishman over three hours?"), [])


class LongSummary(unittest.TestCase):
    def chat(self):
        full = []
        for i in range(12):
            u = "We're watching with my 7-year-old, so keep everything PG, and nothing over 90 minutes." if i == 0 else f"Question {i} about a film " + "z" * 60
            full += [{"role": "user", "content": u}, {"role": "assistant", "content": f"Answer {i}. " + "y" * 900}]
        return full

    def test_off_is_the_old_behaviour(self):
        import longsum
        old = longsum.LONG_SUMMARY
        longsum.LONG_SUMMARY = False
        try:
            self.assertEqual(longsum.note(self.chat(), self.chat()[-6:]), "")
            self.assertEqual(longsum.planner_lines([{"q": "a"}] * 8, 4), [])
        finally:
            longsum.LONG_SUMMARY = old

    def test_on_keeps_turn_one_and_the_limits(self):
        import longsum
        old = longsum.LONG_SUMMARY
        longsum.LONG_SUMMARY = True
        try:
            full = self.chat()
            n = longsum.note(full, full[-6:])
            self.assertIn("Under 90 min", n)
            self.assertIn("PG and under", n)
            self.assertIn("7-year-old", n)                                  # the first message survives the size cap
            self.assertLessEqual(len(n), longsum.NOTE_CHARS + 2)
            self.assertEqual(longsum.note(full, full), "")                 # nothing cut: no note
            pl = longsum.planner_lines([{"q": full[0]["content"]}] + [{"q": f"q{i}"} for i in range(7)], 4)
            self.assertTrue(pl[0].startswith("Limits the user set") and "Under 90 min" in pl[0])
        finally:
            longsum.LONG_SUMMARY = old


class StatedLimits(unittest.TestCase):
    def test_v2_limits_become_filters_only_when_on(self):
        import longsum
        old = longsum.LONG_SUMMARY
        try:
            longsum.LONG_SUMMARY = False
            self.assertEqual(longsum.with_stated_limits({"genres": ["Drama"]}, ["keep it PG please"]), {"genres": ["Drama"]})
            longsum.LONG_SUMMARY = True
            f = longsum.with_stated_limits({"max_runtime": 90, "max_cert": "G"}, ["We only have Netflix", "keep it PG, under two hours, no horror"])
            self.assertEqual(f, {"max_runtime": 90, "max_cert": "G", "never_genres": ["Horror"], "services": ["netflix"], "services_only": True, "strict": True})
            self.assertEqual(user_prefs.clean_chat(f)["max_cert"], "G")                      # the stricter value wins
            self.assertIsNone(longsum.with_stated_limits(None, ["who directed Up?"]))
        finally:
            longsum.LONG_SUMMARY = old


class ChatLimits(unittest.TestCase):
    def test_clean_and_merge(self):
        cc = user_prefs.clean_chat({"never_genres": ["Horror", "Nope"], "min_year": "1990", "services": ["hulu", "x"], "services_only": True})
        self.assertEqual(cc, {"services_only": True, "never_genres": ["Horror"], "min_year": 1990, "services": ["hulu"]})
        m = user_prefs.merge_chat(dict(user_prefs.DEFAULTS, never_genres=["War"], min_year=2000), cc)
        self.assertEqual(m["never_genres"], ["Horror", "War"])
        self.assertEqual(m["min_year"], 2000)                                # the stricter of the two
        self.assertEqual((m["services"], m["services_only"]), (["hulu"], True))
        self.assertEqual(user_prefs.clean_chat({"min_year": 1800}), {})


if __name__ == "__main__":
    unittest.main()
