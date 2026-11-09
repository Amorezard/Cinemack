"""
v8 (9 Nov 2026): the data behind the new home page, release calendar and watchlist alerts. Read-only on facts.db
(film_info's connection); alerts are stored in logs/alerts.db. Every list says where it comes from and how fresh it
is; nothing is invented to fill an empty list.

    home(user)              rails: continue (saved chats), watchlist, because you liked <film> (MovieLens neighbours of
                            the person's liked films, minus anything already rated, seen or listed), now playing and
                            coming soon (TMDB lists, US), popular new releases (last 120 days, TMDB popularity)
    calendar(user, days)    dated releases only: US theatrical (TMDB release types 1-3), US digital (type 4), new TV
                            seasons (tv_seasons.air_date); theatrical rows get a labelled outbound "Find showtimes"
                            search link - Cinemack never shows a showtime it doesn't have
    alerts(user)            watchlist films that became available on a US subscription service since the last check,
                            and watchlist films whose US release date has now passed; one alert per film per change;
                            no "leaving soon" (no dated source for it)
"""
import datetime
import json
import os
import sqlite3
import threading
import time
import urllib.parse
from pathlib import Path

import film_info

RAG_DIR = Path(__file__).resolve().parent.parent
ALERTS_DB = os.environ.get("ALERTS_DB", str(RAG_DIR / "logs" / "alerts.db"))
MOVIES = os.environ.get("MOVIES_DB", str(RAG_DIR / "movies.db"))
_lock = threading.Lock()
_C = {}
RELEASE_KIND = {1: "theatrical", 2: "theatrical", 3: "theatrical", 4: "digital", 5: "physical", 6: "tv"}


def _f():
    return film_info._ro(film_info.FACTS)


def _q(sql, params=()):
    return film_info._q(_f(), sql, params)


def _today():
    return datetime.date.today().isoformat()


def cards(ids, user=""):
    ids = [int(i) for i in ids if i is not None]
    if not ids:
        return []
    meta = {r[0]: r[1:] for r in film_info._many(_f(), "select tmdb_id, title, year, vote_average, vote_count from films "
                                                        "where tmdb_id in", ids)}
    out = [{"id": i, "title": meta[i][0], "year": meta[i][1],
            "rating": round(meta[i][2], 1) if meta[i][2] and (meta[i][3] or 0) >= 20 else None}
           for i in ids if i in meta and meta[i][0]]
    return film_info.enrich(out, user)


def _list(name, limit=20):
    r = _q("select max(fetched_at) from lists where list_name = ? and region = 'US'", (name,))
    at = r[0][0] if r and r[0][0] else None
    if not at:
        return [], None
    ids = [x[0] for x in _q("select tmdb_id from lists where list_name = ? and region = 'US' and fetched_at = ? "
                             "order by rank limit ?", (name, at, limit * 2))]
    return ids, at[:10]


def _seen_or_rated(user):
    if not user:
        return set()
    s = set()
    try:
        import personal
        likes, dis, _st = personal.load(user)
        s |= set(likes) | set(dis)
    except Exception:  # noqa: BLE001
        pass
    try:
        with film_info._lock:
            s |= {r[0] for r in film_info._users().execute("select tmdb_id from watch where user_id = ?", (user,))}
    except Exception:  # noqa: BLE001
        pass
    try:
        import history
        s |= {r[0] for r in history.conn().execute("select tmdb_id from watch_events where user_key = ? and "
                                                   "media_type = 'movie'", (user,))}
    except Exception:  # noqa: BLE001
        pass
    return s


def _first_theatrical(ids):
    """First US limited/wide date ever, per film; else the film's US or world release date."""
    ids = [int(i) for i in ids]
    if not ids:
        return {}
    q = ",".join(str(i) for i in ids)
    first = dict(_q(f"select tmdb_id, min(substr(release_date, 1, 10)) from certifications where region = 'US' "
                    f"and release_type in (2, 3) and tmdb_id in ({q}) group by tmdb_id"))
    for fid, d in _q(f"select tmdb_id, substr(coalesce(us_release_date, release_date), 1, 10) from films where tmdb_id in ({q})"):
        first.setdefault(fid, d)
    return first


def _years(ids):
    if not ids:
        return {}
    return dict(_q("select tmdb_id, year from films where tmdb_id in (" + ",".join(str(int(i)) for i in ids) + ")"))


def _split_rereleases(ids, days=120):
    """TMDB's now-playing list includes re-releases (anniversary and holiday screenings, restorations). New = first US
    theatrical date within `days` and a release year no older than last year; anything else is a re-release.
    The year check catches restorations with no older US date on record (Raise the Red Lantern, 1991)."""
    today = datetime.date.today()
    cut = (today - datetime.timedelta(days=days)).isoformat()
    first, years = _first_theatrical(ids), _years(ids)
    def old(i):
        return bool((first.get(i) and first[i] < cut) or (years.get(i) and years[i] < today.year - 1))
    return [i for i in ids if not old(i)], [i for i in ids if old(i)]


def because_you_liked(user, rails=2, per=12):
    try:
        import personal
        likes, _dis, _st = personal.load(user)
    except Exception:  # noqa: BLE001
        return []
    if not likes or not os.path.exists(MOVIES):
        return []
    with film_info._lock:
        order = [r[0] for r in film_info._users().execute(
            "select tmdb_id from film_prefs where user_id = ? and signal > 0 order by created_at desc", (user,))]
    skip, out = _seen_or_rated(user), []
    m = sqlite3.connect(f"file:{MOVIES}?mode=ro", uri=True)
    try:
        for fid in order[:6]:
            nb = [r[0] for r in m.execute("select neighbour_id from ml_neighbours where tmdb_id = ? order by rank limit 40",
                                          (fid,)) if r[0] not in skip]
            if len(nb) < 4:
                continue
            t = _q("select title from films where tmdb_id = ?", (fid,))
            out.append({"key": f"because_{fid}", "title": f"Because you liked {t[0][0] if t else 'a film you liked'}",
                        "why": "Films that people who rated it highly also rated highly (MovieLens).",
                        "items": cards(nb[:per], user)})
            skip |= set(nb[:per])
            if len(out) >= rails:
                break
    except sqlite3.Error:
        pass
    finally:
        m.close()
    return out


def home(user=""):
    rails = []
    try:
        import chats
        cs = chats.list_chats(user)[:6] if user else []
        if cs:
            rails.append({"key": "continue", "title": "Continue exploring", "kind": "chats", "items": cs})
    except Exception:  # noqa: BLE001
        pass
    wl = film_info.watchlist(user, limit=20) if user else []
    rails.append({"key": "watchlist", "title": "Your watchlist", "items": cards([w["id"] for w in wl], user),
                  "empty": "Tap Want to watch on any film and it lands here."})
    rails += because_you_liked(user) if user else []
    ids, at = _list("now_playing", 40)
    new_ids, rerel = _split_rereleases(ids)
    rails.append({"key": "now_playing", "title": "In theatres now", "items": cards(new_ids[:20], user),
                  "source": "TMDB now playing (US), new releases", "as_of": at})
    if rerel:
        rails.append({"key": "back_in_theatres", "title": "Back in theatres", "items": cards(rerel[:20], user),
                      "source": "Re-releases on TMDB's now-playing list (US)", "as_of": at})
    ids, at = _list("upcoming", 40)
    dates = dict(_q("select tmdb_id, coalesce(us_release_date, release_date) from films where tmdb_id in (" +
                    ",".join(str(int(i)) for i in ids or [0]) + ")"))
    soon = [i for i in ids if dates.get(i) and dates[i] >= _today()]          # keep TMDB's list order
    rails.append({"key": "coming_soon", "title": "Coming soon", "items": cards(soon[:20], user),
                  "source": "TMDB upcoming (US)", "as_of": at})
    since = (datetime.date.today() - datetime.timedelta(days=120)).isoformat()
    pop = [r[0] for r in _q("select tmdb_id from films where coalesce(us_release_date, release_date) between ? and ? "
                            "and coalesce(vote_count, 0) >= 20 order by popularity desc limit 20", (since, _today()))]
    rails.append({"key": "new_popular", "title": "Popular new releases", "items": cards(pop, user),
                  "source": "TMDB popularity, released in the last 120 days"})
    return {"rails": rails, "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S")}


def similar(fid, user="", n=12):
    if not os.path.exists(MOVIES):
        return []
    m = sqlite3.connect(f"file:{MOVIES}?mode=ro", uri=True)
    try:
        ids = [r[0] for r in m.execute("select neighbour_id from ml_neighbours where tmdb_id = ? order by rank limit ?",
                                       (int(fid), n))]
    except sqlite3.Error:
        ids = []
    finally:
        m.close()
    return cards(ids, user)


def calendar(user="", days=60, start=None):
    start = start or _today()
    end = (datetime.date.fromisoformat(start) + datetime.timedelta(days=int(days))).isoformat()
    rows = {}
    # Theatrical: limited (2) or wide (3) US dates in the window; festival premieres (1) are not public releases.
    for fid, typ, d in _q("select tmdb_id, release_type, substr(release_date, 1, 10) from certifications where region = 'US' "
                          "and substr(release_date, 1, 10) between ? and ? and release_type in (2, 3)", (start, end)):
        k = (fid, "theatrical")
        if k not in rows or d < rows[k]:
            rows[k] = d
    # Digital: only a film's FIRST US digital date. TMDB adds a new digital date each time a film reaches another
    # service (e.g. Love Hurts, 2025: four digital dates), which would otherwise show old films as new releases.
    for fid, d in _q("select tmdb_id, min(substr(release_date, 1, 10)) from certifications where region = 'US' "
                     "and release_type = 4 group by tmdb_id having min(substr(release_date, 1, 10)) between ? and ?",
                     (start, end)):
        rows[(fid, "digital")] = d
    # Fallback: films with a known US date but no US release-type rows at all. Films with only a non-US date are left
    # out (the calendar is US releases).
    have_us = {r[0] for r in _q("select distinct tmdb_id from certifications where region = 'US'")}
    # TMDB's US upcoming / now-playing lists, plus the 60 most popular of its "announced in the next year" list; the
    # rest of "announced" is mostly tiny releases and event screenings with no US release type.
    on_lists = {r[0] for r in _q("select distinct tmdb_id from lists where region = 'US' and (list_name in "
                                 "('upcoming', 'now_playing') or (list_name = 'announced' and rank <= 60))")}
    cand = {fid: d for fid, d in _q("select tmdb_id, substr(us_release_date, 1, 10) from films where us_release_date "
                                    "is not null and substr(us_release_date, 1, 10) between ? and ?", (start, end))
            if fid not in have_us and fid in on_lists}  # unknown release type: only films TMDB lists for the US
    # TMDB's US lists still carry many tiny releases and event screenings with no release type. Keep one only if it
    # draws at least as much interest (TMDB popularity) as the median film with a known US theatrical date here.
    known = [f for f, k in rows if k == "theatrical"]
    pop = dict(_q("select tmdb_id, popularity from films where tmdb_id in (" +
                  ",".join(str(int(i)) for i in (known + list(cand)) or [0]) + ")"))
    ref = sorted(pop[f] for f in known if pop.get(f) is not None)
    bar = ref[len(ref) // 2] if ref else None
    for fid, d in cand.items():
        if bar is None or (pop.get(fid) is not None and pop[fid] >= bar):
            rows[(fid, "theatrical_or_unknown")] = d
    # A theatrical date for a film that opened in US theatres before the window is a re-release.
    # Re-release: the film first opened in US theatres more than a year before this date, or it is from an earlier
    # year. A limited opening followed by a wide one weeks later is not a re-release.
    th = [f for f, k in rows if k == "theatrical"]
    first, years = _first_theatrical(th), _years(th)
    for (fid, kind), d in list(rows.items()):
        if kind != "theatrical":
            continue
        year_ago = (datetime.date.fromisoformat(d) - datetime.timedelta(days=365)).isoformat()
        if (first.get(fid) and first[fid] < year_ago) or (years.get(fid) and years[fid] < int(d[:4]) - 1):
            rows[(fid, "theatrical_rerelease")] = rows.pop((fid, kind))
    film_cards = {c["id"]: c for c in cards(list({f for f, _k in rows}), user)}
    on_wl = {w["id"] for w in film_info.watchlist(user, 500)} if user else set()
    out = []
    for (fid, kind), d in rows.items():
        c = film_cards.get(fid)
        if not c:
            continue
        e = {"date": d, "kind": kind, "media": "movie", "film": c, "on_watchlist": fid in on_wl,
             "label": {"theatrical": "In theatres", "theatrical_rerelease": "Back in theatres (re-release)",
                       "digital": "Streaming / digital", "theatrical_or_unknown": "US release (type not given)"}[kind]}
        if kind in ("theatrical", "theatrical_rerelease"):
            e["showtimes"] = {"label": "Find showtimes (Google search)",
                              "url": "https://www.google.com/search?" + urllib.parse.urlencode({"q": f"{c['title']} showtimes"})}
        out.append(e)
    for sid, sn, d, name, poster, nets in _q(
            "select s.tmdb_id, s.season_number, s.air_date, t.name, coalesce(s.poster_path, t.poster_path), t.networks_json "
            "from tv_seasons s join tv_shows t on t.tmdb_id = s.tmdb_id where s.season_number > 0 and s.air_date between ? and ?",
            (start, end)):
        try:
            net = ", ".join(json.loads(nets or "[]")[:2])
        except (ValueError, TypeError):
            net = ""
        out.append({"date": d, "kind": "tv_season", "media": "tv", "label": f"Season {sn} premieres" + (f" on {net}" if net else ""),
                    "show": {"id": sid, "title": name, "season": sn, "poster": film_info.img(poster, "w185") if poster else None}})
    out.sort(key=lambda e: (e["date"], e["kind"]))
    at = _q("select max(fetched_at) from lists")
    return {"from": start, "to": end, "events": out, "as_of": (at[0][0] or "")[:10] if at else None,
            "note": "Dates come from TMDB. Cinemack doesn't have cinema showtimes; the showtimes link opens a web search."}


# ------------------------------------------------------------------ alerts
def _adb():
    if ALERTS_DB not in _C:
        os.makedirs(os.path.dirname(ALERTS_DB), exist_ok=True)
        c = sqlite3.connect(ALERTS_DB, check_same_thread=False, timeout=30)
        c.executescript("""
            create table if not exists snapshot(user_key text, tmdb_id integer, providers text, released integer,
              taken_at text, primary key (user_key, tmdb_id));
            create table if not exists alerts(user_key text, tmdb_id integer, kind text, detail text, created_at text,
              seen integer default 0, unique (user_key, tmdb_id, kind, detail));""")
        _C[ALERTS_DB] = c
    return _C[ALERTS_DB]


def _providers(ids):
    out = {}
    for fid, name in film_info._many(_f(), "select tmdb_id, provider_name from watch_providers where region = 'US' and "
                                           "kind = 'flatrate' and tmdb_id in", ids):
        out.setdefault(fid, set()).add(name)
    return out


def _released(ids):
    today = _today()
    rel = {}
    for fid, d in film_info._many(_f(), "select tmdb_id, coalesce(us_release_date, release_date) from films where tmdb_id in", ids):
        rel[fid] = bool(d and d[:10] <= today)
    return rel


def refresh_alerts(user):
    wl = film_info.watchlist(user, 500) if user else []
    ids = [w["id"] for w in wl]
    if not ids:
        return 0
    titles = {w["id"]: w["title"] for w in wl}
    prov, rel = _providers(ids), _released(ids)
    new = 0
    with _lock:
        c = _adb()
        snap = {r[0]: (set(json.loads(r[1] or "[]")), r[2]) for r in c.execute(
            "select tmdb_id, providers, released from snapshot where user_key = ?", (user,))}
        now = time.strftime("%Y-%m-%dT%H:%M:%S")
        for fid in ids:
            cur_p, cur_r = prov.get(fid, set()), int(rel.get(fid, False))
            if fid in snap:                                   # first sight of a film only takes a snapshot
                old_p, old_r = snap[fid]
                for p in sorted(cur_p - old_p):
                    new += c.execute("insert or ignore into alerts values (?,?,?,?,?,0)",
                                     (user, fid, "now_streaming", p, now)).rowcount
                if cur_r and not old_r:
                    new += c.execute("insert or ignore into alerts values (?,?,?,?,?,0)",
                                     (user, fid, "released", "", now)).rowcount
            c.execute("insert or replace into snapshot values (?,?,?,?,?)",
                      (user, fid, json.dumps(sorted(cur_p)), cur_r, now))
        c.commit()
    return new


def alerts(user, refresh=True):
    if not user:
        return {"alerts": [], "unseen": 0}
    if refresh:
        refresh_alerts(user)
    rows = _adb().execute("select tmdb_id, kind, detail, created_at, seen from alerts where user_key = ? "
                          "order by created_at desc limit 100", (user,)).fetchall()
    cs = {c["id"]: c for c in cards(list({r[0] for r in rows}), user)}
    out = []
    for fid, kind, detail, at, seen in rows:
        c = cs.get(fid, {"id": fid, "title": ""})
        text = (f"{c.get('title')} is now streaming on {detail}" if kind == "now_streaming"
                else f"{c.get('title')} is out now")
        out.append({"film": c, "kind": kind, "detail": detail, "text": text, "at": at, "seen": bool(seen)})
    return {"alerts": out, "unseen": sum(1 for a in out if not a["seen"])}


def mark_seen(user):
    with _lock:
        c = _adb()
        c.execute("update alerts set seen = 1 where user_key = ?", (user,))
        c.commit()


def delete_user(user):
    with _lock:
        c = _adb()
        c.execute("delete from alerts where user_key = ?", (user,))
        c.execute("delete from snapshot where user_key = ?", (user,))
        c.commit()
