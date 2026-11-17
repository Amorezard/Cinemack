"""v8.1 round 49 (17 Nov 2026): follow a person (an actor, a director) and see what they have made and what is coming.

Off unless FOLLOW_PEOPLE=1: while it is off every route below answers 404 and nothing is stored.

    follow(user, person_id, facts)      -> remembers it (the person must exist in facts.db; at most MAX_FOLLOWS each)
    unfollow(user, person_id)
    following(user, facts)              -> [{person_id, name, since}]
    feed(user, facts, today)            -> per followed person: films with a date still to come, their latest films,
                                           and stored Guardian articles that name them
    people_of(facts, tmdb_id, user)     -> the film's directors and first cast with person ids (for a Follow button)
    search(facts, q)                    -> people whose name starts with / contains q
    delete_user(user)                   -> every follow of that person is removed (account deletion calls this)

Everything comes from data already on the server: facts.db (people, credits, films) and the Guardian articles stored
by web/guardian_live.py. No outside call is made here, and nothing is guessed: a person with no dated film to come
simply has an empty "coming" list. Follows are private: no route shows one person's follows to anyone else.

Tables in logs/follows.db (FOLLOWS_DB overrides): follows(user_key, person_id, created_at).
"""
from __future__ import annotations

import datetime
import os
import re
import sqlite3
from pathlib import Path
from typing import Dict, List, Optional

try:                                   # at module top: a route's type hints are resolved from module globals
    from fastapi import Request
    from fastapi.responses import JSONResponse
    from pydantic import BaseModel

    class FollowBody(BaseModel):
        person_id: int
        on: bool = True
except Exception:  # noqa: BLE001  - the functions work without the web framework (tests, tools)
    Request = JSONResponse = FollowBody = None

_ROOT = Path(__file__).resolve().parent.parent
DB = Path(os.environ.get("FOLLOWS_DB", str(_ROOT / "logs" / "follows.db")))
FOLLOW_PEOPLE = os.environ.get("FOLLOW_PEOPLE", "0") == "1"
MAX_FOLLOWS = 200
MAX_COMING = 5
MAX_LATEST = 3
MAX_ARTICLES = 3
MAX_CAST = 8

DDL = """
create table if not exists follows(user_key text, person_id integer, created_at text, primary key (user_key, person_id));
"""


class FollowError(Exception):
    pass


def connect(path=None) -> sqlite3.Connection:
    p = Path(path) if path else DB
    p.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(p), timeout=10)
    c.executescript(DDL)
    try:
        os.chmod(str(p), 0o600)
    except OSError:
        pass
    return c


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _pid(person_id) -> int:
    try:
        p = int(person_id)
    except (TypeError, ValueError):
        raise FollowError("bad person id")
    if not 0 < p < 10 ** 10:
        raise FollowError("bad person id")
    return p


def _name(facts, pid: int) -> Optional[str]:
    r = facts.execute("select name from people where person_id = ?", (pid,)).fetchone()
    return r[0] if r and r[0] else None


def follow(user: str, person_id, facts, db=None) -> Dict:
    if not user:
        raise FollowError("no user")
    pid = _pid(person_id)
    name = _name(facts, pid)
    if not name:
        raise FollowError("no such person")
    c = connect(db)
    try:
        n = c.execute("select count(*) from follows where user_key = ?", (user,)).fetchone()[0]
        have = c.execute("select 1 from follows where user_key = ? and person_id = ?", (user, pid)).fetchone()
        if not have and n >= MAX_FOLLOWS:
            raise FollowError("You follow %d people already; unfollow someone first." % MAX_FOLLOWS)
        c.execute("insert or ignore into follows(user_key, person_id, created_at) values (?,?,?)", (user, pid, _now()))
        c.commit()
    finally:
        c.close()
    return {"person_id": pid, "name": name, "following": True}


def unfollow(user: str, person_id, db=None) -> Dict:
    pid = _pid(person_id)
    c = connect(db)
    try:
        c.execute("delete from follows where user_key = ? and person_id = ?", (user or "", pid))
        c.commit()
    finally:
        c.close()
    return {"person_id": pid, "following": False}


def _ids(user: str, db=None) -> List:
    if not user:
        return []
    c = connect(db)
    try:
        return c.execute("select person_id, created_at from follows where user_key = ? order by created_at desc, person_id",
                         (user,)).fetchall()
    finally:
        c.close()


def following(user: str, facts, db=None) -> List[Dict]:
    out = []
    for pid, at in _ids(user, db):
        name = _name(facts, pid)
        if name:                                   # a person removed from facts.db is not shown (the row stays)
            out.append({"person_id": pid, "name": name, "since": (at or "")[:10]})
    return out


def _role(kind, job, character) -> str:
    if kind == "cast":
        return "as %s" % character if character else "cast"
    return job or "crew"


def _credits(facts, pid: int) -> List[Dict]:
    """Every film of this person that has a full release date, one row per film (their most senior credit)."""
    rows = facts.execute(
        "select f.tmdb_id, f.title, f.release_date, c.kind, c.job, c.character, c.ord "
        "from credits c join films f on f.tmdb_id = c.tmdb_id where c.person_id = ? "
        "and f.release_date is not null and length(f.release_date) = 10", (pid,)).fetchall()
    best: Dict[int, Dict] = {}
    for fid, title, date, kind, job, ch, o in rows:
        rank = (0 if (job or "") == "Director" else 1 if kind == "cast" else 2, o if o is not None else 999)
        if fid not in best or rank < best[fid]["_rank"]:
            best[fid] = {"id": fid, "title": title, "date": date, "role": _role(kind, job, ch), "_rank": rank}
    return list(best.values())


def _word_pattern(name: str):
    parts = [re.escape(p) for p in name.split() if p]
    if len(parts) < 2:                              # a single word ("Cher") matches too much to be trusted
        return None
    return re.compile(r"(?<![\w])" + r"\s+".join(parts) + r"(?![\w])", re.I)


def articles_about(name: str, guardian_db=None, limit=MAX_ARTICLES) -> List[Dict]:
    """Stored Guardian articles whose headline or summary names the person in full. Only https theguardian.com
    links are returned. Reads the store written by guardian_live; never calls the Guardian."""
    pat = _word_pattern(name or "")
    if pat is None:
        return []
    import guardian_live as GL
    p = Path(guardian_db) if guardian_db else GL.DB
    if not p.exists():
        return []
    c = sqlite3.connect("file:%s?mode=ro" % p, uri=True, timeout=5)
    try:
        last = [w for w in name.split() if w][-1]
        rows = c.execute("select article_id, headline, url, published, kind, trail from articles "
                         "where headline like ? or trail like ? order by published desc limit 400",
                         ("%" + last + "%", "%" + last + "%")).fetchall()
    except sqlite3.Error:
        return []
    finally:
        c.close()
    out, seen = [], set()
    for aid, head, url, pub, kind, trail in rows:
        if aid in seen or not (url or "").startswith("https://www.theguardian.com/"):
            continue
        if pat.search(head or "") or pat.search(trail or ""):
            seen.add(aid)
            out.append({"headline": head, "url": url, "published": (pub or "")[:10], "kind": kind})
            if len(out) >= limit:
                break
    return out


def feed(user: str, facts, today: Optional[datetime.date] = None, db=None, guardian_db=None) -> List[Dict]:
    t = (today or datetime.date.today()).isoformat()
    out = []
    for p in following(user, facts, db):
        cr = _credits(facts, p["person_id"])
        coming = sorted((x for x in cr if x["date"] > t), key=lambda x: (x["date"], x["id"]))[:MAX_COMING]
        latest = sorted((x for x in cr if x["date"] <= t), key=lambda x: (x["date"], x["id"]), reverse=True)[:MAX_LATEST]
        strip = lambda xs: [{k: v for k, v in x.items() if k != "_rank"} for x in xs]  # noqa: E731
        out.append(dict(p, coming=strip(coming), latest=strip(latest),
                        articles=articles_about(p["name"], guardian_db), credit="From the Guardian"))
    return out


def people_of(facts, tmdb_id: int, user: str = "", db=None) -> List[Dict]:
    mine = {pid for pid, _ in _ids(user, db)}
    rows = facts.execute(
        "select p.person_id, p.name, c.kind, c.job, c.character, c.ord from credits c join people p "
        "on p.person_id = c.person_id where c.tmdb_id = ? and (c.job = 'Director' or c.kind = 'cast') "
        "order by case when c.job = 'Director' then 0 else 1 end, c.ord", (int(tmdb_id),)).fetchall()
    out, seen, cast = [], set(), 0
    for pid, name, kind, job, ch, _o in rows:
        if pid in seen or not name:
            continue
        if job != "Director":
            if cast >= MAX_CAST:
                continue
            cast += 1
        seen.add(pid)
        out.append({"person_id": pid, "name": name, "role": _role(kind, job, ch), "following": pid in mine})
    return out


def search(facts, q: str, limit: int = 10) -> List[Dict]:
    q = re.sub(r"[%_\\]", " ", (q or "")).strip()[:60]
    if len(q) < 2:
        return []
    rows = facts.execute(
        "select p.person_id, p.name, count(c.tmdb_id) n from people p join credits c on c.person_id = p.person_id "
        "where p.name like ? group by p.person_id order by (p.name like ?) desc, n desc, p.name limit ?",
        ("%" + q + "%", q + "%", max(1, min(int(limit), 25)))).fetchall()
    return [{"person_id": pid, "name": name, "films": n} for pid, name, n in rows]


def delete_user(user: str, db=None) -> int:
    if not user or not Path(db or DB).exists():      # nothing was ever stored: do not create the file
        return 0
    c = connect(db)
    try:
        n = c.execute("delete from follows where user_key = ?", (user,)).rowcount
        c.commit()
        return n
    finally:
        c.close()


def move_user(old: str, new: str, db=None) -> int:
    """Signing in: the browser's follows become the account's (ones the account already has are kept once)."""
    if not old or not new or old == new or not Path(db or DB).exists():
        return 0
    c = connect(db)
    try:
        n = c.execute("insert or ignore into follows(user_key, person_id, created_at) "
                      "select ?, person_id, created_at from follows where user_key = ?", (new, old)).rowcount
        c.execute("delete from follows where user_key = ?", (old,))
        c.commit()
        return n
    finally:
        c.close()


def install(app, gate, user_of, facts_path=None):
    """GET/POST /api/v8/follows, GET /api/v8/film/{id}/people, GET /api/v8/people/search. 404 while FOLLOW_PEOPLE is off."""
    import asyncio

    fp = facts_path or os.environ.get("FACTS_DB", str(_ROOT / "facts.db"))
    off = lambda: JSONResponse({"detail": "Not Found"}, status_code=404)  # noqa: E731

    def with_facts(fn, *a):
        facts = sqlite3.connect("file:%s?mode=ro" % fp, uri=True)
        try:
            return fn(facts, *a)
        finally:
            facts.close()

    @app.get("/api/v8/follows")
    async def follows_get(request: Request):
        if not FOLLOW_PEOPLE:
            return off()
        if (g := gate(request)):
            return g
        uk = user_of(request)
        out = await asyncio.to_thread(with_facts, lambda f: feed(uk, f))
        return JSONResponse({"people": out, "max": MAX_FOLLOWS}, headers={"Cache-Control": "no-store"})

    @app.post("/api/v8/follows")
    async def follows_post(body: FollowBody, request: Request):
        if not FOLLOW_PEOPLE:
            return off()
        if (g := gate(request)):
            return g
        uk = user_of(request)
        if not uk:
            return JSONResponse({"error": "No browser id."}, status_code=400)
        try:
            if body.on:
                out = await asyncio.to_thread(with_facts, lambda f: follow(uk, body.person_id, f))
            else:
                out = await asyncio.to_thread(unfollow, uk, body.person_id)
        except FollowError as e:
            return JSONResponse({"error": str(e)}, status_code=400)
        return JSONResponse(out)

    @app.get("/api/v8/film/{tmdb_id}/people")
    async def film_people(tmdb_id: int, request: Request):
        if not FOLLOW_PEOPLE:
            return off()
        if (g := gate(request)):
            return g
        if not 0 < tmdb_id < 10 ** 10:
            return JSONResponse({"error": "bad film id"}, status_code=400)
        uk = user_of(request)
        out = await asyncio.to_thread(with_facts, lambda f: people_of(f, tmdb_id, uk))
        return JSONResponse({"people": out}, headers={"Cache-Control": "no-store"})

    @app.get("/api/v8/people/search")
    async def people_search(request: Request, q: str = ""):
        if not FOLLOW_PEOPLE:
            return off()
        if (g := gate(request)):
            return g
        return JSONResponse({"people": await asyncio.to_thread(with_facts, lambda f: search(f, q))})
    return follows_get
