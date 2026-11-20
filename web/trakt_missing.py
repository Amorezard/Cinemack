"""v8.1 round 55 (20 Nov 2026): the nightly Trakt sync - history, and ratings / watchlist "only add what is missing".

Owner, 20 Nov: YES to a nightly Trakt sync. The Sync button (web/trakt_sync.sync) writes over what is in Cinemack: a
Trakt rating replaces the person's own review with an empty one, and every Trakt watchlist film is set back to
"want" even if they took it off the list here. Run every night that would undo their own changes, so the night job
uses these rules instead:

  history    as the button: rows carry Trakt's own ids, importing twice changes nothing.
  ratings    a Trakt rating is added only when Cinemack has NO rating or review of that film for this person, and
             only the first time Trakt shows it (table seen_items). Changing or deleting it here is never undone.
  watchlist  a Trakt watchlist film is added as "want" only when the person has NO choice for that film here
             (not on the list, not liked / not for me / not seen), and only the first time Trakt shows it - so a
             film they removed in Cinemack does not come back the next night.

    sync_missing(T, user)   -> {"history": ..., "ratings_added": n, "watchlist_added": n, "skipped": n}
"""
from __future__ import annotations

import time

DDL = """create table if not exists seen_items(user_key text, kind text, tmdb_id integer, first_seen text,
           primary key (user_key, kind, tmdb_id))"""


def _seen(T, user, kind, ids):
    with T._lock:
        c = T.conn()
        c.execute(DDL)
        if not ids:
            return set()
        marks = ",".join("?" * len(ids))
        return {r[0] for r in c.execute(f"select tmdb_id from seen_items where user_key = ? and kind = ? and tmdb_id in ({marks})",
                                        [user, kind, *ids])}


def _mark(T, user, kind, ids):
    if not ids:
        return
    with T._lock:
        c = T.conn()
        c.execute(DDL)
        now = time.strftime("%Y-%m-%dT%H:%M:%S")
        c.executemany("insert or ignore into seen_items values (?,?,?,?)", [(user, kind, int(i), now) for i in ids])
        c.commit()


def _has_review(social, user, tid):
    return social.conn().execute("select 1 from reviews where user_key = ? and tmdb_id = ?", (user, int(tid))).fetchone() is not None


def sync_missing(T, user, social=None, film_info=None):
    if social is None:
        import social
    if film_info is None:
        import film_info
    res = {"ratings_added": 0, "watchlist_added": 0, "skipped": 0}
    items = T._get_all(user, "/sync/history/movies") + T._get_all(user, "/sync/history/episodes")
    res["history"] = T._import_by_id(user, T._rows_history(items))

    rated = []
    for it in T._get_all(user, "/sync/ratings/movies"):
        tid = ((it.get("movie") or {}).get("ids") or {}).get("tmdb")
        if tid and it.get("rating"):
            rated.append((int(tid), it["rating"]))
    seen = _seen(T, user, "rating", [t for t, _ in rated])
    for tid, r in rated:
        if tid in seen or _has_review(social, user, tid):
            res["skipped"] += 1
            continue
        social.save_review(user, tid, max(0.5, min(5.0, round(r / 2 * 2) / 2)), "")
        res["ratings_added"] += 1
    _mark(T, user, "rating", [t for t, _ in rated])

    wl = []
    for it in T._get_all(user, "/sync/watchlist/movies"):
        m = it.get("movie") or {}
        tid = (m.get("ids") or {}).get("tmdb")
        if tid:
            wl.append((int(tid), (m.get("title") or "")[:200]))
    seen = _seen(T, user, "watchlist", [t for t, _ in wl])
    have = film_info.user_choices(user, [t for t, _ in wl]) if wl else {}
    for tid, title in wl:
        if tid in seen or tid in have:
            res["skipped"] += 1
            continue
        film_info.choose(user, tid, title, "want")
        res["watchlist_added"] += 1
    _mark(T, user, "watchlist", [t for t, _ in wl])

    with T._lock:
        c = T.conn()
        c.execute("update tokens set last_sync = ? where user_key = ?", (time.strftime("%Y-%m-%dT%H:%M:%S"), user))
        c.commit()
    return res
