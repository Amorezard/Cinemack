"""
Movie Buff web server.

    cd ~/movie-buff/rag/web
    source ~/venvs/movie-rag/bin/activate
    MOVIE_BUFF_CODE=pick-a-code python server.py     # REDACTED
    MOVIE_BUFF_MOCK=1 python server.py               # fake model, for checking the page

Listens on port 49151 (the port the department opened) on all interfaces, so
it is reachable from outside the server. Because of that, every question needs
the access code in MOVIE_BUFF_CODE - otherwise anyone who stumbles on the port
can queue work on the GPU. Leave MOVIE_BUFF_CODE unset only for local testing.
HOST / PORT environment variables override the defaults.

The page talks to this server, never to Ollama. The server runs the same
retrieval and prompt code as chat.py, then streams the answer back as
Server-Sent Events:

    status   {"stage": "queued", "ahead": 1} | {"stage": "searching"} | {"stage": "answering"}
    sources  {"id", "films": [...], "steps": [...], "alternatives": [...], "retrieve_s": 1.3}
    thinking {"text": "..."}             (only when reasoning is on)
    token    {"text": "..."}
    done     {"id", "answer", "recent_titles", "timings", "stopped", "check", "cached"}
    error    {"message": "..."}

One A10 runs one 27B generation at a time, so questions are served one at a
time in arrival order. A second person sees "1 question ahead of yours"
instead of both answers slowing to a crawl.

Added 23 Sep 2026:
  change 4   every question is logged to ../logs/requests.jsonl (question,
             films found, retrieval steps, timings, answer, fact check), and
             the page's thumbs up/down go to ../logs/feedback.jsonl via
             POST /api/feedback. python review_feedback.py lists the thumbs-down.
  change 16  the finished answer is fact-checked against the films it was given
             (factcheck.py); the result rides on the "done" event.
  change 24  an exact repeat of a conversation (same question, same history,
             same reasoning setting) is answered from memory, instantly.
             In-process only: a restart (after any code or data change) clears it.
  v3 (6 Oct) every finished turn is saved under the browser id + conversation id
             (web/chats.py, logs/chats.db); /api/chats lists, opens and deletes them,
             and reopening a chat restores its conversation state.
  /how       the How It Works page (how.html); /api/stats serves
             ../results/site_stats.json for its numbers, if present.
  v6 (20 Oct) film cards carry posters, runtime, critic scores, IMDb rating and the person's own
             choice (web/film_info.py, from facts.db tables filled by fetch_v6_data.py);
             GET /api/film/{id} feeds the film panel; POST /api/film takes a `choice`
             (liked / not / unseen / want / "") for the four labelled taste buttons;
             the sources event carries the planner's `intent` so the page can offer
             follow-up chips; /about holds the TMDB, Wikipedia and IMDb credits.
  v6 (21 Oct) accounts (web/accounts.py): sign up / log in / log out / delete, a session cookie,
             and "keep this browser's history" on sign-up. Everything per person is keyed by
             user_of(): the account ("acct<id>") when signed in, else the browser id.
             Settings and onboarding (web/user_prefs.py): style lines added to the answer prompt,
             genre / year / runtime limits applied in code to recommendations.
             Memory (web/memory.py): a notepad of facts the person told Movie Buff, in every
             prompt; X-MB-Private: 1 = a private chat (not saved, nothing remembered or used).
"""

import asyncio
import hmac
import hashlib
import json
import os
import threading
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import List, Optional

from fastapi import FastAPI, Request, Response
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel

import pipeline
import chats
import film_info
import links          # v6 (20 Oct night): trailer / review / box-office / where-to-watch links
import accounts
import user_prefs
import memory
import semcache       # v7 D11
import longsum        # 3b long-chat summaries (LONG_SUMMARY=1), 10 Nov

import sys
if str(pipeline.RAG_DIR) not in sys.path:      # feedback.py / personal.py live next to chat.py
    sys.path.insert(0, str(pipeline.RAG_DIR))

# v6: recommendations obey each person's genre / year / runtime limits before the prompt is built,
# and a search cached for one set of limits is never reused for another.
user_prefs.install(getattr(pipeline, "chat", None))
if hasattr(pipeline, "user_tag") and not getattr(pipeline.user_tag, "_mb_v6", False):
    _orig_user_tag = pipeline.user_tag

    def _user_tag_v6(user):
        base = _orig_user_tag(user)
        try:                                   # 6 Nov: the settings of the question being answered (saved + chat filters)
            cur = user_prefs.CURRENT.get("settings")
            ft = user_prefs.filter_tag(cur if cur is not None else (user_prefs.load(user)["settings"] if user else None))
        except Exception:  # noqa: BLE001
            ft = ""
        try:                                   # 21 Oct: the planner sees the person's films, so they key the search
            pb = user_prefs.past_films_block(user) if (user and PLAN_PAST) else ""
        except Exception:  # noqa: BLE001
            pb = ""
        if pb:
            ft = f"{ft}|p{hashlib.sha1(pb.encode()).hexdigest()[:8]}"
        return f"{base}|{ft}" if ft else base
    _user_tag_v6._mb_v6 = True
    pipeline.user_tag = _user_tag_v6

# 21 Oct: the answer length setting is also a hard cap (Ollama num_predict) on the answer itself. pipeline.py
# builds the answer's options with _opts() (no argument; the warm-up passes its own num_predict), so the cap is
# added there for the one question being answered. Thinking mode is never capped.
if hasattr(pipeline, "_opts") and not getattr(pipeline._opts, "_mb_cap", False):
    _orig_opts = pipeline._opts

    def _opts_capped(extra=None):
        o = _orig_opts(extra)
        n = user_prefs.CURRENT.get("num_predict")
        if n and extra is None and "num_predict" not in (o or {}):
            o = dict(o or {}, num_predict=n)
        return o
    _opts_capped._mb_cap = True
    pipeline._opts = _opts_capped

PLAN_PAST = os.environ.get("PLAN_PAST", "1") == "1"      # the person's films in the planner's prompt (web only)


def _request_context(prefs, user, private):
    """21 Oct: per-person settings that reach tools.py (money / critic lines) and the planner (their films)
    for the one question being answered. Returns a function that clears them again."""
    mods = []
    try:
        import tools as _tools
        _tools.REQUEST["hide"] = user_prefs.tools_hide(prefs)
        mods.append(lambda: _tools.REQUEST.update(hide=set()))
    except Exception:  # noqa: BLE001 - MOCK mode has no tools.py
        pass
    try:
        import retrieve as _R
        _R.PERSON["block"] = user_prefs.past_films_block(user) if (user and PLAN_PAST and not private) else ""
        mods.append(lambda: _R.PERSON.update(block=""))
    except Exception:  # noqa: BLE001
        pass

    def clear():
        for f in mods:
            f()
    return clear

HERE = Path(__file__).resolve().parent
MAX_QUESTION_CHARS = 2000
ACCESS_CODE = os.environ.get("MOVIE_BUFF_CODE", "")
LOG_DIR = pipeline.RAG_DIR / "logs"
STATS = pipeline.RAG_DIR / "results" / "site_stats.json"
site_cache = pipeline.site_cache       # answers + searches, kept across restarts
log_lock = threading.Lock()


def log_line(name, record):
    """Append one JSON line to logs/<name>.jsonl. Never raises."""
    try:
        LOG_DIR.mkdir(exist_ok=True)
        with log_lock, open(LOG_DIR / f"{name}.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as e:
        print(f"[movie-buff] couldn't write {name} log: {e!r}", flush=True)


def code_ok(request):
    if not ACCESS_CODE:
        return True
    given = request.headers.get("x-access-code", "")
    return hmac.compare_digest(given.encode(), ACCESS_CODE.encode())


@asynccontextmanager
async def lifespan(app):
    threading.Thread(target=pipeline.prewarm, daemon=True).start()
    yield


app = FastAPI(title="Cinemack", lifespan=lifespan, docs_url=None, redoc_url=None)

gpu_lock = asyncio.Lock()   # FIFO: asyncio wakes waiters in arrival order
line = []                   # requests holding or waiting for the lock, in order


class Turn(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    message: str
    history: List[Turn] = []
    recent_titles: List[str] = []
    conv: Optional[dict] = None        # v3 conversation state (conv.py), echoed back in "done"
    think: bool = False
    filters: Optional[dict] = None     # 6 Nov: this chat's filter bar (user_prefs.clean_chat); never saved


def sse(event, **data):
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


V8_HOME = os.environ.get("V8_HOME", "1") == "1"   # round 27: / is the v8 site; the old page moved to /classic


@app.get("/")
def index():
    if V8_HOME and (HERE / "v8" / "index.html").exists():
        return FileResponse(HERE / "v8" / "index.html", headers={"Cache-Control": "no-store"})
    return FileResponse(HERE / "index.html", headers={"Cache-Control": "no-store"})


@app.get("/classic")
def classic():
    return FileResponse(HERE / "index.html", headers={"Cache-Control": "no-store"})


@app.get("/how")
def how():
    return FileResponse(HERE / "how.html", headers={"Cache-Control": "no-store"})


@app.get("/about")
def about():
    return FileResponse(HERE / "about.html", headers={"Cache-Control": "no-store"})


STATIC = HERE / "static"


@app.get("/static/{name}")
def static_file(name: str):
    """web/static/: the TMDB logo for the About page. Plain file names only."""
    p = (STATIC / name).resolve()
    if p.parent != STATIC.resolve() or not p.is_file():
        return JSONResponse({"error": "not found"}, status_code=404)
    return FileResponse(p, headers={"Cache-Control": "max-age=86400"})


@app.get("/api/stats")
def stats():
    try:
        return JSONResponse(json.loads(STATS.read_text()))
    except Exception:
        return JSONResponse({}, status_code=404)


class Feedback(BaseModel):
    id: str
    vote: str
    comment: str = ""
    reasons: List[str] = []


# v3: what each recent answer was built from, so feedback can be stored with it (feedback.py).
RECENT = {}
RECENT_MAX = 2000


def browser_of(request):
    """Anonymous browser id from the page (localStorage)."""
    u = request.headers.get("x-mb-user", "")[:64]
    return "".join(ch for ch in u if ch.isalnum() or ch in "-_")


def account_of(request):
    """v6: the signed-in account (session cookie), or None."""
    try:
        return accounts.account_for(request.cookies.get(accounts.COOKIE, ""))
    except Exception as e:  # noqa: BLE001
        print(f"[movie-buff] session check failed: {e!r}", flush=True)
        return None


def user_of(request):
    """The key all per-person data is stored under: "acct<id>" when signed in (v6), otherwise the
    anonymous browser id, for the taste profile, chats, settings, memory and the feedback cap."""
    a = account_of(request)
    return accounts.user_key(a["id"]) if a else browser_of(request)


def private_of(request):
    return request.headers.get("x-mb-private", "") == "1"


@app.post("/api/feedback")
async def feedback(fb: Feedback, request: Request):
    if not code_ok(request):
        return JSONResponse({"error": "Wrong or missing access code."}, status_code=401)
    if fb.vote not in ("up", "down"):
        return JSONResponse({"error": "vote must be up or down"}, status_code=400)
    log_line("feedback", {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "id": fb.id[:40],
                          "vote": fb.vote, "comment": fb.comment[:1000], "reasons": fb.reasons[:5]})
    try:
        import feedback as fbstore            # v3: logs/feedback.db, with the answer's full record
        fbstore.record(fb.id[:40], fb.vote, fb.reasons, fb.comment, user_of(request), RECENT.get(fb.id[:40]))
    except Exception as e:
        print(f"[movie-buff] feedback store failed: {e!r}", flush=True)
    return JSONResponse({"ok": True})


class FilmPref(BaseModel):
    id: int
    title: str = ""
    signal: int = 0          # v3: 1 liked the film, -1 not for me, 0 undo
    choice: Optional[str] = None   # v6: "liked" | "not" | "unseen" | "want" | "" (clear); wins over signal
    message_id: str = ""


@app.post("/api/film")
async def film_pref(fp: FilmPref, request: Request):
    """A film preference, kept apart from answer feedback. v3: liked / not for me (personal.py).
    v6: four labelled choices, one per film (film_info.choose); the reply says where it was saved."""
    if not code_ok(request):
        return JSONResponse({"error": "Wrong or missing access code."}, status_code=401)
    user = user_of(request)
    if not user:
        return JSONResponse({"error": "need a user id"}, status_code=400)
    choice = fp.choice
    if choice is None:
        if fp.signal not in (-1, 0, 1):
            return JSONResponse({"error": "signal must be -1, 0 or 1"}, status_code=400)
        choice = {1: "liked", -1: "not", 0: ""}[fp.signal]
    if choice not in film_info.CHOICES + ("",):
        return JSONResponse({"error": "choice must be liked, not, unseen, want or empty"}, status_code=400)
    try:
        said = film_info.choose(user, fp.id, fp.title[:200], choice, fp.message_id[:40])
        return JSONResponse({"ok": True, "choice": choice, "message": said})
    except Exception as e:
        print(f"[movie-buff] film preference failed: {e!r}", flush=True)
        return JSONResponse({"error": "couldn't save that"}, status_code=500)


@app.get("/api/film/{fid}")
async def film_detail(fid: int, request: Request):
    """v6: the film panel (backdrop, cast with photos, budget and gross, links)."""
    if not code_ok(request):
        return JSONResponse({"error": "Wrong or missing access code."}, status_code=401)
    info = await asyncio.get_running_loop().run_in_executor(None, film_info.detail, fid, user_of(request))
    if not info.get("title"):
        return JSONResponse(dict(info, error="No details for that film yet."), status_code=404)
    return JSONResponse(info)


@app.get("/api/watchlist")
async def watchlist(request: Request):
    if not code_ok(request):
        return JSONResponse({"error": "Wrong or missing access code."}, status_code=401)
    return JSONResponse({"films": film_info.watchlist(user_of(request))})


@app.get("/api/taste")
async def taste(request: Request):
    if not code_ok(request):
        return JSONResponse({"error": "Wrong or missing access code."}, status_code=401)
    try:
        import personal
        return JSONResponse(personal.summary(user_of(request)))
    except Exception:
        return JSONResponse({})


@app.get("/api/chats")
async def chat_list(request: Request):
    """v3: this browser's saved conversations, newest first (web/chats.py)."""
    if not code_ok(request):
        return JSONResponse({"error": "Wrong or missing access code."}, status_code=401)
    return JSONResponse({"chats": chats.list_chats(user_of(request))})


@app.get("/api/chats/{conv_id}")
async def chat_open(conv_id: str, request: Request):
    if not code_ok(request):
        return JSONResponse({"error": "Wrong or missing access code."}, status_code=401)
    c = chats.load_chat(user_of(request), conv_id)
    if c:
        for t in c.get("turns", []):                  # v6: posters + current choices on reopened chats
            film_info.enrich(t.get("films") or [], user_of(request))
    return JSONResponse(c) if c else JSONResponse({"error": "No such chat."}, status_code=404)


@app.delete("/api/chats/{conv_id}")
async def chat_delete(conv_id: str, request: Request):
    if not code_ok(request):
        return JSONResponse({"error": "Wrong or missing access code."}, status_code=401)
    user = user_of(request)
    ok = chats.delete_chat(user, conv_id)
    forgot = memory.forget_conv(user, conv_id) if ok else 0     # v6: "forget this chat" includes its facts
    return JSONResponse({"ok": ok, "forgot_facts": forgot})


# ------------------------------------------------------------------ v6: accounts, settings, memory
def _who(request):
    """What the page needs to know about the person: account, name, settings, onboarding, greeting."""
    a = account_of(request)
    key = accounts.user_key(a["id"]) if a else browser_of(request)
    prof = user_prefs.load(key)
    name = prof["display_name"] or (a["display_name"] if a else "")
    try:
        greet = user_prefs.greeting(key, name)
    except Exception as e:  # noqa: BLE001
        print(f"[movie-buff] greeting failed: {e!r}", flush=True)
        greet = None
    return {"account": {"username": a["username"], "display_name": a["display_name"]} if a else None,
            "name": name, "settings": prof["settings"], "onboarded": prof["onboarded"], "groups": prof["groups"],
            "greeting": greet, "memory_count": len(memory.notes(key)), "genres": user_prefs.GENRES, "certs": user_prefs.CERTS[:4],
            "about_max": user_prefs.ABOUT_MAX,
            "services_all": [[k, v[0]] for k, v in user_prefs.SERVICES.items()]}


def _gate(request):
    return None if code_ok(request) else JSONResponse({"error": "Wrong or missing access code."}, status_code=401)


def _with_session(payload, token):
    r = JSONResponse(payload)
    r.set_cookie(accounts.COOKIE, token, max_age=accounts.SESSION_DAYS * 86400, httponly=True, samesite="lax",
                 secure=False, path="/")     # plain HTTP: the cookie can't be Secure until the site has HTTPS
    return r


@app.get("/api/me")
async def me(request: Request):
    if (g := _gate(request)):
        return g
    return JSONResponse(await asyncio.get_running_loop().run_in_executor(None, _who, request))


class SignUp(BaseModel):
    username: str
    password: str
    display_name: str = ""
    keep_history: bool = True


@app.post("/api/signup")
async def signup(body: SignUp, request: Request):
    if (g := _gate(request)):
        return g
    loop = asyncio.get_running_loop()
    try:
        aid = await loop.run_in_executor(None, accounts.sign_up, body.username, body.password, body.display_name)
    except accounts.AccountError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    key, guest = accounts.user_key(aid), browser_of(request)
    moved = accounts.move_data(guest, key) if body.keep_history and guest else {}
    if body.display_name.strip():
        user_prefs.save(key, display_name=body.display_name)
    token = accounts.new_session(aid)
    request.cookies[accounts.COOKIE] = token                 # so _who sees the new session
    payload = await loop.run_in_executor(None, _who, request)
    payload["moved"] = moved
    return _with_session(payload, token)


class LogIn(BaseModel):
    username: str
    password: str


@app.post("/api/login")
async def login(body: LogIn, request: Request):
    if (g := _gate(request)):
        return g
    loop = asyncio.get_running_loop()
    try:
        aid = await loop.run_in_executor(None, accounts.log_in, body.username, body.password)
    except accounts.AccountError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    token = accounts.new_session(aid)
    request.cookies[accounts.COOKIE] = token
    return _with_session(await loop.run_in_executor(None, _who, request), token)


@app.post("/api/logout")
async def logout(request: Request):
    accounts.end_session(request.cookies.get(accounts.COOKIE, ""))
    r = JSONResponse({"ok": True})
    r.delete_cookie(accounts.COOKIE, path="/")
    return r


class DeleteAccount(BaseModel):
    password: str


@app.post("/api/account/delete")
async def account_delete(body: DeleteAccount, request: Request):
    if (g := _gate(request)):
        return g
    a = account_of(request)
    if not a:
        return JSONResponse({"error": "You're not signed in."}, status_code=400)
    try:
        gone = await asyncio.get_running_loop().run_in_executor(None, accounts.delete_account, a["id"], body.password)
    except accounts.AccountError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    r = JSONResponse({"ok": True, "deleted": gone})
    r.delete_cookie(accounts.COOKIE, path="/")
    return r


class Settings(BaseModel):
    settings: Optional[dict] = None
    name: Optional[str] = None


@app.post("/api/settings")
async def settings_save(body: Settings, request: Request):
    if (g := _gate(request)):
        return g
    key = user_of(request)
    if not key:
        return JSONResponse({"error": "need a user id"}, status_code=400)
    prof = user_prefs.save(key, settings=body.settings, display_name=body.name)
    return JSONResponse({"ok": True, "settings": prof["settings"], "name": prof["display_name"]})


@app.get("/api/onboarding")
async def onboarding_get(request: Request):
    if (g := _gate(request)):
        return g
    loop = asyncio.get_running_loop()
    groups = await loop.run_in_executor(None, user_prefs.groups)
    famous = await loop.run_in_executor(None, user_prefs.famous)
    film_info.enrich(famous, user_of(request))               # their current choices, if any
    return JSONResponse({"groups": groups, "films": famous})


class Onboarding(BaseModel):
    name: Optional[str] = None
    groups: List[str] = []
    avoid: List[str] = []                 # 4 Nov: groups marked "Not for me"
    settings: Optional[dict] = None


@app.post("/api/onboarding")
async def onboarding_save(body: Onboarding, request: Request):
    if (g := _gate(request)):
        return g
    key = user_of(request)
    if not key:
        return JSONResponse({"error": "need a user id"}, status_code=400)
    try:
        user_prefs.set_groups(key, body.groups[:20], body.avoid[:20])
    except Exception as e:  # noqa: BLE001
        print(f"[movie-buff] onboarding groups failed: {e!r}", flush=True)
    user_prefs.save(key, settings=body.settings, display_name=body.name, onboarded=True)
    return JSONResponse(await asyncio.get_running_loop().run_in_executor(None, _who, request))


class LetterboxdImport(BaseModel):
    files: dict = {}                      # {filename: csv text} from the person's own Letterboxd export


@app.post("/api/import/letterboxd")
async def import_letterboxd(body: LetterboxdImport, request: Request):
    """4 Nov: the person's own Letterboxd export (ratings / watched / watchlist CSVs) -> their taste profile."""
    if (g := _gate(request)):
        return g
    key = user_of(request)
    if not key:
        return JSONResponse({"error": "need a user id"}, status_code=400)
    files = {str(k)[:80]: str(v) for k, v in list((body.files or {}).items())[:6]}
    if sum(len(v) for v in files.values()) > 8_000_000:
        return JSONResponse({"error": "Those files are too big (8 MB at most). Choose ratings.csv, watched.csv and watchlist.csv."},
                            status_code=413)
    import imports

    def run():
        parsed = imports.parse_letterboxd(files)
        return imports.apply(key, parsed, film_info.FACTS, film_info.choose, film_info.user_choices)
    try:
        counts = await asyncio.get_running_loop().run_in_executor(None, run)
    except Exception as e:  # noqa: BLE001
        print(f"[cinemack] letterboxd import failed: {e!r}", flush=True)
        return JSONResponse({"error": "Couldn't read those files. Choose the CSV files from your Letterboxd export."},
                            status_code=400)
    return JSONResponse({"ok": True, **counts})


@app.get("/api/memory")
async def memory_list(request: Request):
    if (g := _gate(request)):
        return g
    u = user_of(request)
    return JSONResponse({"notes": memory.notes(u), "cards": memory.cards(u), "cards_on": memory.MEM_CARDS})   # round 27: + cards


class NoteEdit(BaseModel):
    text: str


@app.patch("/api/memory/{note_id}")
async def memory_edit(note_id: int, body: NoteEdit, request: Request):
    if (g := _gate(request)):
        return g
    return JSONResponse({"ok": memory.edit(user_of(request), note_id, body.text)})


@app.delete("/api/memory/{note_id}")
async def memory_delete(note_id: int, request: Request):
    if (g := _gate(request)):
        return g
    return JSONResponse({"ok": memory.delete(user_of(request), note_id)})


@app.delete("/api/memory/card/{conv_id}")
async def memory_card_delete(conv_id: str, request: Request):
    if (g := _gate(request)):
        return g
    return JSONResponse({"ok": memory.delete_card(user_of(request), conv_id[:40])})


@app.delete("/api/memory")
async def memory_clear(request: Request):
    if (g := _gate(request)):
        return g
    u = user_of(request)
    return JSONResponse({"ok": True, "deleted": memory.clear(u), "cards_deleted": memory.clear_cards(u)})


@app.get("/api/health")
async def health(request: Request):
    info = await asyncio.get_running_loop().run_in_executor(None, pipeline.health)
    info["queue"] = len(line)
    info["needs_code"] = bool(ACCESS_CODE)
    info["code_ok"] = code_ok(request)
    if not info["code_ok"]:
        info.pop("error", None)   # don't describe the server to strangers
    return JSONResponse(info, status_code=200 if info.get("ok") else 503)


@app.post("/api/chat")
async def chat(req: ChatRequest, request: Request):
    if not code_ok(request):
        return JSONResponse({"error": "Wrong or missing access code."}, status_code=401)
    question = req.message.strip()
    if not question:
        return JSONResponse({"error": "Type a question first."}, status_code=400)
    if len(question) > MAX_QUESTION_CHARS:
        return JSONResponse({"error": f"Keep questions under {MAX_QUESTION_CHARS} characters."},
                            status_code=400)

    history = [{"role": t.role, "content": t.content}
               for t in req.history if t.role in ("user", "assistant") and t.content]
    full_history = history
    history = history[-pipeline.MAX_HISTORY_MESSAGES:]
    if hasattr(pipeline, "trim_history"):
        history = pipeline.trim_history(history)
    earlier = longsum.note(full_history, history)      # 3b (LONG_SUMMARY=1): what the cut turns said, and the stated limits
    rid = uuid.uuid4().hex[:12]
    conv_in = req.conv if pipeline.CONV_ON else None
    if conv_in is not None and len(json.dumps(conv_in, default=str)) > 20000:
        conv_in = None                     # a client sending junk gets a fresh conversation
    user = user_of(request)
    conv_id = request.headers.get("x-mb-conv", "")[:40]
    private = private_of(request)                       # v6: private chat - not saved, no memory either way
    chat_f = longsum.with_stated_limits(req.filters, [t.content for t in req.history if t.role == "user" and t.content])   # 3b v2 (LONG_SUMMARY=1)
    prefs = user_prefs.merge_chat(user_prefs.load(user)["settings"], chat_f)   # 6 Nov: + this chat's filters
    mem_on = bool(prefs.get("memory")) and not private
    utag = pipeline.user_tag(user) if hasattr(pipeline, "user_tag") else ""
    rec_block, rec_tag = "", ""
    if mem_on and prefs.get("recall", True):
        try:                                   # 21 Oct: relevant exchanges from the person's other chats
            rec_block, rec_tag = memory.recall_block(user, question, conv_id)
        except Exception as e:  # noqa: BLE001
            print(f"[movie-buff] recall failed: {e!r}", flush=True)
    extra_tag = "|".join(t for t in (user_prefs.tag(prefs), memory.tag(user) if mem_on else "", rec_tag, longsum.cache_tag(earlier)) if t)
    if extra_tag:
        utag = f"{utag}|{extra_tag}"
    key = site_cache.answer_key(question, req.recent_titles, history, conv_in, utag)
    hit = None if req.think else site_cache.get_answer(key)
    single_turn = not history and not req.recent_titles and not (conv_in or {}).get("turns")
    if hit is None and not req.think and single_turn and semcache.SEMCACHE:
        # v7 D11: a strict same-meaning match with an earlier single-turn question (web/semcache.py)
        k2 = await asyncio.get_running_loop().run_in_executor(None, semcache.lookup, question, utag)
        if k2:
            hit = site_cache.get_answer(k2)

    if hit:
        # Change 24: a repeat (same question, same point in the conversation) - replay
        # the stored answer, no GPU needed. Now kept on disk and matched after
        # normalizing (web/site_cache.py).
        src, done = hit
        src = dict(src, films=film_info.enrich([dict(f) for f in src.get("films") or []], user))

        async def replay():
            yield sse("sources", **dict(src, id=rid))
            yield sse("status", stage="answering")
            yield sse("token", text=done["answer"])
            lk = links.for_answer(question, src.get("films"), src.get("intent"), done.get("answer", ""))
            if lk:
                yield sse("links", groups=lk)
            yield sse("done", **dict(done, id=rid, cached=True,
                                     timings={"total_s": 0.0, "retrieve_s": 0.0, "first_text_s": 0.0}))
            if mem_on and memory.FIRST_PERSON.search(question):   # v6: a replayed answer still learns the facts
                try:
                    async with gpu_lock:
                        added = await asyncio.get_running_loop().run_in_executor(
                            None, lambda: memory.remember(user, question, conv_id, mock=getattr(pipeline, "MOCK", False)))
                    if added:
                        yield sse("memory", added=added)
                except Exception as e:  # noqa: BLE001
                    print(f"[movie-buff] memory failed: {e!r}", flush=True)
        log_line("requests", {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "id": rid,
                              "question": question, "cached": True})
        if not private:
            chats.save_turn(user, conv_id, question, done.get("answer", ""), src.get("films"),
                            done.get("recent_titles"), done.get("conv"), rid, cached=True)
        return StreamingResponse(replay(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    import safety2
    if safety2.GUARD2:                                  # round 27: guard v2, step 1 (the question)
        q2, g2reply, g2note = safety2.check_question(question)
        if g2reply:
            log_line("safety", {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "id": rid, "question": question,
                                "blocked": "guard2: " + g2note})
            return _plain_reply(g2reply, rid, question, req)
        if q2 != question:
            log_line("safety", {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "id": rid, "question": question,
                                "notes": ["guard2: " + g2note]})
            question = q2
    if MODERATION and not getattr(pipeline, "MOCK", False):
        mres = await asyncio.get_running_loop().run_in_executor(None, _moderate, question)
        if mres and not mres["ok"]:
            log_line("safety", {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "id": rid, "question": question,
                                "blocked": mres["why"]})
            return _plain_reply(mres["reply"], rid, question, req)
    if FAST and not req.think and not getattr(pipeline, "MOCK", False) and not memory.FIRST_PERSON.search(question):
        fast = await asyncio.get_running_loop().run_in_executor(None, _fast_answer, question, req.recent_titles)
        if fast:
            return _fast_reply(fast, question, req, rid, user, conv_id, conv_in, private, key)

    out = asyncio.Queue()
    cancel = threading.Event()
    state = {"started": False}
    me = object()
    line.append(me)

    async def produce():
        loop = asyncio.get_running_loop()

        def put(item):
            loop.call_soon_threadsafe(out.put_nowait, item)

        def work():
            t0 = time.perf_counter()
            safety2.reset()                            # round 27: guard v2 remembers what it removed, per request
            put(sse("status", stage="searching"))
            user_prefs.CURRENT["settings"] = prefs           # v6: the person's limits, for chat._personalize
            user_prefs.CURRENT["user"] = user                # 12 Nov: whose TV taste (web/tv_prefs.py)
            clear_ctx = _request_context(prefs, user, private)
            try:
                prompt, hits = pipeline.prepare(question, req.recent_titles, conv_in, user)
            finally:
                user_prefs.CURRENT["settings"] = None
                user_prefs.CURRENT["user"] = None
                clear_ctx()
            trace = pipeline.last_trace()
            try:
                tool_style = pipeline.chat.LAST_TOOL.get("style")      # round 27: read in this thread (per-thread state)
            except Exception:  # noqa: BLE001
                tool_style = None
            retrieve_s = time.perf_counter() - t0
            intent = (((hits[0].get("_plan") or {}).get("intent") if hits else None)
                      or ((trace or {}).get("plan") or {}).get("intent"))
            if intent == "recommend":
                hits = user_prefs.filter_hits(prefs, hits)   # already done before the prompt; the cards match
            prompt += user_prefs.prompt_block(prefs, intent, question)
            try:                                       # 4 Nov: which films above are on their streaming services
                prompt += user_prefs.services_block(prefs, hits, intent, question)
            except Exception as e:  # noqa: BLE001
                print(f"[cinemack] services line failed: {e!r}", flush=True)
            if mem_on:
                mb = memory.block(user) + rec_block
                if GUARD and mb:                       # v7 D5: the person's notes and old answers are data too
                    import safety
                    mb = safety.sanitize(mb)
                elif safety2.GUARD2 and mb:            # round 27: guard v2 on memory too
                    mb = safety2.clean_data(mb)
                prompt += mb
            src = dict(films=film_info.enrich([pipeline.card(h) for h in hits], user), steps=pipeline.steps(hits),
                       alternatives=pipeline.alternatives(hits), retrieve_s=round(retrieve_s, 2), intent=intent)
            if prefs.get("chat_filters"):                  # 6 Nov: shown under the answer
                src["chat_filters"] = prefs["chat_filters"]
            if hits and hits[0].get("_choices"):           # 3 Nov (CLARIFY=1): "did you mean the film or the show?"
                src["choices"] = hits[0]["_choices"]
            put(sse("sources", id=rid, **src))
            if cancel.is_set():
                return

            put(sse("status", stage="answering"))
            if earlier:
                prompt = earlier + prompt
            messages = history + [{"role": "user", "content": prompt}]
            t1 = time.perf_counter()
            first = None
            answer = []
            cap = None if req.think else user_prefs.LENGTH_TOKENS.get(prefs.get("length", "normal"))
            user_prefs.CURRENT["num_predict"] = cap
            n_tok = 0
            try:
                for kind, text in pipeline.stream_answer(messages, req.think, cancel):
                    if first is None:
                        first = time.perf_counter() - t0
                    if kind == "token":
                        answer.append(text)
                        n_tok += 1
                    put(sse(kind, text=text))
            finally:
                user_prefs.CURRENT["num_predict"] = None
            if cap and n_tok >= cap - 2 and not cancel.is_set():
                # it stopped at the cap, probably mid-sentence: say so rather than leave it hanging
                tail = " …\n\n*(Cut short at your answer-length setting. Ask me to go on, or choose a longer length in Settings.)*"
                answer.append(tail)
                put(sse("token", text=tail))

            final = "".join(answer).strip()
            rw = None
            if final and not cancel.is_set():
                try:                                   # round 27 (4c, CLAIM_REWRITE=1): unsupported sentences rewritten
                    import claim_rewrite
                    if claim_rewrite.applies(tool_style):
                        ctx = prompt.split("\n\nQuestion:", 1)[0]
                        final2, rw = claim_rewrite.rewrite(final, ctx)
                        if final2 != final:
                            final = final2
                            put(sse("replace", text=final))
                except Exception as e:  # noqa: BLE001 - the answer stands as written
                    print(f"[cinemack] claim rewrite failed: {e!r}", flush=True)
            if final and not cancel.is_set() and (GUARD or MODERATION or safety2.GUARD2):
                notes = []
                if GUARD:                              # v7 D5: leak check + link allow-list on the finished answer
                    import safety
                    final2, notes = safety.post_check(final)
                elif safety2.GUARD2:                   # round 27: guard v2, step 3 (the answer)
                    final2, notes = safety2.post_check(final)
                else:
                    final2 = final
                if MODERATION:                         # v7 D6: the answer itself
                    try:
                        import moderation
                        mres = moderation.check(question, final2)
                        if not mres["ok"]:
                            final2, notes = mres["reply"], notes + [mres["why"]]
                    except Exception as e:  # noqa: BLE001
                        print(f"[movie-buff] answer moderation failed: {e!r}", flush=True)
                if final2 != final:
                    final = final2
                    put(sse("replace", text=final))
                    log_line("safety", {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "id": rid, "question": question,
                                        "notes": notes})
            if final and not cancel.is_set() and not getattr(pipeline, "MOCK", False) and prefs.get("sources", True):
                try:                                   # v4 WIKI_CREDIT=1: sources line added in code
                    import chat as _chat
                    credit = _chat.wiki_credit(hits) if hasattr(_chat, "wiki_credit") else ""
                except Exception:  # noqa: BLE001
                    credit = ""
                try:                                   # v7 R16: the evidence the answer cited, as links
                    import tools as _tools
                    cites = (hits[0].get("_citations") if hits else None) or {}
                    footer = _tools.citation_footer(final, cites) if cites else ""
                except Exception:  # noqa: BLE001
                    footer = ""
                if footer:
                    credit = footer + (credit.replace("\n\n*Sources: ", "\n*Wikipedia articles: ", 1) if credit else "")
                if credit:
                    put(sse("token", text=credit))
                    final += credit
            checked = pipeline.check(final, hits, question) if not cancel.is_set() else \
                {"ok": None, "flags": [], "checked": 0}
            done = dict(answer=final,
                        recent_titles=pipeline.next_recent_titles(hits, req.recent_titles),
                        conv=pipeline.next_conv(conv_in, question, hits, final) if not cancel.is_set() else conv_in,
                        stopped=cancel.is_set(), check=checked, cached=False,
                        rewrite=rw,
                        timings={"retrieve_s": round(retrieve_s, 2),
                                 "first_text_s": round(first, 2) if first else None,
                                 "generate_s": round(time.perf_counter() - t1, 2),
                                 "total_s": round(time.perf_counter() - t0, 2)})
            if final and not cancel.is_set():
                lk = links.for_answer(question, src["films"], intent, final)
                if lk:
                    put(sse("links", groups=lk))
            put(sse("done", id=rid, **done))
            if final and not private:
                chats.save_turn(user, conv_id, question, final, src["films"], done["recent_titles"],
                                done["conv"], rid)
                if mem_on and memory.MEM_CARDS and conv_id and not cancel.is_set():
                    try:                               # round 27 (3a): this chat's memory card, after the answer
                        memory.update_card(user, conv_id, mock=getattr(pipeline, "MOCK", False))
                    except Exception as e:  # noqa: BLE001
                        print(f"[cinemack] memory card failed: {e!r}", flush=True)
            if final and mem_on and not cancel.is_set():
                try:                                       # v6: facts from what the person said (memory.py)
                    added = memory.remember(user, question, conv_id, mock=getattr(pipeline, "MOCK", False))
                    if added:
                        put(sse("memory", added=added))
                except Exception as e:  # noqa: BLE001
                    print(f"[movie-buff] memory failed: {e!r}", flush=True)
            if final and not cancel.is_set() and not req.think and hits:
                site_cache.put_answer(key, question, src, done,
                                      pipeline.last_ttl() if hasattr(pipeline, "last_ttl") else None)
                if single_turn and semcache.SEMCACHE:
                    semcache.add(question, utag, key)
            # v4: the prompt itself, the history sent with it and the fact check, so a liked answer
            # can become a training example in the real RAG format (feedback.py, like_filter.py).
            RECENT[rid] = {"question": question, "conversation_id": conv_id, "films": done.get("recent_titles"),
                           "stages": trace, "answer": final, "versions": pipeline.versions(),
                           "prompt_chars": len(prompt), "prompt": prompt[:40000],
                           "history": [{"role": m["role"], "content": m["content"][:4000]} for m in history[-6:]],
                           "check": checked}
            while len(RECENT) > RECENT_MAX:
                RECENT.pop(next(iter(RECENT)))
            plan_prefs = ((hits[0].get("_plan") or {}).get("preferences") if hits else None) or []
            if user and plan_prefs and not private:
                try:
                    import personal
                    personal.record_stated(user, plan_prefs)     # "I hate jump scares" -> taste profile
                except Exception:
                    pass
            log_line("requests", {
                "time": time.strftime("%Y-%m-%d %H:%M:%S"), "id": rid, "question": question,
                "recent_titles": req.recent_titles, "history_turns": len(history) // 2,
                "think": req.think, "films": [f"{c['title']} ({c['year']})" for c in src["films"]],
                "steps": src["steps"], "timings": done["timings"], "answer": final,
                "check": checked, "stopped": done["stopped"], "cached": False,
                "stages": trace, "versions": pipeline.versions(),
                "conv_turns": len((conv_in or {}).get("turns") or []),
                "answer_chars": len(final), "prompt_chars": len(prompt)})

        try:
            ahead = line.index(me)
            if ahead:
                await out.put(sse("status", stage="queued", ahead=ahead))
            async with gpu_lock:
                state["started"] = True
                if cancel.is_set():
                    return
                await loop.run_in_executor(None, work)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"[movie-buff] error on {question!r}: {e!r}", flush=True)
            await out.put(sse("error", message=friendly(e)))
        finally:
            if me in line:
                line.remove(me)
            await out.put(None)

    task = asyncio.create_task(produce())

    async def stream():
        last_ahead = line.index(me) if me in line else None  # produce() already sent this one
        try:
            while True:
                try:
                    item = await asyncio.wait_for(out.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    if await request.is_disconnected():
                        break
                    if not state["started"] and me in line:
                        ahead = line.index(me)
                        if ahead != last_ahead:
                            last_ahead = ahead
                            yield sse("status", stage="queued", ahead=ahead)
                    yield ": keep-alive\n\n"
                    continue
                if item is None:
                    break
                yield item
        finally:
            # Browser closed, pressed Stop, or we finished. A waiting request
            # gives up its place; a running one stops generating at the next
            # token and releases the GPU.
            cancel.set()
            if not state["started"]:
                task.cancel()

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ------------------------------------------------------------------ v7 D5 / D6: guard + moderation switches
GUARD = os.environ.get("GUARD", "0") == "1"
MODERATION = os.environ.get("MODERATION", "0") == "1"


def _moderate(question):
    try:
        import moderation
        return moderation.check(question)
    except Exception as e:  # noqa: BLE001 - moderation down: answer as usual
        print(f"[movie-buff] moderation failed: {e!r}", flush=True)
        return None


def _plain_reply(text, rid, question, req):
    """A fixed reply (moderation): no films, no GPU."""
    async def gen():
        yield sse("sources", id=rid, films=[], steps=[], alternatives=[], retrieve_s=0, intent=None)
        yield sse("status", stage="answering")
        yield sse("token", text=text)
        yield sse("done", id=rid, answer=text, recent_titles=req.recent_titles, conv=req.conv, stopped=False,
                  check={"ok": None, "flags": [], "checked": 0}, cached=False, timings={"total_s": 0.0})
    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ------------------------------------------------------------------ v7 D7: the fact fast path (FAST=1)
FAST = os.environ.get("FAST", "0") == "1"


def _fast_answer(question, recent_titles):
    """fastpath.answer() plus the film's card, or None. Never raises."""
    try:
        import fastpath
        import retrieve as _Rq
        if getattr(_Rq, "TV", False):                  # 3 Nov: the fast path knows only films - a show question,
            import tv as _tvq                          # or a title that is also a show (CLARIFY), takes the pipeline
            if _tvq.wants_tv(question, recent_titles):
                return None
        r = fastpath.answer(question, recent_titles)
        if not r:
            return None
        import retrieve as _R
        hits = _R.hydrate(pipeline.chat._CTX_DB, [(r["tmdb_id"], 0.0)]) if hasattr(pipeline, "chat") else []
        if not hits:
            import tools as _tools
            t = _tools.film_row(r["tmdb_id"])
            hits = _tools.facts_hits([t[1]], year=t[2]) if t else []
        if hits and getattr(_Rq, "CLARIFY", False) and _Rq.clarify_show(question, hits[0].get("title")):
            return None                                # also a show: the pipeline answers and offers the choice
        if hits:
            hits[0]["_plan"] = {"intent": "named_film", "films": [hits[0]["title"]], "fast": r["kind"]}
        r["hits"] = hits
        return r
    except Exception as e:  # noqa: BLE001 - the normal pipeline answers instead
        print(f"[movie-buff] fast path failed: {e!r}", flush=True)
        return None


def _fast_reply(fast, question, req, rid, user, conv_id, conv_in, private, key):
    """Stream a fast-path answer: same events as a normal answer, no GPU, no queue."""
    t0 = time.perf_counter()
    hits = fast["hits"]
    films = film_info.enrich([pipeline.card(h) for h in hits], user)
    src = dict(films=films, steps=[f"Answered straight from the database ({fast['kind']}, {fast['ms']} ms)"],
               alternatives=[], retrieve_s=round(fast["ms"] / 1000, 3), intent="named_film")
    final = fast["answer"]
    done = dict(answer=final, recent_titles=pipeline.next_recent_titles(hits, req.recent_titles),
                conv=pipeline.next_conv(conv_in, question, hits, final), stopped=False,
                check={"ok": None, "flags": [], "checked": 0}, cached=False, fast=True,
                timings={"retrieve_s": round(fast["ms"] / 1000, 3), "first_text_s": round(time.perf_counter() - t0, 3),
                         "generate_s": 0.0, "total_s": round(time.perf_counter() - t0, 3)})

    async def gen():
        yield sse("sources", id=rid, **src)
        yield sse("status", stage="answering")
        yield sse("token", text=final)
        lk = links.for_answer(question, films, "named_film", final)
        if lk:
            yield sse("links", groups=lk)
        yield sse("done", id=rid, **done)
    if not private:
        chats.save_turn(user, conv_id, question, final, films, done["recent_titles"], done["conv"], rid)
    site_cache.put_answer(key, question, src, done, getattr(pipeline, "TTL_RECENT", None))
    log_line("requests", {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "id": rid, "question": question, "fast": fast["kind"],
                          "films": [f"{c['title']} ({c['year']})" for c in films], "answer": final,
                          "timings": done["timings"], "versions": pipeline.versions()})
    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def friendly(e):
    text = str(e).lower()
    if "connection" in text or "refused" in text or "connecterror" in type(e).__name__.lower():
        return "Can't reach the model server (Ollama). It may be restarting; try again in a minute."
    if "not found" in text and "model" in text:
        return "The movie-buff model isn't loaded in Ollama. Run: ollama create movie-buff -f ~/movie-buff/Modelfile"
    if "no such table" in text or "unable to open database" in text:
        return "The movie database couldn't be opened. Check that movies.db is in ~/movie-buff/rag."
    return "Something went wrong answering that. Try again, or rephrase the question."


# v8 (9 Nov 2026): new page at /v8 plus profile, friends, history, calendar and alerts routes (web/v8routes.py).
try:
    import v8routes
    v8routes.install(app, user_of=user_of, account_of=account_of, gate=_gate)
except Exception as _e:  # noqa: BLE001  - the classic page keeps working if v8 fails to load
    print(f"[cinemack] v8 routes not loaded: {_e!r}", flush=True)
# v8.1 round 47: Guardian articles for one film (web/guardian_live.py). 404 for everything while GUARDIAN_LIVE is off.
try:
    import guardian_live
    guardian_live.install(app, _gate, user_of)
except Exception as _e:  # noqa: BLE001
    print(f"[cinemack] guardian lookups not loaded: {_e!r}", flush=True)
# v8.1 round 48: the box-office forecast route (web/forecast_api.py). 404 for everything while FORECAST_PANEL is off.
try:
    import forecast_api
    forecast_api.install(app, _gate)
except Exception as _e:  # noqa: BLE001
    print(f"[cinemack] forecast route not loaded: {_e!r}", flush=True)
# v8.1 round 49: follow a person (web/follows.py). 404 for everything while FOLLOW_PEOPLE is off.
try:
    import follows
    follows.install(app, _gate, user_of)
except Exception as _e:  # noqa: BLE001
    print(f"[cinemack] follows not loaded: {_e!r}", flush=True)
# v8.1 round 50: anonymous shown / opened / saved events from the page (web/events_api.py). 404 while REC_EVENTS is off.
try:
    import events_api
    events_api.install(app, _gate)
except Exception as _e:  # noqa: BLE001
    print(f"[cinemack] events route not loaded: {_e!r}", flush=True)

# v8.1 round 55: stored news and reviews next to search results (web/news_search.py). 404 while SEARCH_NEWS is off.
try:
    import news_search
    news_search.install(app, _gate)
except Exception as _e:  # noqa: BLE001
    print(f"[cinemack] news search not loaded: {_e!r}", flush=True)


if __name__ == "__main__":
    import uvicorn
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "49151"))
    if not ACCESS_CODE and host != "127.0.0.1":
        print("[movie-buff] WARNING: no MOVIE_BUFF_CODE set - anyone who can reach "
              f"port {port} can use the model.", flush=True)
    print(f"[movie-buff] model: {pipeline.MODEL}   listening on {host}:{port}", flush=True)
    uvicorn.run(app, host=host, port=port, log_level="warning")
