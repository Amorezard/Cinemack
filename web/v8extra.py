"""
v8 redesign (10 Nov 2026): the data behind the five redesigned pages that the earlier v8 routes did not cover.

  * TV progress the person records ("watched through S2 E4", "finished season 2"), merged with episodes imported from
    Netflix / Trakt, gives Continue Watching. Watchlist and Profile read the same endpoint, so they always agree.
  * Upcoming from your watchlist: dated US theatrical / digital releases of saved films, dated next seasons of followed
    shows, and "date not announced" when TMDB lists a season or film without a date. Nothing is guessed.
  * Per-film alert opt-out (alert_mute); followed shows keep their own notify switch (tv_follow.notify).
  * Header search over films and shows, chat rename, and one profile summary (stats, taste, favourites, activity).

Everything is per person (user_of(request)); no route takes another person's key. Friends' data still goes only through
/api/v8/friend/{key}, which checks friendship and per-category privacy (social.can_view).
Episode counts per season are not in facts.db, so no completion percentage is ever computed for an episode; the only
bar shown is seasons finished out of seasons aired, which is known.
"""
import datetime
import json
import sqlite3
import time

import film_info
import history
import tmdb_extra
import tvdata   # round 21: episodes, show pages, TV picks



def _dlock():
    import discovery
    return discovery._lock
RELEASE_LABEL = {"theatrical": "In theaters", "digital": "Streaming", "us_release": "US release", "tv_season": "New season"}


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _today():
    return datetime.date.today().isoformat()


def _facts(sql, params=()):
    f = film_info._ro(film_info.FACTS)
    if f is None:
        return []
    try:
        return f.execute(sql, params).fetchall()
    except sqlite3.Error:
        return []


def _marks(ids):
    return ",".join(str(int(i)) for i in ids) or "0"


# ------------------------------------------------------------------ tables
def _pdb():
    c = history.conn()
    c.execute("create table if not exists tv_progress(user_key text, show_id integer, season integer, episode integer, "
              "finished integer default 0, updated_at text, primary key (user_key, show_id))")
    return c


def _mdb():
    import discovery
    c = discovery._adb()
    c.execute("create table if not exists alert_mute(user_key text, tmdb_id integer, created_at text, "
              "primary key (user_key, tmdb_id))")
    return c


def muted(user):
    if not user:
        return set()
    with _dlock():
        return {r[0] for r in _mdb().execute("select tmdb_id from alert_mute where user_key = ?", (user,))}


def set_mute(user, film_id, mute=True):
    if not user:
        raise ValueError("no user")
    with _dlock():
        c = _mdb()
        if mute:
            c.execute("insert or ignore into alert_mute values (?,?,?)", (user, int(film_id), _now()))
        else:
            c.execute("delete from alert_mute where user_key = ? and tmdb_id = ?", (user, int(film_id)))
        c.commit()


def move_user(old, new):
    with history._lock:
        c = _pdb()
        c.execute("update or ignore tv_progress set user_key = ? where user_key = ?", (new, old))
        c.execute("delete from tv_progress where user_key = ?", (old,))
        c.commit()
    with _dlock():
        m = _mdb()
        m.execute("update or ignore alert_mute set user_key = ? where user_key = ?", (new, old))
        m.execute("delete from alert_mute where user_key = ?", (old,))
        m.commit()
    try:                                                  # round 27: a guest's banner moves into the new account
        p, _t = banner_file(old)
        if p and not banner_file(new)[0]:
            BANNER_DIR.mkdir(parents=True, exist_ok=True)
            p.replace(_banner_path(new).with_suffix(p.suffix))
    except Exception as e:  # noqa: BLE001
        print(f"[cinemack] banner move failed: {e!r}", flush=True)


def delete_user(user):
    with history._lock:
        c = _pdb()
        c.execute("delete from tv_progress where user_key = ?", (user,))
        c.commit()
    with _dlock():
        m = _mdb()
        m.execute("delete from alert_mute where user_key = ?", (user,))
        m.commit()
    banner_delete(user)                                   # round 27


# ------------------------------------------------------------------ TV progress
def _seasons(show_ids):
    """{show_id: [(season_number, air_date or '')]} for seasons > 0, in order."""
    out = {}
    for sid, sn, d in _facts(f"select tmdb_id, season_number, air_date from tv_seasons where tmdb_id in ({_marks(show_ids)}) "
                             f"and season_number > 0 order by season_number"):
        out.setdefault(sid, []).append((sn, (d or "")[:10]))
    for sid, ss in tmdb_extra.seasons([i for i in show_ids if i not in out]).items():   # 10 Nov, round 18: shows from Home's TV rows
        out[sid] = ss
    return out


def _shows(show_ids):
    rows = _facts(f"select tmdb_id, name, first_year, poster_path, networks_json from tv_shows where tmdb_id in ({_marks(show_ids)})")
    out = {}
    for i, n, y, p, nets in rows:
        try:
            net = ", ".join(json.loads(nets or "[]")[:2])
        except (ValueError, TypeError):
            net = ""
        out[i] = {"id": i, "title": n, "year": y, "network": net, "media": "tv",
                  "poster": film_info.img(p, "w342") if p else None}
    for i, m in tmdb_extra.show_meta([x for x in show_ids if x not in out]).items():
        out[i] = {"id": i, "title": m["title"], "year": m["year"], "network": ", ".join(m["networks"][:2]), "media": "tv",
                  "poster": film_info.img(m["poster_path"], "w342")}
    return out


class ProgressError(ValueError):
    pass


def set_progress(user, show_id, season=None, episode=None, finished=False, clear=False):
    """Record how far the person has watched. `episode` None with finished=True means the whole season is done."""
    if not user:
        raise ProgressError("No browser id.")
    show_id = int(show_id)
    if not _shows([show_id]):
        raise ProgressError("Unknown show.")
    with history._lock:
        c = _pdb()
        if clear:
            c.execute("delete from tv_progress where user_key = ? and show_id = ?", (user, show_id))
            c.commit()
            return None
    ss = _seasons([show_id]).get(show_id, [])
    top = max([n for n, _d in ss] or [0])
    try:
        season = int(season)
        episode = int(episode) if episode not in (None, "") else None
    except (TypeError, ValueError):
        raise ProgressError("Season and episode must be numbers.")
    if season < 1 or (top and season > top):
        raise ProgressError(f"This show has {top} season{'s' if top != 1 else ''} on record." if top else "Season must be 1 or more.")
    if episode is not None and not (1 <= episode <= 500):
        raise ProgressError("Episode must be between 1 and 500.")
    if episode is None and not finished:
        raise ProgressError("Give an episode, or mark the season finished.")
    with history._lock:
        c = _pdb()
        c.execute("insert or replace into tv_progress values (?,?,?,?,?,?)",
                  (user, show_id, season, episode, int(bool(finished)), _now()))
        c.commit()
    return {"season": season, "episode": episode, "finished": bool(finished)}


def _next_item(nx):
    d = nx.get("date")
    lab = f"S{nx['season']} E{nx['episode']}"
    out = {"season": nx["season"], "episode": nx["episode"], "title": nx.get("title"), "date": d, "label": lab}
    if not nx.get("aired"):
        out["upcoming"] = True
        out["label"] = f"{lab} airs {datetime.date.fromisoformat(d).strftime('%b %-d')}" if d else f"{lab}: date not announced"
    return out


def continue_watching(user, limit=24):
    """Shows the person is part-way through. Sources, in order of trust: their own recorded progress, then the
    latest imported episode (Netflix gives the season and the episode title, never the episode number)."""
    if not user:
        return []
    with history._lock:
        prog = {r[0]: {"season": r[1], "episode": r[2], "finished": bool(r[3]), "at": r[4]} for r in _pdb().execute(
            "select show_id, season, episode, finished, updated_at from tv_progress where user_key = ?", (user,))}
    imp = {}
    for sid, sn, ep, d, src in history.conn().execute(
            "select tmdb_id, season, episode_title, watched_at, source from watch_events where user_key = ? and "
            "media_type = 'episode' order by coalesce(watched_at, '') desc, rowid desc", (user,)):
        if sid not in imp:
            imp[sid] = {"season": sn, "title": ep, "date": (d or "")[:10], "source": src}
    ids = list(dict.fromkeys(list(prog) + list(imp)))
    if not ids:
        return []
    shows, seasons = _shows(ids), _seasons(ids)
    try:
        import discovery
        follow = {x["id"]: x for x in discovery.tv_following(user)}
    except Exception:  # noqa: BLE001
        follow = {}
    today = _today()
    out = []
    for sid in ids:
        sh = shows.get(sid)
        if not sh:
            continue
        ss = seasons.get(sid, [])
        aired = [n for n, d in ss if d and d <= today]
        future = [(n, d) for n, d in ss if d and d > today]
        undated = [n for n, d in ss if not d and n > max(aired or [0])]
        p, i = prog.get(sid), imp.get(sid)
        use_prog = p and (not i or not i["date"] or p["at"][:10] >= i["date"])
        item = dict(sh, following=sid in follow, notify=bool(follow.get(sid, {}).get("notify")), seasons_out=len(aired))
        if use_prog:
            s, e = p["season"], p["episode"]
            item["last"] = {"season": s, "episode": e, "finished": p["finished"], "at": p["at"][:10], "source": "you",
                            "label": f"Finished season {s}" if p["finished"] else f"S{s} E{e}"}
            if not p["finished"]:
                item["next"] = {"season": s, "episode": e + 1, "label": f"S{s} E{e + 1}"}
                nx = tvdata.next_after(sid, s, e)                 # round 21: the real next episode, with its date
                if nx:
                    item["next"] = _next_item(nx)
                done = s - 1
            else:
                done = s
                nxt = s + 1
                nx = tvdata.next_after(sid, s, 10 ** 6)
                if nx and nx["season"] == nxt:
                    item["next"] = _next_item(nx)
                elif nxt in aired:
                    item["next"] = {"season": nxt, "episode": 1, "label": f"S{nxt} E1"}
                else:
                    fut = next((d for n, d in future if n == nxt), None)
                    item["next"] = ({"season": nxt, "date": fut, "label": f"Season {nxt} starts", "upcoming": True} if fut else
                                    {"season": nxt, "label": f"Season {nxt}: date not announced", "upcoming": True}
                                    if nxt in undated else {"label": "You're caught up", "caught_up": True})
        else:
            s = i["season"]
            item["last"] = {"season": s, "episode": None, "title": i["title"], "at": i["date"], "source": i["source"],
                            "label": (f"S{s}" if s else "Episode") + (f" · {i['title']}" if i["title"] else "")}
            item["next"] = None                      # unknown: the import has no episode numbers
            done = None                              # and no proof that earlier seasons were finished
            m = tvdata.episode_by_title(sid, s, i["title"])   # round 21: TMDB's episode list gives the number
            if m:
                item["last"].update(season=m[0], episode=m[1], label=f"S{m[0]} E{m[1]}" + (f" · {i['title']}" if i["title"] else ""))
                nx = tvdata.next_after(sid, m[0], m[1])
                item["next"] = _next_item(nx) if nx else {"label": "You're caught up", "caught_up": True}
                done = m[0] - 1
        if aired and done is not None:
            item["seasons_bar"] = {"done": max(0, min(done, len(aired))), "total": len(aired)}
        item["updated"] = item["last"]["at"]
        if item["next"] and item["next"].get("caught_up") and not future and not undated:
            item["caught_up"] = True
        out.append(item)
    out.sort(key=lambda x: (not x.get("caught_up", False), x["updated"] or ""), reverse=True)
    return out[:limit]


# ------------------------------------------------------------------ upcoming from the watchlist
def upcoming(user):
    if not user:
        return {"items": []}
    import discovery
    wl = film_info.watchlist(user, 500)
    ids = [w["id"] for w in wl]
    today = _today()
    mute = muted(user)
    items = []
    if ids:
        best = {}
        for fid, typ, d in _facts(f"select tmdb_id, release_type, substr(release_date, 1, 10) from certifications where "
                                  f"region = 'US' and release_type in (2, 3, 4) and tmdb_id in ({_marks(ids)})"):
            kind = "digital" if typ == 4 else "theatrical"
            best.setdefault(fid, {}).setdefault(kind, []).append(d)
        rel = dict(_facts(f"select tmdb_id, substr(coalesce(us_release_date, release_date), 1, 10) from films "
                          f"where tmdb_id in ({_marks(ids)})"))
        provs = discovery._providers(ids)
        cards = {c["id"]: c for c in discovery.cards(ids, user)}
        for fid in ids:
            c = cards.get(fid)
            if not c:
                continue
            k = best.get(fid, {})
            th, dg = sorted(k.get("theatrical", [])), sorted(k.get("digital", []))
            out_th = th and th[0] <= today
            ev = None
            if th and th[0] > today:
                ev = ("theatrical", th[0])
            elif dg and dg[0] > today and not provs.get(fid):
                ev = ("digital", dg[0])
            elif not th and not dg and rel.get(fid) and rel[fid] > today:
                ev = ("us_release", rel[fid])
            elif not th and not dg and not rel.get(fid):
                ev = ("us_release", None)            # TMDB has no date at all: "date not announced"
            if not ev or (out_th and ev[0] == "us_release"):
                continue
            items.append({"media": "movie", "id": fid, "film": c, "kind": ev[0], "date": ev[1],
                          "badge": RELEASE_LABEL[ev[0]], "notify": fid not in mute,
                          "confirmed": ev[0] in ("theatrical", "digital") and bool(ev[1])})
    for sh in discovery.tv_following(user):
        ss = _seasons([sh["id"]]).get(sh["id"], [])
        aired = max([n for n, d in ss if d and d <= today] or [0])
        if sh.get("next_season"):
            items.append({"media": "tv", "id": sh["id"], "show": sh, "kind": "tv_season", "date": sh["next_season"]["date"],
                          "season": sh["next_season"]["number"], "badge": "New season", "notify": sh["notify"], "confirmed": True})
        else:
            und = [n for n, d in ss if not d and n > aired]
            if und:
                items.append({"media": "tv", "id": sh["id"], "show": sh, "kind": "tv_season", "date": None, "season": und[0],
                              "badge": "New season", "notify": sh["notify"], "confirmed": False})
    seen, uniq = set(), []
    for it in sorted(items, key=lambda x: (x["date"] is None, x["date"] or "")):
        k = (it["media"], it["id"])
        if k not in seen:
            seen.add(k)
            uniq.append(it)
    return {"items": uniq, "note": "Dates come from TMDB (US). Undated releases say so; nothing is estimated."}


# ------------------------------------------------------------------ search
def search(q, user="", films=6, shows=4):
    q = (q or "").strip()
    if len(q) < 2:
        return {"films": [], "shows": []}
    like = f"%{q}%"
    fr = _facts("select tmdb_id from films where title like ? or original_title like ? "
                "order by (lower(title) = lower(?)) desc, (title like ?) desc, coalesce(popularity, 0) desc, "
                "coalesce(vote_count, 0) desc limit ?", (like, like, q, f"{q}%", films))
    import discovery
    out_f = discovery.cards([r[0] for r in fr], user)
    sr = _facts("select tmdb_id from tv_shows where name like ? order by (lower(name) = lower(?)) desc, "
                "coalesce(vote_count, 0) desc limit ?", (like, q, shows))
    sh = _shows([r[0] for r in sr])
    try:
        mine = {x["id"] for x in discovery.tv_following(user)} if user else set()
    except Exception:  # noqa: BLE001
        mine = set()
    out_s = [dict(sh[r[0]], following=r[0] in mine) for r in sr if r[0] in sh]
    return {"films": out_f, "shows": out_s}


# ------------------------------------------------------------------ chats
def rename_chat(user, conv_id, title):
    import chats
    title = " ".join((title or "").split())[:80]
    if not title:
        raise ValueError("Give the chat a name.")
    uid, cid = chats._clean_id(user), chats._clean_id(conv_id, 40)
    with chats._lock:
        c = chats.conn()
        n = c.execute("update chats set title = ? where conv_id = ? and user_id = ?", (title, cid, uid)).rowcount
        c.commit()
    if not n:
        raise LookupError("No such chat.")
    return title


# ------------------------------------------------------------------ profile summary
def _film_meta(ids):
    out = {}
    for fid, t, y, g, rt in _facts(f"select tmdb_id, title, year, genres_json, runtime from films where tmdb_id in ({_marks(ids)})"):
        try:
            gs = json.loads(g or "[]")
        except (ValueError, TypeError):
            gs = []
        out[fid] = {"title": t, "year": y, "genres": gs, "runtime": rt}
    imgs = {r[0]: (r[1], r[2]) for r in _facts(f"select tmdb_id, poster_path, backdrop_path from film_images "
                                               f"where tmdb_id in ({_marks(ids)})")}
    for fid, m in out.items():
        p, b = imgs.get(fid, (None, None))
        m["poster"] = film_info.img(p, "w342") if p else None
        m["backdrop"] = film_info.img(b, "w1280") if b else None
    return out


def _signals(user):
    rows = []
    try:
        with film_info._lock:
            rows = film_info._users().execute("select tmdb_id, signal, created_at from film_prefs where user_id = ?",
                                              (user,)).fetchall()
    except Exception:  # noqa: BLE001
        pass
    return rows


def summary(user, activity=20):
    import discovery
    import social
    hist = history.history(user, 500)
    st = history.stats(user)
    reviews = social.reviews_of(user)
    wl = film_info.watchlist(user, 500)
    sig = _signals(user)
    follows = discovery.tv_following(user)
    rated = [r for r in reviews if r.get("rating") is not None]
    written = [r for r in reviews if (r.get("text") or "").strip()]
    stats = {"films": st["films"], "shows": st["shows"], "episodes": st["episodes"], "ratings": len(rated),
             "reviews": len(written), "watchlist": len(wl), "following": len(follows),
             "liked": sum(1 for _i, s, _d in sig if s and s > 0),
             "avg_rating": round(sum(r["rating"] for r in rated) / len(rated), 1) if rated else None}
    watched_films = [x["id"] for x in hist if x["media"] == "movie"]
    liked = [i for i, s, _d in sig if s and s > 0]
    disliked = {i for i, s, _d in sig if s and s < 0} | {r["id"] for r in rated if r["rating"] <= 2}
    good = [r["id"] for r in rated if r["rating"] >= 3.5]
    basis_ids = [i for i in dict.fromkeys(watched_films + liked + good) if i not in disliked]
    meta = _film_meta(list(set(basis_ids) | {r["id"] for r in reviews} | {x["id"] for x in hist if x["media"] == "movie"}
                          | set(liked) | {w["id"] for w in wl}))
    weight = {}
    for i in watched_films:
        weight[i] = weight.get(i, 0) + 1
    for i in liked + good:
        weight[i] = weight.get(i, 0) + 1
    gcount, total = {}, 0
    for i in basis_ids:
        for g in (meta.get(i) or {}).get("genres", []):
            gcount[g] = gcount.get(g, 0) + weight.get(i, 1)
            total += weight.get(i, 1)
    n_basis = sum(1 for i in basis_ids if (meta.get(i) or {}).get("genres"))
    inferred = [{"genre": g, "pct": round(100 * n / total)} for g, n in sorted(gcount.items(), key=lambda x: -x[1])[:6]] if total else []
    try:
        import user_prefs
        settings = user_prefs.load(user)["settings"]
    except Exception:  # noqa: BLE001
        settings = {}
    explicit = {"decades": settings.get("decades") or [], "languages": settings.get("languages") or [],
                "never": settings.get("never_genres") or [], "services": settings.get("services") or []}
    try:
        with film_info._lock:
            told = [t for t, p in film_info._users().execute(
                "select text, polarity from stated_prefs where user_id = ? order by created_at desc limit 12", (user,)) if p and p > 0]
    except Exception:  # noqa: BLE001
        told = []
    explicit["told"] = told[:5]
    taste = {"inferred": inferred, "basis": n_basis, "enough": n_basis >= 5, "explicit": explicit}
    # favourites: never invented. Highest-rated first; otherwise the films they gave a thumbs-up.
    fav_label, fav = None, []
    top = sorted([r for r in rated if r["rating"] >= 4], key=lambda r: (-r["rating"], r.get("updated") or ""))
    if top:
        fav_label = "Your highest rated"
        fav = [dict(id=r["id"], rating=r["rating"], **{k: (meta.get(r["id"]) or {}).get(k) for k in ("title", "year", "poster")})
               for r in top[:12] if meta.get(r["id"])]
    elif liked:
        fav_label = "Films you liked"
        fav = [dict(id=i, rating=None, **{k: (meta.get(i) or {}).get(k) for k in ("title", "year", "poster")})
               for i in liked[:12] if meta.get(i)]
    # activity: real rows only
    acts = []
    for x in hist[:200]:
        if x["media"] == "movie":
            acts.append({"kind": "watched", "id": x["id"], "media": "movie", "title": x["title"], "year": x["year"],
                         "at": x["last"] or "", "source": x["source"]})
        else:
            acts.append({"kind": "watched_tv", "id": x["id"], "media": "tv", "title": x["title"], "at": x["last"] or "",
                         "episodes": x.get("episodes"), "source": x["source"]})
    for r in reviews:
        acts.append({"kind": "reviewed" if (r.get("text") or "").strip() else "rated", "id": r["id"], "media": "movie",
                     "title": (meta.get(r["id"]) or {}).get("title") or "", "year": (meta.get(r["id"]) or {}).get("year"),
                     "rating": r.get("rating"), "at": r.get("updated") or ""})
    for sh in follows:
        acts.append({"kind": "followed", "id": sh["id"], "media": "tv", "title": sh["title"], "at": sh.get("followed") or ""})
    for w in wl:
        acts.append({"kind": "saved", "id": w["id"], "media": "movie", "title": w["title"], "at": w.get("added") or ""})
    acts = [a for a in acts if a["at"]]
    acts.sort(key=lambda a: a["at"], reverse=True)
    posters = {}
    for a in acts[:max(1, activity)]:
        if a["media"] == "movie" and meta.get(a["id"]):
            a["poster"] = meta[a["id"]]["poster"]
        elif a["media"] == "tv":
            posters.setdefault("tv", set()).add(a["id"])
    tvp = _shows(list(posters.get("tv", [])))
    for a in acts[:max(1, activity)]:
        if a["media"] == "tv" and a["id"] in tvp:
            a["poster"] = tvp[a["id"]]["poster"]
    banner = None
    bp, _bt = banner_file(user)
    if bp:
        banner = {"custom": True, "from": "your upload", "v": int(bp.stat().st_mtime)}
    for i in ([] if banner else [f["id"] for f in fav] + watched_films):
        b = (meta.get(i) or {}).get("backdrop")
        if b:
            banner = {"url": b, "from": meta[i]["title"]}
            break
    rv = [dict(r, **{k: (meta.get(r["id"]) or {}).get(k) for k in ("title", "year", "poster", "genres")}) for r in reviews]
    hist_out = []
    tvh = _shows([x["id"] for x in hist if x["media"] == "tv"])
    for x in hist:
        y = dict(x)
        y["poster"] = (meta.get(x["id"]) or {}).get("poster") if x["media"] == "movie" else (tvh.get(x["id"]) or {}).get("poster")
        hist_out.append(y)
    return {"stats": stats, "taste": taste, "favorites": {"label": fav_label, "items": fav},
            "activity": acts[:activity], "activity_total": len(acts), "banner": banner, "reviews": rv, "history": hist_out}


# ------------------------------------------------------------------ routes
# ------------------------------------------------------------------ round 27: custom profile banner
import base64
import hashlib
import os
from pathlib import Path

BANNER_DIR = Path(os.environ.get("BANNER_DIR", str(Path(__file__).resolve().parent.parent / "logs" / "banners")))
BANNER_MAX = 2_000_000                      # bytes after decoding
_MAGIC = ((b"\xff\xd8\xff", "jpg", "image/jpeg"), (b"\x89PNG\r\n\x1a\n", "png", "image/png"),
          (b"RIFF", "webp", "image/webp"))


def _banner_path(user):
    """One file per person, named by a hash of their key (no names or ids on disk)."""
    h = hashlib.sha256(("cinemack-banner|" + str(user)).encode()).hexdigest()[:32]
    for _m, ext, _t in _MAGIC:
        p = BANNER_DIR / f"{h}.{ext}"
        if p.exists():
            return p
    return BANNER_DIR / h


def banner_save(user, data_url):
    """data:image/...;base64,... -> saved; returns (ok, message). Only JPEG / PNG / WebP by their first bytes."""
    if not user:
        return False, "No user."
    try:
        raw = base64.b64decode(str(data_url).split(",", 1)[-1], validate=True)
    except (ValueError, TypeError):
        return False, "That file could not be read."
    if len(raw) > BANNER_MAX:
        return False, "Images up to 2 MB, please."
    kind = next(((ext, t) for m, ext, t in _MAGIC if raw.startswith(m) and (ext != "webp" or raw[8:12] == b"WEBP")), None)
    if not kind:
        return False, "Use a JPEG, PNG or WebP image."
    BANNER_DIR.mkdir(parents=True, exist_ok=True)
    banner_delete(user)
    p = _banner_path(user).with_suffix("." + kind[0]) if _banner_path(user).suffix == "" else _banner_path(user)
    tmp = p.with_suffix(".tmp")
    tmp.write_bytes(raw)
    tmp.replace(p)
    return True, "Banner saved."


def banner_delete(user):
    p = _banner_path(user)
    if p.suffix and p.exists():
        p.unlink()
        return True
    return False


def banner_file(user):
    p = _banner_path(user)
    if p.suffix and p.exists():
        return p, next(t for _m, ext, t in _MAGIC if ext == p.suffix[1:])
    return None, None


def install(app, user_of, gate):
    from fastapi import Request
    from fastapi.responses import JSONResponse
    from pydantic import BaseModel
    import asyncio

    def run(fn, *a):
        return asyncio.get_running_loop().run_in_executor(None, fn, *a)

    class Progress(BaseModel):
        id: int
        season: int | None = None
        episode: int | None = None
        finished: bool = False
        clear: bool = False

    class Mute(BaseModel):
        id: int
        muted: bool = True

    class Rename(BaseModel):
        id: str
        title: str

    @app.get("/api/v8/show/{sid}")
    async def v8_show(sid: int, request: Request):
        if (g := gate(request)):
            return g
        d = await run(tvdata.show, sid, user_of(request))
        return JSONResponse(d) if d else JSONResponse({"error": "No details for that show yet."}, status_code=404)

    @app.get("/api/v8/show/{sid}/season/{n}")
    async def v8_season(sid: int, n: int, request: Request):
        if (g := gate(request)):
            return g
        return JSONResponse({"episodes": await run(tvdata.episodes, sid, n)})

    @app.get("/api/v8/pick/tv")
    async def v8_pick_tv(request: Request, genre: str = "", max_cert: str = "", max_runtime: int = 0, services: int = 0,
                         novelty: str = "familiar", exclude: str = ""):
        if (g := gate(request)):
            return g
        import user_prefs
        key = user_of(request)
        st = user_prefs.load(key)["settings"] if key else {}
        ex = [int(x) for x in exclude.split(",") if x.strip().isdigit()][:200]
        return JSONResponse(await run(lambda: tvdata.pick(key, genre if genre in user_prefs.GENRES else None,
                                                          max_cert if max_cert in user_prefs.CERTS[:4] else None, max_runtime or None,
                                                          (st.get("services") or []) if services else None, st.get("never_genres") or [],
                                                          "different" if novelty == "different" else "familiar", ex)))

    @app.get("/api/v8/chips")
    async def v8_chips(request: Request, text: str = ""):
        """3c preference chips (PREF_CHIPS=1): limits stated in a chat message, offered as filter chips."""
        if (g := gate(request)):
            return g
        import os as _os
        if _os.environ.get("PREF_CHIPS", "0") != "1":
            return JSONResponse({"chips": [], "on": False})
        import pref_phrases
        return JSONResponse({"chips": pref_phrases.chips(text[:600]), "on": True})

    @app.get("/api/v8/continue")
    async def v8_continue(request: Request):
        if (g := gate(request)):
            return g
        return JSONResponse({"items": await run(continue_watching, user_of(request))})

    @app.post("/api/v8/tv/progress")
    async def v8_progress(body: Progress, request: Request):
        if (g := gate(request)):
            return g
        key = user_of(request)
        try:
            saved = await run(lambda: set_progress(key, body.id, body.season, body.episode, body.finished, body.clear))
        except ProgressError as e:
            return JSONResponse({"error": str(e)}, status_code=400)
        return JSONResponse({"ok": True, "saved": saved, "items": await run(continue_watching, key)})

    @app.get("/api/v8/watchlist/upcoming")
    async def v8_upcoming(request: Request):
        if (g := gate(request)):
            return g
        return JSONResponse(await run(upcoming, user_of(request)))

    @app.post("/api/v8/alerts/mute")
    async def v8_mute(body: Mute, request: Request):
        if (g := gate(request)):
            return g
        key = user_of(request)
        if not key:
            return JSONResponse({"error": "No browser id."}, status_code=400)
        await run(set_mute, key, body.id, body.muted)
        return JSONResponse({"ok": True, "muted": body.muted})

    @app.get("/api/v8/search")
    async def v8_search(request: Request, q: str = ""):
        if (g := gate(request)):
            return g
        return JSONResponse(await run(search, q[:80], user_of(request)))

    @app.post("/api/v8/chats/rename")
    async def v8_rename(body: Rename, request: Request):
        if (g := gate(request)):
            return g
        try:
            return JSONResponse({"ok": True, "title": await run(rename_chat, user_of(request), body.id, body.title)})
        except ValueError as e:
            return JSONResponse({"error": str(e)}, status_code=400)
        except LookupError as e:
            return JSONResponse({"error": str(e)}, status_code=404)

    class Banner(BaseModel):
        data: str = ""

    @app.post("/api/v8/banner")
    async def v8_banner_up(b: Banner, request: Request):
        if (g := gate(request)):
            return g
        if len(b.data) > BANNER_MAX * 4 // 3 + 200:
            return JSONResponse({"error": "Images up to 2 MB, please."}, status_code=413)
        ok, msg = await run(banner_save, user_of(request), b.data)
        return JSONResponse({"ok": ok, "message": msg} if ok else {"error": msg}, status_code=200 if ok else 400)

    @app.delete("/api/v8/banner")
    async def v8_banner_del(request: Request):
        if (g := gate(request)):
            return g
        return JSONResponse({"ok": await run(banner_delete, user_of(request))})

    @app.get("/api/v8/banner")
    async def v8_banner_get(request: Request):
        """The person's own banner image (fetched by the page with its headers; never public)."""
        from fastapi.responses import FileResponse
        if (g := gate(request)):
            return g
        p, t = banner_file(user_of(request))
        if not p:
            return JSONResponse({"error": "No banner."}, status_code=404)
        return FileResponse(p, media_type=t, headers={"Cache-Control": "private, no-cache"})

    @app.get("/api/v8/me/summary")
    async def v8_summary(request: Request, activity: int = 20):
        if (g := gate(request)):
            return g
        return JSONResponse(await run(summary, user_of(request), max(1, min(activity, 200))))
