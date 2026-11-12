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

import dbsafe   # 10 Nov: shared connection, one call at a time (see dbsafe.py)
import threading
import time
import urllib.parse
from pathlib import Path

import film_info
import tvdata       # round 21: IMDb-ranked shows, episodes
import tmdb_extra   # 10 Nov, round 18: TV ratings / genres, missing artwork and runtimes (logs/tmdb_extra.db)

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
    playing = _now_playing_set()
    for c in out:
        if c["id"] in playing:
            c["in_theatres"] = True                 # on TMDB's US now-playing list: a Showtimes link is offered
    film_info.enrich(out, user)
    gap = [c["id"] for c in out if not c.get("poster") or not c.get("runtime")]
    if gap:                                         # 10 Nov, round 18: artwork / runtime facts.db does not have yet
        art = tmdb_extra.film_art(gap)
        for c in out:
            p, _b, rt = art.get(c["id"], (None, None, None))
            if p and not c.get("poster"):
                c["poster"], c["poster_lg"] = film_info.img(p, "w185"), film_info.img(p, "w342")
            if rt and not c.get("runtime"):
                c["runtime"] = rt
    return out


# ------------------------------------------------------------------ short films (10 Nov, round 18)
# A short (the Academy's rule: 40 minutes or less) is not offered as "a movie" on Home or in the Release Calendar.
# Unknown runtime (common for films not out yet) is kept. Search, the chat, saved films and film pages still show them.
SHORT_MAX = 40


def _runtimes(ids):
    ids = [int(i) for i in ids if i is not None]
    if not ids:
        return {}
    out = {r[0]: r[1] for r in film_info._many(_f(), "select tmdb_id, runtime from films where tmdb_id in", ids) if r[1]}
    for fid, (_p, _b, rt) in tmdb_extra.film_art([i for i in ids if i not in out]).items():
        if rt:
            out[fid] = rt
    return out


def no_shorts(ids):
    """The ids (same order) minus films with a known runtime of 40 minutes or less."""
    ids = list(ids)
    rt = _runtimes(ids)
    return [i for i in ids if not (rt.get(i) and 0 < rt[i] <= SHORT_MAX)]


def _now_playing_set():
    ids, _at = _list("now_playing", 60)
    return set(ids)


def _genres_of(ids):
    try:                                                # 12 Nov GENRE_MAIN: main genres (web/genres.py)
        import genres as _G
        if _G.ON:
            return {k: set(v[0]) for k, v in _G.films(ids).items()}
    except ImportError:
        pass
    out = {}
    for fid, g in film_info._many(_f(), "select tmdb_id, genres_json from films where tmdb_id in", [int(i) for i in ids]):
        try:
            out[fid] = set(json.loads(g or "[]"))
        except (ValueError, TypeError):
            out[fid] = set()
    return out


# ------------------------------------------------------------------ genre filter (10 Nov: Home redesign)
# One list of genres for the server and the page (the page draws its chips from GENRE_OPTIONS in /api/v8/home).
# Rules: none selected = no restriction; "any" = at least one selected genre; "all" = every selected genre;
# every genre selected = no restriction (same rails, same order as none). The filter is applied to each shelf's whole
# candidate list BEFORE it is cut to 20, and it never reorders a shelf: each shelf keeps its own ranking rule.
GENRE_OPTIONS = ["Action", "Adventure", "Animation", "Comedy", "Crime", "Documentary", "Drama", "Family", "Fantasy",
                 "History", "Horror", "Music", "Mystery", "Romance", "Science Fiction", "Thriller", "War", "Western"]


def genre_filter(genres, mode="any"):
    """(selected genres in canonical order, mode, every_genre_selected). Unknown names are ignored, never truncated."""
    want = set(genres or [])
    sel = [g for g in GENRE_OPTIONS if g in want]
    mode = "all" if mode == "all" and len(sel) > 1 else "any"
    every = len(sel) == len(GENRE_OPTIONS)
    return ([] if every else sel), mode, every


def match_genres(ids, sel, mode="any"):
    """The ids (same order) whose film has any / all of `sel`. A film with no genre data matches no genre filter."""
    if not sel:
        return list(ids)
    want, g = set(sel), _genres_of(ids)
    ok = (lambda gs: want <= gs) if mode == "all" else (lambda gs: bool(gs & want))
    return [i for i in ids if ok(g.get(i, set()))]


def _genre_sql(sel, mode):
    if not sel:
        return "", []
    j = " and " if mode == "all" else " or "
    return " and (" + j.join("genres_json like ?" for _ in sel) + ")", [f'%"{g}"%' for g in sel]


TOP_MIN_VOTES, TOP_PRIOR_VOTES = 500, 1500


def top_rated(sel=(), mode="any", limit=20):
    """Weighted rating (the IMDb Top 250 formula): WR = v/(v+m)*R + m/(v+m)*C with m = 1500 votes and C the mean
    rating of films with 500+ votes, so a 9.0 from 600 votes does not beat an 8.6 from 20,000. Ties: tmdb_id.
    Short films (40 minutes or less) are left out."""
    r = _q("select avg(vote_average) from films where vote_count >= ?", (TOP_MIN_VOTES,))
    c = r[0][0] if r and r[0][0] else 6.5
    gsql, gargs = _genre_sql(list(sel), mode)
    m = TOP_PRIOR_VOTES
    rows = _q(f"select tmdb_id, (vote_count * 1.0 / (vote_count + ?)) * vote_average + (? * 1.0 / (vote_count + ?)) * ? as wr "
              f"from films where vote_count >= ? and vote_average is not null and not (coalesce(runtime, 0) between 1 and ?){gsql} "
              f"order by wr desc, tmdb_id asc", tuple([m, m, m, c, TOP_MIN_VOTES, SHORT_MAX] + gargs))
    ids = [x[0] for x in rows]
    if sel:                                             # 12 Nov: the SQL test is TMDB's list; keep main-genre films
        ids = match_genres(ids, list(sel), mode)
    return ids[:limit], len(ids)


def _release_of(ids):
    """Next confirmed US date per film: earliest future theatrical (2, 3) or digital (4) date from TMDB; otherwise the
    film's US release date marked as type not given."""
    if not ids:
        return {}
    q, today = ",".join(str(int(i)) for i in ids), _today()
    out = {}
    for fid, typ, d in _q(f"select tmdb_id, release_type, substr(release_date, 1, 10) from certifications where region = 'US' "
                          f"and release_type in (2, 3, 4) and substr(release_date, 1, 10) >= ? and tmdb_id in ({q}) "
                          f"order by substr(release_date, 1, 10), release_type", (today,)):
        out.setdefault(fid, {"date": d, "kind": "digital" if typ == 4 else "theatrical"})
    for fid, d in _q(f"select tmdb_id, substr(coalesce(us_release_date, release_date), 1, 10) from films where tmdb_id in ({q})"):
        if fid not in out and d:
            out[fid] = {"date": d, "kind": "us_release"}
    return out


def theatres(fid, zip_code=""):
    """Showtimes links for a film on TMDB's US now-playing list. Cinemack has no showtime data, so these are
    searches on sites that do; nothing is offered for a film that is not on the list."""
    fid = int(fid)
    if fid not in _now_playing_set():
        return {"in_theatres": False, "links": []}
    row = _q("select title, year from films where tmdb_id = ?", (fid,))
    if not row:
        return {"in_theatres": False, "links": []}
    title = row[0][0]
    z = "".join(ch for ch in str(zip_code or "") if ch.isdigit())[:5]
    z = z if len(z) == 5 else ""
    q = urllib.parse.urlencode
    near = f" near {z}" if z else " near me"
    links = [{"label": "Google showtimes", "site": "Google", "url": "https://www.google.com/search?" + q({"q": f"{title} showtimes{near}"})},
             {"label": "Fandango", "site": "Fandango", "url": "https://www.fandango.com/search?" + q({"q": title})},
             {"label": "AMC Theatres", "site": "AMC", "url": "https://www.google.com/search?" + q({"q": f"{title} showtimes AMC Theatres{near}"})},
             {"label": "Regal", "site": "Regal", "url": "https://www.google.com/search?" + q({"q": f"{title} showtimes Regal{near}"})},
             {"label": "Showcase Cinemas", "site": "Showcase", "url": "https://www.google.com/search?" + q({"q": f"{title} showtimes Showcase Cinemas{near}"})}]
    new, old = _split_rereleases([fid])
    return {"in_theatres": True, "rerelease": bool(old), "zip": z, "links": links,
            "note": "Cinemack doesn't have showtimes; these open a search on sites that do."}


def _list(name, limit=20):
    r = _q("select max(fetched_at) from lists where list_name = ? and region = 'US'", (name,))
    at = r[0][0] if r and r[0][0] else None
    if not at:
        return [], None
    ids = [x[0] for x in _q("select tmdb_id from lists where list_name = ? and region = 'US' and fetched_at = ? "
                             "order by rank, tmdb_id limit ?", (name, at, limit * 2))]
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


def because_you_liked(user, rails=2, per=12, keep=None):
    """MovieLens neighbours of films the person liked. `keep` (the genre filter) runs on all 40 neighbours before the
    cut to `per`, so a filtered rail is not just the leftovers of an unfiltered one."""
    try:
        import personal
        likes, _dis, _st = personal.load(user)
    except Exception:  # noqa: BLE001
        return []
    if not likes or not os.path.exists(MOVIES):
        return []
    with film_info._lock:
        order = [r[0] for r in film_info._users().execute(
            "select tmdb_id from film_prefs where user_id = ? and signal > 0 order by created_at desc, tmdb_id", (user,))]
    skip, out = _seen_or_rated(user), []
    m = sqlite3.connect(f"file:{MOVIES}?mode=ro", uri=True)
    try:
        for fid in order[:6]:
            nb = [r[0] for r in m.execute("select neighbour_id from ml_neighbours where tmdb_id = ? order by rank, neighbour_id limit 40",
                                          (fid,)) if r[0] not in skip]
            nb = no_shorts(nb)
            if keep:
                nb = keep(nb)
            if len(nb) < 4:
                continue
            t = _q("select title from films where tmdb_id = ?", (fid,))
            out.append({"key": f"because_{fid}", "title": f"Because you liked {t[0][0] if t else 'a film you liked'}",
                        "why": "Films that people who rated it highly also rated highly (MovieLens).",
                        "seed": t[0][0] if t else None, "items": cards(nb[:per], user), "total": len(nb)})
            skip |= set(nb[:per])
            if len(out) >= rails:
                break
    except sqlite3.Error:
        pass
    finally:
        m.close()
    return out


def home(user="", genres=None, mode="any", per=20):
    """Home shelves. Each has its own eligibility and ranking rule (in `rule`), and the genre filter only removes films
    from it (see genre_filter). `total` is how many eligible films matched before the cut to `per`. The hero carousel
    is the unfiltered list of new films in theatres."""
    sel, mode, every = genre_filter(genres, mode)
    keep = (lambda ids: match_genres(ids, sel, mode)) if sel else (lambda ids: list(ids))
    rails = []
    np_ids, at = _list("now_playing", 200)
    np_ids = no_shorts(np_ids)                      # 10 Nov, round 18: a 7-minute short is not "a movie in theaters"
    new_ids, rerel = _split_rereleases(np_ids)
    hero_ids = (new_ids + rerel)[:8]
    hero = cards(hero_ids, user)
    extra = {r[0]: r[1:] for r in film_info._many(_f(), "select f.tmdb_id, f.overview, i.backdrop_path from films f left join "
                                                        "film_images i on i.tmdb_id = f.tmdb_id where f.tmdb_id in", hero_ids)} if hero_ids else {}
    for c in hero:                                    # one request for the whole carousel, not one per slide
        ov, bd = extra.get(c["id"], (None, None))
        c["overview"] = ov or ""
        c["backdrop"] = film_info.img(bd, "w1280") if bd else None
    th = keep(new_ids)
    rails.append({"key": "now_playing", "title": "In Theaters Now", "items": cards(th[:per], user), "total": len(th),
                  "rule": "TMDB's US now-playing list, in TMDB's order (new releases; re-releases have their own row)",
                  "source": "TMDB now playing (US)", "as_of": at})
    if user:
        rails += because_you_liked(user, keep=keep if sel else None)
    top, n_top = top_rated(sel, mode, per)
    rails.append({"key": "top_rated", "title": "Top Rated", "items": cards(top, user), "total": n_top,
                  "rule": f"Weighted TMDB rating, films with {TOP_MIN_VOTES}+ votes", "source": "TMDB ratings"})
    since = (datetime.date.today() - datetime.timedelta(days=120)).isoformat()
    pop = [r[0] for r in _q("select tmdb_id from films where coalesce(us_release_date, release_date) between ? and ? "
                            "and coalesce(vote_count, 0) >= 20 and not (coalesce(runtime, 0) between 1 and ?) "
                            "order by popularity desc, tmdb_id asc", (since, _today(), SHORT_MAX))]
    shown_th = {c["id"] for c in rails[0]["items"]}
    trend = [i for i in keep(pop) if i not in shown_th]            # already on the In Theaters row: not repeated here
    rails.append({"key": "trending", "title": "Trending Now", "items": cards(trend[:per], user), "total": len(trend),
                  "rule": "TMDB popularity, released in the last 120 days; films already in the In Theaters row are left out",
                  "source": "TMDB popularity"})
    up_ids, at2 = _list("upcoming", 200)
    up_ids = no_shorts(up_ids)
    rel = _release_of(up_ids)
    soon = sorted([i for i in up_ids if rel.get(i) and rel[i]["date"] >= _today()], key=lambda i: (rel[i]["date"], i))
    soon = keep(soon)
    sc = cards(soon[:per], user)
    for c in sc:
        c["release"] = rel[c["id"]]
    rails.append({"key": "coming_soon", "title": "Opening Soon", "items": sc, "total": len(soon),
                  "rule": "TMDB's US upcoming list, by confirmed date, earliest first", "source": "TMDB upcoming (US)", "as_of": at2})
    rails += tv_rails(user, sel, mode, per)          # 10 Nov, round 18: New Seasons, Top Rated Shows
    wl = film_info.watchlist(user, limit=200) if user else []
    mine = keep([w["id"] for w in wl])
    rails.append({"key": "watchlist", "title": "Your List", "items": cards(mine[:per], user), "total": len(mine), "saved": len(wl),
                  "rule": "Films you saved, newest first"})
    rr = keep(rerel)
    rails.append({"key": "back_in_theatres", "title": "Back on the Big Screen", "items": cards(rr[:per], user), "total": len(rr),
                  "rule": "Re-releases on TMDB's US now-playing list", "source": "TMDB now playing (US)", "as_of": at})
    return {"rails": rails, "hero": hero, "genres": sel, "mode": mode, "all_selected": every, "genre_options": GENRE_OPTIONS,
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S")}


# ------------------------------------------------------------------ TV rows on Home (10 Nov, round 18)
NEW_SEASON_DAYS = 30


def _following_ids(user):
    if not user:
        return set()
    with _lock:
        return {r[0] for r in _tvdb().execute("select show_id from tv_follow where user_key = ?", (user,))}


def tv_lookup(ids):
    """{id: {"title", "year", "poster_path", "networks"}} from facts.db, then logs/tmdb_extra.db for shows facts.db
    does not have (a show followed from a Home row must still show up on the Watchlist)."""
    ids = [int(i) for i in ids if i is not None]
    if not ids:
        return {}
    out = {}
    for i, n, y, p, nets in _q(f"select tmdb_id, name, first_year, poster_path, networks_json from tv_shows "
                               f"where tmdb_id in ({','.join(str(x) for x in ids)})"):
        out[i] = {"title": n, "year": y, "poster_path": p, "networks": tmdb_extra._loads(nets)}
    for i, m in tmdb_extra.show_meta([x for x in ids if x not in out]).items():
        out[i] = {"title": m["title"], "year": m["year"], "poster_path": m["poster_path"], "networks": m["networks"]}
    return out


def tv_seasons_of(ids):
    """{id: [(season_number, air_date)]}: facts.db's seasons, plus tmdb_extra seasons facts.db does not list."""
    ids = [int(i) for i in ids if i is not None]
    out = {}
    if not ids:
        return out
    for sid, sn, d in _q(f"select tmdb_id, season_number, air_date from tv_seasons where tmdb_id in "
                         f"({','.join(str(x) for x in ids)}) and season_number > 0 order by season_number"):
        out.setdefault(sid, []).append((sn, (d or "")[:10]))
    for sid, ss in tmdb_extra.seasons(ids).items():
        have = {n for n, _d in out.get(sid, [])}
        extra = [(n, d) for n, d in ss if n not in have]
        if extra:
            out[sid] = sorted(out.get(sid, []) + extra)
    return out


def tv_cards(ids, user="", season=None):
    """Cards for TV shows: rating (TMDB, 20+ votes), genres, network, whether the person follows it.
    `season` = {id: (season_number, air_date)} adds the new season to the card."""
    meta = tmdb_extra.show_meta(ids)
    base = tv_lookup([i for i in ids if i not in meta])
    fol = _following_ids(user)
    fb = tvdata.basics(ids)                       # round 21: facts.db card (IMDb rating first) when the show is there
    out = []
    for i in ids:
        if i in fb:
            c = dict(fb[i], following=i in fol)
            if season and i in season:
                c["season"] = {"number": season[i][0], "date": season[i][1]}
            out.append(c)
            continue
        m = meta.get(i)
        if m:
            t, y, p, nets = m["title"], m["year"], m["poster_path"], m["networks"]
        elif i in base:
            b = base[i]
            t, y, p, nets = b["title"], b["year"], b["poster_path"], b["networks"]
        else:
            continue
        c = {"id": i, "media": "tv", "title": t, "year": y, "poster": film_info.img(p, "w185"), "poster_lg": film_info.img(p, "w342"),
             "network": ", ".join(nets[:2]), "following": i in fol,
             "rating": round(m["vote_average"], 1) if m and m["vote_average"] and m["vote_count"] >= 20 else None,
             "genres": ", ".join(m["genres"][:3]) if m else ""}
        if season and i in season:
            c["season"] = {"number": season[i][0], "date": season[i][1]}
        out.append(c)
    return out


def new_seasons(sel=(), mode="any", days=NEW_SEASON_DAYS):
    """[(show_id, season_number, air_date)] for shows whose newest season premiered in the last `days` days (season 1
    included: a new series), most popular first (TMDB popularity), then newest premiere, then id. News, talk, soap
    and reality shows are left out. A show with no genre data matches no genre filter (as films)."""
    today = datetime.date.today()
    a, z = (today - datetime.timedelta(days=days)).isoformat(), today.isoformat()
    latest = {}
    rows = [(s, n, (d or "")[:10]) for s, n, d in _q("select tmdb_id, season_number, air_date from tv_seasons where season_number > 0 "
                                                      "and substr(air_date, 1, 10) between ? and ?", (a, z))]
    for s, n, d in rows + tmdb_extra.seasons_between(a, z):
        if s not in latest or n > latest[s][0]:
            latest[s] = (n, d)
    meta = tmdb_extra.show_meta(list(latest))
    known = tv_lookup([s for s in latest if s not in meta])
    keep = []
    for s, (n, d) in latest.items():
        m = meta.get(s)
        if m is None and s not in known:
            continue
        if m and tmdb_extra.skipped(m):
            continue
        if sel and not (m and tmdb_extra.genre_ok(m, sel, mode)):
            continue
        keep.append((s, n, d, (m or {}).get("popularity")))
    keep.sort(key=lambda k: k[0])                            # stable sorts: id, then newest premiere, then popularity
    keep.sort(key=lambda k: k[2], reverse=True)
    keep.sort(key=lambda k: (k[3] is None, -(k[3] or 0)))
    return [(s, n, d) for s, n, d, _p in keep]


def tv_rails(user="", sel=(), mode="any", per=20):
    rails = []
    at = tmdb_extra.as_of()
    ns = new_seasons(sel, mode)
    rails.append({"key": "new_seasons", "title": "New Seasons", "media": "tv",
                  "items": tv_cards([s for s, _n, _d in ns[:per]], user, {s: (n, d) for s, n, d in ns[:per]}), "total": len(ns),
                  "rule": f"TV seasons that premiered in the last {NEW_SEASON_DAYS} days (new series included), most popular "
                          "first; news, talk, soap and reality shows left out", "source": "TMDB", "as_of": at})
    top = tvdata.top_rated(sel, mode)               # round 21: IMDb ratings (20,000+ votes), weighted
    if top:
        rails.append({"key": "top_shows", "title": "Top Rated Shows", "media": "tv", "items": tv_cards(top[:per], user), "total": len(top),
                      "rule": f"IMDb rating, weighted, shows with {tvdata.TOP_MIN_IMDB_VOTES:,}+ IMDb votes; news, talk, soap and reality "
                              "shows left out", "source": "IMDb ratings", "as_of": at or "IMDb datasets"})
    else:
        top = tmdb_extra.top_rated_shows(sel, mode)
        rails.append({"key": "top_shows", "title": "Top Rated Shows", "media": "tv", "items": tv_cards(top[:per], user), "total": len(top),
                      "rule": f"TMDB's top-rated TV, weighted rating, shows with {tmdb_extra.TV_TOP_MIN_VOTES}+ votes; news, talk, soap "
                              "and reality shows left out", "source": "TMDB TV ratings", "as_of": at})
    return rails


# ------------------------------------------------------------------ TV shows the person follows (9 Nov evening)
def _tvdb():
    c = _adb()
    c.execute("create table if not exists tv_follow(user_key text, show_id integer, created_at text, notify integer default 1, "
              "primary key (user_key, show_id))")
    return c


def tv_follow(user, show_id, on=True, notify=None):
    if not user:
        raise ValueError("no user")
    with _lock:
        c = _tvdb()
        if on:
            c.execute("insert or ignore into tv_follow(user_key, show_id, created_at) values (?,?,?)",
                      (user, int(show_id), time.strftime("%Y-%m-%dT%H:%M:%S")))
            if notify is not None:
                c.execute("update tv_follow set notify = ? where user_key = ? and show_id = ?",
                          (int(bool(notify)), user, int(show_id)))
        else:
            c.execute("delete from tv_follow where user_key = ? and show_id = ?", (user, int(show_id)))
        c.commit()


def tv_search(q, limit=8):
    q = (q or "").strip()
    if len(q) < 2:
        return []
    rows = _q("select tmdb_id, name, first_year, poster_path from tv_shows where name like ? order by vote_count desc limit ?",
              (f"%{q}%", limit))
    return [{"id": i, "title": n, "year": y, "poster": film_info.img(p, "w185") if p else None} for i, n, y, p in rows]


def tv_following(user):
    """Followed shows with what is confirmed: seasons out, the next dated season, and episodes watched (from the
    person's imported history). Episode-level air dates are not in the database, so none are shown."""
    if not user:
        return []
    with _lock:
        rows = _tvdb().execute("select show_id, created_at, notify from tv_follow where user_key = ? "
                               "order by created_at desc", (user,)).fetchall()
    if not rows:
        return []
    marks = ",".join(str(int(r[0])) for r in rows)
    shows = {i: (x["title"], x["year"], x["poster_path"], x["networks"]) for i, x in tv_lookup([r[0] for r in rows]).items()}
    seasons = tv_seasons_of([r[0] for r in rows])
    watched = {}
    try:
        import history
        for sid, n, last in history.conn().execute(
                f"select tmdb_id, count(*), max(watched_at) from watch_events where user_key = ? and media_type = 'tv' "
                f"and tmdb_id in ({marks}) group by tmdb_id", (user,)):
            watched[sid] = (n, (last or "")[:10])
    except Exception:  # noqa: BLE001
        pass
    today = _today()
    out = []
    for sid, at, notify in rows:
        if sid not in shows:
            continue
        name, year, poster, nets = shows[sid]
        ss = seasons.get(sid, [])
        aired = [(n, d) for n, d in ss if d and d <= today]
        upcoming = [(n, d) for n, d in ss if d and d > today]
        net = ", ".join((nets or [])[:2])
        nxt = upcoming[0] if upcoming else None
        latest = aired[-1] if aired else None
        new_recent = bool(latest and (datetime.date.today() - datetime.date.fromisoformat(latest[1])).days <= 45)
        out.append({"id": sid, "title": name, "year": year, "network": net, "media": "tv",
                    "poster": film_info.img(poster, "w185") if poster else None,
                    "seasons_out": len(aired), "latest_season": {"number": latest[0], "date": latest[1]} if latest else None,
                    "next_season": {"number": nxt[0], "date": nxt[1]} if nxt else None,
                    "badge": ("Upcoming season" if nxt else "New season" if new_recent else None),
                    "episodes_watched": watched.get(sid, (0, None))[0], "last_watched": watched.get(sid, (0, None))[1],
                    "notify": bool(notify), "followed": (at or "")[:10]})
    return out


def _refresh_tv_alerts(user):
    """One alert when a followed show (notifications on) gets a dated future season, one when a season starts.
    The unique key (user, show, kind, detail) stops repeats; undated seasons never alert."""
    new = 0
    today = _today()
    for sh in tv_following(user):
        if not sh["notify"]:
            continue
        rowsets = []
        if sh["next_season"]:
            rowsets.append(("tv_season_dated", f"{sh['next_season']['number']}|{sh['next_season']['date']}"))
        if sh["latest_season"] and sh["followed"] <= sh["latest_season"]["date"] <= today:
            rowsets.append(("tv_season_out", str(sh["latest_season"]["number"])))
        with _lock:
            c = _adb()
            for kind, detail in rowsets:
                new += c.execute("insert or ignore into alerts values (?,?,?,?,?,0)",
                                 (user, sh["id"], kind, detail, time.strftime("%Y-%m-%dT%H:%M:%S"))).rowcount
            c.commit()
    return new


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
    keep_ids = set(no_shorts(list({f for f, _k in rows})))     # 10 Nov, round 18: short films are not movie releases
    rows = {k: d for k, d in rows.items() if k[0] in keep_ids}
    film_cards = {c["id"]: c for c in cards(list({f for f, _k in rows}), user)}
    bd = {r[0]: r[1] for r in film_info._many(_f(), "select tmdb_id, backdrop_path from film_images where tmdb_id in", list(film_cards)) if r[1]}
    for fid, (_p, b, _rt) in tmdb_extra.film_art([i for i in film_cards if i not in bd]).items():
        if b:
            bd[fid] = b
    for fid, c in film_cards.items():               # wide tiles in the week view (10 Nov, round 18)
        if bd.get(fid):
            c["backdrop"] = film_info.img(bd[fid], "w300")
    provs = _providers([f for f, k in rows if k == "digital"])        # 10 Nov: who streams a digital release, if known
    on_wl = {w["id"] for w in film_info.watchlist(user, 500)} if user else set()
    out = []
    for (fid, kind), d in rows.items():
        c = film_cards.get(fid)
        if not c:
            continue
        e = {"date": d, "kind": kind, "media": "movie", "film": c, "on_watchlist": fid in on_wl,
             "label": {"theatrical": "In theatres", "theatrical_rerelease": "Back in theatres (re-release)",
                       "digital": "Streaming / digital", "theatrical_or_unknown": "US release (type not given)"}[kind]}
        e["confirmed"] = kind != "theatrical_or_unknown"     # TMDB gave a dated US release of this type
        if kind == "digital":
            e["providers"] = sorted(provs.get(fid, []))           # empty: service not known yet (shown as such)
        if kind in ("theatrical", "theatrical_rerelease"):
            e["showtimes"] = {"label": "Find showtimes (Google search)",
                              "url": "https://www.google.com/search?" + urllib.parse.urlencode({"q": f"{c['title']} showtimes"})}
        out.append(e)
    following = _following_ids(user)
    tvrows = _q("select s.tmdb_id, s.season_number, s.air_date, t.name, coalesce(s.poster_path, t.poster_path), t.networks_json "
                "from tv_seasons s join tv_shows t on t.tmdb_id = s.tmdb_id where s.season_number > 0 and s.air_date between ? and ?",
                (start, end))
    have = {(r[0], r[1]) for r in tvrows}
    extra = [(s, n, d) for s, n, d in tmdb_extra.seasons_between(start, end) if (s, n) not in have]   # 10 Nov, round 18
    xm = tmdb_extra.show_meta([s for s, _n, _d in extra])
    for s, n, d in extra:
        m = xm.get(s)
        if m and not tmdb_extra.skipped(m):
            tvrows.append((s, n, d, m["title"], m["poster_path"], json.dumps(m["networks"])))
    for sid, sn, d, name, poster, nets in tvrows:
        try:
            net = ", ".join(json.loads(nets or "[]")[:2])
        except (ValueError, TypeError):
            net = ""
        out.append({"date": d, "kind": "tv_season", "media": "tv", "label": f"Season {sn} premieres" + (f" on {net}" if net else ""),
                    "following": sid in following, "confirmed": True, "network": net,
                    "show": {"id": sid, "title": name, "season": sn, "poster": film_info.img(poster, "w185") if poster else None}})
    # round 21: episodes of followed shows (every episode) and season finales of well-known shows
    prem = {(e["show"]["id"], e["show"]["season"]) for e in out if e["kind"] == "tv_season"}
    eps = tvdata.episodes_between(start, end, following)
    eb = tvdata.basics(list({r[0] for r in eps}))
    for sid, sn, ep, title, d, typ in eps:
        if ep == 1 and (sid, sn) in prem:
            continue                                    # the premiere is already there as a new season
        b = eb.get(sid)
        if not b or set(b["tv_genres"]) & tvdata.SKIP_TV:
            continue
        out.append({"date": d[:10], "kind": "tv_episode", "media": "tv", "label": f"S{sn} E{ep}" + (" (season finale)" if typ == "finale" else ""),
                    "following": sid in following, "confirmed": True, "network": b["network"], "finale": typ == "finale",
                    "show": {"id": sid, "title": b["title"], "season": sn, "episode": ep, "episode_title": title, "poster": b["poster"]}})
    credits = _tvmaze_calendar(out, start, end, following)          # round 27: air times + followed shows' streaming drops
    out.sort(key=lambda e: (e["date"], e.get("airstamp") or "", e["kind"]))
    at = _q("select max(fetched_at) from lists")
    return {"from": start, "to": end, "events": out, "as_of": (at[0][0] or "")[:10] if at else None,
            "note": "Dates come from TMDB" + (", episode air times from TVmaze" if credits else "")
                    + ". Cinemack doesn't have cinema showtimes; the showtimes link opens a web search.",
            "credits": credits}


def _tvmaze_calendar(out, start, end, following):
    """Round 27: add TVmaze air times (airstamp, shown in the viewer's own time) and where it airs to the TV events,
    and add episodes of followed shows that TVmaze dates but TMDB does not. Returns the credit lines to show (CC BY-SA)."""
    try:
        import tvmaze
        if not tvmaze.available():
            return []
        ids = {e["show"]["id"] for e in out if e["media"] == "tv"} | set(following or [])
        ups = tvmaze.upcoming(start, end, sorted(ids))
    except Exception as e:  # noqa: BLE001
        print(f"[cinemack] tvmaze calendar: {e!r}", flush=True)
        return []
    if not ups:
        return []
    by = {(u["tmdb_id"], u["season"], u["episode"]): u for u in ups}
    used = False
    have = set()
    for e in out:
        if e["media"] != "tv":
            continue
        sh = e["show"]
        key = (sh["id"], sh["season"], sh.get("episode") or 1)
        have.add(key)
        u = by.get(key)
        if u and u.get("airstamp"):
            e["airstamp"], e["where"], e["streaming"] = u["airstamp"], u.get("where"), u.get("streaming")
            used = True
    b = None
    for u in ups:
        key = (u["tmdb_id"], u["season"], u["episode"])
        if key in have or u["tmdb_id"] not in set(following or []) or not u.get("season") or not u.get("episode"):
            continue
        if b is None:
            b = tvdata.basics(list({x["tmdb_id"] for x in ups if x["tmdb_id"] in set(following or [])}))
        bb = b.get(u["tmdb_id"])
        if not bb:
            continue
        out.append({"date": u["date"], "kind": "tv_episode", "media": "tv",
                    "label": f"S{u['season']} E{u['episode']}" + (f" on {u['where']}" if u.get("where") else ""),
                    "following": True, "confirmed": True, "network": bb["network"], "finale": False, "src": "TVmaze",
                    "airstamp": u.get("airstamp"), "where": u.get("where"), "streaming": u.get("streaming"),
                    "show": {"id": u["tmdb_id"], "title": bb["title"], "season": u["season"], "episode": u["episode"],
                             "episode_title": u.get("name"), "poster": bb["poster"]}})
        used = True
    return [tvmaze.CREDIT] if used else []


# ------------------------------------------------------------------ alerts
def _adb():
    if ALERTS_DB not in _C:
        os.makedirs(os.path.dirname(ALERTS_DB), exist_ok=True)
        c = dbsafe.wrap(sqlite3.connect(ALERTS_DB, check_same_thread=False, timeout=30))
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
        try:                                                  # 10 Nov: films the person muted get no new alerts
            c.execute("create table if not exists alert_mute(user_key text, tmdb_id integer, created_at text, "
                      "primary key (user_key, tmdb_id))")
            muted = {r[0] for r in c.execute("select tmdb_id from alert_mute where user_key = ?", (user,))}
        except sqlite3.Error:
            muted = set()
        for fid in ids:
            cur_p, cur_r = prov.get(fid, set()), int(rel.get(fid, False))
            if fid in snap and fid not in muted:              # first sight of a film only takes a snapshot
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
        try:
            _refresh_tv_alerts(user)
        except Exception as e:  # noqa: BLE001
            print(f"[cinemack] tv alerts failed: {e!r}", flush=True)
    rows = _adb().execute("select tmdb_id, kind, detail, created_at, seen from alerts where user_key = ? "
                          "order by created_at desc limit 100", (user,)).fetchall()
    cs = {c["id"]: c for c in cards(list({r[0] for r in rows if not r[1].startswith("tv_")}), user)}
    tv = {x["id"]: x for x in tv_following(user)}
    out = []
    for fid, kind, detail, at, seen in rows:
        if kind.startswith("tv_"):
            sh = tv.get(fid)
            if not sh:
                continue
            if kind == "tv_season_dated":
                n, d = (detail.split("|") + [""])[:2]
                text = f"{sh['title']}: season {n} has a date, {d}"
            else:
                text = f"{sh['title']}: season {detail} is out"
            out.append({"film": dict(sh), "kind": kind, "detail": detail, "text": text, "at": at, "seen": bool(seen)})
            continue
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


# ------------------------------------------------------------------ watchlist overview (9 Nov evening)
def _signals(key, social_mod, viewer=None):
    """{tmdb_id: +1 / -1}: thumbs (film_prefs) plus star ratings (4+ = +1, 2 or less = -1). When `viewer` is someone
    else, only what the owner shares as "ratings" with friends, minus hidden items."""
    if viewer and not social_mod.can_view(key, viewer, "ratings"):
        return {}
    sig = {}
    try:
        with film_info._lock:
            for fid, s in film_info._users().execute("select tmdb_id, signal from film_prefs where user_id = ?", (key,)):
                if s:
                    sig[fid] = 1 if s > 0 else -1
    except Exception:  # noqa: BLE001
        pass
    for r in social_mod.reviews_of(key):
        if r.get("rating") is not None:
            sig[r["id"]] = 1 if r["rating"] >= 4 else (-1 if r["rating"] <= 2 else sig.get(r["id"], 0))
    if viewer and viewer != key:
        hid = social_mod.hidden_ids(key, "ratings")
        sig = {k: v for k, v in sig.items() if k not in hid}
    return {k: v for k, v in sig.items() if v}


def watchlist_overview(user, name_of=lambda k: k, landed_days=30, last_chance_days=35):
    """The Watchlist page as a plan: watch next, just landed, last chance in theatres, watch together, everything.
    No leaving-streaming section: no source publishes those dates."""
    import social as social_mod
    wl = film_info.watchlist(user, 500)
    ids = [w["id"] for w in wl]
    out = {"items": [], "watch_next": [], "just_landed": [], "last_chance": [], "together": [], "shows": tv_following(user)}
    if not ids:
        return out
    added = {w["id"]: (w.get("added") or "")[:10] for w in wl}
    cs = {c["id"]: c for c in cards(ids, user)}
    prov, rel, first = _providers(ids), _released(ids), _first_theatrical(ids)
    np_ids, _at = _list("now_playing", 60)
    in_th = set(np_ids) & set(ids)
    today = datetime.date.today()
    for fid in ids:
        c = dict(cs.get(fid) or {"id": fid, "title": ""})
        c["added"] = added.get(fid)
        c["streaming_on"] = sorted(prov.get(fid, []))
        c["status"] = ("streaming" if prov.get(fid) else "theatres" if fid in in_th else
                       "out" if rel.get(fid) else "coming")
        out["items"].append(c)
    by = {c["id"]: c for c in out["items"]}
    # just landed: alert rows from the last `landed_days` days for films still on the list
    refresh_alerts(user)
    cut = (today - datetime.timedelta(days=landed_days)).isoformat()
    seen = set()
    for fid, kind, detail, at in _adb().execute("select tmdb_id, kind, detail, created_at from alerts where user_key = ? "
                                                "and created_at >= ? order by created_at desc", (user, cut)):
        if fid in by and (fid, kind) not in seen:
            seen.add((fid, kind))
            out["just_landed"].append({"film": by[fid], "kind": kind, "detail": detail, "at": at[:10],
                                       "text": f"Now streaming on {detail}" if kind == "now_streaming" else "Out now"})
    # last chance: on the list, on TMDB's now-playing list, first US theatrical date 5+ weeks ago
    for fid in ids:
        d = first.get(fid)
        if fid in in_th and d:
            age = (today - datetime.date.fromisoformat(d)).days
            if age >= last_chance_days:
                out["last_chance"].append({"film": by[fid], "opened": d, "weeks": age // 7,
                                           "text": f"Opened {age // 7} weeks ago; most films leave theatres 6 to 10 weeks in (estimate)"})
    # watch together: friends who share their watchlist with you
    mine = _signals(user, social_mod)
    together_ids = {}
    for f in (social_mod.friends(user)["friends"] if social_mod.is_account(user) else []):
        other = f["key"]
        theirs = []
        if social_mod.can_view(other, user, "watchlist"):
            theirs = [w["id"] for w in social_mod.visible_items(other, user, "watchlist", film_info.watchlist(other, 500))]
        both = [i for i in ids if i in set(theirs)]
        sig = _signals(other, social_mod, viewer=user)
        common = [k for k in sig if k in mine]
        agree = sum(1 for k in common if sig[k] == mine[k])
        loved = [i for i in ids if sig.get(i) == 1 and i not in both]
        if not (both or common or loved):
            continue
        for i in both:
            together_ids.setdefault(i, []).append(name_of(other))
        out["together"].append({"key": other, "name": name_of(other), "both": [by[i] for i in both],
                                "taste": {"films": len(common), "agree": agree,
                                          "pct": round(100 * agree / len(common)) if len(common) >= 3 else None},
                                "loved": [by[i] for i in loved][:12],
                                "shares_watchlist": social_mod.can_view(other, user, "watchlist"),
                                "shares_ratings": social_mod.can_view(other, user, "ratings")})
    out["together"].sort(key=lambda t: (-len(t["both"]), -(t["taste"]["pct"] or 0)))
    # watch next: things you can actually watch now, best reason first
    services = set()
    try:
        import user_prefs
        services = set(user_prefs.load(user)["settings"].get("services") or [])
    except Exception:  # noqa: BLE001
        pass
    last_ids = {x["film"]["id"] for x in out["last_chance"]}
    scored = []
    for c in out["items"]:
        fid, why, score = c["id"], [], 0.0
        if c.get("on_services"):
            score += 3; why.append("On your " + ", ".join(c["on_services"][:2]))
        elif c["streaming_on"]:
            score += 1.5; why.append("Streaming on " + ", ".join(c["streaming_on"][:2]))
        if fid in in_th:
            score += 2; why.append("In theatres now")
        if fid in last_ids:
            score += 1.5; why.append("Before it leaves theatres")
        if fid in together_ids:
            score += 1; why.append("Also on " + " and ".join(together_ids[fid][:2]) + "'s list")
        if not why:
            continue
        score += (c.get("rating") or 0) / 10
        scored.append((score, fid, why))
    scored.sort(key=lambda x: -x[0])
    out["watch_next"] = [dict(by[fid], why="; ".join(why)) for _s, fid, why in scored[:6]]
    out["has_services"] = bool(services)
    out["shows"] = tv_following(user)
    return out


def delete_user(user):
    with _lock:
        c = _adb()
        c.execute("delete from alerts where user_key = ?", (user,))
        c.execute("delete from snapshot where user_key = ?", (user,))
        try:
            c.execute("delete from tv_follow where user_key = ?", (user,))
        except sqlite3.Error:
            pass
        c.commit()
