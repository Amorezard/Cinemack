#!/usr/bin/env python3
"""v8.1 slice 1.0 (13 Nov 2026): how clean are Cinemack's film / show identities today? Counts only, read-only.

Kit package 1 asks for one canonical id per film or show with every outside id attached (TMDB, IMDb, Wikidata,
TVmaze) and its source. Before any table is added, this measures the problems such a table must handle:
missing outside ids, one IMDb / Wikidata id on two titles, a film row whose IMDb id is a TV title (or the reverse),
the same title and year twice, aliases pointing at films that do not exist.
    python tools/v81_identity_audit.py          # -> results/v81_identity_audit.txt
"""
import os
import re
import sqlite3
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
import facts_db as F  # noqa: E402

TV_TYPES = ("tvSeries", "tvMiniSeries", "tvEpisode", "tvSpecial", "tvShort")
FILM_TYPES = ("movie", "tvMovie", "video", "short")


def main():
    c = sqlite3.connect(f"file:{F.PATH}?mode=ro", uri=True)
    L = [f"Identity audit (v8.1 slice 1.0) on {F.PATH}", ""]

    def one(label, sql, p=()):
        try:
            v = c.execute(sql, p).fetchone()[0]
        except sqlite3.Error as e:
            v = f"(failed: {e})"
        L.append(f"  {label:62s} {v:,}" if isinstance(v, int) else f"  {label:62s} {v}")
        return v

    L.append("films")
    one("films", "select count(*) from films")
    one("  without IMDb id", "select count(*) from films where imdb_id is null or imdb_id = ''")
    one("  IMDb id not of the form tt + digits", "select count(*) from films where imdb_id != '' and imdb_id not glob 'tt[0-9]*'")
    one("  without Wikidata id", "select count(*) from films where wikidata_id is null or wikidata_id = ''")
    one("  IMDb ids used by more than one film", "select count(*) from (select imdb_id from films where imdb_id != '' group by 1 having count(*) > 1)")
    one("  Wikidata ids used by more than one film", "select count(*) from (select wikidata_id from films where wikidata_id != '' group by 1 having count(*) > 1)")
    one("  IMDb id is a TV title in IMDb's own list", "select count(*) from films f join imdb_titles t on t.tconst = f.imdb_id "
        f"where t.title_type in ({','.join('?' * len(TV_TYPES))})", TV_TYPES)
    one("  IMDb id missing from IMDb's title list (stale or wrong)", "select count(*) from films f where f.imdb_id glob 'tt[0-9]*' "
        "and not exists (select 1 from imdb_titles t where t.tconst = f.imdb_id)")
    one("  IMDb year differs from TMDB year by more than 1", "select count(*) from films f join imdb_titles t on t.tconst = f.imdb_id "
        "where t.start_year is not null and f.year is not null and abs(t.start_year - f.year) > 1")
    one("  without a release date", "select count(*) from films where release_date is null or release_date = ''")
    one("  same title (case-folded) and year as another film", "select coalesce(sum(n), 0) from (select count(*) n from films "
        "where title is not null group by lower(title), year having count(*) > 1)")
    L.append("")
    L.append("TV shows")
    one("shows", "select count(*) from tv_shows")
    one("  without IMDb id", "select count(*) from tv_shows where imdb_id is null or imdb_id = ''")
    one("  without Wikidata id", "select count(*) from tv_shows where wikidata_id is null or wikidata_id = ''")
    one("  IMDb ids used by more than one show", "select count(*) from (select imdb_id from tv_shows where imdb_id != '' group by 1 having count(*) > 1)")
    one("  IMDb id is a film title in IMDb's own list", "select count(*) from tv_shows s join imdb_titles t on t.tconst = s.imdb_id "
        f"where t.title_type in ({','.join('?' * len(FILM_TYPES))})", FILM_TYPES)
    L.append("")
    L.append("films and shows together (the same TMDB number means different things in the two tables)")
    one("  TMDB numbers used by both a film and a show", "select count(*) from films f join tv_shows s on s.tmdb_id = f.tmdb_id")
    one("  film-show pairs sharing an IMDb id", "select count(*) from films f join tv_shows s on s.imdb_id = f.imdb_id where f.imdb_id != ''")
    one("  same title (case-folded) for a film and a show", "select count(*) from (select distinct lower(f.title) from films f "
        "join tv_shows s on lower(s.name) = lower(f.title))")
    L.append("")
    L.append("aliases")
    one("  alias rows", "select count(*) from aliases")
    one("  alias rows pointing at a film id that does not exist", "select count(*) from aliases a where a.tmdb_id is not null "
        "and not exists (select 1 from films f where f.tmdb_id = a.tmdb_id) and not exists (select 1 from tv_shows s where s.tmdb_id = a.tmdb_id)")
    one("  aliases shared by more than one title", "select count(*) from (select norm from aliases group by norm having count(distinct tmdb_id) > 1)")
    L.append("")
    L.append("TVmaze link (logs/tvmaze.db in the LIVE folder, opened read-only, if present)")
    tvm = os.path.expanduser("~/movie-buff/rag/logs/tvmaze.db")
    if os.path.exists(tvm):
        t = sqlite3.connect(f"file:{tvm}?mode=ro", uri=True)
        tabs = [r[0] for r in t.execute("select name from sqlite_master where type = 'table'")]
        L.append("  tables: " + ", ".join(tabs))
        for tab in tabs:
            cols = [r[1] for r in t.execute(f"pragma table_info({tab})")]
            if "tmdb_id" in cols or any(re.search(r"tmdb|imdb", x) for x in cols):
                n = t.execute(f"select count(*) from {tab}").fetchone()[0]
                L.append(f"  {tab}: {n:,} rows; id columns {', '.join(x for x in cols if re.search(r'id$', x))}")
    else:
        L.append("  not found")
    out = os.path.join(HERE, "results", "v81_identity_audit.txt")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    open(out, "w").write("\n".join(L) + "\n")
    print("\n".join(L))


if __name__ == "__main__":
    main()
