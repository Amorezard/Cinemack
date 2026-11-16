"""v8.1 slice 7.2 (15 Nov 2026): an allow-list of typed tools the answer model may ask for.

The model can only NAME one of four tools and fill in its typed fields; it can never send SQL, a URL, a shell
command, a file path or another person's id, because no tool has a field that would carry one:

    lookup     {media_id}                 -> title / year / kind of one film or show (facts.db, read-only)
    search     {plan, limit}              -> media ids meeting a validated query plan (web/query_plan.py)
    recommend  {plan, k}                  -> ranked films with one reason each (web/recs.py)
    forecast   {media_id, horizon}        -> the stored box-office forecast (web/forecast_api.py)

Rules enforced here, each with a test:
  - unknown tool names are refused (including "sql", "http", "shell", "watchlist", "memory")
  - unknown fields are refused, so {"user": ...}, {"sql": ...}, {"url": ...} never reach a tool
  - who is asking comes ONLY from `ctx` (set by the server from the session); a tool that needs a person
    (recommend) reads that person's own likes through ctx and nothing else
  - free text (a plan's query_text) is data: it is never executed, formatted into SQL, fetched or logged
  - results are capped (50 ids, 20 recommendations) and requests over 8,000 characters are refused
  - a tool that depends on a switch that is off answers "not available" instead of running

Switch TOOL_ROUTER (off by default): while it is off every call is refused with "disabled". Nothing calls the
router yet; connecting it to the chat model is a separate, approved step.
"""
from __future__ import annotations

import json
import os
import sys
from typing import Any, Callable, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import media_identity as MI  # noqa: E402
import query_plan as QP  # noqa: E402

TOOL_ROUTER = os.environ.get("TOOL_ROUTER", "0") == "1"
MAX_REQUEST_CHARS = 8000
MAX_IDS, MAX_RECS = 50, 20


class ToolError(ValueError):
    """A refused or failed tool request. `.kind`: disabled | unknown_tool | bad_request | not_available | failed."""

    def __init__(self, kind, message):
        super().__init__(message)
        self.kind = kind


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


def _media_id(v):
    MI.parse(v)
    return v


class LookupArgs(_Args):
    media_id: str = Field(max_length=20)
    _v = field_validator("media_id")(_media_id)


class SearchArgs(_Args):
    plan: Dict[str, Any]
    limit: int = Field(default=20, ge=1, le=MAX_IDS)


class RecommendArgs(_Args):
    plan: Dict[str, Any]
    k: int = Field(default=10, ge=1, le=MAX_RECS)


class ForecastArgs(_Args):
    media_id: str = Field(max_length=20)
    horizon: Literal["d90", "d7"] = "d7"
    _v = field_validator("media_id")(_media_id)


class Ctx:
    """What the SERVER knows about this request. Nothing in here ever comes from the model."""

    def __init__(self, conn, user_id: Optional[str] = None, likes: Optional[Callable[[str], tuple]] = None,
                 watched: Optional[Callable[[str], List[int]]] = None, movies_db: Optional[str] = None,
                 forecast_db: Optional[str] = None):
        self.conn, self.user_id, self.likes, self.watched = conn, user_id, likes, watched
        self.movies_db, self.forecast_db = movies_db, forecast_db


def _plan(d) -> QP.QueryPlan:
    try:
        return QP.parse(d)
    except ValueError as e:
        raise ToolError("bad_request", "plan refused: %s" % str(e)[:300]) from None


def _server_plan(plan: QP.QueryPlan, ctx: Ctx) -> QP.QueryPlan:
    """The model's plan with the watched films of the person who is ASKING (from the server) added to the
    "never show" list when the plan asks for that. Ids the model put there itself stay (they only ever remove)."""
    if not plan.filters.exclude_watched or not ctx.user_id or ctx.watched is None:
        return plan
    d = plan.model_dump()
    have = set(d["filters"]["exclude_media_ids"]) | set(d["filters"]["include_media_ids"])
    d["filters"]["exclude_media_ids"] = d["filters"]["exclude_media_ids"] + [
        m for m in ("movie:%d" % int(i) for i in ctx.watched(ctx.user_id)) if m not in have]
    return QP.parse(d)


def _lookup(a: LookupArgs, ctx: Ctx):
    kind, n = MI.parse(a.media_id)
    if kind == "movie":
        row = ctx.conn.execute("select title, year from films where tmdb_id = ?", (n,)).fetchone()
    else:
        row = ctx.conn.execute("select name, first_year from tv_shows where tmdb_id = ?", (n,)).fetchone()
    if not row:
        return {"found": False, "media_id": a.media_id}
    return {"found": True, "media_id": a.media_id, "media_type": kind, "title": row[0], "year": row[1]}


def _search(a: SearchArgs, ctx: Ctx):
    import plan_sql_tv as PT
    plan = _server_plan(_plan(a.plan), ctx)
    ids = PT.matching_media_ids(ctx.conn, plan, limit=a.limit)
    return {"media_ids": ids, "limit_per_media_type": a.limit}      # a mixed plan gets up to `limit` films and `limit` shows


def _recommend(a: RecommendArgs, ctx: Ctx):
    import recs
    if not recs.enabled():
        raise ToolError("not_available", "recommendations v2 is switched off")
    plan = _server_plan(_plan(a.plan), ctx)
    likes, dislikes = ((), ())
    if ctx.user_id and ctx.likes is not None:
        got = ctx.likes(ctx.user_id)                 # ONLY the asking person's own likes
        likes, dislikes = tuple(got[0]), tuple(got[1])
    try:
        return {"picks": recs.rank(ctx.conn, plan, likes=likes, dislikes=dislikes, k=a.k, movies_db=ctx.movies_db)}
    except recs.Unsupported as e:
        raise ToolError("bad_request", str(e)) from None


def _forecast(a: ForecastArgs, ctx: Ctx):
    import forecast_api
    if not a.media_id.startswith("movie:"):
        raise ToolError("bad_request", "forecasts exist for films only")
    status, body = forecast_api.handle(a.media_id, a.horizon, db_path=ctx.forecast_db)
    if status == 404 and "detail" in body:
        raise ToolError("not_available", "the forecast panel is switched off")
    if status != 200:
        return {"found": False, "media_id": a.media_id}
    return {"found": True, "forecast": body}


TOOLS = {"lookup": (LookupArgs, _lookup), "search": (SearchArgs, _search),
         "recommend": (RecommendArgs, _recommend), "forecast": (ForecastArgs, _forecast)}


def describe() -> List[Dict[str, Any]]:
    """The tool list to show the model: names and field schemas only."""
    return [{"name": n, "arguments": m.model_json_schema()} for n, (m, _f) in TOOLS.items()]


def parse_request(req) -> Dict[str, Any]:
    """{"tool": name, "args": {...}} from a dict or a JSON string. Anything else is refused."""
    if isinstance(req, (str, bytes)):
        if len(req) > MAX_REQUEST_CHARS:
            raise ToolError("bad_request", "request too long")
        try:
            req = json.loads(req)
        except ValueError:
            raise ToolError("bad_request", "request is not JSON") from None
    if not isinstance(req, dict) or set(req) != {"tool", "args"} or not isinstance(req["tool"], str) \
            or not isinstance(req["args"], dict):
        raise ToolError("bad_request", 'a request is {"tool": name, "args": {...}} and nothing else')
    if len(json.dumps(req, default=str)) > MAX_REQUEST_CHARS:
        raise ToolError("bad_request", "request too long")
    return req


def call(req, ctx: Ctx, enabled: Optional[bool] = None) -> Dict[str, Any]:
    """Run one tool request. Raises ToolError; never raises anything else for a bad request."""
    if not (TOOL_ROUTER if enabled is None else enabled):
        raise ToolError("disabled", "the tool router is switched off")
    req = parse_request(req)
    if req["tool"] not in TOOLS:
        raise ToolError("unknown_tool", "no such tool; allowed: %s" % ", ".join(TOOLS))
    model, fn = TOOLS[req["tool"]]
    try:
        args = model.model_validate(req["args"])
    except ValidationError as e:
        why = "; ".join("%s: %s" % (".".join(str(p) for p in err["loc"]) or "args", err["msg"]) for err in e.errors())
        raise ToolError("bad_request", why[:400]) from None
    try:
        return {"tool": req["tool"], "ok": True, "result": fn(args, ctx)}
    except ToolError:
        raise
    except Exception as e:  # noqa: BLE001 - a tool failure is reported, never an internal message
        print(f"[cinemack] tool {req['tool']} failed: {e!r}", flush=True)
        raise ToolError("failed", "the tool could not answer") from None


def safe_call(req, ctx: Ctx, enabled: Optional[bool] = None) -> Dict[str, Any]:
    """call() that returns the refusal as data (what the model is shown)."""
    try:
        return call(req, ctx, enabled)
    except ToolError as e:
        name = req.get("tool") if isinstance(req, dict) and isinstance(req.get("tool"), str) else None
        return {"tool": name if name in TOOLS else None, "ok": False, "error": {"kind": e.kind, "message": str(e)}}
