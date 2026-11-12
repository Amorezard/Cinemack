"""
v8 (9 Nov 2026): Trakt sign-in (OAuth 2.0 authorisation code + PKCE, no client secret) and import.

    start(user, base_url)      -> the trakt.tv URL to send the person to (state + PKCE verifier kept 10 minutes)
    finish(state, code)        -> exchanges the code for tokens, stores them in logs/trakt.db, returns the user key
    sync(user)                 -> watched films and episodes (history.import), ratings (social reviews), watchlist
    disconnect(user)           -> revokes the token at Trakt, deletes it and every row imported from Trakt

Only the tokens and the imported films are stored; Trakt's items carry TMDB ids, so nothing is matched by title.
Needs TRAKT_CLIENT_ID (keys.env) and the app's redirect URI set to <site>/trakt/callback (TRAKT_REDIRECT overrides).
API: https://api.trakt.tv, headers trakt-api-version: 2 and trakt-api-key: <client id>.
"""
import base64
import hashlib
import json
import os
import secrets
import sqlite3

import dbsafe   # 10 Nov: shared connection, one call at a time (see dbsafe.py)
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import history

RAG_DIR = Path(__file__).resolve().parent.parent
DB = os.environ.get("TRAKT_DB", str(RAG_DIR / "logs" / "trakt.db"))
# 12 Nov (round 38): the site process does not load keys.env (start_site.sh reads only the switch files), so the
# Trakt client ID set there never reached it ("Trakt is not set up"). Read it from the environment, else keys.env in
# the rag folder, else ~/.movie-buff.env, under any of the usual names. Values are never printed.
_ID_NAMES = ("TRAKT_CLIENT_ID", "TRAKT_ID", "TRAKT_API_KEY", "TRAKT_KEY", "TRAKT_CLIENT")
_SECRET_NAMES = ("TRAKT_CLIENT_SECRET", "TRAKT_SECRET")


def _key_from_files(names, paths=None):
    for p in paths or (RAG_DIR / "keys.env", Path.home() / ".movie-buff.env"):
        try:
            lines = Path(p).read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            line = line.strip()
            if line.startswith("export "):
                line = line[7:].strip()
            if "=" not in line or line.startswith("#"):
                continue
            k, v = line.split("=", 1)
            v = v.split(" #")[0].strip().strip("'\"")
            if k.strip() in names and v:
                return v
    return ""


def _key(names):
    for n in names:
        v = os.environ.get(n, "").strip().strip("'\"")
        if v:
            return v
    return _key_from_files(names)


CLIENT_ID = _key(_ID_NAMES)
CLIENT_SECRET = _key(_SECRET_NAMES)       # optional: PKCE needs none; sent on the device-code exchange if present
REDIRECT = os.environ.get("TRAKT_REDIRECT", "REDACTED/trakt/callback")
API = "https://api.trakt.tv"
UA = "Cinemack/1.0 (Merrimack College student research project)"   # 12 Nov: Trakt behind Cloudflare
AUTH = "https://auth.trakt.tv/oauth/authorize"           # 12 Nov: Trakt's sign-in host (developer.trakt.tv)
_pending = {}            # state -> (user, verifier, created)
_lock = threading.Lock()
_C = {}
HTTP = None              # tests replace this with a fake: HTTP(method, url, body, headers) -> (status, json, headers)


class TraktError(Exception):
    pass


def enabled():
    return bool(CLIENT_ID)


def conn():
    if DB not in _C:
        os.makedirs(os.path.dirname(DB), exist_ok=True)
        c = dbsafe.wrap(sqlite3.connect(DB, check_same_thread=False, timeout=30))
        c.execute("create table if not exists tokens(user_key text primary key, access text, refresh text, "
                  "expires_at real, created_at text, last_sync text)")
        _C[DB] = c
        try:
            os.chmod(DB, 0o600)
        except OSError:
            pass
    return _C[DB]


def _http(method, url, body=None, headers=None):
    if HTTP:
        return HTTP(method, url, body, headers or {})
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers=dict({"Content-Type": "application/json", "User-Agent": UA},
                                                                            **(headers or {})))
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else None), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, None, dict(e.headers or {})


def start(user):
    if not enabled():
        raise TraktError("Trakt isn't set up on this server (TRAKT_CLIENT_ID).")
    verifier = secrets.token_urlsafe(64)[:96]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    state = secrets.token_urlsafe(24)
    with _lock:
        now = time.time()
        for s in [s for s, v in _pending.items() if now - v[2] > 600]:
            _pending.pop(s, None)
        _pending[state] = (user, verifier, now)
    return AUTH + "?" + urllib.parse.urlencode({"response_type": "code", "client_id": CLIENT_ID, "redirect_uri": REDIRECT,
                                                "state": state, "code_challenge": challenge,
                                                "code_challenge_method": "S256"})


def finish(state, code):
    with _lock:
        p = _pending.pop(state or "", None)
    if not p or time.time() - p[2] > 600:
        raise TraktError("That sign-in link expired. Start again from your profile.")
    user, verifier, _t = p
    st, tok, _h = _http("POST", API + "/oauth/token", {"code": code, "client_id": CLIENT_ID, "redirect_uri": REDIRECT,
                                                       "grant_type": "authorization_code", "code_verifier": verifier})
    if st != 200 or not tok or not tok.get("access_token"):
        raise TraktError(f"Trakt didn't accept the sign-in (HTTP {st}).")
    _store(user, tok)
    return user


def _store(user, tok):
    with _lock:
        c = conn()
        c.execute("insert or replace into tokens values (?,?,?,?,?,(select last_sync from tokens where user_key = ?))",
                  (user, tok["access_token"], tok.get("refresh_token"), time.time() + float(tok.get("expires_in") or 0),
                   time.strftime("%Y-%m-%dT%H:%M:%S"), user))
        c.commit()


def connected(user):
    r = conn().execute("select created_at, last_sync from tokens where user_key = ?", (user,)).fetchone()
    return {"connected": bool(r), "since": r[0] if r else None, "last_sync": r[1] if r else None, "available": enabled()}


def _token(user):
    r = conn().execute("select access, refresh, expires_at from tokens where user_key = ?", (user,)).fetchone()
    if not r:
        raise TraktError("Trakt isn't connected.")
    access, refresh, exp = r
    if exp and time.time() > exp - 300 and refresh:
        st, tok, _h = _http("POST", API + "/oauth/token", dict({"refresh_token": refresh, "client_id": CLIENT_ID, "grant_type": "refresh_token",
                                                                "redirect_uri": REDIRECT if REDIRECT.startswith("https://") else "urn:ietf:wg:oauth:2.0:oob"},
                                                               **({"client_secret": CLIENT_SECRET} if CLIENT_SECRET else {})))
        if st == 200 and tok and tok.get("access_token"):
            _store(user, tok)
            access = tok["access_token"]
    return access


def _get_all(user, path, max_pages=50):
    h = {"trakt-api-version": "2", "trakt-api-key": CLIENT_ID, "Authorization": f"Bearer {_token(user)}"}
    out, page = [], 1
    while page <= max_pages:
        st, data, hdr = _http("GET", f"{API}{path}{'&' if '?' in path else '?'}page={page}&limit=100", None, h)
        if st == 401:
            raise TraktError("Trakt signed this account out. Connect it again.")
        if st != 200 or not isinstance(data, list):
            raise TraktError(f"Trakt returned HTTP {st} for {path}.")
        out += data
        pages = int({k.lower(): v for k, v in (hdr or {}).items()}.get("x-pagination-page-count", 1) or 1)
        if page >= pages:
            break
        page += 1
    return out


def _rows_history(items):
    rows = []
    for i, it in enumerate(items, 1):
        d = (it.get("watched_at") or "")[:10] or None
        if it.get("type") == "movie" and it.get("movie"):
            m = it["movie"]
            rows.append({"row": i, "raw": f"{m.get('title')} ({m.get('year')})", "kind": "movie", "title": m.get("title"),
                         "year": m.get("year"), "tmdb": (m.get("ids") or {}).get("tmdb"), "date": d,
                         "key": str(it.get("id") or f"m{i}")})
        elif it.get("type") == "episode" and it.get("show"):
            s, e = it["show"], it.get("episode") or {}
            rows.append({"row": i, "raw": f"{s.get('title')} S{e.get('season')}E{e.get('number')}", "kind": "episode",
                         "title": s.get("title"), "year": s.get("year"), "tmdb": (s.get("ids") or {}).get("tmdb"),
                         "season": e.get("season"), "episode": e.get("title"), "date": d, "key": str(it.get("id") or f"e{i}")})
    return rows


class _NoTitleMatch:
    """Trakt rows carry TMDB ids; a row without one is reported, never matched by title."""
    def film(self, title, year=None):
        return None, "no TMDB id on Trakt"

    def show(self, title):
        return None, "no TMDB id on Trakt"


def _import_by_id(user, rows):
    return history.import_rows(user, "trakt", rows, matcher=_NoTitleMatch())


def sync(user):
    res = {}
    items = _get_all(user, "/sync/history/movies") + _get_all(user, "/sync/history/episodes")
    res["history"] = _import_by_id(user, _rows_history(items))
    try:
        import social
        n = 0
        for it in _get_all(user, "/sync/ratings/movies"):
            tid = ((it.get("movie") or {}).get("ids") or {}).get("tmdb")
            if tid and it.get("rating"):
                social.save_review(user, tid, max(0.5, min(5.0, round(it["rating"] / 2 * 2) / 2)), "")
                n += 1
        res["ratings"] = n
    except TraktError:
        raise
    except Exception as e:  # noqa: BLE001
        res["ratings_error"] = str(e)
    try:
        import film_info
        n = 0
        for it in _get_all(user, "/sync/watchlist/movies"):
            m = it.get("movie") or {}
            tid = (m.get("ids") or {}).get("tmdb")
            if tid:
                film_info.choose(user, tid, (m.get("title") or "")[:200], "want")
                n += 1
        res["watchlist"] = n
    except TraktError:
        raise
    except Exception as e:  # noqa: BLE001
        res["watchlist_error"] = str(e)
    with _lock:
        c = conn()
        c.execute("update tokens set last_sync = ? where user_key = ?", (time.strftime("%Y-%m-%dT%H:%M:%S"), user))
        c.commit()
    return res


# ------------------------------------------------------------------ export files (12 Nov 2026, round 36)
# Trakt's own export (trakt.tv Settings > Data > Export, free) is a ZIP of JSON files in the API's shapes, so it works
# without TRAKT_CLIENT_ID. Files are recognised by their items, not their names: history items (watched_at + type),
# ratings (rating + rated_at), a "watched" summary (plays / last_watched_at; used only when the file has no history
# items), watchlist items (listed_at, in a file whose name says watchlist). Rows keep Trakt's history ids as keys, so
# a file imported twice, or after a Connect-Trakt sync, adds nothing twice. Other files (lists, comments) are skipped.
def parse_export(text, name=""):
    try:
        data = json.loads(text)
    except ValueError:
        raise ValueError("That is not a Trakt JSON file. Trakt: Settings, Data, Export; unzip the download and choose "
                         "the .json files.")
    if isinstance(data, dict):
        data = next((v for v in data.values() if isinstance(v, list)), [])
    items = [it for it in data if isinstance(it, dict)] if isinstance(data, list) else []
    low = (name or "").lower()
    out = {"history": [], "ratings": [], "watchlist": [], "summary": [], "other": 0}
    for it in items:
        if it.get("watched_at") and it.get("type") in ("movie", "episode"):
            out["history"].append(it)
        elif it.get("rating") is not None and it.get("rated_at"):
            out["ratings"].append(it)
        elif it.get("listed_at") and "watchlist" in low:
            out["watchlist"].append(it)
        elif (it.get("plays") or it.get("last_watched_at")) and it.get("movie"):
            out["summary"].append(it)
        else:
            out["other"] += 1
    return out


def import_export(user, text, name=""):
    p = parse_export(text, name)
    rows = _rows_history(p["history"])
    if not rows and p["summary"]:                     # a watched-movies summary: one view per film, at its last date
        for i, it in enumerate(p["summary"], 1):
            m = it["movie"]
            tid = (m.get("ids") or {}).get("tmdb")
            rows.append({"row": i, "raw": f"{m.get('title')} ({m.get('year')})", "kind": "movie", "title": m.get("title"),
                         "year": m.get("year"), "tmdb": tid, "date": (it.get("last_watched_at") or "")[:10] or None,
                         "key": f"wm{tid or i}"})
    res = _import_by_id(user, rows) if rows else {"rows": 0, "matched": 0, "duplicates": 0, "issues": [], "added": 0}
    n = 0
    if p["ratings"]:
        import social
        for it in p["ratings"]:
            tid = ((it.get("movie") or {}).get("ids") or {}).get("tmdb")
            if it.get("type", "movie") == "movie" and tid and it.get("rating"):
                social.save_review(user, tid, max(0.5, min(5.0, round(it["rating"] / 2 * 2) / 2)), "")
                n += 1
    res["ratings"] = n
    n = 0
    if p["watchlist"]:
        import discovery
        import film_info
        for it in p["watchlist"]:
            m, s = it.get("movie") or {}, it.get("show") or {}
            if m.get("ids", {}).get("tmdb"):
                film_info.choose(user, m["ids"]["tmdb"], (m.get("title") or "")[:200], "want")
                n += 1
            elif s.get("ids", {}).get("tmdb"):
                discovery.tv_follow(user, s["ids"]["tmdb"], True)
                n += 1
    res["watchlist"] = n
    res["skipped"] = p["other"]
    return res


# ------------------------------------------------------------------ device-code sign-in (12 Nov 2026, round 38)
# Trakt only redirects back to https:// addresses (developer.trakt.tv/docs/pkce) and this site is plain http, so
# "Connect Trakt" uses Trakt's device flow instead: the person opens trakt.tv/activate, types a short code, and the
# page polls until Trakt says yes. No redirect address is involved.
_device = {}             # user -> {"code", "interval", "expires"}


def device_start(user):
    if not enabled():
        raise TraktError("Trakt isn't set up on this server (no Trakt client ID found in keys.env).")
    try:
        st, d, _h = _http("POST", API + "/oauth/device/code", {"client_id": CLIENT_ID},
                          {"trakt-api-version": "2", "trakt-api-key": CLIENT_ID})
    except (urllib.error.URLError, OSError) as e:
        raise TraktError(f"Couldn't reach Trakt right now ({e.__class__.__name__}). Try again in a minute.")
    if st != 200 or not d or not d.get("device_code"):
        raise TraktError(f"Trakt didn't give a sign-in code (HTTP {st}). Check the client ID in keys.env.")
    iv, exp = int(d.get("interval") or 5), int(d.get("expires_in") or 600)
    with _lock:
        _device[user] = {"code": d["device_code"], "interval": iv, "expires": time.time() + exp}
    return {"user_code": d.get("user_code"), "url": d.get("verification_url") or "https://trakt.tv/activate",
            "interval": iv, "expires_in": exp}


DEVICE_STATUS = {400: "pending", 429: "slow_down", 404: "invalid", 409: "used", 410: "expired", 418: "denied",
                 401: "unauthorized", 403: "unauthorized"}


def device_poll(user):
    with _lock:
        p = _device.get(user)
    if not p:
        return {"status": "none"}
    if time.time() > p["expires"]:
        with _lock:
            _device.pop(user, None)
        return {"status": "expired"}
    body = {"code": p["code"], "client_id": CLIENT_ID}
    if CLIENT_SECRET:
        body["client_secret"] = CLIENT_SECRET
    try:
        st, tok, _h = _http("POST", API + "/oauth/device/token", body, {"trakt-api-version": "2", "trakt-api-key": CLIENT_ID})
    except (urllib.error.URLError, OSError):
        return {"status": "error"}                 # the page keeps checking
    if st == 200 and tok and tok.get("access_token"):
        with _lock:
            _device.pop(user, None)
        _store(user, tok)
        try:
            res = sync(user)
        except Exception as e:  # noqa: BLE001  (connected either way; Settings > Imports can sync again)
            res = {"error": str(e)}
        return {"status": "connected", "sync": res}
    status = DEVICE_STATUS.get(st, "error")
    if status in ("invalid", "used", "expired", "denied"):
        with _lock:
            _device.pop(user, None)
    return {"status": status, "http": st}


def install_device(app, user_of, gate, run):
    from fastapi import Request
    from fastapi.responses import JSONResponse

    @app.post("/api/v8/trakt/device/start")
    async def trakt_device_start(request: Request):
        if (g := gate(request)):
            return g
        key = user_of(request)
        if not key:
            return JSONResponse({"error": "No browser id."}, status_code=400)
        try:
            return JSONResponse(await run(device_start, key))
        except TraktError as e:
            return JSONResponse({"error": str(e)}, status_code=400)

    @app.post("/api/v8/trakt/device/poll")
    async def trakt_device_poll(request: Request):
        if (g := gate(request)):
            return g
        return JSONResponse(await run(device_poll, user_of(request)))


def disconnect(user):
    r = conn().execute("select access from tokens where user_key = ?", (user,)).fetchone()
    if r:
        _http("POST", API + "/oauth/revoke", {"token": r[0], "client_id": CLIENT_ID})
    with _lock:
        c = conn()
        c.execute("delete from tokens where user_key = ?", (user,))
        c.commit()
    return {"removed_events": history.delete_source(user, "trakt")}
