#!/usr/bin/env python3
"""v8.1 slice 4.2 (12 Nov 2026): what box-office data exists, by period. Counts only; read-only; no film is named.

Answers, before any v8.1 model is designed: which money figures exist per source (is there any domestic or
international gross?), how complete each factor group the kit asks about is (director, writers, producers, companies,
cast, certificate, trailers, release dates), and how that changes across the periods the model is trained and tested on.

    python tools/v81_boxoffice_inventory.py            # -> results/v81_boxoffice_inventory.txt
"""
import os
import sqlite3
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
import facts_db as F  # noqa: E402

OUT = os.path.join(HERE, "results", "v81_boxoffice_inventory.txt")
L = []


def say(s=""):
    print(s)
    L.append(s)


def q(c, sql, p=()):
    try:
        return c.execute(sql, p).fetchall()
    except sqlite3.Error as e:
        say(f"  (query failed: {e})")
        return []


def cols(c, t):
    return [r[1] for r in q(c, f"pragma table_info({t})")]


PERIODS = [("<= 1999", 1960, 1999), ("2000-2015", 2000, 2015), ("2016-2019", 2016, 2019), ("2020-2023", 2020, 2023),
           ("2024 on", 2024, 2100)]
UNI = ("select f.tmdb_id from films f join money m on m.tmdb_id = f.tmdb_id "
       "where m.budget >= 100000 and m.gross > 0 and f.year between ? and ?")


def main():
    c = sqlite3.connect(f"file:{F.PATH}?mode=ro", uri=True)
    say(f"Box-office data inventory (v8.1 slice 4.2) from {F.PATH}")
    say("")
    say("1. facts table: rows per field and source (every money figure with its source)")
    for field, source, n, cur in q(c, "select field, source, count(*), count(distinct currency) from facts group by 1, 2 order by 1, 3 desc"):
        say(f"  {str(field):28s} {str(source):22s} {n:8,d} rows, {cur} currencies")
    say("")
    say("2. money table (one row per film)")
    say("  columns: " + ", ".join(cols(c, "money")))
    for label, sql in (("rows", "select count(*) from money"), ("with budget", "select count(*) from money where budget > 0"),
                       ("with gross", "select count(*) from money where gross > 0"),
                       ("with both", "select count(*) from money where budget > 0 and gross > 0"),
                       ("sources disagree (conflict)", "select count(*) from money where conflict = 1")):
        r = q(c, sql)
        say(f"  {label}: {r[0][0]:,}" if r else f"  {label}: ?")
    for name in ("gross_src", "budget_src"):
        say(f"  {name}: " + ", ".join(f"{s} {n:,}" for s, n in q(c, f"select {name}, count(*) from money group by 1 order by 2 desc limit 8")))
    say("")
    say("3. model universe (budget >= $100k, gross > 0) and how complete each factor group is, by period")
    checks = [
        ("US release date", "select count(*) from films where tmdb_id in (%s) and us_release_date is not null and us_release_date != ''"),
        ("release date (any)", "select count(*) from films where tmdb_id in (%s) and release_date is not null and release_date != ''"),
        ("runtime", "select count(*) from films where tmdb_id in (%s) and runtime > 0"),
        ("in a collection (franchise)", "select count(*) from films where tmdb_id in (%s) and collection_id is not null"),
        ("production companies", "select count(*) from films where tmdb_id in (%s) and companies_json is not null and companies_json not in ('', '[]')"),
        ("IMDb id", "select count(*) from films where tmdb_id in (%s) and imdb_id is not null and imdb_id != ''"),
        ("Wikidata id", "select count(*) from films where tmdb_id in (%s) and wikidata_id is not null and wikidata_id != ''"),
        ("director credit", "select count(distinct tmdb_id) from credits where tmdb_id in (%s) and job = 'Director'"),
        ("writing credit", "select count(distinct tmdb_id) from credits where tmdb_id in (%s) and department = 'Writing'"),
        ("producer credit", "select count(distinct tmdb_id) from credits where tmdb_id in (%s) and job in ('Producer', 'Executive Producer')"),
        ("cast credit", "select count(distinct tmdb_id) from credits where tmdb_id in (%s) and kind = 'cast'"),
        ("certificate", "select count(distinct tmdb_id) from certifications where tmdb_id in (%s)"),
        ("trailer / video", "select count(distinct tmdb_id) from film_videos where tmdb_id in (%s)"),
        ("keywords", "select count(distinct tmdb_id) from keywords where tmdb_id in (%s)"),
        ("awards row", "select count(distinct tmdb_id) from awards where tmdb_id in (%s)"),
        ("Wikipedia pageviews", "select count(distinct tmdb_id) from pageviews where tmdb_id in (%s)"),
        ("Guardian pre-release fetch done", "select count(distinct tmdb_id) from film_press_done where tmdb_id in (%s)"),
        ("people-in-the-news row", "select count(distinct tmdb_id) from film_people_news where tmdb_id in (%s)"),
        ("Letterboxd row", "select count(distinct tmdb_id) from letterboxd where tmdb_id in (%s)"),
    ]
    head = "  %-34s" % "" + "".join("%14s" % p[0] for p in PERIODS)
    say(head)
    ns = [q(c, f"select count(*) from ({UNI})", (a, b)) for _l, a, b in PERIODS]
    ns = [r[0][0] if r else 0 for r in ns]
    say("  %-34s" % "films in the universe" + "".join("%14s" % f"{n:,}" for n in ns))
    for label, sql in checks:
        row = []
        for (_l, a, b), n in zip(PERIODS, ns):
            r = q(c, sql % UNI, (a, b))
            row.append("%14s" % (f"{100 * r[0][0] / n:.0f}%" if r and n else "?"))
        say("  %-34s" % label + "".join(row))
    say("")
    say("4. credits: the 25 most common (kind, department, job) triples")
    for kind, dep, job, n in q(c, "select kind, department, job, count(*) from credits group by 1, 2, 3 order by 4 desc limit 25"):
        say(f"  {str(kind):6s} {str(dep):20s} {str(job):28s} {n:9,d}")
    say("")
    say("5. tables whose name suggests release or money data, with their columns")
    for (t,) in q(c, "select name from sqlite_master where type = 'table' and (name like '%release%' or name like '%money%' "
                     "or name like '%cert%' or name like '%perf%' or name like '%macro%' or name like '%cpi%' or name like '%fx%') order by 1"):
        say(f"  {t}: " + ", ".join(cols(c, t)))
    say("")
    say("6. films table: release-date fields filled, by period (all films, not only the universe)")
    for label, a, b in PERIODS:
        r = q(c, "select count(*), sum(release_date is not null and release_date != ''), sum(us_release_date is not null and us_release_date != ''), "
                 "sum(budget > 0), sum(revenue > 0) from films where year between ? and ?", (a, b))
        if r:
            n, rd, us, bu, rv = r[0]
            say(f"  {label:10s} films {n:7,d} | release date {rd or 0:7,d} | US date {us or 0:7,d} | TMDB budget {bu or 0:6,d} | TMDB revenue {rv or 0:6,d}")
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    open(OUT, "w").write("\n".join(L) + "\n")


if __name__ == "__main__":
    main()
