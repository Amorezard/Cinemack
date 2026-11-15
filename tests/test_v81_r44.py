"""v8.1 round 44: identity tables (1.1), recommendation events (5.1), deal offers (9.1). No real database, no network:
    python -m unittest tests.test_v81_r44"""
import copy
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(HERE, "web"))
import deals as D  # noqa: E402
import media_identity as M  # noqa: E402
import rec_events as R  # noqa: E402

TMP = tempfile.mkdtemp()
KIT = os.path.join(HERE, "planning", "cinemack-v8.1")
OFFER = {"schema_version": 1, "offer_id": "EXAMPLE_NOT_A_REAL_OFFER", "provider_name": "Example Cinema",
         "offer_type": "discount_day", "title": "Illustrative Tuesday member offer", "region": "US-MA",
         "location_ids": [], "eligible_weekdays": ["tuesday"], "eligibility": ["Hypothetical free membership requirement"],
         "exclusions": ["Terms unknown; example only"], "promotion_terms": "FICTIONAL SAMPLE. No actual offer is being asserted.",
         "price_status": "unknown", "price": None, "currency": None, "source_url": "https://example.com/not-a-real-offer",
         "last_verified_at": "2026-11-12T20:00:00Z", "verification_status": "unverified", "valid_from": None, "valid_until": None}
EVENT = {"schema_version": 1, "event_id": "EXAMPLE_E1", "occurred_at": "2026-11-12T21:00:00Z", "subject_key": None,
         "session_key": "EXAMPLE_SESSION", "media_id": "EXAMPLE_FILM", "placement": "pick_tonight", "event_type": "impression",
         "ranking_version": "example-ranker-v1", "exposure_id": "EXAMPLE_EXPOSURE", "position": 0,
         "context_key": "EXAMPLE_CONTEXT", "consent_scope": "anonymous", "explicit_rating": None}
NOW = datetime(2026, 11, 14, 12, 0, tzinfo=timezone.utc)


def facts():
    c = sqlite3.connect(":memory:")
    c.executescript("""create table films(tmdb_id integer primary key, imdb_id text, wikidata_id text, wiki_doc_id text,
        title text, original_title text, release_date text, year integer);
      create table tv_shows(tmdb_id integer primary key, imdb_id text, tvdb_id integer, wikidata_id text, name text,
        original_name text, first_air_date text, first_year integer);
      create table imdb_titles(tconst text primary key, title_type text, start_year integer);""")
    c.executemany("insert into films values (?,?,?,?,?,?,?,?)", [
        (1, "tt0000001", "Q1", "d1", "Heat", "Heat", "1995-12-15", 1995),
        (2, "tt0000002", None, None, "Really A Series", "x", "2001-01-01", 2001),      # IMDb says tvSeries
        (3, "tt0000003", "Q3", None, "Gone From IMDb", "x", "2010-01-01", 2010),       # not in imdb_titles
        (4, "tt0000004", "Q4", None, "Year Gap", "x", "2015-01-01", 2015),             # IMDb year 2011
        (5, None, None, None, "No Ids", "x", None, None),
        (6, "tt0000006", "Q6", None, "Shared With Show", "x", "2019-01-01", 2019),
        (8, "tt0000008", "Q8", None, "Shared, IMDb Undecided", "x", "2020-01-01", 2020)])
    c.executemany("insert into tv_shows values (?,?,?,?,?,?,?,?)", [
        (1, "tt0000101", 77, "Q101", "Same Number As Heat", "x", "2005-01-01", 2005),
        (6, "tt0000006", None, "Q106", "Shared With Film", "x", "2019-05-01", 2019),
        (7, "tt0000107", None, None, "Really A Film", "x", "2012-01-01", 2012),
        (8, "tt0000008", None, None, "Shared, IMDb Undecided", "x", "2020-01-01", 2020)])
    c.executemany("insert into imdb_titles values (?,?,?)", [
        ("tt0000001", "movie", 1995), ("tt0000002", "tvSeries", 2001), ("tt0000004", "movie", 2011),
        ("tt0000006", "movie", 2019), ("tt0000008", "videoGame", 2020), ("tt0000101", "tvSeries", 2005), ("tt0000107", "movie", 2012)])
    c.commit()
    return c


class Identity(unittest.TestCase):
    def setUp(self):
        self.c = facts()
        self.n = M.build(self.c, now="2026-11-14T00:00:00Z")

    def test_counts_and_types(self):
        self.assertEqual((self.n["films"], self.n["shows"], self.n["identities"]), (7, 4, 11))
        ids = {r[0] for r in self.c.execute("select media_id from media_identity")}
        self.assertIn("movie:1", ids)
        self.assertIn("tv:1", ids)                      # same TMDB number, two identities

    def test_parse_fmt(self):
        self.assertEqual(M.parse("tv:1396"), ("tv", 1396))
        self.assertEqual(M.fmt("movie", 603), "movie:603")
        for bad in ("603", 603, "film:1", "movie:0", "movie:-1", "movie:1 or 1=1", "tv:", None, "movie:01"):
            with self.assertRaises(ValueError):
                M.parse(bad)
        with self.assertRaises(ValueError):
            M.fmt("book", 1)

    def test_quality(self):
        q = {r[0]: r[1] for r in self.c.execute("select media_id, quality from media_external_ids where source='imdb'")}
        self.assertEqual(q, {"movie:1": "ok", "movie:2": "wrong_type", "movie:3": "stale", "movie:4": "year_gap",
                             "movie:6": "ok", "movie:8": "shared", "tv:1": "ok", "tv:6": "wrong_type", "tv:7": "wrong_type",
                             "tv:8": "shared"})
        self.assertEqual(self.n["imdb_shared_ids"], 2)
        self.assertEqual(json.loads(self.c.execute("select flags from media_identity where media_id='movie:5'").fetchone()[0]),
                         ["no_imdb", "no_release_date", "no_wikidata"])

    def test_resolve(self):
        self.assertEqual(M.resolve(self.c, "imdb", "tt0000001"), ["movie:1"])
        self.assertEqual(M.resolve(self.c, "imdb", "tt0000002"), [])                 # wrong type: not trusted
        self.assertEqual(M.resolve(self.c, "imdb", "tt0000002", trusted_only=False), ["movie:2"])
        self.assertEqual(M.resolve(self.c, "imdb", "tt0000006"), ["movie:6"])        # shared, IMDb says film
        self.assertEqual(M.resolve(self.c, "imdb", "tt0000008"), [])                 # shared, undecided: ask which kind
        self.assertEqual(M.resolve(self.c, "imdb", "tt0000008", media_type="tv"), ["tv:8"])
        self.assertEqual(M.resolve(self.c, "tmdb", 1), ["movie:1", "tv:1"])
        self.assertEqual(M.resolve(self.c, "tmdb", 1, media_type="movie"), ["movie:1"])
        self.assertEqual(M.resolve(self.c, "imdb", "x' or '1'='1"), [])
        self.assertEqual(M.external(self.c, "tv:1")["tvdb"], {"id": "77", "quality": "ok"})

    def test_additive_and_repeatable(self):
        before = self.c.execute("select * from films order by 1").fetchall(), self.c.execute("select * from tv_shows order by 1").fetchall()
        again = M.build(self.c, now="2026-11-15T00:00:00Z")
        self.assertEqual(again["identities"], 11)
        self.assertEqual(before, (self.c.execute("select * from films order by 1").fetchall(),
                                  self.c.execute("select * from tv_shows order by 1").fetchall()))
        M.drop(self.c)
        names = {r[0] for r in self.c.execute("select name from sqlite_master")}
        self.assertFalse({"media_identity", "media_external_ids"} & names)
        self.assertTrue({"films", "tv_shows", "imdb_titles"} <= names)

    def test_off_by_default(self):
        self.assertFalse(M.ENABLED)


class Events(unittest.TestCase):
    def setUp(self):
        self.db = os.path.join(TMP, "rec_%s.db" % self.id().split(".")[-1])

    def test_kit_example_valid(self):
        R.validate(dict(EVENT))
        p = os.path.join(KIT, "examples", "example_recommendation_event.json")
        if os.path.exists(p):
            R.validate(json.load(open(p)))

    def test_invalid_rejected(self):
        for k, v in (("event_type", "liked"), ("placement", "sidebar"), ("position", -1), ("position", 1.5),
                     ("explicit_rating", 6), ("schema_version", 2), ("occurred_at", "yesterday"),
                     ("occurred_at", "2026-11-12T21:00:00"), ("session_key", ""), ("consent_scope", "all")):
            e = dict(EVENT)
            e[k] = v
            with self.assertRaises(R.EventError, msg=k):
                R.validate(e)
        e = dict(EVENT)
        e["prompt"] = "what the person typed"
        with self.assertRaises(R.EventError):
            R.validate(e)
        e = dict(EVENT)
        del e["exposure_id"]
        with self.assertRaises(R.EventError):
            R.validate(e)

    def test_off_stores_nothing(self):
        self.assertFalse(R.ENABLED)
        self.assertIsNone(R.record(7, "s1", "movie:1", "home", "click", "r1", db=self.db))
        self.assertIsNone(R.record_exposure(7, "s1", ["movie:1"], "home", "r1", db=self.db))
        self.assertFalse(os.path.exists(self.db))

    def test_identity_from_server_only(self):
        ev = R.record(7, "s1", "movie:1", "home", "click", "r1", consent="consented_personalization", db=self.db, enabled=True)
        R.validate(ev)
        self.assertNotIn("7", (ev["subject_key"][:1],))
        self.assertNotEqual(ev["subject_key"], "7")
        self.assertNotEqual(ev["session_key"], "s1")
        with self.assertRaises(TypeError):                      # no way to hand in a ready-made subject key
            R.record(7, "s1", "movie:1", "home", "click", "r1", subject_key="someone-else", db=self.db, enabled=True)
        anon = R.record(7, "s1", "movie:2", "home", "click", "r1", consent="anonymous", db=self.db, enabled=True)
        self.assertIsNone(anon["subject_key"])                  # no consent: not linked to the person
        self.assertEqual(anon["session_key"], ev["session_key"])

    def test_two_users_isolated_and_forget(self):
        kw = dict(consent="consented_personalization", db=self.db, enabled=True)
        R.record(1, "sa", "movie:1", "home", "rating_positive", "r1", explicit_rating=5, **kw)
        R.record(1, "sa", "movie:2", "home", "not_interested", "r1", **kw)
        R.record(2, "sb", "movie:3", "chat", "watchlist_add", "r1", **kw)
        self.assertEqual([e["media_id"] for e in R.for_user(1, db=self.db)], ["movie:1", "movie:2"])
        self.assertEqual([e["media_id"] for e in R.for_user(2, db=self.db)], ["movie:3"])
        self.assertEqual(R.for_user(3, db=self.db), [])
        self.assertEqual(R.forget(1, db=self.db), 2)
        self.assertEqual(R.for_user(1, db=self.db), [])
        self.assertEqual(len(R.for_user(2, db=self.db)), 1)

    def test_exposure_positions_and_no_text(self):
        secret = "my private question about a film"
        ex = R.record_exposure(None, "s9", ["movie:5", "tv:5", "movie:6"], "pick_tonight", "r1",
                               context={"q": secret, "max_runtime": 120}, db=self.db, enabled=True)
        c = sqlite3.connect(self.db)
        rows = c.execute("select media_id, position, event_type, exposure_id, subject_key from rec_event order by position").fetchall()
        self.assertEqual([(r[0], r[1], r[2]) for r in rows], [("movie:5", 0, "impression"), ("tv:5", 1, "impression"), ("movie:6", 2, "impression")])
        self.assertEqual({r[3] for r in rows}, {ex})
        self.assertEqual({r[4] for r in rows}, {None})
        dump = "\n".join(c.iterdump())
        self.assertNotIn(secret, dump)
        self.assertNotIn("s9", [r[0] for r in c.execute("select session_key from rec_event")])

    def test_purge_and_strength(self):
        old = datetime(2025, 1, 1, tzinfo=timezone.utc)
        R.record(1, "s", "movie:1", "home", "click", "r1", now=old, db=self.db, enabled=True)
        R.record(1, "s", "movie:2", "home", "click", "r1", now=NOW, db=self.db, enabled=True)
        self.assertEqual(R.purge(365, now=NOW, db=self.db), 1)
        self.assertIsNone(R.strength("impression"))             # shown is not liked
        self.assertIsNone(R.strength("already_watched"))        # watched is not liked
        self.assertEqual(R.strength("watch_started")[0], "weak")
        self.assertLess(R.strength("rating_negative")[1], 0)
        self.assertEqual(set(R.STRENGTH), set(R.EVENT_TYPES))


def real(**kw):
    o = copy.deepcopy(OFFER)
    o.update({"offer_id": "t1", "source_url": "https://cinema.test-chain.com/offers/tuesday", "verification_status": "verified",
              "last_verified_at": "2026-11-10T00:00:00Z"})
    o.update(kw)
    return o


class Deals(unittest.TestCase):
    def test_kit_example_valid_but_never_shown(self):
        D.validate(copy.deepcopy(OFFER))
        p = os.path.join(KIT, "examples", "example_deal_offer_MOCK_ONLY.json")
        if os.path.exists(p):
            D.validate(json.load(open(p)))
        self.assertTrue(D.is_placeholder(OFFER))
        self.assertEqual(D.shown([OFFER], now=NOW, enabled=True), [])

    def test_off_shows_nothing(self):
        self.assertFalse(D.ENABLED)
        self.assertEqual(D.shown([real()], now=NOW), [])
        self.assertEqual(len(D.shown([real()], now=NOW, enabled=True)), 1)

    def test_invalid_rejected(self):
        for k, v in (("offer_type", "half_price"), ("eligible_weekdays", ["tues"]), ("eligible_weekdays", ["monday", "monday"]),
                     ("price", -1), ("currency", "usd"), ("source_url", "http://cinema.test-chain.com/x"),
                     ("source_url", "javascript:alert(1)"), ("price_status", "guess"), ("verification_status", "ok"),
                     ("last_verified_at", "last week"), ("region", "U"), ("schema_version", 2), ("location_ids", [""])):
            with self.assertRaises(D.OfferError, msg=k):
                D.validate(real(**{k: v}))
        with self.assertRaises(D.OfferError):
            D.validate(real(discount_pct=50))                        # no invented fields
        with self.assertRaises(D.OfferError):
            D.validate(real(price=7.0, currency="USD"))              # a price with price_status unknown
        with self.assertRaises(D.OfferError):
            D.validate(real(price=7.0, price_status="published_offer"))   # a price with no currency
        with self.assertRaises(D.OfferError):
            D.validate(real(valid_from="2026-12-01T00:00:00Z", valid_until="2026-11-01T00:00:00Z"))

    def test_price_only_when_earned(self):
        self.assertIsNone(D.card(real(), NOW)["price"])
        self.assertIn("unknown", D.card(real(), NOW)["price_label"])
        ok = real(price=7.0, currency="USD", price_status="published_offer")
        self.assertEqual((D.card(ok, NOW)["price"], D.card(ok, NOW)["currency"]), (7.0, "USD"))
        for change in ({"verification_status": "unverified"}, {"verification_status": "disputed"},
                       {"valid_until": "2026-11-01T00:00:00Z"}, {"last_verified_at": "2026-09-01T00:00:00Z"},
                       {"valid_from": "2026-12-01T00:00:00Z"}):
            o = real(price=7.0, currency="USD", price_status="published_offer", **change)
            self.assertIsNone(D.card(o, NOW)["price"], change)
            self.assertEqual(D.shown([o], now=NOW, enabled=True), [], change)
        card = D.card(ok, NOW)
        self.assertFalse({"saving", "discount", "discount_pct", "profit"} & set(card))
        self.assertIn("extra", card["caveat"])

    def test_states(self):
        self.assertEqual(D.state(real(), NOW), "active")
        self.assertEqual(D.state(real(verification_status="expired"), NOW), "expired")
        self.assertEqual(D.state(real(valid_until="2026-11-13T00:00:00Z"), NOW), "expired")
        self.assertEqual(D.state(real(last_verified_at="2026-10-01T00:00:00Z"), NOW), "needs_recheck")
        self.assertEqual(D.state(real(verification_status="unverified"), NOW), "unverified")

    def test_region_and_weekday(self):
        offers = [real(offer_id="ma"), real(offer_id="ny", region="US-NY"), real(offer_id="any", eligible_weekdays=[]),
                  real(offer_id="us", region="US")]
        got = lambda **kw: sorted(c["offer_id"] for c in D.shown(offers, now=NOW, enabled=True, **kw))
        self.assertEqual(got(region="US-MA"), ["any", "ma", "us"])
        self.assertEqual(got(region="US-NY"), ["ny", "us"])
        self.assertEqual(got(region="US-MA", weekday="monday"), ["any"])
        self.assertEqual(got(region="US-MA", weekday="tuesday"), ["any", "ma", "us"])


class KitSchemas(unittest.TestCase):
    """Where jsonschema and the kit files are present, the hand-written checks must agree with the kit's own schemas."""
    def test_agree(self):
        try:
            import jsonschema
        except ImportError:
            self.skipTest("jsonschema not installed")
        for name, good, check, err, cases in (
                ("deal_offer", real(), D.validate, D.OfferError, [("offer_type", "x"), ("price", -1), ("currency", "usd"), ("eligible_weekdays", ["x"])]),
                ("recommendation_event", dict(EVENT), R.validate, R.EventError, [("event_type", "x"), ("position", -1), ("explicit_rating", 9), ("placement", "x")])):
            p = os.path.join(KIT, "specs", name + ".schema.json")
            if not os.path.exists(p):
                self.skipTest("kit schema not found")
            v = jsonschema.Draft202012Validator(json.load(open(p)))
            self.assertEqual(list(v.iter_errors(good)), [])
            check(copy.deepcopy(good))
            for k, bad in cases:
                o = copy.deepcopy(good)
                o[k] = bad
                self.assertTrue(list(v.iter_errors(o)), k)
                with self.assertRaises(err):
                    check(o)


if __name__ == "__main__":
    unittest.main()
