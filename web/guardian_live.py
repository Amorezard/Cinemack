"""v8.1 round 47 (16 Nov 2026): Guardian articles for one film, fetched when someone opens it - cache first.

User decision (16 Nov 09:51 / 09:56): the Guardian key's 500 calls a day are shared like this
  - live lookups when someone opens a film, about 20 a day for now, every result stored so a film is looked up
    once and never again (films out less than 30 days: at most once a day);
  - the nightly bulk run keeps its fixed 400 for now; once the reset time is known, bulk work moves to just
    before the reset and spends every call left (tools/v81_guardian_quota.py topup).

    lookup(conn_facts, tmdb_id, user_key)   -> {"articles": [...], "source": ..., "reason": ...}
    GET /api/v8/film/{tmdb_id}/press        -> the same, as JSON (install(app, gate, user_of))

Order of work, each step cheaper than the next:
  1. stored    Guardian reviews already in facts.db (critic_reviews) and articles this module stored earlier.
               A film that has either is answered from them and costs NO call.
  2. limits    a live call only when ALL hold: switch GUARDIAN_LIVE=1; the film was not looked up before (or is
               new and was looked up over a day ago); fewer than LIVE_DAILY_CAP (20) live calls in the last 24 h;
               this person made fewer than USER_HOURLY_CAP (5) live calls in the last hour; the Guardian's own
               count of calls left (last reply header, minus the calls made since) is above FLOOR (5); no "too
               many requests" reply in the last hour.
  3. call      ONE search: the film's title in quotes, section film, from a year before release, 4 s timeout.
               Only results whose headline or summary contain the title are kept (a word-for-word match).
  4. store     every result (or an empty answer) is stored with the time, so the next visit is step 1.
Every call - live, bulk, probe - is meant to go through record() into one shared ledger (calls table), so the
parts cannot spend the same calls twice. Nothing here ever raises into the page: a failure answers with what is
stored. The key comes from GUARDIAN_API_KEY (else keys.env; see guardian_key) and is never stored, logged or returned.
Shown to people: headline, date, critic, star rating, the Guardian's own short summary, and the link - with the
credit line "From the Guardian". Article text is not fetched or stored.
"""
from __future__ import annotations

import datetime
import hashlib
import json
import os
import re
import sqlite3
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Dict, List, Optional

try:                                    # the lookup itself works without the web framework
    from fastapi import Request
    from fastapi.responses import JSONResponse
except ImportError:                     # pragma: no cover
    Request = JSONResponse = None

GUARDIAN_LIVE = os.environ.get("GUARDIAN_LIVE", "0") == "1"
_ROOT = Path(__file__).resolve().parent.parent
DB = Path(os.environ.get("GUARDIAN_LIVE_DB", _ROOT / "logs" / "guardian_live.db"))
PROBE_LOG = Path(os.environ.get("GUARDIAN_QUOTA_LOG", _ROOT / "logs" / "guardian_quota.jsonl"))
G_API = "https://content.guardianapis.com/search"
UA = "Cinemack/1.0 (university research project)"
LIVE_DAILY_CAP = int(os.environ.get("GUARDIAN_LIVE_DAILY", "20"))
USER_HOURLY_CAP = 5
FLOOR = 5
TIMEOUT = 4
NEW_DAYS = 30
STALE_H = 6                      # "calls left" readings older than this are ignored
MAX_ARTICLES = 12
CREDIT = "From the Guardian"
_lock = threading.Lock()

DDL = """
create table if not exists articles(tmdb_id integer, article_id text, kind text, headline text, url text, published text,
  trail text, byline text, stars real, fetched_at text, primary key (tmdb_id, article_id));
create table if not exists lookups(tmdb_id integer primary key, status text, n integer, fetched_at text);
create table if not exists calls(id integer primary key autoincrement, at text, who text, status integer, remaining integer);
create index if not exists calls_at on calls(at);
create table if not exists user_calls(user_hash text, at text);
create index if not exists user_calls_at on user_calls(user_hash, at);
"""


def now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def iso(t: datetime.datetime) -> str:
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def parse(s: str) -> Optional[datetime.datetime]:
    try:
        return datetime.datetime.strptime(s[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=datetime.timezone.utc)
    except (TypeError, ValueError):
        return None


def connect(path=None) -> sqlite3.Connection:
    p = Path(path) if path else DB
    p.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(p), timeout=10, check_same_thread=False)
    c.executescript(DDL)
    return c


def norm(s) -> str:
    s = unicodedata.normalize("NFKD", str(s or ""))
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", " ", s.casefold().replace("&", "and"))).strip()


def guardian_key(paths=None) -> str:
    """The Guardian key: the environment first, else keys.env in the rag folder, else ~/.movie-buff.env.
    18 Nov (round 51): the site is started by start_site.sh, which reads only the switch files, so the key never
    reached the site process (the same finding as Trakt in round 38). Never printed, logged or returned."""
    v = os.environ.get("GUARDIAN_API_KEY", "").strip().strip("'\"")
    if v:
        return v
    for p in paths or (_ROOT / "keys.env", Path.home() / ".movie-buff.env"):
        try:
            lines = Path(p).read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            line = line.strip()
            if line.startswith("export "):
                line = line[7:].strip()
            if line.startswith("#") or "=" not in line:
                continue
            k, val = line.split("=", 1)
            val = val.split(" #")[0].strip().strip("'\"")
            if k.strip() == "GUARDIAN_API_KEY" and val:
                return val
    return ""


def user_hash(user_key) -> str:
    return hashlib.sha256(("guardian-live|" + str(user_key or "anon")).encode()).hexdigest()[:16]


# ------------------------------------------------------------------ the shared ledger
def record(c, who: str, status, remaining, at=None):
    """One Guardian call, whoever made it (live | bulk | probe)."""
    with _lock:
        c.execute("insert into calls(at, who, status, remaining) values (?,?,?,?)",
                  (at or iso(now()), who, status, remaining))
        c.commit()


def calls_left(c, at=None, probe_log=None) -> Optional[int]:
    """How many Guardian calls are left today, read cautiously. None = unknown.
    Round 48: the Guardian's "calls left" header is NOT one counter - seven probes on 16 Nov read 376, 499, 498, 499, 497,
    498, 499 in that order, which only makes sense if each of its servers counts on its own. So one reply cannot be
    trusted: every reading from the last STALE_H hours is taken, each lowered by the calls made since it, and the
    LOWEST result is used."""
    t = at or now()
    since = iso(t - datetime.timedelta(hours=STALE_H))
    seen = list(c.execute("select at, remaining from calls where remaining is not null and at > ?", (since,)))
    p = Path(probe_log) if probe_log else PROBE_LOG
    try:
        for line in open(p, encoding="utf-8"):
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("remaining_day") is not None and str(r.get("at", "")) > since and str(r["at"]) <= iso(t):
                seen.append((r["at"], int(r["remaining_day"])))
    except OSError:
        pass
    if not seen:
        return None
    return min(left - c.execute("select count(*) from calls where at > ?", (when,)).fetchone()[0] for when, left in seen)


def why_not(c, uhash: str, at=None, probe_log=None) -> Optional[str]:
    """None when a live call is allowed now, else the reason it is not."""
    t = at or now()
    day, hour = iso(t - datetime.timedelta(days=1)), iso(t - datetime.timedelta(hours=1))
    if c.execute("select count(*) from calls where who = 'live' and at > ?", (day,)).fetchone()[0] >= LIVE_DAILY_CAP:
        return "daily live budget used"
    if c.execute("select count(*) from user_calls where user_hash = ? and at > ?", (uhash, hour)).fetchone()[0] >= USER_HOURLY_CAP:
        return "too many lookups from this person in the last hour"
    if c.execute("select count(*) from calls where status = 429 and at > ?", (hour,)).fetchone()[0]:
        return "the Guardian said 'too many requests' in the last hour"
    left = calls_left(c, t, probe_log)
    if left is not None and left <= FLOOR:
        return f"only {left} Guardian calls left today"
    return None


# ------------------------------------------------------------------ what is stored
def stored_reviews(facts, tmdb_id: int) -> List[Dict]:
    """Guardian reviews the nightly fetch already matched to this film (facts.db critic_reviews)."""
    try:
        rows = facts.execute("""select review_key, headline, url, published, summary, critic, stars from critic_reviews
                                where source = 'guardian' and tmdb_id = ? order by published desc limit ?""",
                             (int(tmdb_id), MAX_ARTICLES)).fetchall()
    except sqlite3.Error:
        return []
    return [{"id": k, "kind": "review", "headline": h, "url": u, "published": (p or "")[:10], "summary": s, "byline": cr,
             "stars": st, "from": "nightly"} for k, h, u, p, s, cr, st in rows]


def cached(c, tmdb_id: int) -> List[Dict]:
    rows = c.execute("""select article_id, kind, headline, url, published, trail, byline, stars from articles where tmdb_id = ?
                        order by published desc limit ?""", (int(tmdb_id), MAX_ARTICLES)).fetchall()
    return [{"id": a, "kind": k, "headline": h, "url": u, "published": p, "summary": t, "byline": b, "stars": s, "from": "lookup"}
            for a, k, h, u, p, t, b, s in rows]


def due(c, tmdb_id: int, release: Optional[datetime.date], at=None) -> bool:
    """True when this film has never been looked up, or is new (< 30 days out) and was looked up over a day ago."""
    t = at or now()
    row = c.execute("select fetched_at from lookups where tmdb_id = ?", (int(tmdb_id),)).fetchone()
    if not row:
        return True
    last = parse(row[0])
    if release and last and 0 <= (t.date() - release).days < NEW_DAYS:
        return (t - last).total_seconds() > 24 * 3600
    return False


# ------------------------------------------------------------------ the one call
def strip_html(s) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", str(s or ""))).strip()


def search(key: str, title: str, year: Optional[int], opener=urllib.request.urlopen):
    """(status, remaining-or-None, results-or-None). Never raises."""
    q = {"q": '"%s"' % title.replace('"', ""), "section": "film", "order-by": "relevance", "page-size": 20,
         "show-fields": "headline,trailText,byline,starRating", "show-tags": "tone", "api-key": key}
    if year:
        q["from-date"] = f"{int(year) - 1}-01-01"
    req = urllib.request.Request(G_API + "?" + urllib.parse.urlencode(q), headers={"User-Agent": UA, "Accept": "application/json"})
    try:
        with opener(req, timeout=TIMEOUT) as r:
            rem = _remaining(r.headers)
            body = json.loads(r.read().decode("utf-8"))
            return r.status, rem, (body.get("response") or {}).get("results") or []
    except urllib.error.HTTPError as e:
        return e.code, _remaining(e.headers or {}), None
    except Exception:  # noqa: BLE001 - timeouts and network trouble are answered from the store
        return None, None, None


def _remaining(headers) -> Optional[int]:
    for k, v in (headers.items() if hasattr(headers, "items") else []):
        if k.lower() == "x-ratelimit-remaining-day":
            try:
                return int(str(v).strip())
            except ValueError:
                return None
    return None


def keep(results, title: str) -> List[Dict]:
    """Results that name the film word for word in the headline or summary."""
    n = norm(title)
    out = []
    for it in results or []:
        f = it.get("fields") or {}
        head, trail = strip_html(f.get("headline") or it.get("webTitle")), strip_html(f.get("trailText"))
        if not n or f" {n} " not in f" {norm(head)} {norm(trail)} ":
            continue
        tones = {t.get("id") for t in it.get("tags") or [] if isinstance(t, dict)}
        stars = f.get("starRating")
        out.append({"id": "guardian:" + str(it.get("id")), "kind": "review" if "tone/reviews" in tones else "news",
                    "headline": head[:300], "url": it.get("webUrl"), "published": (it.get("webPublicationDate") or "")[:10],
                    "summary": trail[:500], "byline": strip_html(f.get("byline"))[:120] or None,
                    "stars": float(stars) if stars not in (None, "") else None, "from": "lookup"})
    return out[:MAX_ARTICLES]


def store(c, tmdb_id: int, status: str, arts: List[Dict], at=None):
    t = at or iso(now())
    with _lock:
        for a in arts:
            c.execute("insert or replace into articles values (?,?,?,?,?,?,?,?,?,?)",
                      (int(tmdb_id), a["id"], a["kind"], a["headline"], a["url"], a["published"], a["summary"], a["byline"],
                       a["stars"], t))
        c.execute("insert or replace into lookups values (?,?,?,?)", (int(tmdb_id), status, len(arts), t))
        c.commit()


# ------------------------------------------------------------------ the whole lookup
def _film(facts, tmdb_id):
    try:
        row = facts.execute("select title, year, coalesce(us_release_date, release_date) from films where tmdb_id = ?",
                            (int(tmdb_id),)).fetchone()
    except sqlite3.Error:
        row = facts.execute("select title, year, null from films where tmdb_id = ?", (int(tmdb_id),)).fetchone()
    if not row:
        return None
    rel = None
    try:
        rel = datetime.date.fromisoformat(str(row[2])[:10]) if row[2] else None
    except ValueError:
        pass
    return row[0], row[1], rel


_GUARDIAN_URL = re.compile(r"^https://(www\.)?theguardian\.com/")


def merge(*lists) -> List[Dict]:
    """One list, newest first, Guardian links only (anything else stored by mistake is never served)."""
    seen, out = set(), []
    for lst in lists:
        for a in lst:
            k = a.get("url") or a.get("id")
            if k in seen or not _GUARDIAN_URL.match(str(a.get("url") or "")):
                continue
            seen.add(k)
            out.append(a)
    return sorted(out, key=lambda a: a.get("published") or "", reverse=True)[:MAX_ARTICLES]


def lookup(facts, tmdb_id: int, user_key=None, c=None, key=None, enabled=None, opener=urllib.request.urlopen,
           at=None, probe_log=None) -> Dict:
    """Articles for one film. See the module text for the order of work."""
    on = GUARDIAN_LIVE if enabled is None else enabled
    film = _film(facts, tmdb_id)
    if not film:
        return {"articles": [], "source": "none", "reason": "unknown film", "credit": CREDIT}
    title, year, rel = film
    c = c or connect()
    have = merge(stored_reviews(facts, tmdb_id), cached(c, tmdb_id))
    t = at or now()
    if have and not due(c, tmdb_id, rel, t):
        return {"articles": have, "source": "stored", "reason": None, "credit": CREDIT}
    if have and c.execute("select 1 from lookups where tmdb_id = ?", (int(tmdb_id),)).fetchone() is None \
            and not (rel and 0 <= (t.date() - rel).days < NEW_DAYS):
        return {"articles": have, "source": "stored", "reason": None, "credit": CREDIT}    # older film with reviews: no call
    if not due(c, tmdb_id, rel, t):
        return {"articles": have, "source": "stored", "reason": "looked up before", "credit": CREDIT}
    if not on:
        return {"articles": have, "source": "stored", "reason": "live lookups are switched off", "credit": CREDIT}
    k = key or guardian_key()
    if not k:
        return {"articles": have, "source": "stored", "reason": "no Guardian key", "credit": CREDIT}
    uh = user_hash(user_key)
    with _lock:
        stop = why_not(c, uh, t, probe_log)
        if stop:
            return {"articles": have, "source": "stored", "reason": stop, "credit": CREDIT}
        c.execute("insert into user_calls values (?,?)", (uh, iso(t)))
        c.commit()
    status, rem, results = search(k, title, year, opener)
    record(c, "live", status, rem, iso(t))
    if results is None:
        return {"articles": have, "source": "stored", "reason": f"the Guardian did not answer ({status})", "credit": CREDIT}
    found = keep(results, title)
    store(c, tmdb_id, "ok", found, iso(t))
    return {"articles": merge(have, found), "source": "live", "reason": None, "credit": CREDIT}


def install(app, gate, user_of, facts_path=None):
    """GET /api/v8/film/{tmdb_id}/press. 404 for everything while GUARDIAN_LIVE is off."""
    import asyncio

    fp = facts_path or os.environ.get("FACTS_DB", str(_ROOT / "facts.db"))

    @app.get("/api/v8/film/{tmdb_id}/press")
    async def press(tmdb_id: int, request: Request):
        if not GUARDIAN_LIVE:
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        if (g := gate(request)):
            return g
        if not 0 < tmdb_id < 10 ** 10:
            return JSONResponse({"error": "bad film id"}, status_code=400)
        uk = user_of(request)

        def go():
            facts = sqlite3.connect(f"file:{fp}?mode=ro", uri=True)
            try:
                return lookup(facts, tmdb_id, uk)
            finally:
                facts.close()
        try:
            out = await asyncio.wait_for(asyncio.to_thread(go), TIMEOUT + 3)
        except Exception:  # noqa: BLE001
            out = {"articles": [], "source": "none", "reason": "lookup failed", "credit": CREDIT}
        return JSONResponse(out, headers={"Cache-Control": "no-store"})
    return press
