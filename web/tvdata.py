"""TV for the website (10 Nov 2026, round 21): show pages, episodes with IMDb + TMDB ratings, Pick Tonight for TV,
Top Rated Shows by IMDb, and the next episode for Continue Watching. Read-only on facts.db.

Data (facts.db): tv_shows / tv_seasons / tv_ratings / tv_providers / tv_credits / tv_videos / tv_keywords (TMDB, v7_data.py
tv), tv_episodes (TMDB, tools/fetch_episodes.py, round 19), imdb_episodes + imdb_ratings + imdb_titles (IMDb datasets,
v7_data.py imdb; joined by the show's IMDb id and season / episode numbers). Nothing is estimated: a missing rating,
date or runtime is shown as missing, and a hard limit (Pick Tonight) fails a show whose fact is unknown.
"""
import datetime
import json
import math

import film_info

IMG = film_info.img
# TMDB TV genres -> the 18 film genres the site's filters use; these four are never offered as "shows"
TV_GENRE = {"Action & Adventure": ["Action", "Adventure"], "Sci-Fi & Fantasy": ["Science Fiction", "Fantasy"],
            "War & Politics": ["War"], "Kids": ["Family"]}
SKIP_TV = {"News", "Talk", "Soap", "Reality"}
# US TV parental guidelines that fit under each film rating (Pick Tonight's "who is watching")
TV_CERT_OK = {"G": {"TV-Y", "TV-Y7", "TV-Y7-FV", "TV-G"}, "PG": {"TV-Y", "TV-Y7", "TV-Y7-FV", "TV-G", "TV-PG"},
              "PG-13": {"TV-Y", "TV-Y7", "TV-Y7-FV", "TV-G", "TV-PG", "TV-14"}}
TV_CERT_OK["R"] = TV_CERT_OK["PG-13"] | {"TV-MA"}
IMDB_PRIOR, IMDB_C = 5000, 7.2          # weighted IMDb rating for shows: v/(v+5000)*R + 5000/(v+5000)*7.2
TMDB_PRIOR, TMDB_C = 500, 7.0


def _f():
    return film_info._ro(film_info.FACTS)


def _q(sql, params=()):
    try:
        return film_info._q(_f(), sql, params)
    except Exception:  # noqa: BLE001  (a table this server does not have yet)
        return []


def _has(table):
    return bool(_q("select 1 from sqlite_master where type = 'table' and name = ?", (table,)))


def _marks(ids):
    return ",".join(str(int(i)) for i in ids) or "0"


def _loads(s):
    try:
        v = json.loads(s or "[]")
        return v if isinstance(v, list) else []
    except (ValueError, TypeError):
        return []


def site_genres(tv_genres):
    out = []
    for g in tv_genres:
        for x in TV_GENRE.get(g, [g]):
            if x not in out:
                out.append(x)
    return out


def _today():
    return datetime.date.today().isoformat()


def weighted(imdb=None, tmdb=None):
    """(score, source, rating, votes): IMDb when it has votes, otherwise TMDB; None when neither."""
    if imdb and imdb[1]:
        r, v = imdb
        return v / (v + IMDB_PRIOR) * r + IMDB_PRIOR / (v + IMDB_PRIOR) * IMDB_C, "IMDb", r, v
    if tmdb and tmdb[1] and tmdb[0]:
        r, v = tmdb
        return v / (v + TMDB_PRIOR) * r + TMDB_PRIOR / (v + TMDB_PRIOR) * TMDB_C, "TMDB", r, v
    return None


def imdb_of(ids):
    """{show_id: (rating, votes)} from IMDb, by the show's IMDb id."""
    if not ids or not _has("imdb_ratings"):
        return {}
    return {sid: (r, v) for sid, r, v in _q(f"select t.tmdb_id, r.rating, r.votes from tv_shows t join imdb_ratings r on r.imdb_id = t.imdb_id "
                                             f"where t.tmdb_id in ({_marks(ids)})")}


def basics(ids):
    """{id: card fields} for TV cards anywhere on the site."""
    ids = [int(i) for i in ids if i is not None]
    if not ids:
        return {}
    imdb = imdb_of(ids)
    certs = dict(_q(f"select tmdb_id, rating from tv_ratings where region = 'US' and tmdb_id in ({_marks(ids)})"))
    out = {}
    for (i, n, y, ey, st, ns, rtm, g, nets, pop, va, vc, p, b, ov) in _q(
            "select tmdb_id, name, first_year, end_year, status, n_seasons, episode_runtime, genres_json, networks_json, popularity, "
            f"vote_average, vote_count, poster_path, backdrop_path, overview from tv_shows where tmdb_id in ({_marks(ids)})"):
        w = weighted(imdb.get(i), (va, vc))
        tg = _loads(g)
        out[i] = {"id": i, "media": "tv", "title": n, "year": y, "end_year": ey, "status": st, "seasons": ns, "episode_runtime": rtm,
                  "tv_genres": tg, "genres": ", ".join(site_genres(tg)[:3]), "network": ", ".join(_loads(nets)[:2]), "popularity": pop,
                  "poster": IMG(p, "w185"), "poster_lg": IMG(p, "w342"), "backdrop": IMG(b, "w780"), "overview": ov or "",
                  "cert": certs.get(i), "rating": round(w[2], 1) if w else None, "rating_src": w[1] if w else None,
                  "votes": w[3] if w else None, "score": w[0] if w else None}
    return out


# ------------------------------------------------------------------ Top Rated Shows (by IMDb)
TOP_MIN_IMDB_VOTES = 20000


def top_rated(sel=(), mode="any"):
    """Shows with 20,000+ IMDb votes by weighted IMDb rating (ties: id); news / talk / soap / reality left out. [] when
    the IMDb tables are missing (the caller falls back to TMDB's list)."""
    if not _has("imdb_ratings"):
        return []
    rows = _q("select t.tmdb_id, r.rating, r.votes, t.genres_json from tv_shows t join imdb_ratings r on r.imdb_id = t.imdb_id "
              "where r.votes >= ?", (TOP_MIN_IMDB_VOTES,))
    want = set(sel)
    scored = []
    for sid, r, v, g in rows:
        tg = _loads(g)
        if set(tg) & SKIP_TV:
            continue
        if want:
            gs = set(site_genres(tg))
            if not (want <= gs if mode == "all" else gs & want):
                continue
        scored.append((-(v / (v + IMDB_PRIOR) * r + IMDB_PRIOR / (v + IMDB_PRIOR) * IMDB_C), sid))
    return [sid for _s, sid in sorted(scored)]


# ------------------------------------------------------------------ episodes
def _imdb_eps(sid, season=None):
    """{(season, episode): (rating, votes, imdb title)} for one show."""
    if not _has("imdb_episodes"):
        return {}
    r = _q("select imdb_id from tv_shows where tmdb_id = ?", (int(sid),))
    if not r or not r[0][0]:
        return {}
    extra, params = ("and e.season = ?", (r[0][0], int(season))) if season is not None else ("", (r[0][0],))
    return {(s, e): (rt, v, t) for s, e, rt, v, t in _q(
        "select e.season, e.episode, r.rating, r.votes, ti.primary_title from imdb_episodes e left join imdb_ratings r on r.imdb_id = e.tconst "
        f"left join imdb_titles ti on ti.tconst = e.tconst where e.parent = ? {extra}", params)}


def episodes(sid, season):
    """One season's episodes: TMDB's list (date, title, synopsis, still, runtime, TMDB rating) with IMDb's rating; when
    TMDB has no list yet, IMDb's episodes (numbers, titles, ratings only)."""
    sid, season = int(sid), int(season)
    im = _imdb_eps(sid, season)
    rows = _q("select episode_number, name, air_date, overview, runtime, still_path, vote_average, vote_count, episode_type "
              "from tv_episodes where tmdb_id = ? and season_number = ? order by episode_number", (sid, season)) if _has("tv_episodes") else []
    today = _today()
    tx = _tvmaze("episode_extras", {}, sid, season)        # round 27: TVmaze summaries and air times (CC BY-SA)
    out = []
    for n, name, d, ov, rtm, still, va, vc, typ in rows:
        r = im.get((season, n))
        t = tx.get((season, n)) or {}
        e = {"season": season, "episode": n, "title": name or (r[2] if r else None) or t.get("name"), "date": d,
             "aired": bool(d and d <= today), "overview": ov or "", "runtime": rtm or t.get("runtime"), "still": IMG(still, "w300"),
             "type": typ, "imdb": {"rating": r[0], "votes": r[1]} if r and r[0] else None,
             "tmdb": {"rating": round(va, 1), "votes": vc} if va and (vc or 0) >= 5 else None}
        if not e["overview"] and t.get("summary"):
            e["overview"], e["overview_src"] = t["summary"], "TVmaze"
        if t.get("airstamp"):
            e["airstamp"] = t["airstamp"]
        out.append(e)
    if not rows and tx:
        for (s_, n), t in sorted(tx.items(), key=lambda x: x[0][1] or 0):
            r = im.get((season, n))
            d = t.get("airdate")
            out.append({"season": season, "episode": n, "title": t.get("name") or (r[2] if r else None), "date": d,
                        "aired": bool(d and d <= today), "overview": t.get("summary") or "", "overview_src": "TVmaze" if t.get("summary") else None,
                        "runtime": t.get("runtime"), "still": t.get("image"), "type": None, "airstamp": t.get("airstamp"),
                        "imdb": {"rating": r[0], "votes": r[1]} if r and r[0] else None, "tmdb": None, "src": "TVmaze"})
        return out
    if not rows:
        for (s, n), (rt_, v, t) in sorted(im.items(), key=lambda x: (x[0][1] or 0)):
            if n is None:
                continue
            out.append({"season": season, "episode": n, "title": t, "date": None, "aired": None, "overview": "", "runtime": None,
                        "still": None, "type": None, "imdb": {"rating": rt_, "votes": v} if rt_ else None, "tmdb": None})
    return out


def best_episodes(sid, n=5, min_votes=300):
    im = _imdb_eps(sid)
    rows = sorted(((v[0], v[1], s, e, v[2]) for (s, e), v in im.items() if v[0] and (v[1] or 0) >= min_votes and s and e),
                  key=lambda x: (-x[0], -x[1]))[:n]
    names = {}
    if rows and _has("tv_episodes"):
        names = {(s, e): t for s, e, t in _q("select season_number, episode_number, name from tv_episodes where tmdb_id = ?", (int(sid),))}
    return [{"season": s, "episode": e, "title": names.get((s, e)) or t, "imdb": {"rating": r, "votes": v}} for r, v, s, e, t in rows]


def next_after(sid, season, episode):
    """The episode after S{season}E{episode} that TMDB lists: {season, episode, title, date, aired}; None if unknown."""
    if not _has("tv_episodes"):
        return None
    r = _q("select season_number, episode_number, name, air_date from tv_episodes where tmdb_id = ? and season_number > 0 and "
           "(season_number > ? or (season_number = ? and episode_number > ?)) order by season_number, episode_number limit 1",
           (int(sid), int(season), int(season), int(episode)))
    if not r:
        return None
    s, e, t, d = r[0]
    return {"season": s, "episode": e, "title": t, "date": d, "aired": bool(d and d <= _today())}


def episode_by_title(sid, season, title):
    """An imported episode title (Netflix gives season + title, no number) -> its episode number, if TMDB lists it."""
    if not title or not _has("tv_episodes"):
        return None
    norm = lambda s: "".join(ch for ch in str(s or "").casefold() if ch.isalnum())  # noqa: E731
    want = norm(title)
    for s, e, t in _q("select season_number, episode_number, name from tv_episodes where tmdb_id = ? and (? is null or season_number = ?)",
                      (int(sid), season, season)):
        if t and norm(t) == want:
            return s, e
    return None


def episodes_between(a, b, show_ids=None, finales_min_votes=200):
    """Episodes airing between dates a and b: every episode of `show_ids` (shows the person follows), plus season
    finales of shows with 200+ TMDB votes. [(show_id, season, episode, title, date, type)]"""
    if not _has("tv_episodes"):
        return []
    out = []
    if show_ids:
        out += _q("select tmdb_id, season_number, episode_number, name, air_date, episode_type from tv_episodes where season_number > 0 and "
                  f"air_date between ? and ? and tmdb_id in ({_marks(show_ids)})", (a, b))
    out += _q("select e.tmdb_id, e.season_number, e.episode_number, e.name, e.air_date, e.episode_type from tv_episodes e join tv_shows t "
              "on t.tmdb_id = e.tmdb_id where e.season_number > 0 and e.episode_type = 'finale' and e.air_date between ? and ? and "
              "coalesce(t.vote_count, 0) >= ?", (a, b, finales_min_votes))
    seen, res = set(), []
    for r in out:
        if (r[0], r[1], r[2]) not in seen:
            seen.add((r[0], r[1], r[2]))
            res.append(r)
    return res


# ------------------------------------------------------------------ the show page
def show(sid, user=""):
    sid = int(sid)
    b = basics([sid]).get(sid)
    if not b:
        return None
    r = _q("select imdb_id, first_air_date, last_air_date, type, in_production, n_episodes, created_by_json, tagline, original_language, "
           "vote_average, vote_count, origin_country_json from tv_shows where tmdb_id = ?", (sid,))[0]
    imdb_id, fa, la, typ, prod, neps, cb, tag, lang, va, vc, oc = r
    imdb = imdb_of([sid]).get(sid)
    seasons = [{"number": n, "name": nm, "date": (d or "")[:10] or None, "episodes": ec, "overview": ov or "", "poster": IMG(p, "w185")}
               for n, nm, d, ec, ov, p in _q("select season_number, name, air_date, episode_count, overview, poster_path from tv_seasons "
                                              "where tmdb_id = ? and season_number > 0 order by season_number", (sid,))]
    # season averages from IMDb episode ratings (only seasons with ratings for most episodes)
    im = _imdb_eps(sid)
    by = {}
    for (s, e), (rt_, v, _t) in im.items():
        if s and rt_:
            by.setdefault(s, []).append(rt_)
    for s in seasons:
        rs = by.get(s["number"], [])
        if rs and (not s["episodes"] or len(rs) >= 0.6 * s["episodes"]):
            s["imdb_avg"] = round(sum(rs) / len(rs), 1)
    provs = {}
    for kind, name in _q("select kind, provider_name from tv_providers where tmdb_id = ? and region = 'US' order by priority", (sid,)):
        provs.setdefault(kind, [])
        if name not in provs[kind]:
            provs[kind].append(name)
    cast = [{"id": pid, "name": n, "character": ch, "episodes": ep, "photo": IMG(pp, "w185")}
            for pid, n, ch, ep, pp in _q("select person_id, name, character, episodes, profile_path from tv_credits where tmdb_id = ? and "
                                         "kind = 'cast' order by ord limit 12", (sid,))]
    creators = [x.get("name") for x in _loads(cb) if isinstance(x, dict) and x.get("name")]
    vids = _q("select video_key, name from tv_videos where tmdb_id = ? and type = 'Trailer' order by official desc, published_at desc limit 1", (sid,))
    nxt = None
    if _has("tv_episodes"):
        x = _q("select season_number, episode_number, name, air_date from tv_episodes where tmdb_id = ? and season_number > 0 and air_date > ? "
               "order by air_date, season_number, episode_number limit 1", (sid, _today()))
        if x:
            nxt = {"season": x[0][0], "episode": x[0][1], "title": x[0][2], "date": x[0][3]}
    tm = _tvmaze("show_extra", {}, sid)                   # round 27: where and when it airs (TVmaze, CC BY-SA)
    n2 = tm.get("next") if tm else None
    if n2 and n2.get("date") and n2["date"] >= _today() and n2.get("season") and n2.get("episode"):
        if nxt and (nxt["season"], nxt["episode"]) == (n2["season"], n2["episode"]):
            nxt["airstamp"] = n2.get("airstamp")
        elif not nxt or n2["date"] < (nxt["date"] or "9999"):
            nxt = {"season": n2["season"], "episode": n2["episode"], "title": n2.get("name"), "date": n2["date"],
                   "airstamp": n2.get("airstamp"), "src": "TVmaze"}
    following = False
    if user:
        try:
            import discovery
            following = sid in discovery._following_ids(user)
        except Exception:  # noqa: BLE001
            pass
    latest = max((s for s in seasons if s["date"] and s["date"] <= _today()), key=lambda s: s["number"], default=None)
    return dict(b, n_seasons=b["seasons"], imdb_id=imdb_id, first_air_date=fa, last_air_date=la, type=typ, in_production=bool(prod), n_episodes=neps,
                creators=creators, tagline=tag or "", language=lang, countries=_loads(oc),
                imdb={"rating": imdb[0], "votes": imdb[1]} if imdb else None,
                tmdb={"rating": round(va, 1), "votes": vc} if va and vc else None,
                seasons=seasons, latest_season=latest["number"] if latest else None, providers=provs, cast=cast,
                trailer={"key": vids[0][0], "name": vids[0][1], "url": f"https://www.youtube.com/watch?v={vids[0][0]}"} if vids else None,
                next_episode=nxt, best_episodes=best_episodes(sid), following=following,
                has_episodes=_has("tv_episodes"),
                tvmaze=({"airs": _tvmaze("airs_text", "", tm), "url": tm.get("url"), "credit": _tvmaze("credit", None)}
                        if tm else None))


def _tvmaze(fn, default, *a):
    """Call web/tvmaze.py (round 27) and never fail the page: logs/tvmaze.db may be missing or still loading."""
    try:
        import tvmaze
        if fn == "credit":
            return tvmaze.CREDIT
        return getattr(tvmaze, fn)(*a) or default
    except Exception:  # noqa: BLE001
        return default


# ------------------------------------------------------------------ Pick Tonight for TV
MOOD_TV = {"Comedy": {"Comedy"}, "Thriller": {"Crime", "Mystery", "Action & Adventure"}, "Drama": {"Drama"},
           "Science Fiction": {"Sci-Fi & Fantasy"}, "Family": {"Family", "Kids", "Animation"}, "Horror": None}


def _watched_or_followed(user):
    s = set()
    if not user:
        return s
    try:
        import discovery
        s |= discovery._following_ids(user)
    except Exception:  # noqa: BLE001
        pass
    try:
        import history
        s |= {r[0] for r in history.conn().execute("select distinct tmdb_id from watch_events where user_key = ? and media_type in "
                                                    "('tv', 'episode')", (user,))}
    except Exception:  # noqa: BLE001
        pass
    return s


def pick(user="", genre=None, max_cert=None, max_runtime=None, services=None, never=(), novelty="familiar", exclude=(), n=3):
    """Ranked shows that pass every limit; a show whose fact is unknown fails that limit (episode length, US TV rating,
    services). familiar = best weighted rating, a little weight on how many people voted; different = well rated but
    less voted on (under 50,000 IMDb / 1,500 TMDB votes). Shows the person follows or has watched are left out.
    -> {picks, candidates (next 12, for "try different"), removed (counts by reason), considered}"""
    rows = _q("select tmdb_id, genres_json, episode_runtime, vote_count from tv_shows where coalesce(vote_count, 0) >= 40")
    seen = _watched_or_followed(user) | {int(x) for x in exclude}
    ids = [r[0] for r in rows if r[0] not in seen]
    gmap = {r[0]: set(_loads(r[1])) for r in rows}
    rtm = {r[0]: r[2] for r in rows}
    removed = {}

    def drop(why):
        removed[why] = removed.get(why, 0) + 1
        return False
    horror = set()
    if genre == "Horror":
        horror = {r[0] for r in _q("select distinct tmdb_id from tv_keywords where lower(name) like '%horror%'")}
    certs = dict(_q("select tmdb_id, rating from tv_ratings where region = 'US'")) if max_cert else {}
    svc_ok = None
    if services:
        import user_prefs
        svc_ok = {}
        for sid, name in _q("select tmdb_id, provider_name from tv_providers where region = 'US' and kind in ('flatrate', 'free', 'ads')"):
            k = user_prefs.service_of(name)
            if k in services:
                svc_ok.setdefault(sid, set()).add(user_prefs.SERVICES[k][0])
    never = set(never or [])
    keep = []
    for sid in ids:
        tg = gmap.get(sid, set())
        if tg & SKIP_TV:
            continue
        if genre:
            want = MOOD_TV.get(genre)
            if want is None and genre == "Horror":
                if sid not in horror and "Horror" not in tg and not drop("not the mood you picked"):
                    continue
            elif want and not (tg & want) and not drop("not the mood you picked"):
                continue
        if never and set(site_genres(list(tg))) & never and not drop("a genre you never want"):
            continue
        if max_runtime and (not rtm.get(sid) or rtm[sid] > max_runtime) and not drop("episodes too long or length unknown"):
            continue
        if max_cert and certs.get(sid) not in TV_CERT_OK.get(max_cert, set()) and not drop("US TV rating too high or unknown"):
            continue
        if svc_ok is not None and sid not in svc_ok and not drop("not on your services"):
            continue
        keep.append(sid)
    b = basics(keep)
    imdbv = {sid: (x["votes"] if x["rating_src"] == "IMDb" else None) for sid, x in b.items()}
    scored = []
    for sid in keep:
        x = b.get(sid)
        if not x or x["score"] is None:
            continue
        if novelty == "different":
            v = x["votes"] or 0
            if (x["rating_src"] == "IMDb" and v >= 50000) or (x["rating_src"] == "TMDB" and v >= 1500) or x["rating"] < 7.4:
                continue
            s = x["score"]
        else:
            s = x["score"] + 0.12 * math.log10(max(x["votes"] or 1, 1))
        scored.append((-s, sid))
    order = [sid for _s, sid in sorted(scored)]
    cards = [dict(b[sid], providers=sorted(svc_ok.get(sid, [])) if svc_ok else [], reasons=_reasons(b[sid], genre, max_runtime, max_cert, svc_ok))
             for sid in order[:n + 12]]
    return {"picks": cards[:n], "candidates": cards[n:], "removed": removed, "considered": len(ids)}


def _reasons(x, genre, max_runtime, max_cert, svc_ok):
    out = []
    gs = x["genres"]
    if genre and gs:
        out.append(f"Fits the mood ({gs.split(', ')[0]})")
    elif gs:
        out.append(" and ".join(gs.split(", ")[:2]))
    if x["episode_runtime"]:
        out.append(f"{x['episode_runtime']}-minute episodes" + (f": fits your {max_runtime} minutes" if max_runtime else ""))
    if max_cert and x["cert"]:
        out.append(f"Rated {x['cert']}")
    if svc_ok and svc_ok.get(x["id"]):
        out.append("On your " + ", ".join(sorted(svc_ok[x["id"]])))
    if x["rating"]:
        out.append(f"{x['rating']} on {x['rating_src']}" + (f" ({x['votes']:,} votes)" if x["votes"] else ""))
    if x["seasons"]:
        out.append(f"{x['seasons']} season{'s' if x['seasons'] != 1 else ''}" + (", ended" if x["status"] in ("Ended", "Canceled") else ", still running" if x["status"] == "Returning Series" else ""))
    return out
