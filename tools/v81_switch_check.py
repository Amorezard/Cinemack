#!/usr/bin/env python3
"""v8.1 round 50 (17 Nov 2026): check the four Thursday switches on a RUNNING copy of the site, through its API only.

    python tools/v81_switch_check.py --port 49153 --code <access code> [--press]

The copy must have been started with FORECAST_PANEL=1 FOLLOW_PEOPLE=1 REC_EVENTS=1 (and GUARDIAN_LIVE=1 for --press).
No chat question is asked (that needs the shared GPU); every check is one or two plain requests:
  page      the page and the new page module load (/, /v8/events.js, film.js has the follow section)
  forecast  a film from the copy's forecast store answers 200 with a range; the words file answers 200
  follow    a film's people answer 200; follow, list, unfollow round-trip for a test browser id
  events    one test impression is stored (and the test row is removed again)
  press     (only with --press) one film page's Guardian section answers 200; reports whether a live call was spent
Prints one line per check and a verdict; exit 0 only if every check passed. Reads the copy's own logs/ databases
(forecast.db for a film id, guardian_live.db for the call count, rec_events.db to remove the test row).
"""
import argparse
import json
import os
import secrets
import sqlite3
import sys
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(HERE, "web"))


def call(base, path, code, user, body=None, timeout=60):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data, method="POST" if body is not None else "GET",
                                 headers={"x-access-code": code, "X-MB-User": user, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            return r.status, raw
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except Exception as e:  # noqa: BLE001
        return 0, str(e).encode()


def j(raw):
    try:
        return json.loads(raw)
    except Exception:  # noqa: BLE001
        return {}


def ro(path):
    return sqlite3.connect("file:%s?mode=ro" % path, uri=True, timeout=10)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--code", required=True)
    ap.add_argument("--press", action="store_true")
    a = ap.parse_args(argv)
    base = "http://127.0.0.1:%d" % a.port
    user = "switchcheck-" + secrets.token_hex(6)
    logs = os.path.join(HERE, "logs")
    res = []

    def ok(name, good, detail):
        res.append(good)
        print("  %-8s %s  %s" % (name, "ok  " if good else "FAIL", detail))

    st, raw = call(base, "/", a.code, user)
    s2, js = call(base, "/v8/events.js", a.code, user)
    s3, film_js = call(base, "/v8/film.js", a.code, user)
    ok("page", st == 200 and s2 == 200 and b"people-follow" in film_js, "/ %d, events.js %d, film.js follow section %s" % (st, s2, b"people-follow" in film_js))

    fid = None
    try:
        c = ro(os.path.join(logs, "forecast.db"))
        r = c.execute("select media_id from forecasts where horizon = 'd7' and media_id like 'movie:%' order by as_of desc limit 1").fetchone()
        c.close()
        fid = r[0] if r else None
    except sqlite3.Error as e:
        print("  forecast store not readable: %s" % e)
    if fid:
        st, raw = call(base, "/api/v8/forecast/%s?horizon=d7" % fid, a.code, user)
        s2, _ = call(base, "/api/v8/i18n/en.json", a.code, user)
        d = j(raw)
        ok("forecast", st == 200 and s2 == 200 and bool(d), "%s -> %d (%d keys), words %d" % (fid, st, len(d), s2))
    else:
        ok("forecast", False, "no stored forecast to ask for")

    film = None
    try:
        c = ro(os.path.join(HERE, "facts.db"))
        r = c.execute("select c.tmdb_id from credits c join films f on f.tmdb_id = c.tmdb_id where c.job = 'Director' "
                      "order by f.popularity desc limit 1").fetchone()
        c.close()
        film = r[0] if r else None
    except sqlite3.Error:
        try:
            c = ro(os.path.join(HERE, "facts.db"))
            r = c.execute("select tmdb_id from credits where job = 'Director' limit 1").fetchone()
            c.close()
            film = r[0] if r else None
        except sqlite3.Error as e:
            print("  facts.db not readable: %s" % e)
    if film:
        st, raw = call(base, "/api/v8/film/%d/people" % film, a.code, user)
        ppl = j(raw).get("people") or []
        steps = ["people %d (%d)" % (st, len(ppl))]
        good = st == 200 and bool(ppl)
        if good:
            pid = ppl[0]["person_id"]
            s1, r1 = call(base, "/api/v8/follows", a.code, user, {"person_id": pid, "on": True})
            s2, r2 = call(base, "/api/v8/follows", a.code, user)
            listed = any(p.get("person_id") == pid for p in j(r2).get("people") or [])
            s3, r3 = call(base, "/api/v8/follows", a.code, user, {"person_id": pid, "on": False})
            s4, r4 = call(base, "/api/v8/follows", a.code, user)
            gone = not (j(r4).get("people") or [])
            good = s1 == 200 and j(r1).get("following") is True and listed and s3 == 200 and gone
            steps.append("follow %d, listed %s, unfollow %d, list empty %s" % (s1, listed, s3, gone))
        ok("follow", good, "film %s: %s" % (film, "; ".join(steps)))
    else:
        ok("follow", False, "no film with a director credit")

    evdb = os.path.join(logs, "rec_events.db")
    existed = os.path.exists(evdb)
    sess = "switchcheck-" + secrets.token_hex(8)
    st, raw = call(base, "/api/v8/events", a.code, user, {"session": sess, "events": [
        {"media_id": "movie:%d" % (film or 1), "placement": "home", "type": "impression", "position": 0}]})
    stored = j(raw).get("stored")
    removed = 0
    try:
        import rec_events as RE
        c = sqlite3.connect(evdb, timeout=10)
        salt = c.execute("select value from rec_meta where key = 'salt'").fetchone()[0]
        removed = c.execute("delete from rec_event where session_key = ?", (RE._key(salt, "s", sess),)).rowcount
        c.commit()
        c.close()
    except Exception as e:  # noqa: BLE001
        print("  test event not removed: %s" % e)
    ok("events", st == 200 and stored == 1 and removed == 1,
       "stored %s, test row removed %d%s" % (stored, removed, "" if existed else " (the events file was new)"))

    if a.press:
        gdb = os.path.join(logs, "guardian_live.db")

        def calls():
            try:
                c = ro(gdb)
                n = c.execute("select count(*) from calls").fetchone()[0]
                c.close()
                return n
            except sqlite3.Error:
                return 0
        before = calls()
        st, raw = call(base, "/api/v8/film/%d/press" % (film or 1), a.code, user, timeout=30)
        arts = j(raw).get("articles") or []
        ok("press", st == 200, "film %s -> %d, %d article(s), live calls spent %d" % (film, st, len(arts), calls() - before))

    print("SWITCH CHECK: %s (%d of %d checks ok)" % ("PASS" if all(res) else "FAIL", sum(res), len(res)))
    return 0 if all(res) else 1


if __name__ == "__main__":
    sys.exit(main())
