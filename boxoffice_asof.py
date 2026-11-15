"""v8.1 slice 4.3 (13 Nov 2026): which box-office inputs may a forecast use at a given horizon?

Every input of the pf4 model is registered here with WHEN it becomes known. A forecast made `days` before release may
use an input only if it is known by then; asking for anything else raises AsOfError (the leak probe in
tests/test_v81_forecast.py checks that). Horizons (kit section 6): d90 = 90 days before release, d7 = 7 days before,
d0 = on the release date (= pf4 as it was evaluated). The opening-weekend update is not built: no opening-weekend
grosses exist in the data (audit F1).

Kinds of availability:
  planned      known once the film is scheduled: budget, genres, language, country, runtime, franchise / source facts,
               earlier films of the same people (they were released before this film), the release calendar around
               the date. ASSUMPTION, stated in the model card: the final release date, runtime and competitor dates
               are taken as known at d90 (studios move dates; the data keeps only the final ones).
  trailers     YouTube trailers / teasers published before the cutoff (recomputed per horizon by trailer_features)
  release      known only on release day or later (pageviews in the last week, opening data): d0 only
  after        reception after release (ratings, reviews, awards, Letterboxd, news after release): never pre-release
"""
import datetime
import math

HORIZON_DAYS = {"d90": 90, "d7": 7, "d0": 0}


class AsOfError(ValueError):
    pass


def _register():
    import performance as P
    reg = {}
    for k in P.BEFORE:
        reg[k] = "planned"
    for k in P.TRAILER:
        reg[k] = "trailers"
    for k in P.BUZZ:
        reg[k] = "release"
    for k in P.RECEPTION + P.LB + P.NEWSF + P.NEWS_SRC + ["log_votes"]:
        reg[k] = "after"
    return reg


_REG = {}


def registry():
    if not _REG:
        _REG.update(_register())
    return dict(_REG)


def allowed(kind, horizon):
    if horizon not in HORIZON_DAYS:
        raise AsOfError(f"unknown horizon {horizon!r}")
    return kind in ("planned", "trailers") or (kind == "release" and horizon == "d0")


def check(cols, horizon):
    """The columns, unchanged, if every one is known at the horizon; otherwise AsOfError naming the offenders."""
    reg = registry()
    unknown = [k for k in cols if k not in reg]
    late = [k for k in cols if k in reg and not allowed(reg[k], horizon)]
    if unknown or late:
        raise AsOfError(f"not usable at {horizon}: unregistered {unknown}, known only later {late}")
    return list(cols)


def cutoff(release_date, horizon):
    return release_date - datetime.timedelta(days=HORIZON_DAYS[horizon])


def trailer_features(rows, videos, horizon):
    """Rows copied with trailer_n_pre / trailer_lead_days recomputed from videos published BEFORE the cutoff.
    rows need tmdb_id, _date (release date); videos: {tmdb_id: [publication dates]}. Same rule as performance.py
    (films before 2007 get no trailer value; YouTube trailers start then)."""
    out = []
    for r in rows:
        x = dict(r)
        d = r.get("_date")
        if d is None or d.year < 2007:
            x["trailer_n_pre"] = x["trailer_lead_days"] = math.nan
        else:
            cut = cutoff(d, horizon)
            pre = sorted(v for v in videos.get(r["tmdb_id"], []) if v and v < cut)
            assert all(v < cut for v in pre)
            x["trailer_n_pre"] = float(len(pre))
            x["trailer_lead_days"] = float((d - pre[0]).days) if pre else math.nan
        x["_cutoff"] = cutoff(d, horizon) if d else None
        out.append(x)
    return out
