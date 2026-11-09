"""9 Nov (v8): Pick tonight - 30 fixed scenarios; every film left after the filters must satisfy every hard limit
(time, kids rating, mood genre, only-my-services). Rule (success criteria, 9 Nov): 100%.
    python -m unittest tests.test_v8_pick
"""
import itertools
import json
import os
import sqlite3
import sys
import tempfile
import unittest

T = tempfile.mkdtemp()
os.environ["FACTS_DB"] = os.path.join(T, "facts.db")
os.environ["USERS_DB"] = os.path.join(T, "users.db")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.join(HERE, "..", "web"), os.path.join(HERE, "..")]
PICK = json.load(open(os.path.join(HERE, "..", "web", "v8", "pick.json")))

GENRES = ["Comedy", "Thriller", "Drama", "Science Fiction", "Horror", "Family", "Animation"]
CERTS = ["G", "PG", "PG-13", "R", None]
c = sqlite3.connect(os.environ["FACTS_DB"])
c.executescript("""create table films(tmdb_id integer primary key, title text, year integer, runtime integer,
  genres_json text, original_language text);
create table certifications(tmdb_id integer, region text, certification text, release_type integer, release_date text);
create table watch_providers(tmdb_id integer, region text, kind text, provider_id integer, provider_name text, priority integer);""")
FILMS = {}
for i in range(1, 121):
    g = [GENRES[i % 7], GENRES[(i * 3) % 7]] if i % 11 else []                 # some films with unknown genres
    rt = None if i % 13 == 0 else 70 + (i * 7) % 110                           # some unknown runtimes
    cert = CERTS[i % 5]
    FILMS[i] = (g, rt, cert)
    c.execute("insert into films values (?,?,?,?,?,?)", (i, f"Film {i}", 1960 + i % 60, rt, json.dumps(g), "en"))
    if cert:
        c.execute("insert into certifications values (?,?,?,?,?)", (i, "US", cert, 3, "2000-01-01"))
    if i % 3 == 0:
        c.execute("insert into watch_providers values (?,?,?,?,?,?)", (i, "US", "flatrate", 8, "Netflix", 1))
    elif i % 3 == 1:
        c.execute("insert into watch_providers values (?,?,?,?,?,?)", (i, "US", "rent", 2, "Apple TV", 1))
c.commit()
c.close()

import user_prefs  # noqa: E402

RANK = {"G": 0, "PG": 1, "PG-13": 2, "R": 3}


def scenarios():
    combos = list(itertools.product(PICK["with"], PICK["mood"], PICK["time"], (False, True)))
    step = max(1, len(combos) // 30)
    return combos[::step][:30]


class PickTonight(unittest.TestCase):
    def test_thirty_scenarios_respect_every_hard_limit(self):
        sc = scenarios()
        self.assertEqual(len(sc), 30)
        hits = [{"id": i, "title": f"Film {i}"} for i in FILMS]
        bad = []
        for w, m, t, svc in sc:
            chat = {**PICK["with"][w]["filters"], **PICK["mood"][m]["filters"], **PICK["time"][t]["filters"], "strict": True}
            if svc:
                chat["services_only"] = True
            settings = user_prefs.clean({"services": ["netflix"]})
            s = user_prefs.merge_chat(settings, chat)
            kept = [h["id"] for h in hits if h["id"] not in user_prefs.excluded(s, hits)]
            on = user_prefs.on_services(s, kept) if svc else {}
            for fid in kept:
                g, rt, cert = FILMS[fid]
                if chat.get("max_runtime") and not (rt and rt <= chat["max_runtime"]):
                    bad.append((w, m, t, svc, fid, "time"))
                if chat.get("max_cert") and not (cert in RANK and RANK[cert] <= RANK[chat["max_cert"]]):
                    bad.append((w, m, t, svc, fid, "rating"))
                if chat.get("genre") and chat["genre"] not in g:
                    bad.append((w, m, t, svc, fid, "genre"))
                if svc and fid not in on:
                    bad.append((w, m, t, svc, fid, "services"))
        self.assertEqual(bad, [], f"{len(bad)} violations, first: {bad[:5]}")

    def test_without_strict_unknown_facts_pass(self):          # the old behaviour is unchanged for normal chats
        hits = [{"id": 13, "title": "Film 13"}]                  # unknown runtime
        s = user_prefs.merge_chat(user_prefs.clean({}), {"max_runtime": 90})
        self.assertEqual(user_prefs.excluded(s, hits), set())

    def test_decades_languages_reorder_never_remove(self):
        hits = [{"id": i} for i in (1, 2, 3, 30, 31)]
        s = user_prefs.clean({"decades": ["1990s"]})
        out = user_prefs.prefer_decades_languages(s, hits)
        self.assertEqual(sorted(h["id"] for h in out), sorted(h["id"] for h in hits))
        ids = [h["id"] for h in out]
        self.assertLess(ids.index(30), 3)                        # 1990, the only 1990s film, moves up (a lift) ...
        self.assertEqual(ids[0], 1)                              # ... but does not jump the top result


if __name__ == "__main__":
    unittest.main()
