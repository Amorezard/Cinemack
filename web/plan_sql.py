"""v8.1 slice 2.1 (13 Nov 2026): compile a validated query plan (web/query_plan.py) into ONE parameterised SQL query
over facts.db that returns the films meeting every hard limit (kit package 2: "the model never writes SQL").

Every value goes in as a ? parameter; the only text pasted into the SQL is from fixed lists in this file. Only film
plans are compiled (media_types ["movie"]); a plan for TV raises Unsupported until slice 2.3.

Meaning of each limit (identical to web/user_prefs.excluded with GENRE_MAIN off, except where noted):
  year_min / year_max        films.year inside the range
  runtime_min / max          films.runtime inside the range (0 or NULL runtime = unknown)
  genres_any                 at least one of the genres in films.genres_json (exact names, never substrings)
  genres_all                 every one of them
  genres_exclude             none of them
  certification_max          the US rating picked as user_prefs.certifications() picks it (theatrical release first,
                             then the lowest release type, rows with no type last - SQLite's
                             NULL order; only G / PG / PG-13 / R / NC-17 count) is at or below it
  watch_provider_ids_any     a listing in watch_region whose provider name belongs to one of the services
                             (user_prefs.service_of) with a kind in watch_monetization (default flatrate/free/ads)
  watch_monetization alone   any listing in watch_region with one of those kinds
  include_media_ids          only these films;   exclude_media_ids: never these films (watched films come here,
                             from the server, never from the model)
  unknown = "fail"           a film whose fact is missing fails the limit that needs it (no year, no runtime, no
                             genres, no US rating); unknown = "pass": a missing fact never removes a film. Provider
                             limits always need a listing (as on the site).
"""
from __future__ import annotations

import os
import sqlite3
import sys
from typing import Dict, List, Sequence, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import query_plan as QP  # noqa: E402

QUERY_PLAN = os.environ.get("QUERY_PLAN", "0") == "1"
CERT_ORDER = QP.CERTS
DEFAULT_KINDS = ("flatrate", "free", "ads")

SERVICE_PATTERNS = {"netflix": ["netflix"], "max": ["max", "hbo max"], "prime": ["amazon prime video", "prime video"],
                    "disney": ["disney plus", "disney"], "hulu": ["hulu"], "apple": ["apple tv plus", "apple tv"],
                    "peacock": ["peacock"], "paramount": ["paramount plus", "paramount"], "tubi": ["tubi"],
                    "pluto": ["pluto tv"], "roku": ["the roku channel", "roku channel"],
                    "criterion": ["criterion channel"], "mubi": ["mubi"], "kanopy": ["kanopy"]}


class Unsupported(ValueError):
    pass


def norm_provider(n):
    """Same normalisation as web/user_prefs._norm_provider."""
    return " ".join("".join(ch if ch.isalnum() else " " for ch in str(n or "").casefold().replace("+", " plus ")).split())


def service_of(provider_name):
    n = norm_provider(provider_name)
    for key, pats in SERVICE_PATTERNS.items():
        if any(n == p or n.startswith(p + " ") for p in pats):
            return key
    return None


def provider_names(conn, region, services) -> List[str]:
    """The exact provider_name strings in watch_providers (this region) that belong to the services."""
    want = set(services)
    names = [r[0] for r in conn.execute("select distinct provider_name from watch_providers where region = ?", (region,))]
    return sorted(n for n in names if service_of(n) in want)


_CERT_SQL = ("(select upper(trim(c.certification)) from certifications c where c.tmdb_id = f.tmdb_id and c.region = 'US' "
             "and upper(trim(c.certification)) in ('G','PG','PG-13','R','NC-17') "
             "order by c.release_type = 3 desc, c.release_type, c.rowid limit 1)")
_HAS_GENRES = "(f.genres_json is not null and json_valid(f.genres_json) and json_array_length(f.genres_json) > 0)"


def _q(n):
    return ",".join("?" * n)


def compile_plan(plan: QP.QueryPlan, conn=None, limit=None) -> Tuple[str, List]:
    """(sql, params) selecting tmdb_id of every film meeting the plan's limits, most-voted first."""
    if list(plan.media_types) != ["movie"]:
        raise Unsupported("only film plans are compiled in slice 2.1")
    f = plan.filters
    fail = f.unknown == "fail"
    where: List[str] = []
    p: List = []

    def rng(col, lo, hi, known):
        if lo is None and hi is None:
            return
        cond = []
        if lo is not None:
            cond.append(f"{col} >= ?")
            p.append(lo)
        if hi is not None:
            cond.append(f"{col} <= ?")
            p.append(hi)
        test = "(" + " and ".join(cond) + ")"
        where.append(f"({known} and {test})" if fail else f"(not ({known}) or {test})")

    rng("f.year", f.year_min, f.year_max, "(f.year is not null)")
    rng("f.runtime", f.runtime_min_minutes, f.runtime_max_minutes, "(f.runtime is not null and f.runtime > 0)")

    gtests = []
    if f.genres_any:
        gtests.append(f"exists (select 1 from json_each(case when json_valid(f.genres_json) then f.genres_json else '[]' end) j where j.value in ({_q(len(f.genres_any))}))")
        p.extend(f.genres_any)
    if f.genres_all:
        gtests.append(f"(select count(distinct j.value) from json_each(case when json_valid(f.genres_json) then f.genres_json else '[]' end) j where j.value in ({_q(len(f.genres_all))})) = ?")
        p.extend(f.genres_all)
        p.append(len(f.genres_all))
    if f.genres_exclude:
        gtests.append(f"not exists (select 1 from json_each(case when json_valid(f.genres_json) then f.genres_json else '[]' end) j where j.value in ({_q(len(f.genres_exclude))}))")
        p.extend(f.genres_exclude)
    if gtests:
        test = "(" + " and ".join(gtests) + ")"
        where.append(f"({_HAS_GENRES} and {test})" if fail else f"(not {_HAS_GENRES} or {test})")

    if f.certification_max:
        allowed = CERT_ORDER[:CERT_ORDER.index(f.certification_max) + 1]
        where.append(f"(coalesce({_CERT_SQL} in ({_q(len(allowed))}), ?))")
        p.extend(allowed)
        p.append(0 if fail else 1)

    if f.watch_provider_ids_any or f.watch_monetization:
        if not f.watch_region:
            raise ValueError("watch limits need watch_region")
        kinds = list(f.watch_monetization) or list(DEFAULT_KINDS)
        sub = (f"exists (select 1 from watch_providers w where w.tmdb_id = f.tmdb_id and w.region = ? "
               f"and w.kind in ({_q(len(kinds))})")
        p.append(f.watch_region)
        p.extend(kinds)
        if f.watch_provider_ids_any:
            if conn is None:
                raise ValueError("provider limits need a connection to resolve provider names")
            names = provider_names(conn, f.watch_region, f.watch_provider_ids_any)
            if not names:
                where.append("0")
            else:
                sub += f" and w.provider_name in ({_q(len(names))})"
                p.extend(names)
        where.append(sub + ")")

    inc = [int(x.split(":")[1]) for x in f.include_media_ids if x.startswith("movie:")]
    exc = [int(x.split(":")[1]) for x in f.exclude_media_ids if x.startswith("movie:")]
    if f.include_media_ids:
        where.append(f"f.tmdb_id in ({_q(len(inc))})" if inc else "0")
        p.extend(inc)
    if exc:
        where.append(f"f.tmdb_id not in ({_q(len(exc))})")
        p.extend(exc)

    sql = "select f.tmdb_id from films f"
    if where:
        sql += " where " + " and ".join(where)
    sql += " order by coalesce(f.vote_count, 0) desc, f.tmdb_id"
    if limit is not None:
        sql += " limit ?"
        p.append(int(limit))
    return sql, p


def matching_ids(conn, plan: QP.QueryPlan, candidate_ids: Sequence[int] = None, limit=None) -> List[int]:
    """Films meeting the plan. With candidate_ids, only those films are considered (order of the result: most-voted)."""
    sql, params = compile_plan(plan, conn, limit=None if candidate_ids is not None else limit)
    if candidate_ids is not None:
        ids = [int(i) for i in candidate_ids]
        conn.execute("create temp table if not exists _qp_cand(id integer primary key)")
        conn.execute("delete from _qp_cand")
        conn.executemany("insert or ignore into _qp_cand values (?)", [(i,) for i in ids])
        sql = sql.replace("select f.tmdb_id from films f", "select f.tmdb_id from films f join _qp_cand k on k.id = f.tmdb_id", 1)
        out = [r[0] for r in conn.execute(sql, params)]
        return out[:limit] if limit else out
    return [r[0] for r in conn.execute(sql, params)]


def explain(plan: QP.QueryPlan) -> Dict[str, object]:
    """The limits in words-ready form (for "why was this removed" and the logs)."""
    f = plan.filters
    return {k: v for k, v in f.model_dump().items() if v not in (None, [], False)}
