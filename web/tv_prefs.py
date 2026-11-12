"""12 Nov 2026 (round 36): TV taste from onboarding, used by Pick Tonight for shows.

Until now a show could only be followed; nothing recorded "loved it" or "not for me", and tvdata.pick ranked shows by
rating alone. Onboarding's taste step now has a TV part:
  kinds of shows   GROUPS: Love it / Not for me             -> tv_groups(user_key, groups_json)
  shows you know   Watching / Loved it / Not for me / Haven't seen  -> tv_prefs(user_key, show_id, choice)
                   Watching also follows the show (Watchlist + new-season alerts, discovery.tv_follow)
Pick Tonight (tvdata.pick): shows marked watching / loved / not for me are left out (seen or unwanted); a show whose
main genres (web/genres.py) meet a loved kind or a loved show's genres gets a lift, a disliked kind a drop. Nothing is
hidden from search. Example posters are hand-picked (the film groups' lesson: queries pick odd examples).
Tables live in users.db (USERS_DB). Routes: install(app, user_of, gate, run) from v8routes.install.
12 Nov (round 38): Ask Cinemack's show recommendations use the same taste (TV_TASTE=1, site.env): install_chat()
wraps tv.retrieve_tv for the person whose question is being answered (user_prefs.CURRENT["user"], set by server.py):
shows they watch, loved or marked not for me leave a recommendation list (never all of it), the rest are ordered by
the same lift; and pipeline.user_tag gets a hash of their TV taste so a cached search is never reused for another
taste. People with no TV taste, and the evaluation (no person), are untouched."""
import json
import os
import sqlite3
import threading
import time
from pathlib import Path

import film_info
import genres

RAG_DIR = Path(__file__).resolve().parent.parent
USERS = os.environ.get("USERS_DB", str(RAG_DIR / "logs" / "users.db"))
CHOICES = ("watching", "liked", "not", "unseen")
# key, label, main genres it stands for, hand-picked examples (name, first year +-1)
GROUPS = [
    ("sitcom", "Sitcoms and comedy", {"Comedy"},
     [("The Office", 2005), ("Parks and Recreation", 2009), ("Brooklyn Nine-Nine", 2013), ("Friends", 1994)]),
    ("crime", "Crime and detectives", {"Crime", "Mystery"},
     [("Breaking Bad", 2008), ("Sherlock", 2010), ("True Detective", 2014), ("Mindhunter", 2017)]),
    ("drama", "Big dramas", {"Drama"},
     [("Succession", 2018), ("The Crown", 2016), ("Mad Men", 2007), ("This Is Us", 2016)]),
    ("thriller", "Thrillers and suspense", {"Thriller"},
     [("Severance", 2022), ("Mr. Robot", 2015), ("Ozark", 2017), ("Homeland", 2011)]),
    ("scifi", "Sci-fi and fantasy", {"Science Fiction", "Fantasy"},
     [("Stranger Things", 2016), ("Black Mirror", 2011), ("Game of Thrones", 2011), ("The Mandalorian", 2019)]),
    ("horror", "Horror", {"Horror"},
     [("The Haunting of Hill House", 2018), ("The Walking Dead", 2010), ("Midnight Mass", 2021), ("American Horror Story", 2011)]),
    ("action", "Action and adventure", {"Action", "Adventure"},
     [("The Boys", 2019), ("Reacher", 2022), ("The Last of Us", 2023), ("24", 2001)]),
    ("anim", "Animation and anime", {"Animation"},
     [("Avatar: The Last Airbender", 2005), ("Arcane", 2021), ("Attack on Titan", 2013), ("Rick and Morty", 2013)]),
    ("history", "History and war", {"History", "War"},
     [("Band of Brothers", 2001), ("Chernobyl", 2019), ("Vikings", 2013), ("Peaky Blinders", 2013)]),
    ("romance", "Romance", {"Romance"},
     [("Bridgerton", 2020), ("Normal People", 2020), ("Outlander", 2014), ("Jane the Virgin", 2014)]),
    ("docs", "Docuseries", {"Documentary"},
     [("Planet Earth", 2006), ("Making a Murderer", 2015), ("Chef's Table", 2015), ("Our Planet", 2019)]),
    ("family", "Kids and family", {"Family"},
     [("Bluey", 2018), ("Gravity Falls", 2012), ("Phineas and Ferb", 2007), ("SpongeBob SquarePants", 1999)]),
]
KNOWN = {g[0]: g for g in GROUPS}
LIFT_GROUP, LIFT_SHOW, DROP = 0.25, 0.1, 0.4      # added to tvdata.pick's score (a weighted rating, ~7-9)
SKIP_TV = {"News", "Talk", "Soap", "Reality"}
_lock, _C, _GC = threading.Lock(), {}, {}


def conn():
    with _lock:
        if "u" not in _C:
            os.makedirs(os.path.dirname(USERS) or ".", exist_ok=True)
            c = sqlite3.connect(USERS, check_same_thread=False)
            c.executescript("""create table if not exists tv_prefs(user_key text, show_id integer, choice text, title text,
                at text, primary key (user_key, show_id));
                create table if not exists tv_groups(user_key text primary key, groups_json text, at text);""")
            c.commit()
            _C["u"] = c
        return _C["u"]


# ------------------------------------------------------------------ storage
def choices(user):
    if not user:
        return {}
    c = conn()
    with _lock:
        return dict(c.execute("select show_id, choice from tv_prefs where user_key = ?", (user,)).fetchall())


def choose(user, show_id, title, choice):
    """Store one show choice ('' clears it). 'watching' follows the show; leaving 'watching' unfollows it."""
    if choice not in CHOICES + ("",):
        raise ValueError("choice must be watching, liked, not, unseen or empty")
    sid = int(show_id)
    c = conn()
    with _lock:
        old = c.execute("select choice from tv_prefs where user_key = ? and show_id = ?", (user, sid)).fetchone()
        if choice:
            c.execute("insert or replace into tv_prefs values (?,?,?,?,?)", (user, sid, choice, (title or "")[:200],
                                                                             time.strftime("%Y-%m-%dT%H:%M:%S")))
        else:
            c.execute("delete from tv_prefs where user_key = ? and show_id = ?", (user, sid))
        c.commit()
    was = (old or [None])[0]
    if choice == "watching" or was == "watching":
        try:
            import discovery
            if choice == "watching":
                discovery.tv_follow(user, sid, True)
            elif was == "watching":
                discovery.tv_follow(user, sid, False)
        except Exception as e:  # noqa: BLE001  (the choice is saved either way)
            print(f"[tv_prefs] follow failed: {e!r}", flush=True)
    return choice


def load_groups(user):
    c = conn()
    with _lock:
        r = c.execute("select groups_json from tv_groups where user_key = ?", (user,)).fetchone()
    try:
        g = json.loads(r[0]) if r else {}
    except ValueError:
        g = {}
    return {"groups": [k for k in g.get("groups", []) if k in KNOWN], "avoid": [k for k in g.get("avoid", []) if k in KNOWN]}


def save_groups(user, picked, avoid=()):
    picked = list(dict.fromkeys(k for k in picked if k in KNOWN))
    avoid = list(dict.fromkeys(k for k in avoid if k in KNOWN and k not in picked))
    c = conn()
    with _lock:
        c.execute("insert or replace into tv_groups values (?,?,?)",
                  (user, json.dumps({"groups": picked, "avoid": avoid}), time.strftime("%Y-%m-%dT%H:%M:%S")))
        c.commit()
    return {"groups": picked, "avoid": avoid}


# ------------------------------------------------------------------ what onboarding shows
def _shows_by_name(name, year):
    want = genres_norm(name)
    rows = film_info._q(film_info._ro(film_info.FACTS),
                        "select tmdb_id, name, genres_json from tv_shows where first_year between ? and ? "
                        "and poster_path is not null order by coalesce(vote_count, 0) desc", (year - 1, year + 1))
    return [r[0] for r in rows if genres_norm(r[1]) == want and not (set(genres._list(r[2])) & SKIP_TV)]


def genres_norm(t):
    import unicodedata
    t = unicodedata.normalize("NFKD", str(t or "")).encode("ascii", "ignore").decode().lower().replace("&", " and ")
    return " ".join("".join(ch if ch.isalnum() else " " for ch in t).split())


def _popular(n=400):
    return [r[0] for r in film_info._q(film_info._ro(film_info.FACTS),
            "select tmdb_id, genres_json from tv_shows where poster_path is not null and coalesce(vote_count, 0) >= 100 "
            "order by vote_count desc limit ?", (n,)) if not (set(genres._list(r[1])) & SKIP_TV)]


def groups():
    """[{key, label, shows:[{id,title,year,poster}]}]: 3 posters per kind, hand-picked first, no show twice."""
    if "g" in _GC and time.time() - _GC["t"] < 3600:
        return _GC["g"]
    import tvdata
    used, out, pop = set(), [], None
    for key, label, gset, picks in GROUPS:
        ids = []
        for name, year in picks:
            for sid in _shows_by_name(name, year):
                if sid not in used:
                    ids.append(sid)
                    used.add(sid)
                    break
            if len(ids) == 3:
                break
        if len(ids) < 3:                               # fill from the most-voted shows whose main genres fit
            pop = pop if pop is not None else _popular()
            main = genres.shows(pop)
            for sid in pop:
                if sid not in used and main.get(sid) and set(main[sid][0]) & gset:
                    ids.append(sid)
                    used.add(sid)
                    if len(ids) == 3:
                        break
        b = tvdata.basics(ids)
        out.append({"key": key, "label": label, "shows": [{"id": i, "title": b[i]["title"], "year": b[i]["year"],
                                                            "poster": b[i]["poster"]} for i in ids if i in b]})
    _GC.update(g=out, t=time.time())
    return out


def famous(user="", n=10):
    """Ten well-known shows to mark (most IMDb votes), none of them a group example."""
    import tvdata
    skip = {s["id"] for g in groups() for s in g["shows"]}
    rows = film_info._q(film_info._ro(film_info.FACTS),
                        "select t.tmdb_id, t.genres_json from tv_shows t join imdb_ratings r on r.imdb_id = t.imdb_id "
                        "where t.poster_path is not null order by r.votes desc limit 80")
    ids = [r[0] for r in rows if r[0] not in skip and not (set(genres._list(r[1])) & SKIP_TV)][:n]
    if len(ids) < n:
        ids += [i for i in _popular(80) if i not in skip and i not in ids][:n - len(ids)]
    b = tvdata.basics(ids)
    mine = choices(user)
    return [{"id": i, "title": b[i]["title"], "year": b[i]["year"], "poster": b[i]["poster"], "mine": mine.get(i, "")}
            for i in ids if i in b]


# ------------------------------------------------------------------ Pick Tonight
def skip_ids(user):
    """Shows Pick Tonight leaves out for this person: watching, loved (seen) or not for me."""
    return {sid for sid, ch in choices(user).items() if ch in ("watching", "liked", "not")}


def boost(user, ids):
    """{show_id: score change} from the person's TV kinds and loved shows; {} when they told us nothing."""
    if not user:
        return {}
    g = load_groups(user)
    liked_g = set().union(*[KNOWN[k][2] for k in g["groups"]]) if g["groups"] else set()
    avoid_g = set().union(*[KNOWN[k][2] for k in g["avoid"]]) if g["avoid"] else set()
    loved = [sid for sid, ch in choices(user).items() if ch in ("liked", "watching")]
    loved_g = {}
    for sid, v in genres.shows(loved).items():
        for x in v[0]:
            loved_g[x] = loved_g.get(x, 0) + 1
    if not (liked_g or avoid_g or loved_g):
        return {}
    out = {}
    for sid, v in genres.shows(ids).items():
        m = set(v[0])
        d = (LIFT_GROUP if m & liked_g else 0.0) + LIFT_SHOW * min(2, sum(1 for x in m if loved_g.get(x)))
        if m & avoid_g:
            d -= DROP
        if d:
            out[sid] = round(d, 3)
    return out


def taste_tag(user):
    """Short hash of this person's TV taste ('' if none): part of the search-cache key."""
    if not user:
        return ""
    g, ch = load_groups(user), choices(user)
    if not (g["groups"] or g["avoid"] or ch):
        return ""
    import hashlib
    return hashlib.sha1(json.dumps([g, sorted(ch.items())]).encode()).hexdigest()[:8]


def personalize_tv(user, hits):
    """Recommendation hits from tv.retrieve_tv (ids in 'tv_id') for this person: marked shows out (never all of
    them), then ordered by boost(); the first hit keeps the plan."""
    if not user or not hits:
        return hits
    plan = hits[0].get("_plan")
    skip = skip_ids(user)
    ids = [h.get("tv_id") for h in hits if h.get("tv_id")]
    b = boost(user, ids)
    if not skip and not b:
        return hits
    kept = [h for h in hits if h.get("tv_id") not in skip] or hits
    kept = [h for _i, h in sorted(enumerate(kept), key=lambda x: (-b.get(x[1].get("tv_id"), 0.0), x[0]))]
    if plan is not None:
        for h in hits:
            h.pop("_plan", None)
        kept[0]["_plan"] = plan
    return kept


def install_chat():
    """Wrap tv.retrieve_tv and pipeline.user_tag (TV_TASTE=1). Safe to call twice."""
    if os.environ.get("TV_TASTE", "0") != "1":
        return False
    try:
        import tv as _tv
        import user_prefs
    except ImportError:
        return False
    orig = getattr(_tv.retrieve_tv, "_tvp_orig", _tv.retrieve_tv)

    def retrieve_tv(question, k=3, recent_titles=None, why=None):
        hits = orig(question, k=k, recent_titles=recent_titles, why=why)
        try:
            if hits and (hits[0].get("_plan") or {}).get("intent") == "recommend":
                hits = personalize_tv(user_prefs.CURRENT.get("user"), hits)
        except Exception as e:  # noqa: BLE001  (taste must never lose the answer)
            print(f"[tv_prefs] chat taste failed: {e!r}", flush=True)
        return hits
    retrieve_tv._tvp_orig = orig
    _tv.retrieve_tv = retrieve_tv
    try:
        import pipeline
        if hasattr(pipeline, "user_tag") and not getattr(pipeline.user_tag, "_tvp", False):
            base = pipeline.user_tag

            def user_tag(user):
                t = base(user)
                try:
                    tt = taste_tag(user)
                except Exception:  # noqa: BLE001
                    tt = ""
                return f"{t}|tv{tt}" if tt else t
            user_tag._tvp = True
            pipeline.user_tag = user_tag
    except Exception as e:  # noqa: BLE001  (no pipeline here, e.g. tests: chat taste still works)
        print(f"[tv_prefs] search-cache key not extended: {e!r}", flush=True)
    return True


# ------------------------------------------------------------------ routes
def install(app, user_of, gate, run):
    from typing import List

    from fastapi import Request
    from fastapi.responses import JSONResponse
    from pydantic import BaseModel

    install_chat()

    class TvChoice(BaseModel):
        id: int
        title: str = ""
        choice: str = ""

    class TvGroups(BaseModel):
        groups: List[str] = []
        avoid: List[str] = []

    @app.get("/api/v8/onboarding/tv")
    async def onboarding_tv(request: Request):
        if (g := gate(request)):
            return g
        key = user_of(request)
        gs = await run(groups)
        shows = await run(famous, key)
        return JSONResponse({"groups": gs, "shows": shows, "picked": (await run(load_groups, key)) if key else {}})

    @app.post("/api/v8/onboarding/tv/groups")
    async def onboarding_tv_groups(body: TvGroups, request: Request):
        if (g := gate(request)):
            return g
        key = user_of(request)
        if not key:
            return JSONResponse({"error": "No browser id."}, status_code=400)
        return JSONResponse(await run(save_groups, key, body.groups[:20], body.avoid[:20]))

    @app.post("/api/v8/tv/choice")
    async def tv_choice(body: TvChoice, request: Request):
        if (g := gate(request)):
            return g
        key = user_of(request)
        if not key:
            return JSONResponse({"error": "No browser id."}, status_code=400)
        try:
            return JSONResponse({"ok": True, "choice": await run(choose, key, body.id, body.title, body.choice)})
        except ValueError as e:
            return JSONResponse({"error": str(e)}, status_code=400)
