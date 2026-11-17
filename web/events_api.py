"""v8.1 round 50 (17 Nov 2026): the page tells the server which titles were shown, opened, saved or rated.

POST /api/v8/events   {"session": "<random id of this browser tab>", "events": [{"media_id": "movie:603",
                       "placement": "home", "type": "impression", "position": 3, "exposure": "<24 hex>"}, ...]}

Off unless REC_EVENTS=1 (web/rec_events.py): while off the route answers 404 and the page stops sending.

What is stored, and why it is safe to switch on:
  - ANONYMOUS only. Events are stored with consent "anonymous": no account, no browser id, no name. The only link
    between two events is a salted hash of a random id the page makes for each tab (sessionStorage), so a visit can be
    studied as a visit, and a person cannot be followed across visits.
  - No text. Chat questions, searches and reviews are never sent; only a title id, where it was shown (home, film
    page, ...), what happened (shown, opened, saved, ...) and its position in the row.
  - Bounded. At most MAX_BATCH events per request and MAX_PER_HOUR per tab; anything malformed is skipped, counted
    and never stored. Old events can be removed with rec_events.purge(days).
What it is for: the ranking test that MovieLens could not give (docs/guide/RECOMMENDATIONS.md) - saves per 100
titles shown, current order against a new order on alternating days - and later, suggestions that react within a visit.
"""
from __future__ import annotations

import re
import threading
import time

try:                                   # at module top: a route's type hints are resolved from module globals
    from fastapi import Request
    from fastapi.responses import JSONResponse
except Exception:  # noqa: BLE001
    Request = JSONResponse = None

import rec_events as RE

RANKING_VERSION = "v8.0"               # the ranking the live site uses; a test of a new order gets its own name
MAX_BATCH = 40
MAX_PER_HOUR = 1500
_SESSION = re.compile(r"^[A-Za-z0-9-]{8,64}$")
_MEDIA = re.compile(r"^(movie|tv):[1-9][0-9]{0,9}$")
_EXPOSURE = re.compile(r"^[0-9a-f]{24}$")
CLIENT_TYPES = ("impression", "click", "watchlist_add", "watchlist_remove", "rating_positive", "rating_negative",
                "already_watched", "pick_tonight_selected", "not_interested", "more_like_this")

_lock = threading.Lock()
_counts: dict = {}                     # session -> (hour, count)


def _allow(session: str, n: int, now: float | None = None) -> int:
    """How many of n events this tab may still send this hour."""
    hour = int((time.time() if now is None else now) // 3600)
    with _lock:
        h, c = _counts.get(session, (hour, 0))
        if h != hour:
            c = 0
        ok = max(0, min(n, MAX_PER_HOUR - c))
        _counts[session] = (hour, c + ok)
        if len(_counts) > 50000:                                   # forget old tabs
            for k in [k for k, (hh, _) in _counts.items() if hh != hour][:25000]:
                _counts.pop(k, None)
        return ok


def clean(ev):
    """One event from the page -> keyword arguments for rec_events.record, or None when anything is off."""
    if not isinstance(ev, dict):
        return None
    m, pl, ty = ev.get("media_id"), ev.get("placement"), ev.get("type")
    if not (isinstance(m, str) and _MEDIA.match(m)) or pl not in RE.PLACEMENTS or ty not in CLIENT_TYPES:
        return None
    pos = ev.get("position")
    if pos is not None and (isinstance(pos, bool) or not isinstance(pos, int) or not 0 <= pos < 500):
        return None
    ex = ev.get("exposure")
    if ex is not None and not (isinstance(ex, str) and _EXPOSURE.match(ex)):
        return None
    return {"media_id": m, "placement": pl, "event_type": ty, "position": pos, "exposure_id": ex}


def store(session: str, events, db=None, enabled=None, now=None) -> dict:
    if not (isinstance(session, str) and _SESSION.match(session)):
        return {"stored": 0, "skipped": len(events or []), "error": "bad session"}
    events = list(events or [])[:MAX_BATCH] if isinstance(events, list) else []
    ok = [c for c in (clean(e) for e in events) if c]
    room = _allow(session, len(ok), now)
    n = 0
    for c in ok[:room]:
        if RE.record(None, session, c["media_id"], c["placement"], c["event_type"], RANKING_VERSION,
                     consent="anonymous", exposure_id=c["exposure_id"], position=c["position"], db=db, enabled=enabled):
            n += 1
    return {"stored": n, "skipped": len(events) - n}


def install(app, gate):
    import asyncio

    @app.post("/api/v8/events")
    async def events_post(request: Request):
        if not RE.ENABLED:
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        if (g := gate(request)):
            return g
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            return JSONResponse({"error": "Send JSON."}, status_code=400)
        if not isinstance(body, dict):
            return JSONResponse({"error": "Send an object."}, status_code=400)
        out = await asyncio.to_thread(store, body.get("session"), body.get("events"))
        return JSONResponse(out, status_code=400 if out.get("error") else 200)
    return events_post
