"""v8.1 round 57 (20 Nov 2026): rent and buy prices on the film page - the deals pilot, switch DEALS.

Sources (both official; checked 20 Nov):
  Watchmode API   GET https://api.watchmode.com/v1/title/movie-<tmdb id>/sources/?regions=US with header X-API-Key
                  (key WATCHMODE_API_KEY in keys.env). Free Developer plan: 2,500 calls a MONTH (the /status call is
                  free), non-commercial, attribution required. Round 56's probe: Interstellar -> 26 US sources
                  (4 subscription, 13 rent, 9 buy) with name, type, format, price, web_url.
  Apple iTunes    GET https://itunes.apple.com/search?term=<title>&media=movie&entity=movie&country=us - no key; used
                  only when Watchmode has no rent / buy price; a result counts only with the same title (word for word)
                  and a release year within one year.
Rules (the kit's deal rules, unchanged): a price is shown only with where it came from and the date it was checked;
nothing here computes a saving, a percentage or a "best deal"; a price older than RECHECK_DAYS is fetched again, and
if that cannot happen it is shown with its date. Subscriptions are listed by name only (no price).
Spending limits so the free month cannot run out: at most DAILY_CAP Watchmode calls a day (60 -> at most ~1,800 a
month), never above MONTH_STOP of the account's reported monthly use, one call per film per RECHECK_DAYS, a 1 h pause
after a 429. Every call is counted in logs/deals.db (calls); the key is never stored, logged or returned.

    prices(facts, tmdb_id)           -> {"rent": [...], "buy": [...], "subscription": [...], "checked": date, "source": ..., "credit": ...}
    GET /api/v8/film/{tmdb_id}/prices  (404 while DEALS is off)
"""
from __future__ import annotations

import datetime
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

try:
    from fastapi import Request
    from fastapi.responses import JSONResponse
except ImportError:                     # pragma: no cover
    Request = JSONResponse = None

_ROOT = Path(__file__).resolve().parent.parent
DEALS = os.environ.get("DEALS", "0") == "1"
DB = Path(os.environ.get("DEALS_DB", _ROOT / "logs" / "deals.db"))
RECHECK_DAYS = int(os.environ.get("DEALS_RECHECK_DAYS", "7"))
DAILY_CAP = int(os.environ.get("DEALS_DAILY_CAP", "60"))
MONTH_STOP = int(os.environ.get("DEALS_MONTH_STOP", "2300"))
TIMEOUT = 6
CREDIT = "Prices: Watchmode (watchmode.com) and Apple; checked on the date shown, they change often."
UA = "Cinemack/8 (student research project)"
_lock = threading.Lock()
_OK_URL = re.compile(r"^https://[^\s]+$")
DDL = """create table if not exists prices(tmdb_id integer primary key, fetched_at text, source text, body text, status text);
create table if not exists calls(at text, source text, status integer, quota_used integer);
create table if not exists meta(key text primary key, value text);"""


def connect(path=None):
    p = Path(path) if path else DB
    p.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(p), timeout=10, check_same_thread=False)
    c.executescript(DDL)
    return c


def now():
    return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0)


def iso(t):
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def key_of(paths=None):
    v = os.environ.get("WATCHMODE_API_KEY", "").strip().strip("'\"")
    if v:
        return v
    for p in paths or (_ROOT / "keys.env", Path.home() / ".movie-buff.env"):
        try:
            for line in Path(p).read_text(encoding="utf-8", errors="replace").splitlines():
                m = re.match(r"\s*(?:export\s+)?WATCHMODE_API_KEY\s*=\s*(.*)$", line)
                if m and m.group(1).strip().strip("'\""):
                    return m.group(1).strip().strip("'\"")
        except OSError:
            continue
    return None


def norm(s):
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode().lower()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", s).split())


def shape_watchmode(rows) -> Dict:
    """Watchmode sources -> rent / buy (cheapest per service and format kept) and subscription names."""
    out = {"rent": [], "buy": [], "subscription": []}
    seen = set()
    for s in rows or []:
        if not isinstance(s, dict) or s.get("region") not in (None, "US"):
            continue
        typ, name = s.get("type"), str(s.get("name") or "").strip()
        if not name:
            continue
        if typ in ("sub", "free", "tve"):
            if name not in out["subscription"]:
                out["subscription"].append(name)
            continue
        if typ not in ("rent", "buy"):
            continue
        price = s.get("price")
        if not isinstance(price, (int, float)) or price <= 0 or price > 200:
            continue
        k = (typ, name, s.get("format"))
        if k in seen:
            continue
        seen.add(k)
        url = s.get("web_url") if isinstance(s.get("web_url"), str) and _OK_URL.match(s["web_url"]) else None
        out["rent" if typ == "rent" else "buy"].append({"service": name[:60], "format": str(s.get("format") or "")[:8] or None,
                                                         "price": round(float(price), 2), "url": url})
    for k in ("rent", "buy"):
        out[k].sort(key=lambda x: (x["price"], x["service"]))
        out[k] = out[k][:8]
    out["subscription"] = out["subscription"][:8]
    return out


def shape_itunes(results, title, year) -> Dict:
    out = {"rent": [], "buy": [], "subscription": []}
    for r in results or []:
        if norm(r.get("trackName")) != norm(title):
            continue
        try:
            ry = int(str(r.get("releaseDate") or "")[:4])
        except ValueError:
            continue
        if year and abs(ry - int(year)) > 1:
            continue
        url = r.get("trackViewUrl") if isinstance(r.get("trackViewUrl"), str) and _OK_URL.match(r["trackViewUrl"]) else None
        for fld, typ, fmt in (("trackRentalPrice", "rent", "SD"), ("trackHdRentalPrice", "rent", "HD"),
                              ("trackPrice", "buy", "SD"), ("trackHdPrice", "buy", "HD")):
            v = r.get(fld)
            if isinstance(v, (int, float)) and 0 < v <= 200:
                out[typ].append({"service": "Apple TV (iTunes)", "format": fmt, "price": round(float(v), 2), "url": url})
        break
    return out


def _get(url, headers, opener):
    req = urllib.request.Request(url, headers=dict(headers, **{"User-Agent": UA, "Accept": "application/json"}))
    try:
        with opener(req, timeout=TIMEOUT) as r:
            used = r.headers.get("X-Account-Quota-Used") if hasattr(r, "headers") else None
            return r.status, json.loads(r.read().decode("utf-8", "replace") or "null"), used
    except urllib.error.HTTPError as e:
        return e.code, None, None
    except Exception:  # noqa: BLE001
        return 0, None, None


def why_not(c, t) -> Optional[str]:
    day = iso(t)[:10]
    n = c.execute("select count(*) from calls where source = 'watchmode' and substr(at, 1, 10) = ?", (day,)).fetchone()[0]
    if n >= DAILY_CAP:
        return "daily limit reached"
    last = c.execute("select at from calls where source = 'watchmode' and status = 429 order by at desc limit 1").fetchone()
    if last and (t - datetime.datetime.strptime(last[0], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc)).total_seconds() < 3600:
        return "paused after a 'too many requests' answer"
    used = c.execute("select value from meta where key = 'quota_used'").fetchone()
    if used and used[0].isdigit() and int(used[0]) >= MONTH_STOP:
        return "monthly allowance nearly used"
    return None


def prices(facts, tmdb_id: int, c=None, key=None, enabled=None, opener=urllib.request.urlopen, at=None) -> Dict:
    on = DEALS if enabled is None else enabled
    empty = {"rent": [], "buy": [], "subscription": [], "checked": None, "source": None, "credit": CREDIT}
    try:
        row = facts.execute("select title, year from films where tmdb_id = ?", (int(tmdb_id),)).fetchone()
    except sqlite3.Error:
        row = None
    if not row:
        return dict(empty, reason="unknown film")
    title, year = row
    c = c or connect()
    t = at or now()
    with _lock:
        cached = c.execute("select fetched_at, source, body from prices where tmdb_id = ?", (int(tmdb_id),)).fetchone()
    if cached:
        age = (t - datetime.datetime.strptime(cached[0], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc)).days
        if age < RECHECK_DAYS or not on:
            return dict(json.loads(cached[2]), checked=cached[0][:10], source=cached[1], credit=CREDIT)
    if not on:
        return dict(empty, reason="switched off")
    k = key or key_of()
    body, src = None, None
    with _lock:
        stop = why_not(c, t) if k else "no Watchmode key"
        if not stop:
            c.execute("insert into calls values (?,?,?,?)", (iso(t), "watchmode", -1, None))
            c.commit()
    if not stop:
        st, data, used = _get("https://api.watchmode.com/v1/title/movie-%d/sources/?regions=US" % int(tmdb_id),
                              {"X-API-Key": k}, opener)
        with _lock:
            c.execute("update calls set status = ?, quota_used = ? where rowid = (select max(rowid) from calls where source = 'watchmode')",
                      (st, int(used) if used and str(used).isdigit() else None))
            if used and str(used).isdigit():
                c.execute("insert or replace into meta values ('quota_used', ?)", (str(used),))
            c.commit()
        if st == 200 and isinstance(data, list):
            body, src = shape_watchmode(data), "watchmode"
    if body is None or not (body["rent"] or body["buy"]):
        q = urllib.parse.urlencode({"term": title, "media": "movie", "entity": "movie", "country": "us", "limit": 10})
        st, data, _u = _get("https://itunes.apple.com/search?" + q, {}, opener)
        with _lock:
            c.execute("insert into calls values (?,?,?,?)", (iso(t), "itunes", st, None))
            c.commit()
        it = shape_itunes((data or {}).get("results") if isinstance(data, dict) else None, title, year)
        if it["rent"] or it["buy"]:
            body = dict(body or {"subscription": []}, rent=it["rent"], buy=it["buy"])
            src = "itunes"                          # the only prices shown are Apple's (Watchmode had none)
    if body is None:
        if cached:
            return dict(json.loads(cached[2]), checked=cached[0][:10], source=cached[1], credit=CREDIT,
                        reason=stop or "could not check again")
        return dict(empty, reason=stop or "no answer")
    with _lock:
        c.execute("insert or replace into prices values (?,?,?,?,?)", (int(tmdb_id), iso(t), src, json.dumps(body), "ok"))
        c.commit()
    return dict(body, checked=iso(t)[:10], source=src, credit=CREDIT)


def install(app, gate, facts_path=None):
    """GET /api/v8/film/{tmdb_id}/prices - 404 for everything while DEALS is off."""
    import asyncio
    fp = facts_path or os.environ.get("FACTS_DB", str(_ROOT / "facts.db"))

    @app.get("/api/v8/film/{tmdb_id}/prices")
    async def film_prices(tmdb_id: int, request: Request):
        if not DEALS:
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        if (g := gate(request)):
            return g
        if not 0 < tmdb_id < 10 ** 10:
            return JSONResponse({"error": "bad film id"}, status_code=400)

        def go():
            facts = sqlite3.connect(f"file:{fp}?mode=ro", uri=True, timeout=5)
            try:
                return prices(facts, tmdb_id)
            finally:
                facts.close()
        try:
            out = await asyncio.wait_for(asyncio.to_thread(go), TIMEOUT * 2 + 2)
        except Exception:  # noqa: BLE001
            out = {"rent": [], "buy": [], "subscription": [], "checked": None, "source": None, "credit": CREDIT, "reason": "failed"}
        return JSONResponse(out, headers={"Cache-Control": "no-store"})
    return film_prices
