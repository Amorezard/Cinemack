"""9 Nov (v8 web): profiles/privacy/friends (leak matrix), watch-history imports, Trakt (fake HTTP), calendar and
alerts on a synthetic facts.db. No server, no model, no network.
    python -m unittest tests.test_v8_web
"""
import json
import os
import sqlite3
import sys
import tempfile
import unittest

T = tempfile.mkdtemp()
for k, f in (("SOCIAL_DB", "social.db"), ("HISTORY_DB", "history.db"), ("TRAKT_DB", "trakt.db"), ("ALERTS_DB", "alerts.db"),
             ("USERS_DB", "users.db"), ("FACTS_DB", "facts.db"), ("MOVIES_DB", "movies.db"), ("WIKI_DB", "wiki.db")):
    os.environ[k] = os.path.join(T, f)
os.environ["TRAKT_CLIENT_ID"] = "test-client"
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.join(HERE, "..", "web"), os.path.join(HERE, "..")]

c = sqlite3.connect(os.environ["FACTS_DB"])
c.executescript("""
create table films(tmdb_id integer primary key, title text, original_title text, year integer, release_date text,
  us_release_date text, vote_count integer, vote_average real, runtime integer, genres_json text, imdb_id text, popularity real);
create table tv_shows(tmdb_id integer primary key, name text, original_name text, first_year integer, vote_count integer,
  networks_json text, poster_path text);
create table tv_seasons(tmdb_id integer, season_number integer, air_date text, poster_path text);
create table certifications(tmdb_id integer, region text, certification text, release_type integer, release_date text);
create table watch_providers(tmdb_id integer, region text, kind text, provider_id integer, provider_name text, priority integer);
create table lists(list_name text, region text, tmdb_id integer, rank integer, fetched_at text);
create table film_images(tmdb_id integer primary key, poster_path text, backdrop_path text);
""")
FILMS = [(1, "Parasite", "Gisaengchung", 2019, 18000), (2, "The Office", "The Office", 2005, 50),
         (3, "Crash", "Crash", 2004, 9000), (4, "Crash", "Crash", 1996, 2000), (5, "Heat", "Heat", 1995, 7000),
         (6, "Heat", "Heat", 1986, 100), (7, "Future Film", "Future Film", 2099, 0), (8, "Roma", "Roma", 2018, 4000)]
for fid, t, ot, y, vc in FILMS:
    c.execute("insert into films(tmdb_id, title, original_title, year, release_date, vote_count) values (?,?,?,?,?,?)",
              (fid, t, ot, y, f"{y}-06-01", vc))
c.execute("insert into tv_shows values (100, 'The Office', 'The Office', 2005, 4000, '[\"NBC\"]', '/o.jpg')")
c.execute("insert into tv_shows values (101, 'Stranger Things', 'Stranger Things', 2016, 9000, '[\"Netflix\"]', '/s.jpg')")
c.execute("insert into tv_seasons values (101, 6, '2099-07-01', null)")
c.execute("insert into certifications values (7, 'US', 'PG-13', 3, '2099-07-04T00:00:00.000Z')")
c.execute("insert into certifications values (7, 'US', '', 4, '2099-08-15T00:00:00.000Z')")
c.commit()
c.close()

import social      # noqa: E402
import history     # noqa: E402
import trakt_sync  # noqa: E402
import discovery   # noqa: E402
import film_info   # noqa: E402

A, B, C, D = "acct1", "acct2", "acct3", "acct4"     # owner, friend, non-friend, blocked
GUEST = "browser-xyz"


class Privacy(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        social.request(B, social.code_of(A))
        social.respond(A, B, True)
        social.request(D, social.code_of(A))
        social.respond(A, D, True)
        social.block(A, D)

    def test_private_by_default(self):
        for k in social.KINDS:
            self.assertFalse(social.can_view(A, B, k), k)
            self.assertTrue(social.can_view(A, A, k), k)

    def test_leak_matrix(self):
        social.set_privacy(A, {k: "friends" for k in social.KINDS})
        try:
            for k in social.KINDS:
                self.assertTrue(social.can_view(A, B, k), f"friend should see {k}")
                for viewer in (C, D, GUEST, "", None):
                    self.assertFalse(social.can_view(A, viewer, k), f"{viewer} must not see {k}")
        finally:
            social.set_privacy(A, {k: "private" for k in social.KINDS})

    def test_hidden_items_not_shown_to_friends(self):
        social.set_privacy(A, {"watchlist": "friends"})
        social.hide(A, "watchlist", 5)
        items = [{"id": 5}, {"id": 8}]
        self.assertEqual(social.visible_items(A, B, "watchlist", items), [{"id": 8}])
        self.assertEqual(social.visible_items(A, A, "watchlist", items), items)
        self.assertEqual(social.visible_items(A, C, "watchlist", items), [])
        social.set_privacy(A, {"watchlist": "private"})

    def test_requests_need_both_sides_and_blocks_stop_them(self):
        social.request(C, social.code_of(B))
        self.assertFalse(social.are_friends(B, C))
        with self.assertRaises(social.SocialError):
            social.request(D, social.code_of(A))          # blocked: same reply as an unknown code
        with self.assertRaises(social.SocialError):
            social.request(A, "NOPE1234")
        self.assertFalse(social.are_friends(A, D))

    def test_guests_cannot_share(self):
        with self.assertRaises(social.SocialError):
            social.set_privacy(GUEST, {"profile": "friends"})
        self.assertIsNone(social.code_of(GUEST))

    def test_delete_user_removes_links(self):
        social.request("acct9", social.code_of(B))
        social.delete_user("acct9")
        self.assertEqual([f for f in social.friends(B)["incoming"] if f["key"] == "acct9"], [])


class WatchlistOverview(unittest.TestCase):
    """The Watchlist page plan: last chance, together (shared lists + taste), watch next reasons, privacy."""
    @classmethod
    def setUpClass(cls):
        import datetime as dt
        cls.me, cls.pal, cls.stranger = "acct30", "acct31", "acct32"
        social.request(cls.pal, social.code_of(cls.me)); social.respond(cls.me, cls.pal, True)
        today = dt.date.today()
        fc = sqlite3.connect(os.environ["FACTS_DB"])
        rows = [(40, "Old Run", today - dt.timedelta(days=50)), (41, "New Run", today - dt.timedelta(days=7)),
                (42, "Streamer", today - dt.timedelta(days=400)), (43, "Shared Pick", today - dt.timedelta(days=300))]
        for fid, t, d in rows:
            fc.execute("insert or ignore into films(tmdb_id, title, year, release_date, us_release_date, vote_count) values (?,?,?,?,?,100)",
                       (fid, t, d.year, d.isoformat(), d.isoformat()))
            fc.execute("insert into certifications values (?, 'US', 'R', 3, ?)", (fid, d.isoformat() + "T00:00:00.000Z"))
        fc.execute("insert into watch_providers values (42, 'US', 'flatrate', 8, 'Netflix', 1)")
        now = today.isoformat() + "T06:00:00"
        fc.execute("delete from lists where list_name = 'now_playing'")
        fc.executemany("insert into lists values ('now_playing', 'US', ?, ?, ?)", [(40, 1, now), (41, 2, now)])
        fc.commit(); fc.close()
        for fid in (40, 41, 42, 43):
            film_info.choose(cls.me, fid, "", "want")
        film_info.choose(cls.pal, 43, "", "want")
        film_info.choose(cls.stranger, 43, "", "want")
        for fid, ch in ((1, "liked"), (3, "liked"), (5, "not"), (8, "liked")):
            film_info.choose(cls.me, fid, "", ch)
        for fid, ch in ((1, "liked"), (3, "liked"), (5, "liked"), (8, "liked")):
            film_info.choose(cls.pal, fid, "", ch)
        social.save_review(cls.pal, 41, 5, "")

    def test_last_chance_and_status(self):
        o = discovery.watchlist_overview(self.me)
        self.assertEqual([x["film"]["title"] for x in o["last_chance"]], ["Old Run"])
        st = {c["title"]: c["status"] for c in o["items"]}
        self.assertEqual(st["Streamer"], "streaming"); self.assertEqual(st["New Run"], "theatres")

    def test_together_respects_privacy(self):
        social.set_privacy(self.pal, {k: "private" for k in social.KINDS})
        self.assertEqual(discovery.watchlist_overview(self.me)["together"], [])
        social.set_privacy(self.pal, {"watchlist": "friends"})
        t = discovery.watchlist_overview(self.me)["together"]
        self.assertEqual([c["title"] for c in t[0]["both"]], ["Shared Pick"])
        self.assertEqual(t[0]["taste"]["films"], 0)            # ratings still private
        social.set_privacy(self.pal, {"ratings": "friends"})
        t = discovery.watchlist_overview(self.me)["together"][0]
        self.assertEqual((t["taste"]["films"], t["taste"]["agree"], t["taste"]["pct"]), (4, 3, 75))
        self.assertEqual([c["title"] for c in t["loved"]], ["New Run"])
        social.set_privacy(self.pal, {k: "private" for k in social.KINDS})

    def test_stranger_never_appears(self):
        social.set_privacy(self.stranger, {k: "friends" for k in social.KINDS})
        names = [t["key"] for t in discovery.watchlist_overview(self.me)["together"]]
        self.assertNotIn(self.stranger, names)

    def test_watch_next_has_reasons(self):
        nxt = discovery.watchlist_overview(self.me)["watch_next"]
        self.assertTrue(nxt)
        self.assertTrue(all(c["why"] for c in nxt))
        self.assertIn("Before it leaves theatres", [c for c in nxt if c["title"] == "Old Run"][0]["why"])


class Evening9Nov(unittest.TestCase):
    """Home genre chips, showtimes links, followed TV shows, shared chat filters."""
    @classmethod
    def setUpClass(cls):
        import datetime as dt
        today = dt.date.today()
        fc = sqlite3.connect(os.environ["FACTS_DB"])
        rows = [(60, "Tense One", '["Thriller", "Mystery"]', 9000, 8.1), (61, "Laugh One", '["Comedy"]', 9000, 8.3),
                (62, "Tense Two", '["Thriller"]', 5000, 7.4), (63, "No Genre", '[]', 5000, 9.0)]
        for fid, t, g, vc, va in rows:
            fc.execute("insert or ignore into films(tmdb_id, title, year, release_date, us_release_date, vote_count, vote_average, genres_json) "
                       "values (?,?,?,?,?,?,?,?)", (fid, t, today.year, today.isoformat(), today.isoformat(), vc, va, g))
            fc.execute("insert into certifications values (?, 'US', 'R', 3, ?)", (fid, today.isoformat() + "T00:00:00.000Z"))
        now = today.isoformat() + "T06:00:00"
        fc.execute("delete from lists where list_name = 'now_playing'")
        fc.executemany("insert into lists values ('now_playing', 'US', ?, ?, ?)", [(60, 1, now), (61, 2, now), (63, 3, now)])
        fc.execute("insert or ignore into tv_shows values (200, 'Ongoing Show', 'Ongoing Show', 2020, 5000, '[\"HBO\"]', '/x.jpg')")
        fc.execute("insert into tv_seasons values (200, 1, '2020-01-01', null)")
        fc.execute("insert into tv_seasons values (200, 2, ?, null)", ((today + dt.timedelta(days=30)).isoformat(),))
        fc.execute("insert into tv_seasons values (200, 3, null, null)")                 # announced, no date
        fc.commit(); fc.close()
        cls.future = (today + dt.timedelta(days=30)).isoformat()

    def test_home_genre_filter_has_no_other_genres(self):
        h = discovery.home("", ["Thriller"])
        self.assertEqual(h["genres"], ["Thriller"])
        for r in h["rails"]:
            for c in r["items"]:
                self.assertIn("Thriller", c.get("genres", ""), f"{c['title']} in {r['key']}")
        titles = {c["title"] for r in h["rails"] for c in r["items"]}
        self.assertIn("Tense One", titles); self.assertNotIn("Laugh One", titles); self.assertNotIn("No Genre", titles)
        self.assertIn("Laugh One", {c["title"] for c in h["hero"]})        # the carousel is not filtered
        self.assertTrue(any(r["key"] == "genre_top" and r["items"] for r in h["rails"]))

    def test_showtimes_only_for_films_in_theatres(self):
        t = discovery.theatres(60, "01845")
        self.assertTrue(t["in_theatres"]); self.assertIn("01845", t["links"][0]["url"])
        self.assertTrue(all(l["url"].startswith("https://") for l in t["links"]))
        self.assertEqual(discovery.theatres(62), {"in_theatres": False, "links": []})
        self.assertNotIn("zip", discovery.theatres(60, "abc")["links"][0]["url"])
        self.assertTrue(discovery.cards([60])[0].get("in_theatres")); self.assertFalse(discovery.cards([62])[0].get("in_theatres"))

    def test_follow_show_next_season_and_one_alert(self):
        u = "browser-tvfan"
        discovery.tv_follow(u, 200)
        sh = discovery.tv_following(u)[0]
        self.assertEqual(sh["next_season"], {"number": 2, "date": self.future})    # the undated season 3 is not shown
        self.assertEqual(sh["seasons_out"], 1); self.assertEqual(sh["badge"], "Upcoming season")
        a1 = [a for a in discovery.alerts(u)["alerts"] if a["kind"].startswith("tv_")]
        a2 = [a for a in discovery.alerts(u)["alerts"] if a["kind"].startswith("tv_")]
        self.assertEqual(len(a1), 1); self.assertEqual(len(a2), 1)
        self.assertIn("season 2 has a date", a1[0]["text"])
        ev = [e for e in discovery.calendar(u, days=60)["events"] if e["kind"] == "tv_season" and e["show"]["id"] == 200]
        self.assertTrue(ev and ev[0]["following"])
        discovery.tv_follow(u, 200, True, notify=False)
        self.assertFalse(discovery.tv_following(u)[0]["notify"])
        discovery.tv_follow(u, 200, False)
        self.assertEqual(discovery.tv_following(u), [])

    def test_chat_filters_take_several_genres_and_brief(self):
        import user_prefs
        c = user_prefs.clean_chat({"genres": ["Thriller", "Mystery", "Nope"], "brief": True})
        self.assertEqual(c, {"genres": ["Thriller", "Mystery"], "brief": True})
        s = user_prefs.merge_chat(dict(user_prefs.DEFAULTS), c)
        self.assertEqual(s["only_genres"], ["Mystery", "Thriller"]); self.assertEqual(s["length"], "short")
        s2 = user_prefs.merge_chat(dict(user_prefs.DEFAULTS, length="detailed"), c)
        self.assertEqual(s2["length"], "detailed")


NETFLIX_SIMPLE = 'Title,Date\n"Parasite","3/1/23"\n"The Office: Season 2: The Dundies","3/2/23"\n' \
                 '"The Office: Season 2: Sexual Harassment","3/3/23"\n"Crash","3/4/23"\n"Unknown Film Xyz","3/5/23"\n'
NETFLIX_FULL = ("Profile Name,Start Time,Duration,Attributes,Title,Supplemental Video Type,Device Type,Bookmark,Latest Bookmark,Country\n"
                "Ann,2023-03-01 20:00:00,02:10:00,,Parasite,,TV,02:10:00,02:10:00,US\n"
                "Ann,2023-03-01 19:58:00,00:00:30,Autoplayed: user action: None;,Stranger Things: Season 4: Chapter One,,TV,,,US\n"
                "Ann,2023-03-02 19:00:00,00:02:00,,Roma (Trailer),TRAILER,TV,,,US\n"
                "Ann,2023-03-03 21:00:00,00:50:00,,Stranger Things: Season 4: Chapter One,,TV,00:50:00,00:50:00,US\n")


class History(unittest.TestCase):
    def test_netflix_simple_no_silent_drops_and_no_duplicates(self):
        rows = history.parse_netflix(NETFLIX_SIMPLE)
        r = history.import_rows("u1", "netflix", rows)
        self.assertEqual(r["rows"], 5)
        self.assertEqual(r["matched"] + len(r["issues"]), r["rows"])          # every row accounted for
        reasons = {i["raw"]: i["reason"] for i in r["issues"]}
        self.assertIn("Unknown Film Xyz", reasons)
        self.assertEqual(reasons.get("Crash"), "ambiguous: 2 titles match")   # 9000 vs 2000 votes: not 5x
        again = history.import_rows("u1", "netflix", history.parse_netflix(NETFLIX_SIMPLE))
        self.assertEqual(again["added"], 0)
        h = history.history("u1")
        office = [x for x in h if x["media"] == "tv" and x["id"] == 100]
        self.assertEqual(office[0]["episodes"], 2)
        self.assertTrue(any(x["id"] == 1 for x in h))

    def test_netflix_full_skips_autoplay_and_trailers(self):
        rows = history.parse_netflix(NETFLIX_FULL)
        self.assertEqual([r["title"] for r in rows], ["Parasite", "Stranger Things"])
        r = history.import_rows("u2", "netflix", rows)
        self.assertEqual(r["matched"], 2)

    def test_dominant_title_wins(self):
        m = history.Matcher()
        self.assertEqual(m.film("Heat")[0][0], 5)         # 7000 vs 100 votes
        self.assertEqual(m.film("Crash", 1996)[0][0], 4)  # the year decides

    def test_letterboxd_watched(self):
        txt = "Date,Name,Year,Letterboxd URI\n2024-01-02,Parasite,2019,https://boxd.it/a\n2024-01-03,Crash,1996,https://boxd.it/b\n"
        r = history.import_rows("u3", "letterboxd", history.parse_letterboxd_watched(txt))
        self.assertEqual((r["matched"], len(r["issues"])), (2, 0))

    def test_not_netflix(self):
        with self.assertRaises(ValueError):
            history.parse_netflix("a,b\n1,2\n")


class Trakt(unittest.TestCase):
    def test_pkce_flow_sync_and_disconnect(self):
        calls = []

        def fake(method, url, body, headers):
            calls.append((method, url, body))
            if url.endswith("/oauth/token"):
                self.assertNotIn("client_secret", body)
                self.assertTrue(body.get("code_verifier"))
                return 200, {"access_token": "AT", "refresh_token": "RT", "expires_in": 7776000}, {}
            if "/sync/history/movies" in url:
                return 200, [{"id": 11, "type": "movie", "watched_at": "2024-02-01T20:00:00.000Z",
                              "movie": {"title": "Parasite", "year": 2019, "ids": {"tmdb": 1}}},
                             {"id": 12, "type": "movie", "watched_at": "2024-02-02T20:00:00.000Z",
                              "movie": {"title": "No Id", "year": 2000, "ids": {}}}], {"X-Pagination-Page-Count": "1"}
            if "/sync/history/episodes" in url:
                return 200, [{"id": 13, "type": "episode", "watched_at": "2024-02-03T20:00:00.000Z",
                              "episode": {"season": 1, "number": 1, "title": "Pilot"},
                              "show": {"title": "Stranger Things", "year": 2016, "ids": {"tmdb": 101}}}], {}
            if "/sync/ratings/movies" in url or "/sync/watchlist/movies" in url:
                return 200, [], {}
            if url.endswith("/oauth/revoke"):
                return 200, None, {}
            return 404, None, {}
        trakt_sync.HTTP = fake
        try:
            url = trakt_sync.start("acct1")
            self.assertIn("code_challenge_method=S256", url)
            state = url.split("state=")[1].split("&")[0]
            self.assertEqual(trakt_sync.finish(state, "CODE"), "acct1")
            with self.assertRaises(trakt_sync.TraktError):
                trakt_sync.finish(state, "CODE")                  # a state works once
            r = trakt_sync.sync("acct1")["history"]
            self.assertEqual((r["rows"], r["matched"], len(r["issues"])), (3, 2, 1))
            self.assertEqual(trakt_sync.sync("acct1")["history"]["added"], 0)
            self.assertTrue(trakt_sync.connected("acct1")["connected"])
            self.assertEqual(trakt_sync.disconnect("acct1")["removed_events"], 2)
            self.assertFalse(trakt_sync.connected("acct1")["connected"])
        finally:
            trakt_sync.HTTP = None


class CalendarAlerts(unittest.TestCase):
    def test_calendar_dated_only_and_labelled(self):
        cal = discovery.calendar("", days=36500, start="2099-01-01")
        kinds = {(e.get("film") or e.get("show"))["title"]: e["kind"] for e in cal["events"]}
        self.assertEqual({e["kind"] for e in cal["events"] if e["media"] == "movie"}, {"theatrical", "digital"})
        self.assertEqual(kinds.get("Stranger Things"), "tv_season")
        theatre = [e for e in cal["events"] if e["kind"] == "theatrical"][0]
        self.assertIn("google.com/search", theatre["showtimes"]["url"])
        self.assertNotIn("time", json.dumps(theatre).replace("showtimes", ""))   # never a showtime value

    def test_calendar_skips_repeat_digital_and_non_us(self):
        fc = sqlite3.connect(os.environ["FACTS_DB"])
        fc.execute("insert or ignore into films(tmdb_id, title, year, release_date, vote_count) values (9, 'Old Digital', 2097, '2097-02-01', 500)")
        fc.execute("insert or ignore into films(tmdb_id, title, year, release_date, vote_count) values (10, 'Abroad Only', 2099, '2099-03-01', 50)")
        fc.execute("insert into certifications values (9, 'US', 'R', 4, '2098-01-01T00:00:00.000Z')")
        fc.execute("insert into certifications values (9, 'US', 'R', 4, '2099-02-01T00:00:00.000Z')")   # new service, not new film
        fc.commit(); fc.close()
        titles = {(e.get("film") or {}).get("title") for e in discovery.calendar("", days=365, start="2099-01-01")["events"]}
        self.assertNotIn("Old Digital", titles)
        self.assertNotIn("Abroad Only", titles)
        self.assertIn("Future Film", titles)

    def test_rereleases_kept_apart(self):
        import datetime as dt
        today = dt.date.today()
        fc = sqlite3.connect(os.environ["FACTS_DB"])
        fc.execute("insert or ignore into films(tmdb_id, title, year, release_date, us_release_date, vote_count) values "
                   "(11, 'Classic', 1976, '1976-05-01', '1976-05-01', 900)")
        fc.execute("insert or ignore into films(tmdb_id, title, year, release_date, us_release_date, vote_count) values "
                   "(12, 'Brand New', ?, ?, ?, 40)", (today.year, today.isoformat(), today.isoformat()))
        fc.execute("insert into certifications values (11, 'US', 'R', 3, '1976-05-01T00:00:00.000Z')")
        fc.execute("insert into certifications values (11, 'US', 'R', 3, '2099-05-01T00:00:00.000Z')")
        fc.execute("insert into certifications values (12, 'US', 'PG', 3, ?)", (today.isoformat() + "T00:00:00.000Z",))
        now = today.isoformat() + "T06:00:00"
        fc.execute("delete from lists where list_name = 'now_playing'")
        fc.executemany("insert into lists values ('now_playing', 'US', ?, ?, ?)", [(11, 1, now), (12, 2, now)])
        fc.commit(); fc.close()
        rails = {r["key"]: [c["title"] for c in r["items"]] for r in discovery.home("")["rails"]}
        self.assertEqual(rails["now_playing"], ["Brand New"])
        self.assertEqual(rails["back_in_theatres"], ["Classic"])
        ev = [e for e in discovery.calendar("", days=365, start="2099-01-01")["events"] if (e.get("film") or {}).get("title") == "Classic"]
        self.assertEqual([e["kind"] for e in ev], ["theatrical_rerelease"])
        self.assertIn("re-release", ev[0]["label"])

    def test_rerelease_edge_cases(self):
        import datetime as dt
        today = dt.date.today()
        fc = sqlite3.connect(os.environ["FACTS_DB"])
        rows = [(13, "Restored", 1991, "1991-10-10", today.isoformat()),          # restoration, no older US date
                (14, "Expanding", 2099, "2099-03-01", "2099-03-01")]               # limited, then wide 3 weeks later
        for r in rows:
            fc.execute("insert or ignore into films(tmdb_id, title, year, release_date, us_release_date, vote_count) values (?,?,?,?,?,10)", r)
        fc.execute("insert into certifications values (14, 'US', 'R', 2, '2099-03-01T00:00:00.000Z')")
        fc.execute("insert into certifications values (14, 'US', 'R', 3, '2099-03-22T00:00:00.000Z')")
        now = today.isoformat() + "T06:00:00"
        fc.execute("delete from lists where list_name = 'now_playing'")
        fc.execute("insert into lists values ('now_playing', 'US', 13, 1, ?)", (now,))
        fc.commit(); fc.close()
        rails = {r["key"]: [c["title"] for c in r["items"]] for r in discovery.home("")["rails"]}
        self.assertEqual(rails["now_playing"], [])
        self.assertEqual(rails["back_in_theatres"], ["Restored"])
        ev = [e["kind"] for e in discovery.calendar("", days=60, start="2099-03-15")["events"] if (e.get("film") or {}).get("title") == "Expanding"]
        self.assertEqual(ev, ["theatrical"])

    def test_untyped_dates_need_interest(self):
        fc = sqlite3.connect(os.environ["FACTS_DB"])
        for fid, t, pop in ((20, "Known A", 10.0), (21, "Known B", 30.0), (22, "Big Untyped", 50.0), (23, "Tiny Untyped", 0.5)):
            fc.execute("insert or ignore into films(tmdb_id, title, year, release_date, us_release_date, vote_count, popularity) "
                       "values (?,?,2098,'2098-06-10','2098-06-10',0,?)", (fid, t, pop))
        for fid in (20, 21):
            fc.execute("insert into certifications values (?, 'US', 'PG', 3, '2098-06-10T00:00:00.000Z')", (fid,))
        fc.executemany("insert into lists values ('upcoming', 'US', ?, ?, 'x')", [(22, 1), (23, 2)])
        fc.commit(); fc.close()
        titles = {(e.get("film") or {}).get("title") for e in discovery.calendar("", days=30, start="2098-06-01")["events"]}
        self.assertIn("Big Untyped", titles)
        self.assertNotIn("Tiny Untyped", titles)

    def test_alerts_once_per_change(self):
        user = "acct5"
        film_info.choose = film_info.choose.__wrapped__ if hasattr(film_info.choose, "__wrapped__") else film_info.choose
        with film_info._lock:
            film_info._users().execute("insert or replace into watch values (?,?,?,?,?,?)", (user, 8, "Roma", "want", "", "x"))
            film_info._users().commit()
        self.assertEqual(discovery.alerts(user)["alerts"], [])           # first look: snapshot only
        fc = sqlite3.connect(os.environ["FACTS_DB"])
        fc.execute("insert into watch_providers values (8, 'US', 'flatrate', 8, 'Netflix', 1)")
        fc.commit()
        fc.close()
        film_info._C.clear()
        a = discovery.alerts(user)
        self.assertEqual([(x["kind"], x["detail"]) for x in a["alerts"]], [("now_streaming", "Netflix")])
        self.assertEqual(len(discovery.alerts(user)["alerts"]), 1)          # no duplicate
        self.assertFalse(any(x["kind"] == "leaving_soon" for x in a["alerts"]))


if __name__ == "__main__":
    unittest.main()
