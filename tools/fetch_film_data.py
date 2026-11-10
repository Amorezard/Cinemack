"""Film artwork, title logos and US streaming services into facts.db (10 Nov 2026, round 19).

Why: posters were loaded once (fetch_v6_data.py images, 21 Oct) and never re-asked, so a film that had no poster then
kept none, and the nightly TMDB fetch does not store poster paths; streaming services are known for few upcoming
digital releases. One TMDB call per film: /movie/{id}?append_to_response=images,watch/providers.

    python tools/fetch_film_data.py window     films on the US now-playing / upcoming / announced lists or with a US
                                               release from 60 days ago to 120 days ahead (nightly)
    python tools/fetch_film_data.py catalog    [--max 4000] catalogue films with no poster on record, most popular first
    python tools/fetch_film_data.py status

Writes: film_images (poster / backdrop; an existing path is never replaced by an empty one), films.runtime when it was
empty, film_logos (best English or language-free title logo), watch_providers (US rows of the film replaced by TMDB's
current list, which comes from JustWatch; only when TMDB returns a US entry).
"""
import argparse
import datetime as dt
import os
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import tmdb_http as T  # noqa: E402

RAG = Path(__file__).resolve().parent.parent
FACTS = os.environ.get("FACTS_DB", str(RAG / "facts.db"))
DDL = """
create table if not exists film_logos(tmdb_id integer primary key, logo_path text, aspect real, lang text, fetched_at text);
"""


def window_ids(conn):
    today = dt.date.today()
    lo, hi = (today - dt.timedelta(days=60)).isoformat(), (today + dt.timedelta(days=120)).isoformat()
    ids = [r[0] for r in conn.execute(
        "select distinct tmdb_id from lists l where region = 'US' and list_name in ('now_playing', 'upcoming', 'announced') "
        "and fetched_at = (select max(fetched_at) from lists l2 where l2.list_name = l.list_name and l2.region = 'US')")]
    ids += [r[0] for r in conn.execute("select distinct tmdb_id from certifications where region = 'US' and release_type in (2, 3, 4) "
                                       "and substr(release_date, 1, 10) between ? and ?", (lo, hi))]
    return list(dict.fromkeys(ids))


def catalog_ids(conn, cap):
    return [r[0] for r in conn.execute(
        "select f.tmdb_id from films f left join film_images i on i.tmdb_id = f.tmdb_id where (i.tmdb_id is null or i.poster_path is null "
        "or i.poster_path = '') order by coalesce(f.popularity, 0) desc, coalesce(f.vote_count, 0) desc, f.tmdb_id limit ?", (cap,))]


def fetch(fid):
    d = T.get(f"/movie/{fid}", language="en-US", append_to_response="images,watch/providers", include_image_language="en,null")
    return fid, d


def best_logo(d):
    logos = [x for x in ((d.get("images") or {}).get("logos") or []) if x.get("file_path") and x.get("iso_639_1") in ("en", None)]
    if not logos:
        return None
    logos.sort(key=lambda x: (x.get("iso_639_1") != "en", -(x.get("vote_average") or 0), -(x.get("width") or 0)))
    return logos[0]


def store(conn, fid, d, ts, n):
    if d is None:
        return
    have = conn.execute("select poster_path, backdrop_path from film_images where tmdb_id = ?", (fid,)).fetchone()
    p, b = d.get("poster_path"), d.get("backdrop_path")
    if have is None:
        T.insert(conn, "film_images", {"tmdb_id": fid, "poster_path": p, "backdrop_path": b, "fetched_at": ts})
        n["new_rows"] += 1
        n["posters"] += bool(p)
    else:
        newp, newb = p or have[0], b or have[1]
        if (newp, newb) != tuple(have):
            conn.execute("update film_images set poster_path = ?, backdrop_path = ? where tmdb_id = ?", (newp, newb, fid))
            n["posters"] += bool(p and not have[0])
    if d.get("runtime"):
        n["runtimes"] += conn.execute("update films set runtime = ? where tmdb_id = ? and (runtime is null or runtime = 0)",
                                      (d["runtime"], fid)).rowcount
    lg = best_logo(d)
    if lg:
        conn.execute("insert or replace into film_logos values (?,?,?,?,?)", (fid, lg["file_path"], lg.get("aspect_ratio"), lg.get("iso_639_1"), ts))
        n["logos"] += 1
    us = ((d.get("watch/providers") or {}).get("results") or {}).get("US")
    if us:
        conn.execute("delete from watch_providers where tmdb_id = ? and region = 'US'", (fid,))
        for kind in ("flatrate", "free", "ads", "rent", "buy"):
            for x in us.get(kind) or []:
                T.insert(conn, "watch_providers", {"tmdb_id": fid, "region": "US", "kind": kind, "provider_id": x.get("provider_id"),
                                                   "provider_name": x.get("provider_name"), "priority": x.get("display_priority"),
                                                   "display_priority": x.get("display_priority"), "fetched_at": ts})
        n["providers"] += 1


def run(conn, ids, workers):
    n = dict.fromkeys(["new_rows", "posters", "runtimes", "logos", "providers", "failed"], 0)
    t0 = time.time()
    print(f"films to ask: {len(ids):,}", flush=True)
    with ThreadPoolExecutor(workers) as ex:
        futs = [ex.submit(fetch, i) for i in ids]
        for i, f in enumerate(as_completed(futs), 1):
            fid, d = f.result()
            if d is T.FAILED:
                n["failed"] += 1
                continue
            store(conn, fid, d, time.strftime("%Y-%m-%dT%H:%M:%S"), n)
            if i % 200 == 0:
                conn.commit()
            if i % 1000 == 0:
                print(f"  {i:,}/{len(ids):,} ({time.time() - t0:.0f}s)", flush=True)
    conn.commit()
    print(f"done in {time.time() - t0:.0f}s: posters found {n['posters']:,}, new image rows {n['new_rows']:,}, runtimes filled {n['runtimes']:,}, "
          f"logos {n['logos']:,}, films with US services {n['providers']:,}, failed {n['failed']}; requests {T.STATS['requests']:,}", flush=True)


def status(conn):
    q = lambda s, p=(): conn.execute(s, p).fetchone()[0]  # noqa: E731
    ids = window_ids(conn)
    marks = ",".join(str(i) for i in ids) or "0"
    print(f"window films: {len(ids)} | with poster: {q(f'select count(*) from film_images where poster_path is not null and tmdb_id in ({marks})')} "
          f"| with logo: {q(f'select count(*) from film_logos where tmdb_id in ({marks})')} "
          f"| with US streaming (flatrate/free/ads): {q(f'select count(distinct tmdb_id) from watch_providers where region = ? and kind in (?,?,?) and tmdb_id in ({marks})', ('US', 'flatrate', 'free', 'ads'))}")
    print(f"catalogue films without a poster: {q('select count(*) from films f left join film_images i on i.tmdb_id = f.tmdb_id where i.poster_path is null'):,} "
          f"of {q('select count(*) from films'):,}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["window", "catalog", "status"])
    ap.add_argument("--max", type=int, default=4000)
    ap.add_argument("--workers", type=int, default=8)
    a = ap.parse_args()
    conn = sqlite3.connect(FACTS, timeout=60)
    conn.execute("pragma journal_mode=WAL")
    conn.executescript(DDL)
    if a.cmd == "window":
        run(conn, window_ids(conn), a.workers)
    elif a.cmd == "catalog":
        run(conn, catalog_ids(conn, a.max), a.workers)
    status(conn)


if __name__ == "__main__":
    main()
