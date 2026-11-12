"""Round 38 (12 Nov): Trakt key from keys.env + device-code sign-in, friends' reviews, Mind-bending without superhero
films, TV taste in chat recommendations.   No real database, model or network:  python -m unittest tests.test_r38"""
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import types
import unittest

T = tempfile.mkdtemp()
for k, f in (("SOCIAL_DB", "social.db"), ("HISTORY_DB", "history.db"), ("TRAKT_DB", "trakt.db"), ("ALERTS_DB", "alerts.db"),
             ("USERS_DB", "users.db"), ("FACTS_DB", "facts.db"), ("MOVIES_DB", "movies.db"), ("CHATS_DB", "chats.db"),
             ("MEMORY_DB", "memory.db"), ("TMDB_EXTRA_DB", "tmdb_extra.db")):
    os.environ[k] = os.path.join(T, f)
os.environ["GENRE_MAIN"] = "2"
os.environ["TV_TASTE"] = "1"
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
create table keywords(tmdb_id integer, keyword_id integer, name text);
""")
for fid, t, kw in ((27205, "Inception", "dream"), (24428, "The Avengers", "superhero"), (157336, "Interstellar", "space")):
    c.execute("insert into films values (?,?,?,2012,'2012-01-01','2012-01-01',30000,8.0,140,'[\"Science Fiction\", \"Action\"]',null,50,'')", (fid, t, t))
    c.execute("insert into film_images values (?, ?, null)", (fid, f"/p{fid}.jpg"))
    c.execute("insert into keywords values (?, 1, ?)", (fid, kw))
c.commit()
c.close()
subprocess.run([sys.executable, os.path.join(ROOT, "tools", "make_demo_tv.py"), os.environ["FACTS_DB"]], check=True, capture_output=True)
c = sqlite3.connect(os.environ["FACTS_DB"])
SHOWS = [r[0] for r in c.execute("select tmdb_id from tv_shows order by tmdb_id")]
for k, sid in enumerate(SHOWS):
    tg, ig = (["Comedy"], "Comedy") if k % 2 else (["Drama"], "Drama")
    c.execute("update tv_shows set genres_json = ?, imdb_id = ? where tmdb_id = ?", (json.dumps(tg), f"tvx{sid}", sid))
    c.execute("insert or replace into imdb_titles(tconst, title_type, primary_title, genres) values (?, 'tvSeries', ?, ?)", (f"tvx{sid}", f"S{k}", ig))
c.commit()
c.close()

import reviews_feed  # noqa: E402
import social  # noqa: E402
import trakt_sync  # noqa: E402
import tv_prefs  # noqa: E402
import user_prefs  # noqa: E402


class TraktKeys(unittest.TestCase):
    def test_reads_keys_env_under_usual_names(self):
        p = os.path.join(T, "keys.env")
        with open(p, "w") as fh:
            fh.write("# keys\nTMDB_API_KEY=abc\nexport TRAKT_ID='xyz123'  # trakt\nTRAKT_CLIENT_SECRET=\n")
        self.assertEqual(trakt_sync._key_from_files(trakt_sync._ID_NAMES, [p]), "xyz123")
        self.assertEqual(trakt_sync._key_from_files(trakt_sync._SECRET_NAMES, [p]), "")
        self.assertEqual(trakt_sync._key_from_files(trakt_sync._ID_NAMES, [os.path.join(T, "none.env")]), "")

    def test_device_flow(self):
        calls, script = [], [(200, {"device_code": "dev1", "user_code": "ABCD1234", "verification_url": "https://trakt.tv/activate",
                                    "expires_in": 600, "interval": 5}, {}),
                             (400, None, {}),
                             (200, {"access_token": "tok", "refresh_token": "ref", "expires_in": 86400}, {})]

        def fake(method, url, body, headers):
            calls.append((method, url, body))
            if "/sync/" in url:
                return 200, [], {}
            return script.pop(0)
        trakt_sync.HTTP, trakt_sync.CLIENT_ID, trakt_sync.CLIENT_SECRET = fake, "cid", ""
        try:
            d = trakt_sync.device_start("acct77")
            self.assertEqual((d["user_code"], d["interval"]), ("ABCD1234", 5))
            self.assertEqual(trakt_sync.device_poll("acct77")["status"], "pending")
            r = trakt_sync.device_poll("acct77")
            self.assertEqual(r["status"], "connected")
            self.assertTrue(trakt_sync.connected("acct77")["connected"])
            self.assertEqual(calls[2][2], {"code": "dev1", "client_id": "cid"})            # no secret sent when none set
            self.assertEqual(trakt_sync.device_poll("acct77")["status"], "none")
        finally:
            trakt_sync.HTTP = None

    def test_device_denied_and_no_key(self):
        trakt_sync.HTTP = lambda m, u, b, h: (200, {"device_code": "d", "user_code": "U", "expires_in": 600, "interval": 5}, {}) \
            if u.endswith("/device/code") else (418, None, {})
        trakt_sync.CLIENT_ID = "cid"
        try:
            trakt_sync.device_start("acct78")
            self.assertEqual(trakt_sync.device_poll("acct78")["status"], "denied")
            import urllib.error

            def down(m, u, b, h):
                raise urllib.error.URLError("no route")
            trakt_sync.HTTP = down
            with self.assertRaises(trakt_sync.TraktError):
                trakt_sync.device_start("acct79")
            trakt_sync.CLIENT_ID = ""
            with self.assertRaises(trakt_sync.TraktError):
                trakt_sync.device_start("acct78")
        finally:
            trakt_sync.HTTP = None


class FriendsReviews(unittest.TestCase):
    def test_only_what_friends_share(self):
        a, b, x = "acct1", "acct2", "acct3"
        self.assertEqual(social.request(a, social.code_of(b)), "requested")
        social.respond(b, a, True)
        social.save_review(b, 27205, 4.5, "Dreams within dreams.")
        social.save_review(b, 157336, 3.0, "")
        social.save_review(x, 27205, 5.0, "Not a friend.")
        self.assertEqual(reviews_feed.friends_reviews(a)["reviews"], [])                     # private by default
        social.set_privacy(b, {"ratings": "friends"})
        r = reviews_feed.friends_reviews(a)
        self.assertEqual({(x["id"], x["rating"], x["text"]) for x in r["reviews"]}, {(27205, 4.5, ""), (157336, 3.0, "")})
        social.set_privacy(b, {"reviews": "friends"})
        social.hide(b, "reviews", 27205)
        r = reviews_feed.friends_reviews(a)
        self.assertEqual({x["id"]: x["text"] for x in r["reviews"]}[27205], "")
        social.hide(b, "reviews", 27205, False)
        r = reviews_feed.friends_reviews(a)
        top = {x["id"]: x for x in r["reviews"]}[27205]
        self.assertEqual((top["text"], top["title"]), ("Dreams within dreams.", "Inception"))
        self.assertEqual((r["friends"], r["sharing"]), (1, 1))
        self.assertEqual(reviews_feed.friends_reviews("guestbrowser")["reviews"], [])


class MindBending(unittest.TestCase):
    def test_mood_kept_and_superheroes_left_out(self):
        c = user_prefs.clean_chat({"genre": "Science Fiction", "mood": "mind", "strict": True})
        self.assertEqual(c.get("mood"), "mind")
        self.assertNotIn("mood", user_prefs.clean_chat({"mood": "evil"}))
        s = user_prefs.merge_chat(user_prefs.DEFAULTS, c)
        hits = [{"id": 27205, "title": "Inception"}, {"id": 24428, "title": "The Avengers"}, {"id": 157336, "title": "Interstellar"}]
        self.assertEqual(user_prefs.excluded(s, hits), {24428})
        s2 = user_prefs.merge_chat(user_prefs.DEFAULTS, user_prefs.clean_chat({"genre": "Science Fiction"}))
        self.assertEqual(user_prefs.excluded(s2, hits), set())
        self.assertNotEqual(user_prefs.filter_tag(s), user_prefs.filter_tag(s2))


class TvTasteInChat(unittest.TestCase):
    def test_retrieve_tv_wrapped_for_current_person(self):
        fake = types.ModuleType("tv")
        hits = [{"id": -s, "tv_id": s, "_tv": True, "title": f"S{s}"} for s in SHOWS[:6]]
        hits[0]["_plan"] = {"intent": "recommend", "media": "tv"}
        fake.retrieve_tv = lambda question, k=3, recent_titles=None, why=None: [dict(h) for h in hits]
        sys.modules["tv"] = fake
        try:
            self.assertTrue(tv_prefs.install_chat())
            user_prefs.CURRENT["user"] = None
            self.assertEqual([h["tv_id"] for h in fake.retrieve_tv("q")], SHOWS[:6])          # no person: untouched
            tv_prefs.choose("u-chat", SHOWS[0], "S0", "not")
            tv_prefs.save_groups("u-chat", ["sitcom"], [])
            user_prefs.CURRENT["user"] = "u-chat"
            got = fake.retrieve_tv("q")
            ids = [h["tv_id"] for h in got]
            self.assertNotIn(SHOWS[0], ids)
            comedy = [s for k, s in enumerate(SHOWS[:6]) if k % 2]
            self.assertEqual(ids[:len(comedy)], comedy)                                       # loved kind first
            self.assertEqual(got[0]["_plan"]["intent"], "recommend")
            self.assertEqual(sum(1 for h in got if "_plan" in h), 1)
            self.assertTrue(tv_prefs.taste_tag("u-chat"))
            self.assertEqual(tv_prefs.taste_tag("nobody"), "")
        finally:
            user_prefs.CURRENT["user"] = None
            sys.modules.pop("tv", None)


if __name__ == "__main__":
    unittest.main()
