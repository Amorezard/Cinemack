"""
v8 (9 Nov 2026): the routes behind the new page (/v8) - home rails, detail extras, calendar, alerts, profile,
privacy, friends, reviews, watch-history imports (Netflix, Letterboxd watched, Trakt).

server.py calls install(app, ...) once. Nothing here changes an existing route or the answer pipeline; the old page
stays at /. Every route checks the access code like the rest of the site; social routes need a signed-in account.
Account sign-up ("keep this browser's history") and deletion are extended to the new tables (history, alerts,
social, Trakt) by wrapping accounts.move_data / accounts.delete_data.
"""
import asyncio
from pathlib import Path
from typing import Optional

from fastapi import Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel

import accounts
import discovery
import film_info
import history
import social
import trakt_sync
import user_prefs

HERE = Path(__file__).resolve().parent
V8 = HERE / "v8"
ASSET_TYPES = {".css": "text/css", ".js": "text/javascript", ".svg": "image/svg+xml", ".png": "image/png",
               ".webmanifest": "application/manifest+json", ".json": "application/json", ".woff2": "font/woff2"}


def _run(fn, *a):
    return asyncio.get_running_loop().run_in_executor(None, fn, *a)


def _name(key):
    try:
        n = user_prefs.load(key)["display_name"]
    except Exception:  # noqa: BLE001
        n = ""
    return n or f"Friend {social.code_of(key) or ''}".strip()


def _wrap_accounts():
    if getattr(accounts.move_data, "_v8", False):
        return
    orig_move, orig_del = accounts.move_data, accounts.delete_data

    def move_data(old_key, new_key):
        moved = orig_move(old_key, new_key)
        if old_key and new_key and old_key != new_key:
            try:
                history.move_user(old_key, new_key)
                moved["watch_events"] = True
            except Exception as e:  # noqa: BLE001
                print(f"[cinemack] v8 history move failed: {e!r}", flush=True)
            try:                                       # 10 Nov: TV progress and alert opt-outs move with the person
                import v8extra
                v8extra.move_user(old_key, new_key)
                moved["tv_progress"] = True
            except Exception as e:  # noqa: BLE001
                print(f"[cinemack] v8 progress move failed: {e!r}", flush=True)
        return moved

    def delete_data(key):
        gone = orig_del(key)
        for name, fn in (("watch_events", history.delete_user), ("social", social.delete_user),
                         ("alerts", discovery.delete_user)):
            try:
                fn(key)
                gone[name] = True
            except Exception as e:  # noqa: BLE001
                print(f"[cinemack] v8 delete {name} failed: {e!r}", flush=True)
        try:
            trakt_sync.disconnect(key)
            gone["trakt"] = True
        except Exception:  # noqa: BLE001
            pass
        try:
            import v8extra
            v8extra.delete_user(key)
            gone["tv_progress"] = True
        except Exception as e:  # noqa: BLE001
            print(f"[cinemack] v8 delete progress failed: {e!r}", flush=True)
        try:                                           # round 27: email address and what was sent
            import emailer
            emailer.delete_user(key)
            gone["email"] = True
        except Exception as e:  # noqa: BLE001
            print(f"[cinemack] v8 delete email failed: {e!r}", flush=True)
        return gone
    move_data._v8 = delete_data._v8 = True
    accounts.move_data, accounts.delete_data = move_data, delete_data


class Privacy(BaseModel):
    changes: dict


class Hide(BaseModel):
    kind: str
    id: int
    hidden: bool = True


class Code(BaseModel):
    code: str


class Other(BaseModel):
    key: str
    accept: Optional[bool] = None


class Review(BaseModel):
    id: int
    rating: Optional[float] = None
    text: str = ""


class Follow(BaseModel):
    id: int
    on: bool = True
    notify: Optional[bool] = None


class Upload(BaseModel):
    text: str
    kind: str = "netflix"          # netflix | letterboxd_watched | trakt (12 Nov: Trakt export JSON)
    name: str = ""                 # 12 Nov: the file name (Trakt watchlist files are told apart by it)


def install(app, user_of, account_of, gate):
    _wrap_accounts()

    def need_account(request):
        if (g := gate(request)):
            return None, g
        a = account_of(request)
        if not a:
            return None, JSONResponse({"error": "Sign in to use friends and sharing."}, status_code=401)
        return accounts.user_key(a["id"]), None

    @app.get("/v8")
    def v8_page():
        return FileResponse(V8 / "index.html", headers={"Cache-Control": "no-store"})

    @app.get("/api/v8/build")
    async def v8_build():
        """round 22: a fingerprint of the page's files; an open page reloads itself when it changes (after a deploy)."""
        import hashlib
        h = hashlib.sha1()
        for p in sorted(V8.glob("*")):
            if p.suffix in ASSET_TYPES or p.suffix == ".html":
                st = p.stat()
                h.update(f"{p.name}:{st.st_size}:{st.st_mtime_ns}".encode())
        return JSONResponse({"build": h.hexdigest()[:12]}, headers={"Cache-Control": "no-store"})

    @app.get("/v8/{name}")
    def v8_asset(name: str):
        p = (V8 / name).resolve()
        if p.parent != V8.resolve() or not p.is_file() or p.suffix not in ASSET_TYPES:
            return JSONResponse({"error": "not found"}, status_code=404)
        return FileResponse(p, media_type=ASSET_TYPES[p.suffix], headers={"Cache-Control": "no-cache"})

    @app.get("/api/v8/home")
    async def v8_home(request: Request, genres: str = "", mode: str = "any"):
        if (g := gate(request)):
            return g
        gs = [x for x in (genres or "").split(",") if x in discovery.GENRE_OPTIONS]   # 10 Nov: no [:6] cut
        return JSONResponse(await _run(discovery.home, user_of(request), gs, "all" if mode == "all" else "any"))

    @app.get("/api/v8/theatres/{fid}")
    async def v8_theatres(fid: int, request: Request, zip: str = ""):
        if (g := gate(request)):
            return g
        return JSONResponse(await _run(discovery.theatres, fid, zip))

    @app.get("/api/v8/tv/search")
    async def v8_tv_search(request: Request, q: str = ""):
        if (g := gate(request)):
            return g
        key = user_of(request)

        def build():
            mine = {x["id"] for x in discovery.tv_following(key)}
            return {"shows": [dict(x, following=x["id"] in mine) for x in discovery.tv_search(q[:80])]}
        return JSONResponse(await _run(build))

    @app.post("/api/v8/tv/follow")
    async def v8_tv_follow(body: Follow, request: Request):
        if (g := gate(request)):
            return g
        key = user_of(request)
        if not key:
            return JSONResponse({"error": "No browser id."}, status_code=400)
        await _run(discovery.tv_follow, key, body.id, body.on, body.notify)
        return JSONResponse({"ok": True, "shows": await _run(discovery.tv_following, key)})

    import tv_prefs as _tvp                             # 12 Nov: TV taste from onboarding
    _tvp.install(app, user_of, gate, _run)

    @app.get("/api/v8/similar/{fid}")
    async def v8_similar(fid: int, request: Request):
        if (g := gate(request)):
            return g
        return JSONResponse({"items": await _run(discovery.similar, fid, user_of(request))})

    @app.get("/api/v8/calendar")
    async def v8_calendar(request: Request, days: int = 60, start: str = ""):
        if (g := gate(request)):
            return g
        import re as _re
        st = start if _re.fullmatch(r"\d{4}-\d{2}-\d{2}", start or "") else None
        return JSONResponse(await _run(discovery.calendar, user_of(request), max(1, min(days, 180)), st))

    @app.get("/api/v8/alerts")
    async def v8_alerts(request: Request):
        if (g := gate(request)):
            return g
        return JSONResponse(await _run(discovery.alerts, user_of(request)))

    @app.post("/api/v8/alerts/seen")
    async def v8_alerts_seen(request: Request):
        if (g := gate(request)):
            return g
        await _run(discovery.mark_seen, user_of(request))
        return JSONResponse({"ok": True})

    @app.get("/api/v8/watchlist")
    async def v8_watchlist(request: Request):
        if (g := gate(request)):
            return g
        key = user_of(request)

        def build():
            ids = [w["id"] for w in film_info.watchlist(key, 500)]
            return {"items": discovery.cards(ids, key)}
        return JSONResponse(await _run(build))

    @app.get("/api/v8/watchlist/overview")
    async def v8_watchlist_overview(request: Request):
        if (g := gate(request)):
            return g
        key = user_of(request)
        return JSONResponse(await _run(lambda: discovery.watchlist_overview(key, name_of=_name)))

    @app.get("/api/v8/profile")
    async def v8_profile(request: Request):
        if (g := gate(request)):
            return g
        key = user_of(request)

        def build():
            out = {"history": history.history(key, 200), "stats": history.stats(key), "imports": history.jobs(key),
                   "watchlist": film_info.watchlist(key, 200), "trakt": trakt_sync.connected(key),
                   "reviews": social.reviews_of(key), "account": social.is_account(key)}
            try:
                import personal
                out["taste"] = personal.summary(key)
            except Exception:  # noqa: BLE001
                out["taste"] = {}
            if social.is_account(key):
                fr = social.friends(key)
                for k in ("friends", "incoming", "outgoing"):
                    for f in fr[k]:
                        f["name"] = _name(f["key"])
                out.update(privacy=social.privacy(key), code=social.code_of(key), friends=fr)
            return out
        return JSONResponse(await _run(build))

    @app.post("/api/v8/privacy")
    async def v8_privacy(body: Privacy, request: Request):
        key, err = need_account(request)
        if err:
            return err
        try:
            return JSONResponse({"privacy": social.set_privacy(key, body.changes)})
        except social.SocialError as e:
            return JSONResponse({"error": str(e)}, status_code=400)

    @app.post("/api/v8/hide")
    async def v8_hide(body: Hide, request: Request):
        key, err = need_account(request)
        if err:
            return err
        try:
            social.hide(key, body.kind, body.id, body.hidden)
            return JSONResponse({"ok": True})
        except social.SocialError as e:
            return JSONResponse({"error": str(e)}, status_code=400)

    @app.post("/api/v8/friends/request")
    async def v8_friend_request(body: Code, request: Request):
        key, err = need_account(request)
        if err:
            return err
        try:
            return JSONResponse({"result": social.request(key, body.code)})
        except social.SocialError as e:
            return JSONResponse({"error": str(e)}, status_code=400)

    @app.post("/api/v8/friends/{action}")
    async def v8_friend_action(action: str, body: Other, request: Request):
        key, err = need_account(request)
        if err:
            return err
        try:
            if action == "respond":
                return JSONResponse({"result": social.respond(key, body.key, bool(body.accept))})
            if action == "remove":
                social.remove(key, body.key)
            elif action == "block":
                social.block(key, body.key)
            elif action == "unblock":
                social.unblock(key, body.key)
            else:
                return JSONResponse({"error": "unknown action"}, status_code=404)
            return JSONResponse({"ok": True})
        except social.SocialError as e:
            return JSONResponse({"error": str(e)}, status_code=400)

    @app.get("/api/v8/friend/{other}")
    async def v8_friend_view(other: str, request: Request):
        key, err = need_account(request)
        if err:
            return err
        if not social.are_friends(key, other):            # the same reply as "no such person"
            return JSONResponse({"error": "Not found."}, status_code=404)

        def build():
            out = {"key": other, "name": _name(other)}
            if social.can_view(other, key, "watchlist"):
                wl = film_info.watchlist(other, 200)
                out["watchlist"] = discovery.cards([w["id"] for w in social.visible_items(other, key, "watchlist", wl)], key)
            if social.can_view(other, key, "history"):
                out["history"] = social.visible_items(other, key, "history", history.history(other, 200))
            if social.can_view(other, key, "ratings") or social.can_view(other, key, "reviews"):
                rv = social.reviews_of(other)
                rv = social.visible_items(other, key, "ratings", rv) if social.can_view(other, key, "ratings") else []
                if not social.can_view(other, key, "reviews"):
                    rv = [dict(r, text="") for r in rv]
                cs = {c["id"]: c for c in discovery.cards([r["id"] for r in rv], key)}     # 10 Nov: titles + posters
                out["ratings"] = [dict(r, title=cs.get(r["id"], {}).get("title"), year=cs.get(r["id"], {}).get("year"),
                                       poster=cs.get(r["id"], {}).get("poster")) for r in rv]
            return out
        return JSONResponse(await _run(build))

    @app.post("/api/v8/review")
    async def v8_review(body: Review, request: Request):
        if (g := gate(request)):
            return g
        try:
            if body.rating is None and not body.text.strip():
                social.delete_review(user_of(request), body.id)
            else:
                social.save_review(user_of(request), body.id, body.rating, body.text)
            return JSONResponse({"ok": True})
        except (social.SocialError, ValueError) as e:
            return JSONResponse({"error": str(e)}, status_code=400)

    @app.post("/api/v8/watched")
    async def v8_watched(body: Review, request: Request):
        """Mark a film watched today from the detail page (source cinemack)."""
        if (g := gate(request)):
            return g
        t = discovery._q("select title, year from films where tmdb_id = ?", (body.id,))
        if not t:
            return JSONResponse({"error": "Unknown film."}, status_code=404)
        history.add_event(user_of(request), "movie", body.id, t[0][0], t[0][1])
        return JSONResponse({"ok": True})

    @app.post("/api/v8/import")
    async def v8_import(body: Upload, request: Request):
        if (g := gate(request)):
            return g
        key = user_of(request)
        if len(body.text) > 15_000_000:
            return JSONResponse({"error": "That file is too big (15 MB at most)."}, status_code=413)

        def run():
            if body.kind == "trakt":                  # 12 Nov: Trakt's export file, no Trakt app key needed
                return trakt_sync.import_export(key, body.text, body.name)
            rows = history.parse_netflix(body.text) if body.kind == "netflix" else history.parse_letterboxd_watched(body.text)
            src = "netflix" if body.kind == "netflix" else "letterboxd"
            return history.import_rows(key, src, rows)
        try:
            return JSONResponse(await _run(run))
        except ValueError as e:
            return JSONResponse({"error": str(e)}, status_code=400)

    @app.post("/api/v8/history/clear")
    async def v8_history_clear(body: Upload, request: Request):
        if (g := gate(request)):
            return g
        if body.kind not in history.SOURCES:
            return JSONResponse({"error": "unknown source"}, status_code=400)
        return JSONResponse({"removed": history.delete_source(user_of(request), body.kind)})

    @app.get("/trakt/start")
    async def trakt_start(request: Request):
        if (g := gate(request)):
            return g
        try:
            return JSONResponse({"url": trakt_sync.start(user_of(request))})
        except trakt_sync.TraktError as e:
            return JSONResponse({"error": str(e)}, status_code=400)

    @app.get("/trakt/callback")
    async def trakt_callback(state: str = "", code: str = "", error: str = ""):
        if error or not code:
            return RedirectResponse("/v8#/profile?trakt=cancelled")
        try:
            user = await _run(trakt_sync.finish, state, code)
            await _run(trakt_sync.sync, user)
            return RedirectResponse("/v8#/profile?trakt=connected")
        except trakt_sync.TraktError:
            return RedirectResponse("/v8#/profile?trakt=failed")

    try:                                   # 10 Nov redesign: continue watching, upcoming, search, profile summary
        import v8extra
        v8extra.install(app, user_of, gate)
    except Exception as e:  # noqa: BLE001 - the rest of v8 keeps working without these routes
        print(f"[cinemack] v8extra routes not installed: {e!r}", flush=True)

    @app.post("/api/v8/trakt/{action}")
    async def trakt_action(action: str, request: Request):
        if (g := gate(request)):
            return g
        try:
            if action == "sync":
                return JSONResponse(await _run(trakt_sync.sync, user_of(request)))
            if action == "disconnect":
                return JSONResponse(await _run(trakt_sync.disconnect, user_of(request)))
        except trakt_sync.TraktError as e:
            return JSONResponse({"error": str(e)}, status_code=400)
        return JSONResponse({"error": "unknown action"}, status_code=404)
