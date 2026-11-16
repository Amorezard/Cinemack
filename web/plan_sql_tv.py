"""v8.1 slice 2.3 (15 Nov 2026): the query plan for TV shows, and one entry point for film + show plans.

web/plan_sql.py compiles film plans only (it raises Unsupported for TV). This module adds the TV half over
tv_shows / tv_ratings / tv_providers and `matching_media_ids`, which answers any plan with media ids
("movie:<tmdb>" / "tv:<tmdb>": a bare TMDB number is not an identity - 1,072 numbers are used by both a film
and a show, round-42 audit). Every value goes in as a ? parameter; the only text pasted into the SQL comes from
fixed lists in this file. plan_sql.py is not changed.

Meaning of each limit for a show (the same plan fields as for films; "unknown" as in plan_sql):
  year_min / year_max        tv_shows.first_year (the year the show started) inside the range
  runtime_min / max          tv_shows.episode_runtime (minutes per episode; 0 or NULL = unknown)
  genres_any / all / exclude the plan's genre names (the film vocabulary of web/query_plan.GENRES) against the show's
                             TMDB genres read through web/tvdata.TV_GENRE: "Action & Adventure" counts as Action AND
                             Adventure, "Sci-Fi & Fantasy" as Science Fiction AND Fantasy, "War & Politics" as War,
                             "Kids" as Family; every other TV genre by its own name
  certification_max          the show's US TV rating (tv_ratings, region US) is one that fits under the film rating,
                             as web/tvdata.TV_CERT_OK says: G -> TV-Y TV-Y7 TV-Y7-FV TV-G; PG -> + TV-PG;
                             PG-13 -> + TV-14; R and NC-17 -> + TV-MA. Any other text (NR, empty) = unknown
  watch_provider_ids_any /   as for films, over tv_providers
  watch_monetization
  include / exclude ids      "tv:<n>" ids only; "movie:<n>" ids never match a show
Not part of the plan (said so the difference is not a surprise): Pick Tonight's TV list also leaves out News / Talk /
Soap / Reality shows, shows with under 40 votes and shows the person follows; those are choices of that page, not
limits the person set. `skip_kinds=True` adds the first of them for callers that want it.

Switch QUERY_PLAN (off by default; nothing imports this module while it is off).
"""
from __future__ import annotations

import os
import sys
from typing import Dict, List, Sequence, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import plan_sql as PS  # noqa: E402
import query_plan as QP  # noqa: E402

# film genre name -> the TMDB TV genre names that count as it (web/tvdata.TV_GENRE read the other way round)
TV_GENRE = {"Action & Adventure": ["Action", "Adventure"], "Sci-Fi & Fantasy": ["Science Fiction", "Fantasy"],
            "War & Politics": ["War"], "Kids": ["Family"]}
SKIP_TV = ("News", "Talk", "Soap", "Reality")
TV_RATINGS = ("TV-Y", "TV-Y7", "TV-Y7-FV", "TV-G", "TV-PG", "TV-14", "TV-MA")
TV_CERT_OK = {"G": ("TV-Y", "TV-Y7", "TV-Y7-FV", "TV-G"), "PG": ("TV-Y", "TV-Y7", "TV-Y7-FV", "TV-G", "TV-PG"),
              "PG-13": ("TV-Y", "TV-Y7", "TV-Y7-FV", "TV-G", "TV-PG", "TV-14")}
TV_CERT_OK["R"] = TV_CERT_OK["PG-13"] + ("TV-MA",)
TV_CERT_OK["NC-17"] = TV_CERT_OK["R"]

_J = "json_each(case when json_valid(s.genres_json) then s.genres_json else '[]' end) j"
_HAS_GENRES = "(s.genres_json is not null and json_valid(s.genres_json) and json_array_length(s.genres_json) > 0)"
_RATING = ("(select upper(trim(r.rating)) from tv_ratings r where r.tmdb_id = s.tmdb_id and r.region = 'US' "
           "and upper(trim(r.rating)) in (%s) limit 1)" % ",".join("'%s'" % x for x in TV_RATINGS))


def tv_names(genre: str) -> List[str]:
    """The TV genre names that count as this (film-vocabulary) genre, the genre's own name first."""
    out = [genre]
    for tv, films in TV_GENRE.items():
        if genre in films:
            out.append(tv)
    return out


def site_genres(tv_genres) -> List[str]:
    """A show's TMDB genres in the film vocabulary (same as web/tvdata.site_genres)."""
    out = []
    for g in tv_genres:
        for x in TV_GENRE.get(g, [g]):
            if x not in out:
                out.append(x)
    return out


def _q(n):
    return ",".join("?" * n)


def compile_tv(plan: QP.QueryPlan, conn=None, limit=None, skip_kinds=False) -> Tuple[str, List]:
    """(sql, params) selecting tmdb_id of every show meeting the plan's limits, most-voted first."""
    if "tv" not in plan.media_types:
        raise PS.Unsupported("not a TV plan")
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

    rng("s.first_year", f.year_min, f.year_max, "(s.first_year is not null)")
    rng("s.episode_runtime", f.runtime_min_minutes, f.runtime_max_minutes,
        "(s.episode_runtime is not null and s.episode_runtime > 0)")

    gtests = []
    if f.genres_any:
        names = sorted({n for g in f.genres_any for n in tv_names(g)})
        gtests.append(f"exists (select 1 from {_J} where j.value in ({_q(len(names))}))")
        p.extend(names)
    for g in f.genres_all:                       # one test per wanted genre: "Sci-Fi & Fantasy" covers two of them
        names = tv_names(g)
        gtests.append(f"exists (select 1 from {_J} where j.value in ({_q(len(names))}))")
        p.extend(names)
    if f.genres_exclude:
        names = sorted({n for g in f.genres_exclude for n in tv_names(g)})
        gtests.append(f"not exists (select 1 from {_J} where j.value in ({_q(len(names))}))")
        p.extend(names)
    if gtests:
        test = "(" + " and ".join(gtests) + ")"
        where.append(f"({_HAS_GENRES} and {test})" if fail else f"(not {_HAS_GENRES} or {test})")

    if f.certification_max:
        allowed = TV_CERT_OK[f.certification_max]
        where.append(f"(coalesce({_RATING} in ({_q(len(allowed))}), ?))")
        p.extend(allowed)
        p.append(0 if fail else 1)

    if f.watch_provider_ids_any or f.watch_monetization:
        if not f.watch_region:
            raise ValueError("watch limits need watch_region")
        kinds = list(f.watch_monetization) or list(PS.DEFAULT_KINDS)
        sub = (f"exists (select 1 from tv_providers w where w.tmdb_id = s.tmdb_id and w.region = ? "
               f"and w.kind in ({_q(len(kinds))})")
        p.append(f.watch_region)
        p.extend(kinds)
        if f.watch_provider_ids_any:
            if conn is None:
                raise ValueError("provider limits need a connection to resolve provider names")
            want = set(f.watch_provider_ids_any)
            names = sorted(n for (n,) in conn.execute(
                "select distinct provider_name from tv_providers where region = ?", (f.watch_region,))
                if PS.service_of(n) in want)
            if not names:
                where.append("0")
            else:
                sub += f" and w.provider_name in ({_q(len(names))})"
                p.extend(names)
        where.append(sub + ")")

    inc = [int(x.split(":")[1]) for x in f.include_media_ids if x.startswith("tv:")]
    exc = [int(x.split(":")[1]) for x in f.exclude_media_ids if x.startswith("tv:")]
    if f.include_media_ids:
        where.append(f"s.tmdb_id in ({_q(len(inc))})" if inc else "0")
        p.extend(inc)
    if exc:
        where.append(f"s.tmdb_id not in ({_q(len(exc))})")
        p.extend(exc)
    if skip_kinds:
        where.append(f"not exists (select 1 from {_J} where j.value in ({_q(len(SKIP_TV))}))")
        p.extend(SKIP_TV)

    sql = "select s.tmdb_id from tv_shows s"
    if where:
        sql += " where " + " and ".join(where)
    sql += " order by coalesce(s.vote_count, 0) desc, s.tmdb_id"
    if limit is not None:
        sql += " limit ?"
        p.append(int(limit))
    return sql, p


def matching_show_ids(conn, plan: QP.QueryPlan, candidate_ids: Sequence[int] = None, limit=None,
                      skip_kinds=False) -> List[int]:
    sql, params = compile_tv(plan, conn, limit=None if candidate_ids is not None else limit, skip_kinds=skip_kinds)
    out = [r[0] for r in conn.execute(sql, params)]
    if candidate_ids is not None:
        keep = {int(i) for i in candidate_ids}
        out = [i for i in out if i in keep]
        return out[:limit] if limit else out
    return out


def _only(plan: QP.QueryPlan, media: str) -> QP.QueryPlan:
    """The same plan for one media type (validated again, so a bad combination cannot slip through)."""
    d = plan.model_dump()
    d["media_types"] = [media]
    if media == "tv" and d["intent"] == "forecast":
        raise PS.Unsupported("a forecast plan is for films")
    return QP.parse(d)


def matching_media_ids(conn, plan: QP.QueryPlan, limit=None, skip_kinds=False) -> List[str]:
    """Media ids meeting the plan, films first then shows (each most-voted first). Works for film, TV and mixed plans."""
    out: List[str] = []
    if "movie" in plan.media_types:
        out += ["movie:%d" % i for i in PS.matching_ids(conn, _only(plan, "movie"), limit=limit)]
    if "tv" in plan.media_types:
        out += ["tv:%d" % i for i in matching_show_ids(conn, _only(plan, "tv"), limit=limit, skip_kinds=skip_kinds)]
    return out


def explain(plan: QP.QueryPlan) -> Dict[str, object]:
    d = PS.explain(plan)
    if "tv" in plan.media_types and d.get("certification_max"):
        d["tv_ratings_allowed"] = list(TV_CERT_OK[d["certification_max"]])
    return d
