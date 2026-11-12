"""12 Nov 2026 (round 36): one definition of "this film's / show's genres" for the website's genre filters.

Why: TMDB lists every genre a film touches, so a Drama filter or Pick Tonight's "Moving" mood returned Inside Out
(Animation, Family, Adventure, Drama, Comedy) and The Wolf of Wall Street (Crime, Drama, Comedy), and "Funny" returned
Forrest Gump (Comedy, Drama, Romance). IMDb lists at most three genres per title: the main ones.

  films  main  = TMDB's genres that IMDb also lists (both sources agree, like round 27's GENRE_AGREE for rankings).
               Family is kept for an animated film TMDB calls Family (IMDb's three slots rarely reach it: Toy Story is
               Adventure, Animation, Comedy). No IMDb genres, or no overlap at all -> TMDB's list unchanged.
  shows  main  = IMDb's genres (TMDB's TV list has no Horror, Thriller, Romance...); no IMDb genres -> TMDB's, mapped.
  both   broad = TMDB's + IMDb's, for "never recommend" (a never-Horror person should not get a horror-comedy whose
               main genre is Comedy).

12 Nov (round 37): on the real data the plain agreement rule (variant 1) also removed genres IMDb's three slots had no
room for: Knives Out left Mystery, Star Wars and Avatar left Science Fiction, Se7en and The Silence of the Lambs left
Thriller. Variants, chosen by GENRE_MAIN (1, 2 or 3; anything else = off):
  1  TMDB's genres that IMDb also lists
  2  only BROAD genres need IMDb's agreement (Drama, Comedy, Action, Adventure, Family, Fantasy, Thriller, Romance:
     the ones TMDB adds loosely); specific genres (Science Fiction, Horror, Mystery, Crime, War, Western, History,
     Music, Documentary, Animation) stay as TMDB lists them
  3  as 2, and TMDB's first-listed genre always stays
  4  as 2, and a broad genre IMDb did not list also stays when the MovieLens tag genome scores it at or above that
     genre's cut-off (round 39; logs/genre_votes.db from tools/build_genre_votes.py; the cut-off is the 25th
     percentile among films TMDB and IMDb agree on). Films the genome does not cover behave as variant 2.
tools/genre_gold.py scores the variants on hand-labelled films and the install picks the winner.

Used by: Home genre chips (discovery), chat genre filters and Pick Tonight films (user_prefs.excluded), Pick Tonight
shows (tvdata.pick), the Profile taste summary (v8extra). Switch GENRE_MAIN=1 (site.env); off = TMDB's lists, as before.
Read-only: facts.db films / tv_shows joined to imdb_titles on the IMDb id."""
import json
import os
import sqlite3
import threading
import time

import film_info

VARIANT = os.environ.get("GENRE_MAIN", "0").strip()
ON = VARIANT in ("1", "2", "3", "4")
VOTES = os.environ.get("GENRE_VOTES_DB", str(film_info.RAG_DIR / "logs" / "genre_votes.db"))

BROAD = {"Drama", "Comedy", "Action", "Adventure", "Family", "Fantasy", "Thriller", "Romance"}
SITE = ["Action", "Adventure", "Animation", "Comedy", "Crime", "Documentary", "Drama", "Family", "Fantasy",
        "History", "Horror", "Music", "Mystery", "Romance", "Science Fiction", "Thriller", "War", "Western"]
IMDB_MAP = {"Sci-Fi": "Science Fiction", "Musical": "Music"}
TV_MAP = {"Action & Adventure": ["Action", "Adventure"], "Sci-Fi & Fantasy": ["Science Fiction", "Fantasy"],
          "War & Politics": ["War"], "Kids": ["Family"]}
# Pick Tonight moods for shows, in main genres (the old table used TMDB TV names: Thriller = Crime, Mystery or
# Action & Adventure; Family = Family, Kids or Animation, which let Rick and Morty into "Cosy")
TV_MOOD = {"Comedy": {"Comedy"}, "Thriller": {"Thriller", "Crime", "Mystery"}, "Drama": {"Drama"},
           "Science Fiction": {"Science Fiction"}, "Family": {"Family"}, "Horror": {"Horror"}}
CHUNK, TTL = 900, 6 * 3600
_cache, _lock = {"t": 0.0, "film": {}, "tv": {}}, threading.Lock()


def _list(s):
    try:
        v = json.loads(s or "[]")
        return [str(x) for x in v] if isinstance(v, list) else []
    except (ValueError, TypeError):
        return []


def imdb_genres(s):
    """'Adventure,Animation,Sci-Fi' -> {'Adventure', 'Animation', 'Science Fiction'} (site genres only)."""
    if not s or s == "\\N":
        return set()
    return {IMDB_MAP.get(x.strip(), x.strip()) for x in str(s).split(",")} & set(SITE)


def film_split(tmdb, imdb, variant=None, votes=None, cuts=None):
    """(main, broad) lists for a film from TMDB's genre list and IMDb's genre string (variant: see the top);
    votes {genre: genome relevance} and cuts {genre: cut-off} only matter for variant 4."""
    t, i = list(tmdb or []), imdb_genres(imdb)
    if not i:
        return t, t
    v = str(variant or VARIANT)
    if v == "4":
        votes, cuts = votes or {}, cuts if cuts is not None else genome_cuts()
        main = [g for g in t if g in i or g not in BROAD or (g in cuts and votes.get(g, -1.0) >= cuts[g])]
    elif v == "3":
        main = [g for k, g in enumerate(t) if g in i or g not in BROAD or k == 0]
    elif v == "2":
        main = [g for g in t if g in i or g not in BROAD]
    else:
        main = [g for g in t if g in i]
    if "Family" in t and "Animation" in main and "Family" not in main:
        main.append("Family")
    if not main:
        main = t
    return main, list(dict.fromkeys(t + [g for g in SITE if g in i]))


_GV = {"t": 0.0, "cuts": None}


def _votes_conn():
    if not os.path.exists(VOTES):
        return None
    try:
        return sqlite3.connect(f"file:{VOTES}?mode=ro", uri=True, check_same_thread=False)
    except sqlite3.Error:
        return None


def genome_cuts():
    """{genre: cut-off} from logs/genre_votes.db ({} when it is not built: variant 4 then equals variant 2)."""
    with _lock:
        if _GV["cuts"] is not None and time.time() - _GV["t"] < TTL:
            return _GV["cuts"]
    c, cuts = _votes_conn(), {}
    if c is not None:
        try:
            cuts = {g: float(x) for g, x in c.execute("select genre, cut from genre_cut")}
        except sqlite3.Error:
            cuts = {}
        c.close()
    with _lock:
        _GV.update(t=time.time(), cuts=cuts)
    return cuts


def genome_votes(ids):
    """{tmdb_id: {genre: relevance}} for the broad genres the genome covers."""
    ids = [int(i) for i in ids]
    c, out = _votes_conn(), {}
    if c is None or not ids:
        return out
    try:
        for k in range(0, len(ids), CHUNK):
            part = ids[k:k + CHUNK]
            for fid, g, rel in c.execute("select tmdb_id, genre, rel from genome_genre where tmdb_id in (" + ",".join("?" * len(part)) + ")", part):
                out.setdefault(fid, {})[g] = rel
    except sqlite3.Error:
        pass
    c.close()
    return out


def tv_split(tmdb_tv, imdb):
    """(main, broad) lists for a show from TMDB's TV genres and IMDb's genre string."""
    t = []
    for g in tmdb_tv or []:
        for x in TV_MAP.get(g, [g]):
            if x in SITE and x not in t:
                t.append(x)
    i = [g for g in SITE if g in imdb_genres(imdb)]
    return (i or t), list(dict.fromkeys(t + i))


def _rows(conn, sql_join, sql_plain, ids):
    out = []
    for k in range(0, len(ids), CHUNK):
        part = ids[k:k + CHUNK]
        marks = ",".join("?" * len(part))
        try:
            out += conn.execute(sql_join.format(marks), part).fetchall()
        except sqlite3.Error:                          # no imdb_titles table on this copy: TMDB only
            try:
                out += [(r[0], r[1], None) for r in conn.execute(sql_plain.format(marks), part).fetchall()]
            except sqlite3.Error:
                pass
    return out


def _lookup(kind, ids):
    ids = list(dict.fromkeys(int(i) for i in ids if isinstance(i, int) or (isinstance(i, str) and i.isdigit())))
    with _lock:
        if time.time() - _cache["t"] > TTL:
            _cache.update(t=time.time(), film={}, tv={})
        have = _cache[kind]
        need = [i for i in ids if i not in have]
    if need:
        conn = film_info._ro(film_info.FACTS)
        if conn is not None:
            if kind == "film":
                rows = _rows(conn, "select f.tmdb_id, f.genres_json, t.genres from films f left join imdb_titles t "
                                   "on t.tconst = f.imdb_id where f.tmdb_id in ({})",
                             "select tmdb_id, genres_json from films where tmdb_id in ({})", need)
                split = film_split
            else:
                rows = _rows(conn, "select s.tmdb_id, s.genres_json, t.genres from tv_shows s left join imdb_titles t "
                                   "on t.tconst = s.imdb_id where s.tmdb_id in ({})",
                             "select tmdb_id, genres_json from tv_shows where tmdb_id in ({})", need)
                split = tv_split
            got = {}
            gv = genome_votes([r[0] for r in rows]) if kind == "film" and ON and VARIANT == "4" else {}
            cuts = genome_cuts() if gv else None
            for fid, g, ig in rows:
                m, b = split(_list(g), ig if ON else None, votes=gv.get(fid), cuts=cuts) if gv else split(_list(g), ig if ON else None)
                got[fid] = (frozenset(m), frozenset(b), tuple(m))
            with _lock:
                _cache[kind].update(got)
    with _lock:
        have = _cache[kind]
        return {i: have[i] for i in ids if i in have}


def films(ids):
    """{tmdb_id: (main frozenset, broad frozenset, main in TMDB order)}; films unknown to facts.db are left out.
    With GENRE_MAIN off, main = broad = TMDB's list."""
    return _lookup("film", ids)


def shows(ids):
    """Same for TV shows (tv_shows.tmdb_id)."""
    return _lookup("tv", ids)


def tv_mood_ok(main, genre, horror_keyword=False):
    """Pick Tonight for shows: does a show with these main genres fit the mood genre?"""
    if genre == "Horror" and horror_keyword:
        return True
    return bool(set(main) & TV_MOOD.get(genre, {genre}))
