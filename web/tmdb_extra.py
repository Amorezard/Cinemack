"""Home and calendar extras from TMDB that facts.db does not hold (10 Nov 2026, round 18).

facts.db is the v8 experiment's frozen fact store and is opened read-only; it has no TV ratings or TV genres, and many
films on TMDB's upcoming list have no poster or runtime there yet. tools/fetch_home_extra.py fetches those from TMDB
into this separate file, logs/tmdb_extra.db, which only the website reads (never the answer pipeline or the
evaluations). If the file is missing or empty, every function here returns nothing and the pages work as before.

Tables (written by the fetcher, whole snapshot replaced atomically on each run):
  meta(key, value)                       fetched_at, counts
  tv_show(tmdb_id, name, first_year, vote_average, vote_count, popularity, genres_json, tv_genres_json, poster_path,
          backdrop_path, networks_json, original_language, top_rank)
          genres_json: TMDB TV genres mapped onto the 18 film genres (Action & Adventure -> Action, Adventure, ...)
          top_rank: position on TMDB's top-rated TV list, null when the show came from elsewhere
  tv_season(tmdb_id, season_number, air_date, episode_count, poster_path)
  film_art(tmdb_id, poster_path, backdrop_path, runtime)
"""
import json
import os
import sqlite3
import threading
from pathlib import Path

RAG_DIR = Path(__file__).resolve().parent.parent
DB = os.environ.get("TMDB_EXTRA_DB", str(RAG_DIR / "logs" / "tmdb_extra.db"))
IMG = "https://image.tmdb.org/t/p/"
# Not "shows" in the sense of a season you follow: excluded from both TV rows (TMDB TV genre names).
SKIP_TV_GENRES = {"News", "Talk", "Soap", "Reality"}
TV_TOP_MIN_VOTES, TV_TOP_PRIOR_VOTES = 300, 1000
_TL = threading.local()


def _c():
    """Per-thread read-only connection, reopened when the fetcher has replaced the file."""
    try:
        st = os.stat(DB)
    except OSError:
        return None
    cur = getattr(_TL, "c", None)
    if cur and cur[1] == (st.st_ino, st.st_mtime_ns):
        return cur[0]
    try:
        c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, check_same_thread=False)
        c.execute("select 1 from meta limit 1")
    except sqlite3.Error:
        return None
    if cur:
        try:
            cur[0].close()
        except sqlite3.Error:
            pass
    _TL.c = (c, (st.st_ino, st.st_mtime_ns))
    return c


def _q(sql, params=()):
    c = _c()
    if c is None:
        return []
    try:
        return c.execute(sql, params).fetchall()
    except sqlite3.Error:
        return []


def _in(ids):
    return ",".join(str(int(i)) for i in ids) or "0"


def img(path, size):
    return f"{IMG}{size}{path}" if path else None


def as_of():
    r = _q("select value from meta where key = 'fetched_at'")
    return r[0][0][:10] if r and r[0][0] else None


def film_art(ids):
    """{tmdb_id: (poster_path, backdrop_path, runtime)} for films facts.db lacks artwork or runtime for."""
    ids = [int(i) for i in ids if i is not None]
    if not ids:
        return {}
    return {r[0]: r[1:] for r in _q(f"select tmdb_id, poster_path, backdrop_path, runtime from film_art where tmdb_id in ({_in(ids)})")}


def _loads(s):
    try:
        v = json.loads(s or "[]")
        return v if isinstance(v, list) else []
    except (ValueError, TypeError):
        return []


def show_meta(ids):
    """{id: {...}} for the shows this file knows (name, year, rating, genres, poster, networks)."""
    ids = [int(i) for i in ids if i is not None]
    if not ids:
        return {}
    out = {}
    for (i, n, y, va, vc, pop, g, tg, p, b, nets, lang, rank) in _q(
            "select tmdb_id, name, first_year, vote_average, vote_count, popularity, genres_json, tv_genres_json, poster_path, "
            f"backdrop_path, networks_json, original_language, top_rank from tv_show where tmdb_id in ({_in(ids)})"):
        out[i] = {"id": i, "title": n, "year": y, "vote_average": va, "vote_count": vc or 0, "popularity": pop,
                  "genres": _loads(g), "tv_genres": _loads(tg), "poster_path": p, "backdrop_path": b,
                  "networks": _loads(nets), "lang": lang, "top_rank": rank}
    return out


def seasons(ids):
    """{id: [(season_number, air_date)]} (season 0, specials, left out), in season order."""
    ids = [int(i) for i in ids if i is not None]
    out = {}
    for sid, sn, d in _q(f"select tmdb_id, season_number, air_date from tv_season where tmdb_id in ({_in(ids)}) "
                         "and season_number > 0 order by tmdb_id, season_number"):
        out.setdefault(sid, []).append((sn, (d or "")[:10]))
    return out


def seasons_between(a, b):
    """[(show_id, season_number, air_date)] for seasons that premiered between dates a and b (inclusive)."""
    return [(s, n, d[:10]) for s, n, d in _q("select tmdb_id, season_number, air_date from tv_season where season_number > 0 "
                                              "and air_date is not null and substr(air_date, 1, 10) between ? and ?", (a, b))]


def skipped(m):
    return bool(set(m.get("tv_genres") or []) & SKIP_TV_GENRES)


def genre_ok(m, sel, mode):
    if not sel:
        return True
    gs = set(m.get("genres") or [])
    return set(sel) <= gs if mode == "all" else bool(gs & set(sel))


def top_rated_shows(sel=(), mode="any"):
    """Shows from TMDB's top-rated TV list, by the same weighted rating as the films' Top Rated row:
    WR = v/(v+m)*R + m/(v+m)*C, m = 1000 votes. C is the mean rating of the OTHER fetched shows with 300+ votes
    (popular shows on air, not the top-rated list itself, whose mean would favour thinly voted shows), or 7.0 when
    fewer than 10 are known. Ties: id. Returns the full ordered list (the genre filter runs before any cut)."""
    rows = _q("select tmdb_id, vote_average, vote_count from tv_show where top_rank is not null and vote_count >= ? "
              "and vote_average is not null", (TV_TOP_MIN_VOTES,))
    if not rows:
        return []
    ref = [r[0] for r in _q("select vote_average from tv_show where top_rank is null and vote_count >= ? and vote_average > 0",
                            (TV_TOP_MIN_VOTES,))]
    c = sum(ref) / len(ref) if len(ref) >= 10 else 7.0
    m = TV_TOP_PRIOR_VOTES
    meta = show_meta([r[0] for r in rows])
    scored = []
    for i, va, vc in rows:
        x = meta.get(i)
        if not x or skipped(x) or not genre_ok(x, sel, mode):
            continue
        scored.append((-(vc / (vc + m) * va + m / (vc + m) * c), i))
    return [i for _s, i in sorted(scored)]
