"""12 Nov 2026 (round 38): reviews from friends, for Profile > Reviews (which replaces the Ratings and Reviews tabs).

friends_reviews(key) -> {"reviews": [{friend, id, title, year, poster, rating, text, updated}], "friends": n, "sharing": m}
Only friends who share with this person (social.can_view): a star rating needs their "ratings" shared, the text their
"reviews"; items they hid are left out (social.hidden_ids). Guests have no friends, so the list is empty.
Route: GET /api/v8/reviews/friends (install from v8routes.install)."""
import social
import user_prefs


def _name(key):
    try:
        n = user_prefs.load(key)["display_name"]
    except Exception:  # noqa: BLE001
        n = ""
    return n or f"Friend {social.code_of(key) or ''}".strip()


def friends_reviews(key, limit=60):
    if not key or not social.is_account(key):
        return {"reviews": [], "friends": 0, "sharing": 0}
    fr = social.friends(key)["friends"]
    out, sharing = [], 0
    for f in fr:
        k = f["key"]
        see_r, see_t = social.can_view(k, key, "ratings"), social.can_view(k, key, "reviews")
        if not (see_r or see_t):
            continue
        sharing += 1
        hid_r = social.hidden_ids(k, "ratings") if see_r else set()
        hid_t = social.hidden_ids(k, "reviews") if see_t else set()
        name = _name(k)
        for r in social.reviews_of(k):
            text = (r["text"] or "") if see_t and r["id"] not in hid_t else ""
            rating = r["rating"] if see_r and r["id"] not in hid_r else None
            if not text.strip() and rating is None:
                continue
            out.append({"friend": name, "id": r["id"], "rating": rating, "text": text, "updated": r["updated"]})
    out.sort(key=lambda x: x["updated"] or "", reverse=True)
    out = out[:limit]
    if out:
        try:
            import v8extra
            meta = v8extra._film_meta(list({x["id"] for x in out}))
        except Exception:  # noqa: BLE001
            meta = {}
        for x in out:
            m = meta.get(x["id"]) or {}
            x.update(title=m.get("title"), year=m.get("year"), poster=m.get("poster"))
    return {"reviews": out, "friends": len(fr), "sharing": sharing}


def install(app, user_of, gate, run):
    from fastapi import Request
    from fastapi.responses import JSONResponse

    @app.get("/api/v8/reviews/friends")
    async def reviews_friends(request: Request):
        if (g := gate(request)):
            return g
        return JSONResponse(await run(friends_reviews, user_of(request)))
