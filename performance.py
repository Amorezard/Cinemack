#!/usr/bin/env python3
"""
v5 (15 Oct evening): the box-office performance model. What did films like this usually return, which
factors moved that expectation, and how did this film compare?

Not a language model and no GPU: a gradient-boosted tree model (LightGBM; scikit-learn if LightGBM is not
installed) on one row per film, trained in about a minute on the CPU. The answer model only words what
this computes (tools.py, PERF=1), the same rule as the news and success blocks.

  target      log(worldwide gross / budget). 0 = earned its budget back, log(2.5) ~ 0.92 = the 2.5x
              rule-of-thumb break-even. Films with budget >= $100k, gross > 0, released 1960 on.
  BEFORE      what was known before release:
              budget in today's dollars (money_extra.py real), year, month, runtime, genres, English-language,
              US production, sequel + the franchise's earlier returns, the director's and top-3 cast's
              earlier films (how many, their average return and TMDB rating - earlier films only),
              big releases within a week either side (count, total budget)
  AFTER       BEFORE + reception: TMDB rating and votes, MovieLens rating, Rotten Tomatoes / Metacritic /
              CinemaScore (money_extra.py critics), awards, share of positive review aspects
  NEWS        AFTER + news at release (news.py nw2: resonance, closest headline), to ask whether the news
              adds anything once everything else is known

Tests (all on films the model never saw):
  held-out years   train on films up to 2015, test on 2016-2019 (and 2020-2023 separately: the pandemic years)
  baselines        (1) the median return of the film's first genre in 2006-2015; (2) a linear model (ridge)
                   on the BEFORE features - so the trees have to beat a straight line too
  news             AFTER vs NEWS on 2016-2019: difference in mean absolute error with a bootstrap 95% CI
Stored per film (perf_film), for the answers: the BEFORE and AFTER expectation from a model that did not see
the film's own 5-year period, and the factors that moved it (SHAP-style contributions from LightGBM; with
scikit-learn, the change when that feature is left blank, marked approximate).

Decision rule (written 15 Oct 2026, 17:40, before any result):
  PERF=1 is adopted if, on 2016-2019,
    (a) the BEFORE model's mean absolute error is at least 5% lower than the genre baseline's, with the
        bootstrap 95% CI of the difference excluding 0, and
    (b) Spearman(predicted, actual) of the BEFORE model >= 0.30, and
    (c) answers: eval_v3 --category why --answers with PERF=1 vs PERF=0: judge passes not lower and mean
        grounding not lower by more than 0.05.
  Release context (added 15 Oct 20:30, before any pf2 result): "the release context adds information" is claimed
  only if BEFORE (with the CONTEXT features) beats BEFORE without them (pf1) on 2016-2019 with the bootstrap CI of the
  MAE difference excluding 0. The stated-reasons table (Wikipedia box office / release / reception sentences) is
  descriptive: it counts what sources say, it is not a test.
  News: "news at release adds information beyond the other factors" is claimed only if NEWS beats AFTER on
  2016-2019 with the CI of the MAE difference excluding 0. Otherwise the write-up says it adds nothing
  measurable. No switch depends on this.

    python performance.py fit            # features, tests, per-film expectations -> facts.db, results/
    python performance.py report         # results/performance_summary.txt again
    python performance.py show "Oppenheimer"
    python performance.py reasons        # only the stated-reasons table (also part of fit)
    python performance.py remove

pf3 (20 Oct night): pre-release attention, each with its own test and rule (written 20 Oct 2026, 23:40, before
any result):
  TRAILER   from TMDB's YouTube trailer list (fetch_free_data.py tmdb), only videos published BEFORE the release
            date: how many trailers/teasers, and how many days before release the first one came out. Films before
            2007 have no YouTube era, so both are blank for them. Tested like the release context: trees BEFORE vs
            BEFORE + TRAILER on 2016-2019. Adopted into the per-film expectations only if the bootstrap 95% CI of
            the MAE difference excludes 0 (better).
  BUZZ      Wikipedia pageviews in the 28 days before release (fetch_v6_data.py pageviews; the API starts July
            2015), log of the 28-day total and the last week's share. Too recent for the <=2015 training set, so it
            gets its own test: 5-fold cross-validation over the 2016-2019 and 2021-2023 films that have pageviews
            (each fold's model trained on every other film, all years). "Pre-release attention adds information"
            is claimed only if the CI of the MAE difference excludes 0. Reported only; never used per film (most
            films have no pageviews).
  Reception (AFTER only): Rotten Tomatoes / Metacritic from OMDb where Wikipedia gave none, and the IMDb rating.
  YouTube view counts are NOT used anywhere: they are today's counts, after release.

pf4 (6 Nov 2026, 00:40; rules written before any result):
  NEWS_SRC  news.py films-src: resonance and closest-headline similarity computed separately for the Wikipedia
            current-events headlines and the NYT front pages (news_film mixes the two). The window is release -30 /
            +30 days, with headlines about films or about the film itself removed (nw2), so it carries no box-office
            news. Two tests on 2016-2019:
              BEFORE + NEWS (both the mixed and the per-source features) vs BEFORE  - "news helps PREDICT success";
              AFTER + NEWS vs AFTER                                                  - the pf2/pf3 question again.
            Each is adopted into the per-film expectations (pre / post) only if the bootstrap 95% CI of the MAE
            difference excludes 0 (better), like the trailer features.
  LB        Letterboxd ratings (letterboxd_data.py: mean rating, log number of ratings; post-release, so AFTER
            only): AFTER + LB vs AFTER on 2016-2019, REPORTED ONLY (the ratings were collected years after release
            and cover a small set of active users), never used per film.
"""
import argparse
import datetime
import json
import math
import os
import re
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import facts_db as F  # noqa: E402

VERSION = "pf4"     # 6 Nov: + news per source (Wikipedia / NYT) and Letterboxd tests (pf3: trailers, pageviews, OMDb + IMDb)
GENRES = ["Action", "Adventure", "Animation", "Comedy", "Crime", "Documentary", "Drama", "Family", "Fantasy",
          "History", "Horror", "Music", "Mystery", "Romance", "Science Fiction", "Thriller", "War", "Western"]
TRAIN_TO, TEST = 2015, (2016, 2019)
LATE = (2020, 2023)
SUMMARY = os.path.join(HERE, "results", "performance_summary.txt")

DDL = """
create table if not exists perf_film(tmdb_id integer primary key, version text, in_universe integer,
  actual real, pre real, post real, pre_contrib text, post_contrib text, fold text, created_at text,
  context_json text);
create table if not exists perf_meta(key text primary key, value text);
create table if not exists perf_reasons(tmdb_id integer, category text, direction text, sentence text,
  section text, url text);
create index if not exists perf_reasons_film on perf_reasons(tmdb_id);
"""

BEFORE = ["log_budget_real", "year", "month", "runtime", "n_genres"] + [f"g_{g}" for g in GENRES] + [
    "lang_en", "us_made", "sequel", "franchise_prev_n", "franchise_prev_ret",
    "dir_prev_n", "dir_prev_ret", "dir_prev_rating", "cast_prev_n", "cast_prev_ret", "cast_prev_rating",
    "comp_n", "comp_log_budget"]
# 15 Oct 20:30 (Wesley's Doctor Sleep example): the release context, measured for every film.
CONTEXT = ["franchise_gap_years", "legacy_sequel", "source_adapted", "author_prev_365_n", "author_prev_365_ret",
           "author_prev_n", "genre_month_ret", "horror_days_after_halloween", "days_to_holiday",
           "same_weekend_n", "same_weekend_log_budget", "prev_10d_log_budget", "same_genre_14d_n",
           "same_genre_prev_30d_n"]
BEFORE_PF1 = list(BEFORE)
BEFORE = BEFORE + CONTEXT
# 15 Oct 20:40: number of TMDB votes taken out - it grows with how many people saw the film, so it measures the
# outcome rather than explaining it (Doctor Sleep: "5,082 votes raises it x4.08")
RECEPTION = ["rating", "ml_rating", "rt_pct", "rt_avg", "mc_score", "cinemascore",
             "awards_won", "awards_nom", "aspects_pos", "imdb_rating"]
TRAILER = ["trailer_n_pre", "trailer_lead_days"]           # pf3: pre-release only (published before release)
BUZZ = ["log_pv_pre28", "pv_last7_share"]                  # pf3: Wikipedia views in the 28 days before release
NEWSF = ["news_resonance", "news_top_sim"]
NEWS_SRC = ["news_wiki_resonance", "news_wiki_top_sim", "news_nyt_resonance", "news_nyt_top_sim"]    # pf4
LB = ["lb_rating", "lb_log_n"]                                                                   # pf4, reported only
AFTER = BEFORE + RECEPTION
WITH_NEWS = AFTER + NEWSF

LABEL = {
    "log_budget_real": "budget", "year": "release year", "month": "release month", "runtime": "running time",
    "n_genres": "number of genres", "lang_en": "English-language", "us_made": "US production",
    "sequel": "sequel / franchise film", "franchise_prev_n": "earlier films in the franchise",
    "franchise_prev_ret": "the franchise's earlier returns", "dir_prev_n": "director's earlier films",
    "dir_prev_ret": "director's earlier returns", "dir_prev_rating": "director's earlier ratings",
    "cast_prev_n": "lead actors' earlier films", "cast_prev_ret": "lead actors' earlier returns",
    "cast_prev_rating": "lead actors' earlier ratings", "comp_n": "big releases the same week",
    "comp_log_budget": "competition's budgets", "rating": "TMDB rating", "log_votes": "number of TMDB votes",
    "ml_rating": "MovieLens rating", "rt_pct": "Rotten Tomatoes score", "rt_avg": "critics' average rating",
    "mc_score": "Metacritic score", "cinemascore": "CinemaScore grade", "awards_won": "awards won",
    "awards_nom": "award nominations", "aspects_pos": "positive share of review comments",
    "imdb_rating": "IMDb rating", "trailer_n_pre": "trailers and teasers before release",
    "trailer_lead_days": "days from the first trailer to release",
    "log_pv_pre28": "Wikipedia views in the 4 weeks before release", "pv_last7_share": "share of those views in the last week",
    "news_resonance": "news at release matching the film", "news_top_sim": "closest headline at release",
    "news_wiki_resonance": "Wikipedia current events at release matching the film",
    "news_wiki_top_sim": "closest Wikipedia current-events item at release",
    "news_nyt_resonance": "NYT front pages at release matching the film", "news_nyt_top_sim": "closest NYT front-page headline",
    "lb_rating": "Letterboxd rating", "lb_log_n": "number of Letterboxd ratings",
    "franchise_gap_years": "years since the previous film in the series", "legacy_sequel": "sequel 15+ years later",
    "source_adapted": "adapted from a book / comic", "author_prev_365_n": "same author's adaptations in the past year",
    "author_prev_365_ret": "same author's recent adaptations' returns", "author_prev_n": "same author's earlier adaptations",
    "genre_month_ret": "how this genre usually does in this month", "horror_days_after_halloween": "horror release vs Halloween",
    "days_to_holiday": "days to the nearest big holiday", "same_weekend_n": "big releases the same weekend",
    "same_weekend_log_budget": "same-weekend competition's budgets", "prev_10d_log_budget": "big releases in the 10 days before",
    "same_genre_14d_n": "same-genre films within two weeks", "same_genre_prev_30d_n": "same-genre films in the month before",
}
LABEL.update({f"g_{g}": f"{g.lower()} genre" for g in GENRES})
MONTHS = ["", "January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December"]
CS_TEXT = {12: "A+", 11: "A", 10: "A-", 9: "B+", 8: "B", 7: "B-", 6: "C+", 5: "C", 4: "C-", 3: "D+", 2: "D", 1: "D-", 0: "F"}


def conn(readonly=False):
    c = F.connect(readonly=readonly)
    if not readonly:
        c.executescript(DDL)
    return c


def _has(c, table):
    try:
        c.execute(f"select 1 from {table} limit 1")
        return True
    except Exception:  # noqa: BLE001
        return False


def _has_videos(vids):
    return bool(vids)


def _date(rd):
    try:
        return datetime.date.fromisoformat((rd or "")[:10])
    except ValueError:
        return None


# ------------------------------------------------------------------ features
def build_table(c):
    """One feature dict per film that has a budget (and, for the training universe, a gross)."""
    films = {}
    for (fid, year, rd, usrd, runtime, genres, lang, countries, coll, va, vc, title) in c.execute(
            "select tmdb_id, year, release_date, us_release_date, runtime, genres_json, original_language, "
            "countries_json, collection_id, vote_average, vote_count, title from films where year >= 1960"):
        films[fid] = dict(year=year, date=_date(usrd) or _date(rd), runtime=runtime,
                          genres=json.loads(genres or "[]"), lang=lang, countries=json.loads(countries or "[]"),
                          coll=coll, va=va, vc=vc, title=title)
    money = {fid: (b, g) for fid, b, g in c.execute("select tmdb_id, budget, gross from money")}
    real = {}
    if _has(c, "money_real"):
        real = {fid: b for fid, b in c.execute("select tmdb_id, budget_real from money_real")}
    ret = {fid: math.log(g / b) for fid, (b, g) in money.items() if b and g and b >= 1e5 and g > 0}

    # earlier-film statistics per person: films sorted by date
    by_person = {}
    for fid, pid, kind, job, ordn in c.execute("select tmdb_id, person_id, kind, job, ord from credits "
                                               "where (kind = 'crew' and job = 'Director') or (kind = 'cast' and ord < 3)"):
        if fid in films and films[fid]["date"]:
            by_person.setdefault(pid, []).append(fid)
    directors, cast = {}, {}
    for fid, pid, kind, job, ordn in c.execute("select tmdb_id, person_id, kind, job, ord from credits "
                                               "where (kind = 'crew' and job = 'Director') or (kind = 'cast' and ord < 3)"):
        (directors if kind == "crew" else cast).setdefault(fid, []).append(pid)
    for pid in by_person:
        by_person[pid].sort(key=lambda f: films[f]["date"])

    def prior(pid, d):
        n, rets, ratings = 0, [], []
        for f in by_person.get(pid, []):
            if films[f]["date"] >= d:
                break
            n += 1
            if f in ret:
                rets.append(ret[f])
            if films[f]["va"] and (films[f]["vc"] or 0) >= 20:
                ratings.append(films[f]["va"])
        return n, rets, ratings

    by_coll = {}
    for fid, f in films.items():
        if f["coll"] and f["date"]:
            by_coll.setdefault(f["coll"], []).append(fid)
    # competition: big releases (budget >= $20M nominal) by date
    big = sorted((films[f]["date"], f, money[f][0]) for f in money
                 if f in films and films[f]["date"] and money[f][0] and money[f][0] >= 2e7)
    big_dates = [x[0] for x in big]

    crit = {}
    if _has(c, "critic_scores"):
        crit = {r[0]: r[1:] for r in c.execute("select tmdb_id, rt_pct, rt_avg, mc_score, cinemascore_num "
                                               "from critic_scores")}
    ml = {fid: m for fid, n, m in c.execute("select tmdb_id, n_ratings, mean from ml_stats") if n >= 20} \
        if _has(c, "ml_stats") else {}
    aw = {fid: (w or 0, n or 0) for fid, w, n in c.execute(
        "select tmdb_id, sum(status = 'won'), sum(status = 'nominated') from awards group by tmdb_id")}
    asp = {}
    if _has(c, "film_aspects"):
        for fid, p, n in c.execute("select tmdb_id, sum(pos), sum(neg) from film_aspects group by tmdb_id"):
            if (p or 0) + (n or 0) >= 5:
                asp[fid] = p / (p + n)
    # pf3: OMDb fills Rotten Tomatoes / Metacritic where Wikipedia's reception text gave none
    if _has(c, "omdb"):
        for fid, rt, mc in c.execute("select tmdb_id, rt_pct, mc_score from omdb where status = 'ok' and tmdb_id is not null"):
            old = crit.get(fid) or (None, None, None, None)
            crit[fid] = (old[0] if old[0] is not None else rt, old[1], old[2] if old[2] is not None else mc, old[3])
    imdb = {}
    if _has(c, "imdb_ratings"):
        imdb = {fid: r for fid, r, v in c.execute("select f.tmdb_id, i.rating, i.votes from films f join imdb_ratings i "
                                                    "on i.imdb_id = f.imdb_id") if (v or 0) >= 50}
    vids = {}
    if _has(c, "film_videos"):
        for fid, pub in c.execute("select tmdb_id, substr(published_at, 1, 10) from film_videos "
                                  "where site = 'YouTube' and type in ('Trailer', 'Teaser') and published_at is not null"):
            vids.setdefault(fid, []).append(_date(pub))
    pv = {}
    if _has(c, "pageviews"):
        for fid, p28, p7, days in c.execute("select tmdb_id, pv_pre28, pv_pre7, days from pageviews"):
            if days and days >= 20 and p28 is not None:
                pv[fid] = (math.log1p(p28), (p7 or 0) / p28 if p28 else np.nan)
    news = {}
    if _has(c, "news_film"):
        news = {fid: (r, t) for fid, r, t in c.execute("select tmdb_id, resonance, top_sim from news_film")}
    news_src = {}                                   # pf4: per source (news.py films-src)
    if _has(c, "news_film_src"):
        for fid, src, r, t in c.execute("select tmdb_id, source, resonance, top_sim from news_film_src"):
            news_src[(fid, "wiki" if src == "wikipedia" else src)] = (r, t)
    lbd = {}                                        # pf4: Letterboxd (letterboxd_data.py), best-matched source first
    if _has(c, "letterboxd"):
        for fid, n, mean in c.execute("select tmdb_id, n, mean from letterboxd where n >= 20 and match >= 1.0 "
                                      "order by n"):
            lbd[fid] = (mean, math.log1p(n))

    import bisect
    rows = []
    for fid, f in films.items():
        b = (money.get(fid) or (None, None))[0]
        if not b or b < 1e5 or not f["date"]:
            continue
        d = f["date"]
        x = {"tmdb_id": fid, "actual": ret.get(fid)}
        br = real.get(fid) or b
        x["log_budget_real"] = math.log(br)
        x["year"] = f["year"] or d.year
        x["month"] = d.month
        x["runtime"] = f["runtime"] or np.nan
        x["n_genres"] = len(f["genres"])
        for g in GENRES:
            x[f"g_{g}"] = 1.0 if g in f["genres"] else 0.0
        x["lang_en"] = 1.0 if f["lang"] == "en" else 0.0
        x["us_made"] = 1.0 if "US" in f["countries"] else 0.0
        earlier = [g for g in by_coll.get(f["coll"], []) if films[g]["date"] < d] if f["coll"] else []
        x["sequel"] = 1.0 if earlier else 0.0
        x["franchise_prev_n"] = len(earlier)
        er = [ret[g] for g in earlier if g in ret]
        x["franchise_prev_ret"] = float(np.mean(er)) if er else np.nan
        for key, people in (("dir", directors.get(fid, [])[:1]), ("cast", cast.get(fid, [])[:3])):
            ns, rs, ra = [], [], []
            for pid in people:
                n, r1, r2 = prior(pid, d)
                ns.append(n)
                rs += r1
                ra += r2
            x[f"{key}_prev_n"] = float(np.mean(ns)) if ns else np.nan
            x[f"{key}_prev_ret"] = float(np.mean(rs)) if rs else np.nan
            x[f"{key}_prev_rating"] = float(np.mean(ra)) if ra else np.nan
        lo = bisect.bisect_left(big_dates, d - datetime.timedelta(days=7))
        hi = bisect.bisect_right(big_dates, d + datetime.timedelta(days=7))
        comp = [bb for (_dd, g, bb) in big[lo:hi] if g != fid]
        x["comp_n"] = len(comp)
        x["comp_log_budget"] = math.log(sum(comp)) if comp else 0.0
        x["rating"] = f["va"] if f["va"] and (f["vc"] or 0) >= 20 else np.nan
        x["log_votes"] = math.log1p(f["vc"] or 0)
        x["ml_rating"] = ml.get(fid, np.nan)
        cr = crit.get(fid) or (None, None, None, None)
        x["rt_pct"], x["rt_avg"], x["mc_score"], x["cinemascore"] = [np.nan if v is None else v for v in cr]
        w, n = aw.get(fid, (0, 0))
        x["awards_won"], x["awards_nom"] = w, n
        x["aspects_pos"] = asp.get(fid, np.nan)
        x["imdb_rating"] = imdb.get(fid, np.nan)
        if d.year >= 2007 and _has_videos(vids):
            pre = sorted(v for v in vids.get(fid, []) if v and v < d)
            x["trailer_n_pre"] = float(len(pre))
            x["trailer_lead_days"] = float((d - pre[0]).days) if pre else np.nan
        else:
            x["trailer_n_pre"] = x["trailer_lead_days"] = np.nan
        x["log_pv_pre28"], x["pv_last7_share"] = pv.get(fid, (np.nan, np.nan))
        r, t = news.get(fid, (None, None))
        x["news_resonance"] = np.nan if r is None else r
        x["news_top_sim"] = np.nan if t is None else t
        for src in ("wiki", "nyt"):
            r, t = news_src.get((fid, src), (None, None))
            x[f"news_{src}_resonance"] = np.nan if r is None else r
            x[f"news_{src}_top_sim"] = np.nan if t is None else t
        x["lb_rating"], x["lb_log_n"] = lbd.get(fid, (np.nan, np.nan))
        rows.append(x)
    release_context(c, films, money, ret, rows)
    return rows


# ------------------------------------------------------------------ release context (pf2)
SOURCE_JOBS = ("Novel", "Book", "Author", "Characters", "Short Story", "Comic Book", "Graphic Novel", "Novelist")


def _holidays(y):
    """Thanksgiving (4th Thursday of November), Christmas, July 4 and Memorial Day (last Monday of May)."""
    nov1 = datetime.date(y, 11, 1)
    thanks = nov1 + datetime.timedelta(days=(3 - nov1.weekday()) % 7 + 21)
    may31 = datetime.date(y, 5, 31)
    memorial = may31 - datetime.timedelta(days=may31.weekday())
    return [thanks, datetime.date(y, 12, 25), datetime.date(y, 7, 4), memorial]


def _usd(v):
    return f"${v / 1e6:,.0f}M" if v else "?"


def release_context(c, films, money, ret, rows):
    """Adds the CONTEXT features to each row and x['_ctx']: plain sentences stating that context (computed facts,
    shown to the answer model whatever the model makes of them)."""
    import bisect
    people = {pid: n for pid, n in c.execute("select person_id, name from people")}
    authors = {}
    for fid, pid in c.execute("select tmdb_id, person_id from credits where kind = 'crew' and job in (%s)"
                              % ",".join("?" * len(SOURCE_JOBS)), SOURCE_JOBS):
        if fid in films and films[fid]["date"]:
            authors.setdefault(fid, [])
            if pid not in authors[fid]:
                authors[fid].append(pid)
    by_author = {}
    for fid, pids in authors.items():
        for pid in pids:
            by_author.setdefault(pid, []).append(fid)
    for pid in by_author:
        by_author[pid].sort(key=lambda f: films[f]["date"])
    by_coll = {}
    for fid, f in films.items():
        if f["coll"] and f["date"]:
            by_coll.setdefault(f["coll"], []).append(fid)
    # releases with a budget: big ones (>= $20M) for competition, any >= $5M for same-genre crowding
    rel = sorted((films[f]["date"], f, money[f][0]) for f in money
                 if f in films and films[f]["date"] and money[f][0] and money[f][0] >= 5e6)
    big = [r for r in rel if r[2] >= 2e7]
    big_d = [r[0] for r in big]
    by_genre = {}
    for d, f, b in rel:
        g = (films[f]["genres"] or ["?"])[0]
        by_genre.setdefault(g, []).append((d, f, b))
    genre_d = {g: [r[0] for r in v] for g, v in by_genre.items()}
    # genre x month typical return, from films of EARLIER years only (no peeking at the film's own year or later)
    gm = {}
    for f, r in ret.items():
        if f in films and films[f]["date"]:
            g = (films[f]["genres"] or ["?"])[0]
            gm.setdefault((g, films[f]["date"].month), []).append((films[f]["year"] or films[f]["date"].year, r))
    for k in gm:
        gm[k].sort()
    gm_years = {k: [y for y, _ in v] for k, v in gm.items()}

    def window(lst, dates, a, b):
        return lst[bisect.bisect_left(dates, a): bisect.bisect_right(dates, b)]

    for x in rows:
        fid = x["tmdb_id"]
        f = films[fid]
        d = f["date"]
        g0 = (f["genres"] or ["?"])[0]
        ctx = []
        # series: how long since the previous film
        earlier = sorted((films[e]["date"], e) for e in by_coll.get(f["coll"], []) if films[e]["date"] < d) if f["coll"] else []
        if earlier:
            pd, pe = earlier[-1]
            gap = (d - pd).days / 365.25
            x["franchise_gap_years"], x["legacy_sequel"] = gap, 1.0 if gap >= 15 else 0.0
            ctx.append(f"Follows {films[pe]['title']} ({pd.year}), the previous film in its series, "
                       f"{gap:.0f} years earlier" + (" - a long gap: much of today's audience may not have seen it"
                                                       if gap >= 15 else ""))
        else:
            x["franchise_gap_years"], x["legacy_sequel"] = np.nan, 0.0
        # source author: adaptations of the same writer, and how recent
        pids = authors.get(fid, [])
        x["source_adapted"] = 1.0 if pids else 0.0
        n365, r365, n_all, recent = 0, [], 0, []
        for pid in pids[:2]:
            for e in by_author.get(pid, []):
                if e == fid or films[e]["date"] >= d:
                    continue
                n_all += 1
                if (d - films[e]["date"]).days <= 365:
                    n365 += 1
                    if e in ret:
                        r365.append(ret[e])
                    recent.append((films[e]["date"], e, pid))
        x["author_prev_365_n"], x["author_prev_n"] = n365, n_all
        x["author_prev_365_ret"] = float(np.mean(r365)) if r365 else np.nan
        if pids:
            name = people.get(pids[0], "its source author")
            if recent:
                recent.sort(reverse=True)
                ctx.append(f"Adapted from {name}; {len(recent)} other adaptation(s) of {name} came out in the "
                           f"previous 12 months: " + "; ".join(
                               f"{films[e]['title']} ({dd:%b %Y}{', ' + format(math.exp(ret[e]), '.1f') + 'x budget' if e in ret else ''})"
                               for dd, e, _p in recent[:4]))
            elif n_all:
                ctx.append(f"Adapted from {name} ({n_all} earlier adaptation(s) in the database, none in the previous 12 months)")
        # season
        ys = gm_years.get((g0, d.month), [])
        prior = gm.get((g0, d.month), [])[:bisect.bisect_left(ys, f["year"] or d.year)]
        x["genre_month_ret"] = float(np.median([r for _y, r in prior])) if len(prior) >= 10 else np.nan
        if "Horror" in f["genres"]:
            dh = (d - datetime.date(d.year, 10, 31)).days
            x["horror_days_after_halloween"] = dh
            if -45 <= dh < 0:
                ctx.append(f"Horror film released {-dh} days before Halloween")
            elif 0 <= dh <= 60:
                ctx.append(f"Horror film released {dh} days after Halloween")
        else:
            x["horror_days_after_halloween"] = np.nan
        x["days_to_holiday"] = min(abs((h - d).days) for y in (d.year - 1, d.year, d.year + 1) for h in _holidays(y))
        if len(prior) >= 10:
            gm_all = [r for _y, r in prior]
            ctx.append(f"{g0} films released in {MONTHS[d.month]} (earlier years) typically returned "
                       f"{math.exp(float(np.median(gm_all))):.1f}x their budgets")
        # competition
        wk = [r for r in window(big, big_d, d - datetime.timedelta(days=3), d + datetime.timedelta(days=3)) if r[1] != fid]
        x["same_weekend_n"] = len(wk)
        x["same_weekend_log_budget"] = math.log(sum(r[2] for r in wk)) if wk else 0.0
        if wk:
            ctx.append("Other big releases the same weekend: " + "; ".join(
                f"{films[r[1]]['title']} ({_usd(r[2])} budget)" for r in sorted(wk, key=lambda r: -r[2])[:4]))
        pre = window(big, big_d, d - datetime.timedelta(days=10), d - datetime.timedelta(days=4))
        x["prev_10d_log_budget"] = math.log(sum(r[2] for r in pre)) if pre else 0.0
        if pre:
            ctx.append("Big releases in the 10 days before, still in theaters: " + "; ".join(
                f"{films[r[1]]['title']} ({r[0]:%b %d}, {_usd(r[2])} budget)" for r in sorted(pre, key=lambda r: -r[2])[:3]))
        sg = [r for r in window(by_genre.get(g0, []), genre_d.get(g0, []), d - datetime.timedelta(days=14),
                                d + datetime.timedelta(days=14)) if r[1] != fid]
        x["same_genre_14d_n"] = len(sg)
        x["same_genre_prev_30d_n"] = len(window(by_genre.get(g0, []), genre_d.get(g0, []),
                                                d - datetime.timedelta(days=30), d - datetime.timedelta(days=1)))
        if sg:
            ctx.append(f"Other {g0.lower()} films within two weeks: " + "; ".join(
                f"{films[r[1]]['title']} ({r[0]:%b %d})" for r in sorted(sg, key=lambda r: -r[2])[:3]))
        x["_ctx"] = ctx


# ------------------------------------------------------------------ reasons the sources state
REASON_CUE = re.compile(r"\b(underperform\w*|disappoint\w*|flop\w*|bomb\w*|box[- ]office failure|fell short|"
                        r"below (expectations|projections)|lower than expected|attribut\w*|blam\w*|credit\w* (to|for)|"
                        r"due (in part )?to|because of|owing to|exceeded expectations|outperform\w*|over-?perform\w*|"
                        r"sleeper hit|word[- ]of[- ]mouth|surpass\w* expectations)\b", re.I)
REASON_CATS = {
    "marketing": r"marketing|campaign|trailers?|advertis\w*|promot\w*|tie-?ins?|branding",
    "release timing": r"release date|halloween|thanksgiving|christmas|holiday|summer|labor day|memorial day|"
                      r"moved (up|back)|delay\w*|post-?poned|release window|weekend after",
    "competition": r"competition|competing|opposite|same weekend|counter-?programm\w*|crowded|holdovers?|"
                   r"went up against|faced",
    "audience (who it was for / unfamiliar)": r"younger audiences?|older audiences?|unfamiliar|had not seen|hadn't seen|"
                                               r"predecessor|original film|decades|nostalgi\w*|long-awaited|meant for|"
                                               r"target(ed)? audience|demographic|over the age of|under the age of",
    "fatigue / saturation": r"fatigue|saturat\w*|oversaturat\w*|too many|glut",
    "runtime": r"run ?time|running time|length",
    "reviews / word of mouth": r"reviews?|critics?|critical|word[- ]of[- ]mouth|cinemascore|rotten tomatoes",
    "rating (R / PG-13)": r"\bR rating|rated R|R-rated|PG-13|NC-17",
    "pandemic / streaming": r"covid|pandemic|streaming|hbo max|day-and-date|simultaneous(ly)? on",
    "budget / costs": r"budget|overrun|costly|expensive|cost",
    "star / cast": r"star power|cast|lead actor|leading man|leading lady",
    "international / China": r"china|chinese|international|overseas|foreign markets",
}
REASON_RX = {k: re.compile(v, re.I) for k, v in REASON_CATS.items()}
NEG_RX = re.compile(r"underperform|disappoint|flop|bomb|failure|fell short|below|lower than expected|blam|lost", re.I)


REVIEW_QUOTE = re.compile(r"\b(gave (the|it) (film|movie)|out of (four|five|ten)|stars?\b|wrote|writing (for|in)|"
                          r"of (the )?[\"“][A-Z]|critic \w+ \w+ (said|called|praised)|praised|called it|review(ed|ing)? (for|in))", re.I)


def extract_reasons(text):
    """Sentences that state why a film did well or badly -> [(category, direction, sentence)]. A critic's review
    quote ("... gave the film three out of five ...") is an opinion of the film, not a reason for its box office."""
    out = []
    for sent in re.split(r"(?<=[.!?])\s+(?=[A-Z\"“])", re.sub(r"\s+", " ", text or "")):
        if len(sent) < 40 or not REASON_CUE.search(sent) or REVIEW_QUOTE.search(sent):
            continue
        cats = [k for k, rx in REASON_RX.items() if rx.search(sent)]
        if not cats:
            continue
        direction = "below" if NEG_RX.search(sent) else "above"
        out.append((cats, direction, sent.strip()[:400]))
    return out


def cmd_reasons(_a, c=None):
    c = c or conn()
    films = {}
    for fid, kind, text, url in c.execute("select tmdb_id, kind, text, url from sections where kind in "
                                          "('box_office', 'release', 'reception') order by tmdb_id, sec_id, position"):
        films.setdefault(fid, []).append((kind, text, url))
    rows, seen = [], set()
    for fid, parts in films.items():
        for kind, text, url in parts:
            for cats, direction, sent in extract_reasons(text):
                key = (fid, sent[:120])
                if key in seen:          # chunks overlap by a sentence
                    continue
                seen.add(key)
                for cat in cats:
                    rows.append((fid, cat, direction, sent, kind, url))
    c.execute("delete from perf_reasons")
    c.executemany("insert into perf_reasons values (?,?,?,?,?,?)", rows)
    c.commit()
    print(f"stated reasons: {len(rows):,} (film, category) rows for {len({r[0] for r in rows}):,} films")
    return rows


def matrix(rows, cols):
    return np.array([[r[k] for k in cols] for r in rows], dtype=np.float64)


# ------------------------------------------------------------------ models
def make_model():
    try:
        import lightgbm as lgb
        return "lightgbm", lambda: lgb.LGBMRegressor(
            objective="huber", alpha=1.0, n_estimators=500, learning_rate=0.03, num_leaves=15,
            min_child_samples=30, subsample=0.8, subsample_freq=1, colsample_bytree=0.8,
            reg_lambda=1.0, random_state=7, verbose=-1)
    except ImportError:
        from sklearn.ensemble import HistGradientBoostingRegressor
        return "sklearn", lambda: HistGradientBoostingRegressor(
            loss="absolute_error", max_iter=500, learning_rate=0.03, max_leaf_nodes=15, min_samples_leaf=30,
            l2_regularization=1.0, random_state=7)


def contributions(kind, model, X):
    """Per-feature contributions (n, k) and the base value."""
    if kind == "lightgbm":
        out = model.predict(X, pred_contrib=True)
        return out[:, :-1], out[:, -1]
    base = model.predict(X)
    cols = []
    for j in range(X.shape[1]):
        Xj = X.copy()
        Xj[:, j] = np.nan
        cols.append(base - model.predict(Xj))
    return np.stack(cols, axis=1), base - np.stack(cols, axis=1).sum(axis=1)


def ridge(Xtr, ytr, Xte, lam=10.0):
    med = np.nanmedian(Xtr, axis=0)
    med = np.where(np.isnan(med), 0, med)
    def prep(X):
        X = np.where(np.isnan(X), med, X)
        return X
    A, B = prep(Xtr), prep(Xte)
    mu, sd = A.mean(0), A.std(0) + 1e-9
    A, B = (A - mu) / sd, (B - mu) / sd
    A1 = np.hstack([A, np.ones((len(A), 1))])
    w = np.linalg.solve(A1.T @ A1 + lam * np.eye(A1.shape[1]), A1.T @ ytr)
    return np.hstack([B, np.ones((len(B), 1))]) @ w


def spearman(a, b):
    ra = np.argsort(np.argsort(a))
    rb = np.argsort(np.argsort(b))
    return float(np.corrcoef(ra, rb)[0, 1])


def boot_diff(err_a, err_b, n=2000, seed=0):
    """MAE(a) - MAE(b) with a bootstrap 95% CI (negative = a is better)."""
    rng = np.random.default_rng(seed)
    d = err_a - err_b
    bs = [d[rng.integers(0, len(d), len(d))].mean() for _ in range(n)]
    return float(d.mean()), float(np.percentile(bs, 2.5)), float(np.percentile(bs, 97.5))


def genre_baseline(train_rows, test_rows):
    med = {}
    for r in train_rows:
        if r["year"] >= TRAIN_TO - 9:
            g = next((gg for gg in GENRES if r[f"g_{gg}"]), "?")
            med.setdefault(g, []).append(r["actual"])
    allm = float(np.median([r["actual"] for r in train_rows if r["year"] >= TRAIN_TO - 9]))
    med = {g: float(np.median(v)) if len(v) >= 10 else allm for g, v in med.items()}
    return np.array([med.get(next((gg for gg in GENRES if r[f"g_{gg}"]), "?"), allm) for r in test_rows])


def buzz_cv(uni, base_cols, buzz_cols, new, folds=5, seed=0):
    """pf3: 5-fold CV over the 2016-2019 / 2021-2023 films with pageviews; every fold's model is trained on all
    other films (all years). Returns MAE with and without the pageview features, the paired difference + CI."""
    elig = [i for i, r in enumerate(uni) if (TEST[0] <= r["year"] <= TEST[1] or LATE[0] + 1 <= r["year"] <= LATE[1])
            and not math.isnan(r["log_pv_pre28"])]
    if len(elig) < 150:
        return {"n": len(elig), "note": "fewer than 150 films with pageviews and a gross: not tested"}
    rng = np.random.default_rng(seed)
    order = rng.permutation(elig)
    parts = np.array_split(order, folds)
    ea, eb = [], []
    for k in range(folds):
        hold = set(int(i) for i in parts[k])
        trr = [r for i, r in enumerate(uni) if i not in hold]
        ter = [uni[i] for i in parts[k]]
        y, yt = np.array([r["actual"] for r in trr]), np.array([r["actual"] for r in ter])
        for cols, out in ((base_cols, ea), (base_cols + buzz_cols, eb)):
            m = new()
            m.fit(matrix(trr, cols), y)
            out.extend(np.abs(m.predict(matrix(ter, cols)) - yt))
    ea, eb = np.array(ea), np.array(eb)
    d = boot_diff(eb, ea)
    return {"n": len(elig), "mae_without": round(float(ea.mean()), 4), "mae_with": round(float(eb.mean()), 4),
            "diff": d, "adds_information": bool(d[2] < 0)}


# ------------------------------------------------------------------ fit
def cmd_fit(a):
    t0 = time.time()
    c = conn()
    c.execute("drop table if exists perf_film")          # pf2 added a column
    c.executescript(DDL)
    rows = build_table(c)
    uni = [r for r in rows if r["actual"] is not None and r["year"] <= LATE[1]]
    print(f"films with a budget: {len(rows):,}; with budget and gross (training universe): {len(uni):,}")
    kind, new = make_model()
    # a feature with no values at all (e.g. no MovieLens file on this server) is left out of every model
    ALLF = WITH_NEWS + TRAILER + BUZZ + NEWS_SRC + LB
    usable = {k for k in ALLF if any(not (isinstance(r[k], float) and math.isnan(r[k])) for r in uni)}
    dropped = [k for k in ALLF if k not in usable]
    TR_ = [k for k in TRAILER if k in usable]
    BZ_ = [k for k in BUZZ if k in usable]
    NW_ = [k for k in NEWSF + NEWS_SRC if k in usable]          # pf4
    LB_ = [k for k in LB if k in usable]
    if dropped:
        print(f"features with no data, left out: {', '.join(dropped)}")
    BEFORE_, AFTER_, NEWS_, PF1_ = ([k for k in cols if k in usable] for cols in (BEFORE, AFTER, WITH_NEWS, BEFORE_PF1))
    tr = [r for r in uni if r["year"] <= TRAIN_TO]
    te = [r for r in uni if TEST[0] <= r["year"] <= TEST[1]]
    late = [r for r in uni if LATE[0] <= r["year"] <= LATE[1]]
    ytr = np.array([r["actual"] for r in tr])
    yte = np.array([r["actual"] for r in te])
    yla = np.array([r["actual"] for r in late])
    res = {"model": kind, "version": VERSION, "n_universe": len(uni), "n_train": len(tr), "n_test": len(te),
           "n_late": len(late), "fitted_at": F.now(), "features_without_data": dropped}
    preds, imps = {}, {}
    arms = [("before", BEFORE_), ("after", AFTER_), ("news", NEWS_), ("before_no_context", PF1_)]
    if TR_:
        arms.append(("before_trailer", BEFORE_ + TR_))
    if NW_:                                                     # pf4: does news help the BEFORE model predict?
        arms.append(("before_news", BEFORE_ + NW_))
        arms.append(("after_news", AFTER_ + NW_))
    if LB_:
        arms.append(("after_lb", AFTER_ + LB_))
    for name, cols in arms:
        m = new()
        m.fit(matrix(tr, cols), ytr)
        p_te = m.predict(matrix(te, cols))
        p_la = m.predict(matrix(late, cols)) if late else np.array([])
        preds[name] = (p_te, p_la)
        con, _b = contributions(kind, m, matrix(te, cols))
        full = sorted(((cols[j], float(np.abs(con[:, j]).mean())) for j in range(len(cols))), key=lambda x: -x[1])
        imps[name] = full[:12]
        if name == "before":
            res["context_importance"] = [(k, round(v, 4), 1 + [n for n, _ in full].index(k)) for k, v in full if k in CONTEXT]
    base = genre_baseline(tr, te)
    lin = ridge(matrix(tr, BEFORE_), ytr, matrix(te, BEFORE_))
    err = {k: np.abs(v[0] - yte) for k, v in preds.items()}
    err["genre_baseline"] = np.abs(base - yte)
    err["linear_before"] = np.abs(lin - yte)
    res["test"] = {k: {"mae": round(float(e.mean()), 4)} for k, e in err.items()}
    for k in [a for a, _c in arms]:
        res["test"][k]["spearman"] = round(spearman(preds[k][0], yte), 4)
        if len(late):
            res.setdefault("late", {})[k] = {"mae": round(float(np.abs(preds[k][1] - yla).mean()), 4),
                                             "spearman": round(spearman(preds[k][1], yla), 4)}
    res["test"]["genre_baseline"]["spearman"] = round(spearman(base, yte), 4)
    res["test"]["linear_before"]["spearman"] = round(spearman(lin, yte), 4)
    d_base = boot_diff(err["before"], err["genre_baseline"])
    d_lin = boot_diff(err["before"], err["linear_before"])
    d_news = boot_diff(err["news"], err["after"])
    d_ctx = boot_diff(err["before"], err["before_no_context"])
    res["diff"] = {"before_minus_genre": d_base, "before_minus_linear": d_lin, "news_minus_after": d_news,
                   "context_minus_no_context": d_ctx}
    # pf3: trailer timing (main split) and pre-release Wikipedia attention (its own cross-validation)
    trailer_ok = False
    if TR_:
        d_tr = boot_diff(err["before_trailer"], err["before"])
        res["diff"]["trailer_minus_before"] = d_tr
        trailer_ok = d_tr[2] < 0
        res["trailer_coverage"] = {"train": sum(1 for r in tr if not math.isnan(r["trailer_n_pre"])),
                                   "test": sum(1 for r in te if not math.isnan(r["trailer_n_pre"]))}
    if BZ_:
        res["buzz"] = buzz_cv(uni, BEFORE_ + (TR_ if trailer_ok else []), BZ_, new)
    news_pre_ok = news_post_ok = False                          # pf4
    if NW_:
        d_bn = boot_diff(err["before_news"], err["before"])
        d_an = boot_diff(err["after_news"], err["after"])
        res["diff"]["before_news_minus_before"] = d_bn
        res["diff"]["after_news_minus_after"] = d_an
        news_pre_ok, news_post_ok = d_bn[2] < 0, d_an[2] < 0
        res["news_features"] = NW_
        res["news_coverage"] = {k: {"train": sum(1 for r in tr if not math.isnan(r[k])),
                                    "test": sum(1 for r in te if not math.isnan(r[k]))} for k in NW_}
    if LB_:
        res["diff"]["after_lb_minus_after"] = boot_diff(err["after_lb"], err["after"])
        res["lb_coverage"] = {"train": sum(1 for r in tr if not math.isnan(r["lb_rating"])),
                              "test": sum(1 for r in te if not math.isnan(r["lb_rating"]))}
    res["importance"] = imps
    rel = 1 - res["test"]["before"]["mae"] / res["test"]["genre_baseline"]["mae"]
    rule_a = rel >= 0.05 and d_base[2] < 0
    rule_b = res["test"]["before"]["spearman"] >= 0.30
    res["rule"] = {"a_mae_5pct_better_ci": rule_a, "a_rel_improvement": round(rel, 4), "b_spearman": rule_b,
                   "model_part_passes": bool(rule_a and rule_b),
                   "c_answers": "run eval_v3 --category why --answers PERF=0 vs PERF=1 (run_v5.sh perfeval)",
                   "news_adds_information": bool(d_news[2] < 0),
                   "context_adds_information": bool(d_ctx[2] < 0),
                   "trailer_adds_information": bool(trailer_ok),
                   "buzz_adds_information": bool((res.get("buzz") or {}).get("adds_information")),
                   "news_helps_prediction": bool(news_pre_ok), "news_adds_after_reception": bool(news_post_ok)}
    if trailer_ok:                     # rule: adopted into the per-film expectations only when it helps
        BEFORE_ = BEFORE_ + TR_
        AFTER_ = AFTER_ + TR_
        print("trailer timing adds information on 2016-2019: used in the per-film expectations", flush=True)
    if news_pre_ok:                    # pf4 rule: news joins the per-film expectations only when it helps
        BEFORE_ = BEFORE_ + NW_
        print("news at release helps the before-release model on 2016-2019: used in the 'pre' expectations", flush=True)
    if news_post_ok:
        AFTER_ = AFTER_ + [k for k in NW_ if k not in AFTER_]
        print("news at release adds to the after-release model on 2016-2019: used in the 'post' expectations", flush=True)

    # per-film expectations: 5-year blocks, each predicted by a model that did not see its own block
    print("per-film expectations (each 5-year block predicted by a model trained on the others) ...", flush=True)
    store = {}
    blocks = sorted({r["year"] // 5 for r in uni})
    for name, cols in (("pre", BEFORE_), ("post", AFTER_)):
        for blk in blocks:
            trn = [r for r in uni if r["year"] // 5 != blk]
            tst = [r for r in uni if r["year"] // 5 == blk]
            if not tst or len(trn) < 200:
                continue
            m = new()
            m.fit(matrix(trn, cols), np.array([r["actual"] for r in trn]))
            X = matrix(tst, cols)
            p = m.predict(X)
            con, _b = contributions(kind, m, X)
            for i, r in enumerate(tst):
                top = sorted(range(len(cols)), key=lambda j: -abs(con[i, j]))[:6]
                s = store.setdefault(r["tmdb_id"], {"actual": r["actual"], "fold": f"{blk * 5}-{blk * 5 + 4}"})
                s[name] = float(p[i])
                s[name + "_c"] = [[cols[j], round(float(con[i, j]), 4), _val(r[cols[j]])] for j in top]
        # films with a budget but no gross yet (or outside the universe): the model trained on everything
        rest = [r for r in rows if r["tmdb_id"] not in store or name not in store[r["tmdb_id"]]]
        if rest:
            m = new()
            m.fit(matrix(uni, cols), np.array([r["actual"] for r in uni]))
            X = matrix(rest, cols)
            p = m.predict(X)
            con, _b = contributions(kind, m, X)
            for i, r in enumerate(rest):
                top = sorted(range(len(cols)), key=lambda j: -abs(con[i, j]))[:6]
                s = store.setdefault(r["tmdb_id"], {"actual": r["actual"], "fold": "all"})
                s[name] = float(p[i])
                s[name + "_c"] = [[cols[j], round(float(con[i, j]), 4), _val(r[cols[j]])] for j in top]
    ctx = {r["tmdb_id"]: r.get("_ctx") or [] for r in rows}
    c.execute("delete from perf_film")
    c.executemany("insert into perf_film values (?,?,?,?,?,?,?,?,?,?,?)", [
        (fid, VERSION, int(s.get("actual") is not None), s.get("actual"), s.get("pre"), s.get("post"),
         json.dumps(s.get("pre_c")), json.dumps(s.get("post_c")), s["fold"], F.now(), json.dumps(ctx.get(fid, [])))
        for fid, s in store.items()])
    # reasons the sources state, and how often each kind is cited for films that fell short vs beat expectations
    c.commit()
    reasons = cmd_reasons(None, c)
    gap = {fid: s["actual"] - s["pre"] for fid, s in store.items() if s.get("actual") is not None and s.get("pre") is not None}
    groups = {"well below expectation": {f for f, g in gap.items() if g < -0.7},
              "well above expectation": {f for f, g in gap.items() if g > 0.7},
              "about as expected": {f for f, g in gap.items() if abs(g) <= 0.7}}
    with_text = {r[0] for r in c.execute("select distinct tmdb_id from sections where kind in ('box_office','release','reception')")}
    tab = {}
    for gname, fs in groups.items():
        fs = fs & with_text
        cited = {}
        for fid, cat, _d, _s, _k, _u in reasons:
            if fid in fs:
                cited.setdefault(cat, set()).add(fid)
        tab[gname] = {"n": len(fs), "share": {k: round(len(v) / max(len(fs), 1), 3) for k, v in cited.items()}}
    res["reasons_by_group"] = tab
    res["typical_miss_x"] = round(math.exp(res["test"]["before"]["mae"]), 2)
    res["typical_miss_after_x"] = round(math.exp(res["test"]["after"]["mae"]), 2)
    res["seconds"] = round(time.time() - t0)
    c.execute("insert or replace into perf_meta values ('result', ?)", (json.dumps(res),))
    c.commit()
    print(f"stored expectations for {len(store):,} films")
    write_summary(res)


def _val(v):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return None
    return round(float(v), 4)


def write_summary(res):
    t, L = res["test"], []
    L.append(f"Box-office performance model {res['version']} ({res['model']}), fitted {res['fitted_at']}")
    L.append(f"target: log(gross / budget); films with budget and gross: {res['n_universe']:,} "
             f"(train <= {TRAIN_TO}: {res['n_train']:,}; test {TEST[0]}-{TEST[1]}: {res['n_test']:,}; "
             f"{LATE[0]}-{LATE[1]}: {res['n_late']:,})")
    L.append("")
    L.append(f"held-out {TEST[0]}-{TEST[1]}      mean abs error (log)  typical miss   Spearman")
    for k, name in (("genre_baseline", "genre median (baseline)"), ("linear_before", "linear, before release"),
                    ("before_no_context", "trees, before, no context"), ("before", "trees, before release"),
                    ("before_trailer", "trees, before + trailers"), ("before_news", "trees, before + news (pf4)"),
                    ("after", "trees, + reception"), ("news", "trees, + reception + news"),
                    ("after_news", "trees, + reception + news by source"), ("after_lb", "trees, + reception + Letterboxd")):
        if k not in t:
            continue
        L.append(f"  {name:36s} {t[k]['mae']:8.3f}          x{math.exp(t[k]['mae']):5.2f}       {t[k]['spearman']:+.3f}")
    if res.get("late"):
        L.append(f"\n{LATE[0]}-{LATE[1]} (pandemic years, reported separately):")
        for k, v in res["late"].items():
            L.append(f"  {k:8s} MAE {v['mae']:.3f}  Spearman {v['spearman']:+.3f}")
    d = res["diff"]
    L.append("")
    L.append(f"before - genre baseline: {d['before_minus_genre'][0]:+.3f} [{d['before_minus_genre'][1]:+.3f}, "
             f"{d['before_minus_genre'][2]:+.3f}] (negative = model better)")
    L.append(f"before - linear:         {d['before_minus_linear'][0]:+.3f} [{d['before_minus_linear'][1]:+.3f}, "
             f"{d['before_minus_linear'][2]:+.3f}]")
    if "context_minus_no_context" in d:
        L.append(f"with release context - without (pf1): {d['context_minus_no_context'][0]:+.3f} "
                 f"[{d['context_minus_no_context'][1]:+.3f}, {d['context_minus_no_context'][2]:+.3f}]  -> release context adds "
                 f"measurable information: {'YES' if res['rule'].get('context_adds_information') else 'no'}")
    if "trailer_minus_before" in d:
        x = d["trailer_minus_before"]
        L.append(f"with trailer timing - without (pf3): {x[0]:+.3f} [{x[1]:+.3f}, {x[2]:+.3f}]  -> adds measurable "
                 f"information: {'YES (used per film)' if res['rule'].get('trailer_adds_information') else 'no'}"
                 f"  (films with trailer data: train {res.get('trailer_coverage', {}).get('train', '?')}, "
                 f"test {res.get('trailer_coverage', {}).get('test', '?')})")
    if res.get("buzz"):
        b = res["buzz"]
        if "diff" in b:
            L.append(f"pre-release Wikipedia views, 5-fold CV on {b['n']} films 2016-2023: MAE {b['mae_without']:.3f} -> "
                     f"{b['mae_with']:.3f}, diff {b['diff'][0]:+.3f} [{b['diff'][1]:+.3f}, {b['diff'][2]:+.3f}]  -> adds "
                     f"measurable information: {'YES' if b['adds_information'] else 'no'} (reported only)")
        else:
            L.append(f"pre-release Wikipedia views: {b.get('note')} (n={b.get('n')})")
    L.append(f"news - reception only:   {d['news_minus_after'][0]:+.3f} [{d['news_minus_after'][1]:+.3f}, "
             f"{d['news_minus_after'][2]:+.3f}]  -> news adds measurable information: "
             f"{'YES' if res['rule']['news_adds_information'] else 'no'}")
    if "before_news_minus_before" in d:            # pf4
        x, y = d["before_news_minus_before"], d["after_news_minus_after"]
        L.append(f"pf4 before + news (mixed + Wikipedia + NYT) - before: {x[0]:+.3f} [{x[1]:+.3f}, {x[2]:+.3f}]  -> news helps "
                 f"predict success: {'YES (used per film)' if res['rule'].get('news_helps_prediction') else 'no'}")
        L.append(f"pf4 after + news by source - after:  {y[0]:+.3f} [{y[1]:+.3f}, {y[2]:+.3f}]  -> "
                 f"{'YES (used per film)' if res['rule'].get('news_adds_after_reception') else 'no'}")
        cov = res.get("news_coverage") or {}
        L.append("    films with each news feature (train / test): " + ", ".join(
            f"{LABEL.get(k, k)} {v['train']}/{v['test']}" for k, v in cov.items()))
    if "after_lb_minus_after" in d:
        x = d["after_lb_minus_after"]
        L.append(f"pf4 after + Letterboxd - after:      {x[0]:+.3f} [{x[1]:+.3f}, {x[2]:+.3f}]  (reported only; "
                 f"films with Letterboxd data train {res.get('lb_coverage', {}).get('train')} / test "
                 f"{res.get('lb_coverage', {}).get('test')})")
    L.append("")
    if res.get("context_importance"):
        L.append("release-context features (mean effect size, rank among all before-release features): " + ", ".join(
            f"{LABEL.get(k, k)} {v:.3f} (#{rk})" for k, v, rk in res["context_importance"]))
    if res.get("reasons_by_group"):
        L.append("reasons stated in Wikipedia box office / release / reception text, share of films citing each:")
        cats = sorted({k for g in res["reasons_by_group"].values() for k in g["share"]})
        for gname, g in res["reasons_by_group"].items():
            L.append(f"  {gname} (n={g['n']}): " + ", ".join(f"{k} {100 * g['share'].get(k, 0):.0f}%" for k in cats))
    for k in [k for k in ("before", "after", "news", "before_news") if k in res["importance"]]:
        L.append(f"most influential ({k}): " + ", ".join(f"{LABEL.get(n, n)} {v:.3f}" for n, v in res["importance"][k][:8]))
    r = res["rule"]
    L.append("")
    L.append(f"rule (a) MAE >= 5% below baseline with CI excluding 0: {'PASS' if r['a_mae_5pct_better_ci'] else 'FAIL'} "
             f"({100 * r['a_rel_improvement']:.1f}% lower)")
    L.append(f"rule (b) Spearman >= 0.30: {'PASS' if r['b_spearman'] else 'FAIL'}")
    L.append(f"rule (c) answers: {r['c_answers']}")
    L.append(f"model part of the rule: {'PASS' if r['model_part_passes'] else 'FAIL'}")
    L.append("\nLimits: films with both budget and gross are mostly wide releases with Wikipedia/TMDB coverage, so the "
             "model describes those, not every film. Budgets are reported figures (marketing not included). "
             "Contributions are associations across films, not causes.")
    text = "\n".join(L)
    os.makedirs(os.path.dirname(SUMMARY), exist_ok=True)
    open(SUMMARY, "w").write(text + "\n")
    print(text)


def cmd_report(_a):
    c = conn(readonly=True)
    row = c.execute("select value from perf_meta where key = 'result'").fetchone()
    if not row:
        raise SystemExit("not fitted yet: python performance.py fit")
    write_summary(json.loads(row[0]))


# ------------------------------------------------------------------ the block tools.py shows the answer model
def _x(v):
    return f"{math.exp(v):.1f}x"


def _describe(name, val):
    lab = LABEL.get(name, name)
    if val is None:
        return f"{lab}: unknown"
    if name == "horror_days_after_halloween":
        v = int(round(val))
        return f"horror released {abs(v)} days {'after' if v >= 0 else 'before'} Halloween"
    if name == "franchise_gap_years":
        return f"previous film in the series {val:.0f} years earlier"
    if name == "genre_month_ret":
        return f"its genre usually returned {math.exp(val):.1f}x in this release month"
    if name == "author_prev_365_ret":
        return f"the same author's adaptations in the past year returned {math.exp(val):.1f}x"
    if name == "trailer_lead_days":
        return f"first trailer {val:.0f} days before release"
    if name == "trailer_n_pre":
        return f"{val:.0f} trailers/teasers before release"
    if name == "log_pv_pre28":
        return f"{math.expm1(val):,.0f} Wikipedia views in the 4 weeks before release"
    if name == "imdb_rating":
        return f"IMDb {val:.1f}/10"
    if name == "days_to_holiday":
        return f"{val:.0f} days from the nearest big holiday"
    if name in ("same_weekend_log_budget", "prev_10d_log_budget"):
        return f"{lab}: ${math.exp(val) / 1e6:,.0f}M combined" if val else f"{lab}: none"
    if name in ("legacy_sequel", "source_adapted"):
        return lab if val else f"not {lab}"
    if name in ("author_prev_365_n", "author_prev_n", "same_weekend_n", "same_genre_14d_n", "same_genre_prev_30d_n"):
        return f"{lab}: {val:.0f}"
    if name == "log_budget_real":
        return f"budget ${math.exp(val) / 1e6:,.0f}M in today's dollars"
    if name == "month":
        return f"released in {MONTHS[int(val)]}"
    if name.endswith("_ret"):
        who = {"dir_prev_ret": "the director's earlier films", "cast_prev_ret": "the lead actors' earlier films",
               "franchise_prev_ret": "the franchise's earlier films"}.get(name, lab)
        return f"{who} typically returned {math.exp(val):.1f}x their budgets"
    if name.startswith("g_"):
        return f"{lab}" if val else f"not {lab}"
    if name in ("sequel", "lang_en", "us_made"):
        return lab if val else f"not {lab}"
    if name.endswith("_prev_rating"):
        who = {"dir_prev_rating": "the director's earlier films", "cast_prev_rating": "the lead actors' earlier films"}[name]
        return f"{who} averaged {val:.1f}/10 on TMDB"
    if name == "cinemascore":
        return f"CinemaScore {CS_TEXT.get(int(round(val)), '?')}"
    if name == "rt_pct":
        return f"Rotten Tomatoes {val:.0f}%"
    if name == "log_votes":
        return f"{math.expm1(val):,.0f} TMDB votes"
    if name == "runtime":
        return f"{val:.0f} min"
    if name == "year":
        return f"films released around {int(val)} (returns in this data vary by year)"
    if name in ("comp_n", "dir_prev_n", "cast_prev_n", "franchise_prev_n", "awards_won", "awards_nom"):
        return f"{lab}: {val:.0f}"
    return f"{lab} {val:.2f}" if isinstance(val, float) else f"{lab} {val}"


def _factors(clist):
    out = []
    for name, con, val in (clist or [])[:6]:
        if abs(con) < 0.03 or (name.startswith("g_") and not val) or val is None:
            continue          # "not a horror film" and unknown values are not reasons a reader can use
        out.append(f"{_describe(name, val)} ({'raises' if con > 0 else 'lowers'} it, x{math.exp(con):.2f})")
    return out[:5]


def performance_block(fid, title, c=None):
    c = c or F.connect(readonly=True)
    try:
        r = c.execute("select actual, pre, post, pre_contrib, post_contrib, fold, context_json from perf_film "
                      "where tmdb_id = ?", (fid,)).fetchone()
        meta = json.loads((c.execute("select value from perf_meta where key = 'result'").fetchone() or ["{}"])[0])
    except Exception:  # noqa: BLE001
        return None
    if not r or r[1] is None:
        return None
    actual, pre, post, prec, postc, fold, ctxj = r
    miss = meta.get("typical_miss_x")
    n = meta.get("n_universe")
    lines = [f"{title} - Movie Buff's box-office model (a statistical estimate from {n:,} films with budget and gross, "
             f"not a fact; computed)" if n else f"{title} - Movie Buff's box-office model (estimate; computed)"]
    ctx = json.loads(ctxj or "[]")
    if ctx:
        lines.append("Release context (facts from the database, computed): " + " | ".join(ctx[:6]))
    lines.append(f"Expected before release for a film like this: about {_x(pre)} its budget"
                 + (f" (the model's typical miss on films it had not seen: a factor of {miss:.1f} either way)" if miss else ""))
    f = _factors(json.loads(prec or "[]"))
    if f:
        lines.append("What moved that expectation most: " + "; ".join(f))
    if actual is not None:
        gap = actual - pre
        word = ("far above" if gap > 1.0 else "above" if gap > 0.4 else "far below" if gap < -1.0
                else "below" if gap < -0.4 else "close to")
        lines.append(f"Actual: {_x(actual)} its budget - {word} what the model expected before release")
        if post is not None:
            g = _factors(json.loads(postc or "[]"))
            lines.append(f"With its reception added, the model expects {_x(post)}"
                         + ("; biggest factors then: " + "; ".join(g) if g else ""))
            if actual < post - 0.7:
                lines.append("It earned far less than films with a similar reception usually do, so its reviews do not "
                             "explain the result; look at the release context and the reasons sources give")
            elif actual > post + 0.7:
                lines.append("It earned far more than films with a similar reception usually do")
    else:
        lines.append("No final gross stored yet, so there is nothing to compare the expectation with.")
    try:
        want = "below" if (actual is not None and actual < pre) else "above"
        rs = c.execute("select group_concat(category, ', '), direction, sentence, section from perf_reasons "
                       "where tmdb_id = ? group by sentence order by section = 'box_office' desc, section = 'release' desc, "
                       "direction = ? desc, length(sentence) limit 3", (fid, want)).fetchall()
    except Exception:  # noqa: BLE001
        rs = []
    if rs:
        lines.append("Reasons sources give (quoted from Wikipedia; these are stated links, unlike the model): " + " | ".join(
            f'[Wikipedia: {sec.replace("_", " ").title()}; {cats}] "{sent}"' for cats, _d, sent, sec in rs))
    lines.append("How to use this: the model shows patterns across many films, not causes. Say 'the model expected' or "
                 "'films like this usually', mention the typical miss, and give causes only where a quoted source states them.")
    return "\n".join(lines)


def cmd_show(a):
    c = conn(readonly=True)
    r = c.execute("select f.tmdb_id, f.title, f.year from films f join perf_film p on p.tmdb_id = f.tmdb_id "
                  "where f.title like ? order by f.vote_count desc limit 1", (a.title,)).fetchone()
    if not r:
        raise SystemExit("no such film with a model estimate")
    print(performance_block(r[0], f"{r[1]} ({r[2]})", c))


def cmd_remove(_a):
    c = conn()
    c.execute("delete from perf_film")
    c.execute("delete from perf_meta")
    c.execute("delete from perf_reasons")
    c.commit()
    print("performance model results removed (PERF=1 now shows nothing)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["fit", "report", "show", "remove", "reasons"])
    ap.add_argument("title", nargs="?")
    a = ap.parse_args()
    {"fit": cmd_fit, "report": cmd_report, "show": cmd_show, "remove": cmd_remove, "reasons": cmd_reasons}[a.cmd](a)


if __name__ == "__main__":
    main()
