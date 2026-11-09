"""
v8 (9 Nov 2026): one watch history across services (workstream D, first slice).

    watch_events   one row per thing watched: movie / episode / show, with the TMDB id when it could be matched,
                   the date, the source (netflix / letterboxd / trakt / cinemack) and a hash of the source row, so
                   importing the same file twice adds nothing (unique on user, source, row hash).
    import_jobs    one row per import: rows read, matched, duplicates, issues.
    import_issues  every row that was NOT matched, with the reason - nothing is dropped silently.

Netflix: the simple "Viewing activity -> Download all" file (Title, Date) and ViewingActivity.csv from a full data
request (Profile Name, Start Time, Duration, Attributes, Title, ...). "Show: Season 1: Episode" titles become the
show (matched to tv_shows); other titles are matched to films by exact normalised title. When two films share a title
the most-voted one is used only if it has at least 5x the votes of the next; otherwise the row is an issue
("ambiguous") - a wrong match is worse than an unmatched row.
Letterboxd: watched.csv / diary.csv rows (Date, Name, Year) -> movie events (title + year).
Trakt: items carry TMDB ids, so no title matching is needed (trakt_sync.py).

Reads facts.db read-only; writes logs/history.db.
"""
import csv
import datetime
import hashlib
import io
import json
import os
import re
import sqlite3
import threading
import time
import unicodedata
from pathlib import Path

RAG_DIR = Path(__file__).resolve().parent.parent
DB = os.environ.get("HISTORY_DB", str(RAG_DIR / "logs" / "history.db"))
FACTS = os.environ.get("FACTS_DB", str(RAG_DIR / "facts.db"))
SOURCES = ("netflix", "letterboxd", "trakt", "cinemack")
MAX_ROWS = 50000
_lock = threading.Lock()
_C = {}
EPISODE = re.compile(r"^(?P<show>.+?): (?P<rest>(?:(?:Season|Series|Part|Volume|Chapter|Limited Series|Book) [^:]+|"
                     r"[^:]*\b(?:Season|Series)\b[^:]*)): (?P<ep>.+)$", re.I)


def conn():
    if DB not in _C:
        os.makedirs(os.path.dirname(DB), exist_ok=True)
        c = sqlite3.connect(DB, check_same_thread=False, timeout=30)
        c.execute("pragma journal_mode=WAL")
        c.executescript("""
            create table if not exists watch_events(user_key text, media_type text, tmdb_id integer, season integer,
              episode_title text, title text, year integer, watched_at text, source text, row_hash text,
              created_at text, unique (user_key, source, row_hash));
            create index if not exists we_user on watch_events(user_key, watched_at);
            create table if not exists import_jobs(id integer primary key autoincrement, user_key text, source text,
              started_at text, rows integer, matched integer, duplicates integer, issues integer);
            create table if not exists import_issues(job_id integer, row_no integer, raw text, reason text);""")
        _C[DB] = c
    return _C[DB]


def norm(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().casefold()
    s = s.replace("&", " and ")
    s = re.sub(r"[^a-z0-9]+", " ", s).strip()
    return re.sub(r"^(the|a|an) ", "", s)


def _hash(*parts):
    return hashlib.sha1("\x1f".join(str(p) for p in parts).encode()).hexdigest()[:20]


def _date(s):
    s = (s or "").strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%m/%d/%y", "%m/%d/%Y", "%d/%m/%Y", "%Y-%m-%dT%H:%M:%S.%fZ",
                "%Y-%m-%dT%H:%M:%SZ"):
        try:
            return datetime.datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


class Matcher:
    def __init__(self, facts=FACTS):
        self.films, self.shows = {}, {}
        if not os.path.exists(facts):
            return
        f = sqlite3.connect(f"file:{facts}?mode=ro", uri=True)
        try:
            for fid, t, ot, y, vc in f.execute("select tmdb_id, title, original_title, year, vote_count from films"):
                for name in {t, ot}:
                    if name:
                        self.films.setdefault(norm(name), []).append((fid, y, vc or 0, t))
            for sid, n, on, y, vc in f.execute("select tmdb_id, name, original_name, first_year, vote_count from tv_shows"):
                for name in {n, on}:
                    if name:
                        self.shows.setdefault(norm(name), []).append((sid, y, vc or 0, n))
        except sqlite3.Error:
            pass
        finally:
            f.close()

    @staticmethod
    def _pick(cands, year=None):
        if not cands:
            return None, "not in the catalogue"
        if year:
            same = [c for c in cands if c[1] and abs(int(c[1]) - int(year)) <= 1]
            if len(same) == 1:
                return same[0], None
            cands = same or cands
        if len(cands) == 1:
            return cands[0], None
        s = sorted(cands, key=lambda c: -c[2])
        if s[0][2] >= 5 * max(s[1][2], 1):
            return s[0], None
        return None, f"ambiguous: {len(cands)} titles match"

    def film(self, title, year=None):
        return self._pick(self.films.get(norm(title), []), year)

    def show(self, title):
        return self._pick(self.shows.get(norm(title), []))


# ------------------------------------------------------------------ parsers -> rows of dicts
def parse_netflix(text):
    rows = list(csv.DictReader(io.StringIO(text.lstrip("﻿"))))
    if not rows:
        return []
    keys = {k.strip().lower(): k for k in rows[0].keys() if k}
    tk = keys.get("title")
    dk = keys.get("date") or keys.get("start time")
    if not tk:
        raise ValueError("This doesn't look like a Netflix viewing-history file (no Title column).")
    out = []
    for i, r in enumerate(rows[:MAX_ROWS], 1):
        if keys.get("attributes") and "autoplayed" in (r.get(keys["attributes"]) or "").lower():
            continue                                  # trailers / previews that played on their own
        if keys.get("supplemental video type") and (r.get(keys["supplemental video type"]) or "").strip():
            continue                                  # trailers, teasers, hooks
        t = (r.get(tk) or "").strip()
        if not t:
            continue
        m = EPISODE.match(t)
        d = _date(r.get(dk)) if dk else None
        prof = (r.get(keys.get("profile name", ""), "") or "").strip()
        if m:
            sn = re.search(r"(\d+)", m.group("rest"))
            out.append({"row": i, "raw": t, "kind": "episode", "title": m.group("show").strip(),
                        "season": int(sn.group(1)) if sn else None, "episode": m.group("ep").strip(), "date": d,
                        "key": _hash(prof, t, d)})
        else:
            out.append({"row": i, "raw": t, "kind": "movie", "title": t, "date": d, "key": _hash(prof, t, d)})
    return out


def parse_letterboxd_watched(text):
    rows = list(csv.DictReader(io.StringIO(text.lstrip("﻿"))))
    out = []
    for i, r in enumerate(rows[:MAX_ROWS], 1):
        name = (r.get("Name") or "").strip()
        if not name:
            continue
        y = (r.get("Year") or "").strip()
        d = _date(r.get("Watched Date") or r.get("Date"))
        out.append({"row": i, "raw": f"{name} ({y})", "kind": "movie", "title": name, "year": int(y) if y.isdigit() else None,
                    "date": d, "key": _hash(r.get("Letterboxd URI") or name, y, d)})
    return out


# ------------------------------------------------------------------ import
def import_rows(user, source, rows, matcher=None):
    if source not in SOURCES:
        raise ValueError("unknown source")
    m = matcher or Matcher()
    res = {"rows": len(rows), "matched": 0, "duplicates": 0, "issues": []}
    events = []
    for r in rows:
        if r.get("tmdb"):                             # the source gave the TMDB id (Trakt): no title matching
            hit, why = (int(r["tmdb"]), r.get("year"), 0, r["title"]), None
            media = "episode" if r["kind"] == "episode" else "movie"
        elif r["kind"] == "episode":
            hit, why = m.show(r["title"])
            media = "episode"
        else:
            hit, why = m.film(r["title"], r.get("year"))
            if not hit and source == "netflix":       # a Netflix film title can also be a single-season show
                h2, _w = m.show(r["title"])
                if h2:
                    hit, why, media = h2, None, "show"
                else:
                    media = "movie"
            else:
                media = "movie"
        if not hit:
            res["issues"].append({"row": r["row"], "raw": r["raw"], "reason": why})
            continue
        events.append((user, media, hit[0], r.get("season"), r.get("episode"), hit[3], hit[1], r.get("date"), source,
                       r["key"], time.strftime("%Y-%m-%dT%H:%M:%S")))
    with _lock:
        c = conn()
        before = c.total_changes
        c.executemany("insert or ignore into watch_events values (?,?,?,?,?,?,?,?,?,?,?)", events)
        added = c.total_changes - before
        res["matched"], res["duplicates"] = len(events), len(events) - added
        cur = c.execute("insert into import_jobs(user_key, source, started_at, rows, matched, duplicates, issues) "
                        "values (?,?,?,?,?,?,?)", (user, source, time.strftime("%Y-%m-%dT%H:%M:%S"), res["rows"],
                                                   res["matched"], res["duplicates"], len(res["issues"])))
        res["job"] = cur.lastrowid
        c.executemany("insert into import_issues values (?,?,?,?)",
                      [(cur.lastrowid, i["row"], i["raw"][:300], i["reason"]) for i in res["issues"]])
        c.commit()
    res["added"] = res["matched"] - res["duplicates"]
    return res


def add_event(user, media, tmdb_id, title, year=None, date=None, source="cinemack", key=None):
    with _lock:
        c = conn()
        c.execute("insert or ignore into watch_events values (?,?,?,?,?,?,?,?,?,?,?)",
                  (user, media, int(tmdb_id), None, None, title, year, date or time.strftime("%Y-%m-%d"), source,
                   key or _hash(media, tmdb_id, date), time.strftime("%Y-%m-%dT%H:%M:%S")))
        c.commit()


def history(user, limit=500):
    """Newest first; a show's episodes are folded into one row per show (with the episode count)."""
    rows = conn().execute("select media_type, tmdb_id, title, year, watched_at, source, season, episode_title "
                          "from watch_events where user_key = ? order by coalesce(watched_at, '') desc, rowid desc",
                          (user,)).fetchall()
    out, shows = [], {}
    for media, tid, t, y, d, src, sn, ep in rows:
        if media in ("episode", "show"):
            if tid in shows:
                shows[tid]["episodes"] += 1 if media == "episode" else 0
                continue
            shows[tid] = {"id": tid, "media": "tv", "title": t, "year": y, "last": d, "source": src,
                          "episodes": 1 if media == "episode" else 0}
            out.append(shows[tid])
        else:
            out.append({"id": tid, "media": "movie", "title": t, "year": y, "last": d, "source": src})
        if len(out) >= limit:
            break
    return out


def stats(user):
    c = conn()
    films = c.execute("select count(distinct tmdb_id) from watch_events where user_key = ? and media_type = 'movie'",
                      (user,)).fetchone()[0]
    shows = c.execute("select count(distinct tmdb_id) from watch_events where user_key = ? and media_type in ('episode','show')",
                      (user,)).fetchone()[0]
    eps = c.execute("select count(*) from watch_events where user_key = ? and media_type = 'episode'", (user,)).fetchone()[0]
    by_src = dict(c.execute("select source, count(*) from watch_events where user_key = ? group by source", (user,)).fetchall())
    return {"films": films, "shows": shows, "episodes": eps, "by_source": by_src}


def jobs(user, limit=10):
    c = conn()
    out = []
    for jid, src, at, n, m, d, iss in c.execute("select id, source, started_at, rows, matched, duplicates, issues from "
                                               "import_jobs where user_key = ? order by id desc limit ?", (user, limit)):
        out.append({"id": jid, "source": src, "at": at, "rows": n, "matched": m, "duplicates": d, "issues": iss,
                    "issue_rows": [{"row": r, "raw": raw, "reason": why} for r, raw, why in c.execute(
                        "select row_no, raw, reason from import_issues where job_id = ? order by row_no limit 200", (jid,))]})
    return out


def delete_source(user, source):
    with _lock:
        c = conn()
        n = c.execute("delete from watch_events where user_key = ? and source = ?", (user, source)).rowcount
        c.commit()
    return n


def delete_user(user):
    with _lock:
        c = conn()
        c.execute("delete from watch_events where user_key = ?", (user,))
        jids = [r[0] for r in c.execute("select id from import_jobs where user_key = ?", (user,))]
        c.executemany("delete from import_issues where job_id = ?", [(j,) for j in jids])
        c.execute("delete from import_jobs where user_key = ?", (user,))
        c.commit()


def move_user(old, new):
    with _lock:
        c = conn()
        c.execute("update or ignore watch_events set user_key = ? where user_key = ?", (new, old))
        c.execute("delete from watch_events where user_key = ?", (old,))
        c.execute("update import_jobs set user_key = ? where user_key = ?", (new, old))
        c.commit()
