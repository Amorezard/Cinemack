"""v8.1 slice 4.6 (15 Nov 2026): the hidden box-office forecast endpoint.

    GET /api/v8/forecast/{media_id}?horizon=d7        media_id = "movie:<tmdb id>"; horizon d90 | d7

Switch FORECAST_PANEL (off by default). While it is off the route answers 404 for everything, exactly as if it did
not exist, and the page never asks for it. It reads ONLY the store written by tools/v81_forecast_store.py
(FORECAST_DB, default logs/forecast.db), opened read-only; it never runs a model and never writes.
What is returned is the kit's prediction_response v1, unchanged, after being checked again here:
  - worldwide gross and the gross-to-budget multiple with 50% and 80% ranges; domestic and international are null
    (decided 13 Nov: worldwide only)
  - status "experimental" and the warnings stored with it
  - the words "profit" and "ROI" may only appear in the fixed "not profit" warning; a response that says otherwise
    is not served
Nothing installs this route yet: adding it to the site is a separate, explicitly approved step (install() below).
"""
from __future__ import annotations

import json
import math
import os
import re
import sqlite3
from pathlib import Path
from typing import Dict, List, Optional, Tuple

try:                                    # the pure functions below work without the web framework
    from fastapi import Request
    from fastapi.responses import JSONResponse
except ImportError:                     # pragma: no cover
    Request = JSONResponse = None

ENABLED = os.environ.get("FORECAST_PANEL", "0") == "1"
DB = Path(os.environ.get("FORECAST_DB", Path(__file__).resolve().parent.parent / "logs" / "forecast.db"))
HORIZONS = ("d90", "d7")
_MEDIA = re.compile(r"^movie:[1-9][0-9]{0,9}$")
_KEYS = ("schema_version", "prediction_id", "media_id", "model_version", "status", "horizon", "as_of", "currency", "budget",
         "domestic_gross", "international_gross", "worldwide_gross", "gross_to_budget_multiple", "source_notes", "warnings")
_MONEY_WORDS = re.compile(r"\b(profit\w*|roi|return on investment|break[- ]?even|loss(?:es)?|lost money|made money)\b", re.I)
_ALLOWED = "not profit"


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and not math.isnan(v) and not math.isinf(v)


def check(d) -> List[str]:
    """Problems with a stored response (empty list = it may be served). The kit schema's rules plus the wording rule."""
    p: List[str] = []
    if not isinstance(d, dict):
        return ["not an object"]
    if set(d) != set(_KEYS):
        p.append("fields %s" % sorted(set(d) ^ set(_KEYS)))
        return p
    if d["schema_version"] != 1:
        p.append("schema_version")
    if d["status"] not in ("experimental", "validated", "disabled"):
        p.append("status")
    if d["horizon"] not in ("d90", "d7", "opening_weekend_update"):
        p.append("horizon")
    if not (isinstance(d["currency"], str) and re.fullmatch(r"[A-Z]{3}", d["currency"])):
        p.append("currency")
    for k in ("prediction_id", "media_id", "model_version", "as_of"):
        if not (isinstance(d[k], str) and d[k]):
            p.append(k)
    b = d["budget"]
    if not (isinstance(b, dict) and set(b) == {"amount", "known_as_of", "source_id"}
            and (b["amount"] is None or (_num(b["amount"]) and b["amount"] >= 0))):
        p.append("budget")
    for k in ("domestic_gross", "international_gross", "worldwide_gross", "gross_to_budget_multiple"):
        v = d[k]
        if not (isinstance(v, dict) and set(v) == {"point", "intervals"} and isinstance(v["intervals"], list)
                and len(v["intervals"]) <= 5):
            p.append(k + " shape")
            continue
        if v["point"] is not None and not (_num(v["point"]) and v["point"] >= 0):
            p.append(k + " point")
        for it in v["intervals"]:
            if not (isinstance(it, dict) and set(it) == {"nominal_coverage", "lower", "upper"}
                    and _num(it["nominal_coverage"]) and 0 < it["nominal_coverage"] < 1):
                p.append(k + " range shape")
            elif it["lower"] is not None and it["upper"] is not None and not (
                    _num(it["lower"]) and _num(it["upper"]) and 0 <= it["lower"] <= it["upper"]):
                p.append(k + " range order")
    for k in ("source_notes", "warnings"):
        if not (isinstance(d[k], list) and all(isinstance(s, str) and len(s) <= 500 for s in d[k])):
            p.append(k)
    if d["domestic_gross"].get("point") is not None or d["international_gross"].get("point") is not None:
        p.append("territory estimate present (worldwide only was decided)")
    if d["status"] != "experimental":
        p.append("status must stay experimental until the user approves a display")
    text = " ".join(str(s) for k in ("source_notes", "warnings") if isinstance(d.get(k), list) for s in d[k]).lower()
    if _MONEY_WORDS.search(text.replace(_ALLOWED, "")):
        p.append("profit / ROI wording")
    return p


def get(media_id: str, horizon: str = "d7", db_path=None) -> Optional[Dict]:
    """The stored, re-checked forecast, or None (unknown film, unknown horizon, no store, or a response that fails)."""
    if not isinstance(media_id, str) or not _MEDIA.match(media_id) or horizon not in HORIZONS:
        return None
    path = Path(db_path) if db_path else DB
    if not path.exists():
        return None
    try:
        c = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            row = c.execute("select response_json from forecasts where media_id = ? and horizon = ?",
                            (media_id, horizon)).fetchone()
        finally:
            c.close()
        if not row:
            return None
        d = json.loads(row[0])
    except (sqlite3.Error, ValueError):
        return None
    if check(d) or d.get("media_id") != media_id or d.get("horizon") != horizon:
        return None
    return d


def handle(media_id: str, horizon: str = "d7", db_path=None, enabled=None) -> Tuple[int, Dict]:
    """(HTTP status, body). 404 for everything while the switch is off."""
    on = ENABLED if enabled is None else enabled
    if not on:
        return 404, {"detail": "Not Found"}
    if not isinstance(media_id, str) or not _MEDIA.match(media_id):
        return 400, {"error": "media_id must look like movie:<tmdb id>"}
    if horizon not in HORIZONS:
        return 400, {"error": "horizon must be d90 or d7"}
    d = get(media_id, horizon, db_path)
    if d is None:
        return 404, {"error": "No forecast for this film."}
    return 200, d


def install(app, gate):
    """Adds the route to a FastAPI app. `gate(request)` is the site's access-code check (None = allowed)."""
    @app.get("/api/v8/forecast/{media_id}")
    async def forecast(media_id: str, request: Request, horizon: str = "d7"):
        if not ENABLED:
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        if (g := gate(request)):
            return g
        status, body = handle(media_id, horizon)
        return JSONResponse(body, status_code=status, headers={"Cache-Control": "no-store"})
    return forecast
