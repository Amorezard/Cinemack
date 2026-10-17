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
"""

import asyncio
import hmac
import json
import os
import threading
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import List, Optional

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel

import pipeline
import chats

import sys
if str(pipeline.RAG_DIR) not in sys.path:      # feedback.py / personal.py live next to chat.py
    sys.path.insert(0, str(pipeline.RAG_DIR))

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


app = FastAPI(title="Movie Buff", lifespan=lifespan, docs_url=None, redoc_url=None)

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


def sse(event, **data):
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


@app.get("/")
def index():
    return FileResponse(HERE / "index.html", headers={"Cache-Control": "no-store"})


@app.get("/how")
def how():
    return FileResponse(HERE / "how.html", headers={"Cache-Control": "no-store"})


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


def user_of(request):
    """Anonymous browser id from the page (localStorage), for the taste profile and the
    per-user feedback cap. Not an account; nothing personal is stored with it."""
    u = request.headers.get("x-mb-user", "")[:64]
    return "".join(ch for ch in u if ch.isalnum() or ch in "-_")


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
    signal: int              # 1 liked the film, -1 not for me, 0 undo
    message_id: str = ""


@app.post("/api/film")
async def film_pref(fp: FilmPref, request: Request):
    """v3: "I liked this film" - a film preference, kept apart from answer feedback (personal.py)."""
    if not code_ok(request):
        return JSONResponse({"error": "Wrong or missing access code."}, status_code=401)
    user = user_of(request)
    if not user or fp.signal not in (-1, 0, 1):
        return JSONResponse({"error": "need a user id and signal -1, 0 or 1"}, status_code=400)
    try:
        import personal
        personal.record_film(user, fp.id, fp.title[:200], fp.signal, "card", fp.message_id[:40])
        return JSONResponse({"ok": True, "taste": personal.summary(user)})
    except Exception as e:
        print(f"[movie-buff] film preference failed: {e!r}", flush=True)
        return JSONResponse({"error": "couldn't save that"}, status_code=500)


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
    return JSONResponse(c) if c else JSONResponse({"error": "No such chat."}, status_code=404)


@app.delete("/api/chats/{conv_id}")
async def chat_delete(conv_id: str, request: Request):
    if not code_ok(request):
        return JSONResponse({"error": "Wrong or missing access code."}, status_code=401)
    return JSONResponse({"ok": chats.delete_chat(user_of(request), conv_id)})


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
    history = history[-pipeline.MAX_HISTORY_MESSAGES:]
    if hasattr(pipeline, "trim_history"):
        history = pipeline.trim_history(history)
    rid = uuid.uuid4().hex[:12]
    conv_in = req.conv if pipeline.CONV_ON else None
    if conv_in is not None and len(json.dumps(conv_in, default=str)) > 20000:
        conv_in = None                     # a client sending junk gets a fresh conversation
    user = user_of(request)
    conv_id = request.headers.get("x-mb-conv", "")[:40]
    utag = pipeline.user_tag(user) if hasattr(pipeline, "user_tag") else ""
    key = site_cache.answer_key(question, req.recent_titles, history, conv_in, utag)
    hit = None if req.think else site_cache.get_answer(key)

    if hit:
        # Change 24: a repeat (same question, same point in the conversation) - replay
        # the stored answer, no GPU needed. Now kept on disk and matched after
        # normalizing (web/site_cache.py).
        src, done = hit

        async def replay():
            yield sse("sources", **dict(src, id=rid))
            yield sse("status", stage="answering")
            yield sse("token", text=done["answer"])
            yield sse("done", **dict(done, id=rid, cached=True,
                                     timings={"total_s": 0.0, "retrieve_s": 0.0, "first_text_s": 0.0}))
        log_line("requests", {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "id": rid,
                              "question": question, "cached": True})
        chats.save_turn(user, conv_id, question, done.get("answer", ""), src.get("films"),
                        done.get("recent_titles"), done.get("conv"), rid, cached=True)
        return StreamingResponse(replay(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

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
            put(sse("status", stage="searching"))
            prompt, hits = pipeline.prepare(question, req.recent_titles, conv_in, user)
            trace = pipeline.last_trace()
            retrieve_s = time.perf_counter() - t0
            src = dict(films=[pipeline.card(h) for h in hits], steps=pipeline.steps(hits),
                       alternatives=pipeline.alternatives(hits), retrieve_s=round(retrieve_s, 2))
            put(sse("sources", id=rid, **src))
            if cancel.is_set():
                return

            put(sse("status", stage="answering"))
            messages = history + [{"role": "user", "content": prompt}]
            t1 = time.perf_counter()
            first = None
            answer = []
            for kind, text in pipeline.stream_answer(messages, req.think, cancel):
                if first is None:
                    first = time.perf_counter() - t0
                if kind == "token":
                    answer.append(text)
                put(sse(kind, text=text))

            final = "".join(answer).strip()
            if final and not cancel.is_set() and not getattr(pipeline, "MOCK", False):
                try:                                   # v4 WIKI_CREDIT=1: sources line added in code
                    import chat as _chat
                    credit = _chat.wiki_credit(hits) if hasattr(_chat, "wiki_credit") else ""
                except Exception:  # noqa: BLE001
                    credit = ""
                if credit:
                    put(sse("token", text=credit))
                    final += credit
            checked = pipeline.check(final, hits, question) if not cancel.is_set() else \
                {"ok": None, "flags": [], "checked": 0}
            done = dict(answer=final,
                        recent_titles=pipeline.next_recent_titles(hits, req.recent_titles),
                        conv=pipeline.next_conv(conv_in, question, hits, final) if not cancel.is_set() else conv_in,
                        stopped=cancel.is_set(), check=checked, cached=False,
                        timings={"retrieve_s": round(retrieve_s, 2),
                                 "first_text_s": round(first, 2) if first else None,
                                 "generate_s": round(time.perf_counter() - t1, 2),
                                 "total_s": round(time.perf_counter() - t0, 2)})
            put(sse("done", id=rid, **done))
            if final:
                chats.save_turn(user, conv_id, question, final, src["films"], done["recent_titles"],
                                done["conv"], rid)
            if final and not cancel.is_set() and not req.think and hits:
                site_cache.put_answer(key, question, src, done,
                                      pipeline.last_ttl() if hasattr(pipeline, "last_ttl") else None)
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
            if user and plan_prefs:
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


def friendly(e):
    text = str(e).lower()
    if "connection" in text or "refused" in text or "connecterror" in type(e).__name__.lower():
        return "Can't reach the model server (Ollama). It may be restarting; try again in a minute."
    if "not found" in text and "model" in text:
        return "The movie-buff model isn't loaded in Ollama. Run: ollama create movie-buff -f ~/movie-buff/Modelfile"
    if "no such table" in text or "unable to open database" in text:
        return "The movie database couldn't be opened. Check that movies.db is in ~/movie-buff/rag."
    return "Something went wrong answering that. Try again, or rephrase the question."


if __name__ == "__main__":
    import uvicorn
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "49151"))
    if not ACCESS_CODE and host != "127.0.0.1":
        print("[movie-buff] WARNING: no MOVIE_BUFF_CODE set - anyone who can reach "
              f"port {port} can use the model.", flush=True)
    print(f"[movie-buff] model: {pipeline.MODEL}   listening on {host}:{port}", flush=True)
    uvicorn.run(app, host=host, port=port, log_level="warning")
