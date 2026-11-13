"""v8.1 slice 2.1 (13 Nov 2026): one validated query plan for film searches (kit package 2).

The plan follows planning/cinemack-v8.1/specs/movie_query.schema.json (schema_version 1) field for field: unknown
fields are rejected, every list has a fixed vocabulary, numbers have ranges. Two optional filter fields are added as
schema version "1.1" (specs/movie_query.v1_1.schema.json) because Cinemack's own limits need them:
  certification_max   "G" | "PG" | "PG-13" | "R" | "NC-17" (US theatrical rating, the site's "max_cert")
  unknown             "fail" (default: a film whose fact is missing fails that limit - the site's strict mode)
                      | "pass" (a missing fact never removes a film - the site's normal mode)
A version-1 plan cannot carry either field (it is checked as the kit wrote it).

Nothing here touches a database or the model. web/plan_sql.py turns a validated plan into parameterised SQL.
Switch QUERY_PLAN=1 (off by default); while it is off no answer path imports this module.
"""
from __future__ import annotations

import re
from typing import List, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

GENRES = ["Action", "Adventure", "Animation", "Comedy", "Crime", "Documentary", "Drama", "Family", "Fantasy",
          "History", "Horror", "Music", "Mystery", "Romance", "Science Fiction", "Thriller", "TV Movie", "War",
          "Western"]
_GENRE_KEY = {g.casefold(): g for g in GENRES}
_GENRE_KEY.update({"sci-fi": "Science Fiction", "scifi": "Science Fiction", "sci fi": "Science Fiction",
                   "documentaries": "Documentary", "musical": "Music"})
SERVICES = ["netflix", "max", "prime", "disney", "hulu", "apple", "peacock", "paramount", "tubi", "pluto", "roku",
            "criterion", "mubi", "kanopy"]          # the keys of web/user_prefs.SERVICES
CERTS = ["G", "PG", "PG-13", "R", "NC-17"]
MEDIA_ID = re.compile(r"^(movie|tv):[1-9][0-9]{0,9}$")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


def canonical_genre(name: str) -> str:
    g = _GENRE_KEY.get(" ".join(str(name).split()).casefold())
    if g is None:
        raise ValueError(f"unknown genre {name!r}")
    return g


class Entity(_Strict):
    internal_media_id: Optional[str]
    tmdb_id: Optional[int] = Field(ge=0)
    imdb_id: Optional[str] = Field(pattern=r"^(tt|nm)[0-9]{5,10}$")
    candidate_title: Optional[str] = Field(max_length=300)

    @field_validator("internal_media_id")
    @classmethod
    def _mid(cls, v):
        if v is not None and not MEDIA_ID.match(v):
            raise ValueError("internal_media_id must look like movie:<tmdb id> or tv:<tmdb id>")
        return v


class Filters(_Strict):
    year_min: Optional[int] = Field(ge=1880, le=2200)
    year_max: Optional[int] = Field(ge=1880, le=2200)
    runtime_min_minutes: Optional[int] = Field(ge=0, le=10000)
    runtime_max_minutes: Optional[int] = Field(ge=0, le=10000)
    genres_any: List[str]
    genres_all: List[str]
    genres_exclude: List[str]
    watch_region: Optional[str] = Field(pattern=r"^[A-Z]{2}$")
    watch_provider_ids_any: List[str]
    watch_monetization: List[Literal["flatrate", "free", "ads", "rent", "buy"]]
    exclude_watched: bool
    include_media_ids: List[str]
    exclude_media_ids: List[str]
    # schema 1.1 only (checked in QueryPlan)
    certification_max: Optional[Literal["G", "PG", "PG-13", "R", "NC-17"]] = None
    unknown: Literal["fail", "pass"] = "fail"

    @field_validator("genres_any", "genres_all", "genres_exclude", mode="after")
    @classmethod
    def _genres(cls, v):
        if len(v) != len(set(x.casefold() for x in v)):
            raise ValueError("genres repeat")
        if any(len(x) > 80 for x in v):
            raise ValueError("genre name too long")
        out = [canonical_genre(x) for x in v]
        if len(out) != len(set(out)):
            raise ValueError("genres repeat")
        return out

    @field_validator("watch_provider_ids_any", mode="after")
    @classmethod
    def _providers(cls, v):
        if len(v) != len(set(v)):
            raise ValueError("providers repeat")
        bad = [x for x in v if x not in SERVICES]
        if bad:
            raise ValueError(f"unknown provider id(s) {bad}; known: {SERVICES}")
        return v

    @field_validator("watch_monetization", mode="after")
    @classmethod
    def _monet(cls, v):
        if len(v) != len(set(v)):
            raise ValueError("monetization repeats")
        return v

    @field_validator("include_media_ids", "exclude_media_ids", mode="after")
    @classmethod
    def _ids(cls, v):
        if len(v) != len(set(v)):
            raise ValueError("media ids repeat")
        for x in v:
            if not MEDIA_ID.match(x):
                raise ValueError(f"bad media id {x!r} (movie:<tmdb id> or tv:<tmdb id>)")
        return v

    @model_validator(mode="after")
    def _ranges(self):
        if self.year_min is not None and self.year_max is not None and self.year_min > self.year_max:
            raise ValueError("year_min is after year_max")
        if (self.runtime_min_minutes is not None and self.runtime_max_minutes is not None
                and self.runtime_min_minutes > self.runtime_max_minutes):
            raise ValueError("runtime_min_minutes is above runtime_max_minutes")
        clash = (set(self.genres_any) | set(self.genres_all)) & set(self.genres_exclude)
        if clash:
            raise ValueError(f"genre(s) both wanted and excluded: {sorted(clash)}")
        if set(self.include_media_ids) & set(self.exclude_media_ids):
            raise ValueError("a media id is both included and excluded")
        if self.watch_provider_ids_any and not self.watch_region:
            raise ValueError("watch providers need watch_region")
        return self


class Retrieval(_Strict):
    routes: List[Literal["sql", "fts", "vector", "articles", "recommendations"]]
    article_mode: Literal["none", "cached", "refresh_if_stale"]
    max_candidates: int = Field(ge=1, le=200)

    @field_validator("routes", mode="after")
    @classmethod
    def _uniq(cls, v):
        if len(v) != len(set(v)):
            raise ValueError("routes repeat")
        return v


class Response(_Strict):
    language: str = Field(pattern=r"^[a-z]{2,3}(-[A-Za-z0-9]{2,8})*$")
    include_explanations: bool
    include_sources: bool


class QueryPlan(_Strict):
    schema_version: Union[Literal[1], Literal["1.1"]]
    intent: Literal["identify", "facts", "compare", "reception", "recommend", "availability", "news", "forecast"]
    media_types: List[Literal["movie", "tv"]] = Field(min_length=1)
    query_text: str = Field(max_length=1200)
    entity: Entity
    filters: Filters
    retrieval: Retrieval
    response: Response

    @field_validator("media_types", mode="after")
    @classmethod
    def _uniq(cls, v):
        if len(v) != len(set(v)):
            raise ValueError("media_types repeat")
        return v

    @model_validator(mode="after")
    def _rules(self):
        if self.intent == "forecast" and "movie" not in self.media_types:
            raise ValueError("a forecast plan must include movie")
        if self.schema_version == 1 and ("certification_max" in self.filters.model_fields_set
                                         or "unknown" in self.filters.model_fields_set):
            raise ValueError("certification_max and unknown need schema_version \"1.1\"")
        return self


def parse(data) -> QueryPlan:
    """A validated plan from a dict or a JSON string. Raises ValueError with a readable reason."""
    try:
        if isinstance(data, (str, bytes)):
            return QueryPlan.model_validate_json(data)
        return QueryPlan.model_validate(data)
    except ValidationError as e:
        reasons = "; ".join(f"{'.'.join(str(p) for p in err['loc']) or 'plan'}: {err['msg']}" for err in e.errors())
        raise ValueError(reasons) from None


def from_settings(settings, query_text="", watched_ids=(), region="US"):
    """A schema-1.1 plan carrying a person's website limits (web/user_prefs settings). Used by the conformance
    check and, later, by the site. min_year / max_year / max_runtime / max_cert / never_genres / only_genres
    (+ genres_all) / services_only (+ services) / strict map one to one. GENRE_MAIN's main-genre rule is NOT part
    of plan v1.1: genres here are TMDB's lists."""
    s = settings or {}
    only = [canonical_genre(g) for g in (s.get("only_genres") or [])]
    all_mode = bool(s.get("genres_all")) and len(only) > 1
    services = [k for k in (s.get("services") or []) if k in SERVICES] if s.get("services_only") else []
    plan = {
        "schema_version": "1.1", "intent": "recommend", "media_types": ["movie"], "query_text": query_text[:1200],
        "entity": {"internal_media_id": None, "tmdb_id": None, "imdb_id": None, "candidate_title": None},
        "filters": {
            "year_min": s.get("min_year"), "year_max": s.get("max_year"),
            "runtime_min_minutes": None, "runtime_max_minutes": s.get("max_runtime"),
            "genres_any": [] if all_mode else only, "genres_all": only if all_mode else [],
            "genres_exclude": [canonical_genre(g) for g in (s.get("never_genres") or [])],
            "watch_region": region if s.get("services_only") else None,
            "watch_provider_ids_any": services,
            "watch_monetization": ["flatrate", "free", "ads"] if s.get("services_only") else [],
            "exclude_watched": bool(watched_ids), "include_media_ids": [],
            "exclude_media_ids": [f"movie:{int(i)}" for i in watched_ids],
            "certification_max": s.get("max_cert") or None,
            "unknown": "fail" if s.get("strict") else "pass",
        },
        "retrieval": {"routes": ["sql"], "article_mode": "none", "max_candidates": 200},
        "response": {"language": "en", "include_explanations": True, "include_sources": True},
    }
    return parse(plan)
