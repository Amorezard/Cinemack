"""v8.1 round 58 (20 Nov 2026): the search's news list also gets Guardian articles from EVERY section that name the
film (one live search when its title is typed out, stored with the film-page lookups, under their own daily and
per-person limits), and the film page's review aspects say "6 of 11 reviews" (not "audience") with a quote of the
same sentiment. No network: every call goes to a fake opener."""
import io
import json
import os
import sqlite3
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, "web"), os.path.join(ROOT, "tools"), ROOT]


class Resp(io.BytesIO):
    def __init__(self, status, body, headers=None):
        super().__init__(json.dumps(body).encode())
        self.status, self.headers = status, headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class TestReviewWords(unittest.TestCase):
    def test_reviews_not_audience_and_matching_quote(self):
        import film_info as FI
        f = sqlite3.connect(":memory:")
        f.execute("create table review_aspects(review_id text, tmdb_id integer, source_type text, aspect text, sentiment integer, "
                  "quote text, model text, version text)")
        f.executemany("insert into review_aspects values (?,?,?,?,?,?,?,?)", [
            ("r1", 9, "audience", "cinematography", -1, "monster CGI is not good", "m", "v"),
            ("r2", 9, "audience", "cinematography", 1, "beautifully shot rubble and ocean scenes", "m", "v"),
            ("r3", 9, "critic", "acting", 1, "a career-best turn", "m", "v")])
        s = FI._aspect_line(f, 9, 'cinematography (audience 6 of 11; "monster CGI is not good")', 1)
        self.assertEqual(s, 'cinematography (6 of 11 reviews; "beautifully shot rubble and ocean scenes")')
        self.assertNotIn("audience", s)
        self.assertEqual(FI._aspect_line(f, 9, 'cinematography (audience 2 of 11; "x")', -1),
                         'cinematography (2 of 11 reviews; "monster CGI is not good")')
        self.assertEqual(FI._aspect_line(f, 9, 'acting (critics 3 of 5; "q")', 1), 'acting (3 of 5 critic reviews; "a career-best turn")')
        self.assertEqual(FI._aspect_line(f, 9, 'pacing (audience 2 of 4)', 1), "pacing (2 of 4 reviews)")       # no quote of that sign
        self.assertEqual(FI._aspect_line(f, 9, "something else", 1), "something else")


class TestNewsLive(unittest.TestCase):
    def setUp(self):
        import guardian_live as G
        import news_search as N
        self.G, self.N = G, N
        self.d = tempfile.mkdtemp()
        self.c = G.connect(os.path.join(self.d, "g.db"))
        self.f = sqlite3.connect(":memory:")
        self.f.execute("create table films(tmdb_id integer primary key, title text, year integer, release_date text, us_release_date text)")
        self.f.execute("insert into films values (11, 'Godzilla Minus One', 2023, '2023-11-03', '2023-12-01')")
        self.f.execute("insert into films values (12, 'Home', 2015, '2015-03-27', null)")
        self.urls = []

    def opener(self, results):
        def op(req, timeout=None):
            self.urls.append(req.full_url)
            return Resp(200, {"response": {"results": results}}, {"X-RateLimit-Remaining-day": "300"})
        return op

    def test_typed_out(self):
        self.assertTrue(self.N.typed_out("godzilla minus one", "Godzilla Minus One"))
        self.assertTrue(self.N.typed_out("Godzilla Minus O", "Godzilla Minus One"))
        self.assertFalse(self.N.typed_out("God", "Godzilla Minus One"))
        self.assertFalse(self.N.typed_out("Pari", "Paris, Texas"))

    def test_all_sections_news_kept_and_stored_once(self):
        res = [{"id": "world/2024/x", "sectionId": "world", "webUrl": "https://www.theguardian.com/world/2024/x",
                "webPublicationDate": "2024-03-11T00:00:00Z", "fields": {"headline": "Godzilla Minus One wins an Oscar for Japan"}},
               {"id": "film/2023/y", "sectionId": "film", "webUrl": "https://www.theguardian.com/film/2023/y",
                "webPublicationDate": "2023-12-01T00:00:00Z", "fields": {"headline": "Godzilla Minus One review", "starRating": "4"},
                "tags": [{"id": "tone/reviews"}]},
               {"id": "world/2024/z", "sectionId": "world", "webUrl": "https://www.theguardian.com/world/2024/z",
                "webPublicationDate": "2024-03-12T00:00:00Z", "fields": {"headline": "A monster storm hits Japan"}}]
        n, why = self.N.live_search(self.f, 11, "u1", c=self.c, key="k", opener=self.opener(res))
        self.assertEqual((n, why), (2, None))
        self.assertNotIn("section=", self.urls[0])                            # every section, not only film
        kinds = sorted(r[0] for r in self.c.execute("select kind from articles where tmdb_id = 11"))
        self.assertEqual(kinds, ["news", "review"])
        n2, why2 = self.N.live_search(self.f, 11, "u1", c=self.c, key="k", opener=self.opener(res))
        self.assertEqual(why2, "searched before")
        self.assertEqual(len(self.urls), 1)
        self.assertEqual(self.c.execute("select count(*) from calls where who = 'search'").fetchone()[0], 1)

    def test_one_word_title_needs_film_context(self):
        res = [{"id": "money/2015/a", "sectionId": "money", "webUrl": "https://www.theguardian.com/money/2015/a",
                "webPublicationDate": "2015-04-01T00:00:00Z", "fields": {"headline": "Home buyers face higher rates"}},
               {"id": "film/2015/b", "sectionId": "film", "webUrl": "https://www.theguardian.com/film/2015/b",
                "webPublicationDate": "2015-03-20T00:00:00Z", "fields": {"headline": "Home: DreamWorks' alien comedy"}}]
        n, _ = self.N.live_search(self.f, 12, "u1", c=self.c, key="k", opener=self.opener(res))
        self.assertEqual(n, 1)

    def test_daily_budget(self):
        old = self.N.SEARCH_LIVE_DAILY
        self.N.SEARCH_LIVE_DAILY = 0
        try:
            self.assertEqual(self.N.live_search(self.f, 11, "u1", c=self.c, key="k", opener=self.opener([]))[1], "daily search budget used")
            self.assertEqual(self.urls, [])
        finally:
            self.N.SEARCH_LIVE_DAILY = old



if __name__ == "__main__":
    unittest.main()
