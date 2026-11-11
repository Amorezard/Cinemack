"""
The one place the web server touches the Movie Buff pipeline.

server.py never imports chat.py or retrieve.py directly; it calls the four
functions below. If chat.py's build_prompt changes shape, this is the only
file that needs to follow it - and because it calls the SAME build_prompt the
terminal chat and the eval harness use, the eval numbers still describe what
people see in the browser.

Set MOVIE_BUFF_MOCK=1 to run the whole site with a fake model (no GPU, no
Ollama, no movies.db) - useful for checking the page itself.
"""

import os
import random
import sys
import time
from pathlib import Path

MOCK = os.environ.get("MOVIE_BUFF_MOCK") == "1"

import site_cache   # results/site_cache.sqlite: reused searches and answers (web only)

# This file lives in ~/movie-buff/rag/web/. chat.py, retrieve.py and movies.db
# live one level up, and retrieve() opens "movies.db" by relative path, so the
# working directory has to be the rag folder.
RAG_DIR = Path(__file__).resolve().parent.parent

MAX_HISTORY_MESSAGES = 12   # last 6 exchanges; num_ctx is 8192

if not MOCK:
    sys.path.insert(0, str(RAG_DIR))
    os.chdir(RAG_DIR)
    import ollama
    import chat  # the terminal chat's module - same prompt, same retrieval

    import inspect

    import functools

    try:
        import factcheck            # change 16, lives next to chat.py
    except ImportError:
        factcheck = None

    MODEL = chat.MODEL
    EMBED_MODEL = getattr(chat, "EMBED_MODEL", "mxbai-embed-large")
    KEEP_ALIVE = getattr(chat, "KEEP_ALIVE", "30m")
    _BUILD_TAKES_RECENT = "recent_titles" in inspect.signature(chat.build_prompt).parameters

    # Ask retrieve() for its step-by-step notes (title guesses, re-rank pick,
    # follow-up resolution...). explain=True only attaches h["_notes"] and
    # prints one log line - the hits and the prompt are unchanged, so the page
    # still gets exactly what chat.py and the eval get.
    # v3 observability: also ask for retrieve()'s trace (per-stage timings, model-call
    # counters, the gate decision) so every request log line says where the time went.
    # The server answers one question at a time (gpu_lock), so one module-level slot is enough.
    _TRACE = {"last": None}
    # 8 Oct fix: keep the real retrieve() before the patch below replaces chat.retrieve.
    # Calling chat.retrieve here (as before) called this wrapper itself -> RecursionError on
    # every live question (the site had only been tested in mock mode since 5 Oct).
    _retrieve_orig = getattr(chat.retrieve, "_mb_orig", chat.retrieve)
    _TAKES_TRACE = "trace" in inspect.signature(_retrieve_orig).parameters

    def _retrieve_logged(*a, **kw):
        if _TAKES_TRACE:
            tr = {}
            kw["trace"] = tr
            _TRACE["last"] = tr
        return _retrieve_orig(*a, **kw)

    if "explain" in inspect.signature(_retrieve_orig).parameters:
        _patched = functools.partial(_retrieve_logged, explain=True)
        _patched._mb_orig = _retrieve_orig      # a second import of this module finds the real one
        chat.build_prompt.__globals__["retrieve"] = _patched
    _BUILD_TAKES_CONV = "conv" in inspect.signature(chat.build_prompt).parameters
    CONV_ON = bool(getattr(chat._R, "CONV", False)) and _BUILD_TAKES_CONV
else:
    MODEL = "movie-buff (mock)"
    CONV_ON = False
    _TRACE = {"last": None}


def _opts(extra=None):
    """Ollama options for movie-buff: the same num_batch (NUM_BATCH) as retrieve.py's
    planner and re-ranker calls, so Ollama never reloads the model between them."""
    lo = getattr(chat, "llm_options", None) if not MOCK else None
    return lo(extra) if lo else dict(extra or {})


# ------------------------------------------------------------------ pipeline

def user_tag(user):
    """Cache key part for the person's taste profile: changes whenever they like/dislike a film,
    so a personalised recommendation is never replayed to someone else or after their tastes move."""
    if MOCK or not user:
        return ""
    try:
        import personal
        if not personal.PERSONAL:
            return ""
        likes, dis, st = personal.load(user)
        return f"{user}:{len(likes)}:{len(dis)}:{len(st)}" if (likes or dis or st) else ""
    except Exception:
        return ""


def prepare(question, recent_titles, conv=None, user=None):
    """Retrieval + prompt building. Returns (prompt, hits).
    conv: v3 conversation state from the page (CONV=1); None = v2 behaviour.
    user: anonymous browser id (PERSONAL=1 taste profile)."""
    _TRACE["last"] = None
    if MOCK:
        return _mock_prepare(question)
    use_conv = CONV_ON and conv is not None
    tag = user_tag(user)
    got = site_cache.get_retrieval(question, recent_titles, conv if use_conv else None, tag)
    if got:                  # asked before: skip the planner, search and re-ranker
        return got
    kw = {"user": user} if "user" in inspect.signature(chat.build_prompt).parameters else {}
    if use_conv:
        prompt, hits = chat.build_prompt(question, conv=conv, **kw)
    elif _BUILD_TAKES_RECENT:
        prompt, hits = chat.build_prompt(question, recent_titles=recent_titles or None, **kw)
    else:
        prompt, hits = chat.build_prompt(question)
    site_cache.put_retrieval(question, recent_titles, prompt, hits, conv if use_conv else None, tag, last_ttl())
    return prompt, hits


TTL_CURRENT = 6 * 3600       # now playing / upcoming: refreshed daily, so never reuse past a few hours
TTL_RECENT = 3 * 86400       # films still in release: box office and ratings still move


def last_ttl():
    """v3 cache freshness: how long the answer just built may be reused (None = no limit)."""
    lt = getattr(chat, "LAST_TOOL", None) if not MOCK else None
    tasks = ((lt or {}).get("route") or {}).get("tasks") or []
    if "current" in tasks:
        return TTL_CURRENT
    if any(k == "facts" for k, _b in (lt or {}).get("blocks") or []) and \
            any("Status: upcoming" in b or "Status: now playing" in b for _k, b in lt.get("blocks") or []):
        return TTL_CURRENT
    if "facts" in tasks or "market" in tasks or "reviews" in tasks:
        return TTL_RECENT
    return None


def next_conv(conv, question, hits, answer):
    """v3: the conversation state after this exchange (sent back to the page)."""
    if MOCK or not CONV_ON:
        return None
    import conv as _conv
    plan = (hits[0].get("_plan") if hits else None)
    return _conv.update(conv, question, hits, plan=plan, answer=answer)


def last_trace():
    """Per-stage numbers from the retrieve() call that just ran (None when it was cached)."""
    tr = _TRACE.get("last")
    if not tr:
        return None
    keep = ("path", "planner", "plan_ms", "search_ms", "rerank_ms", "total_ms", "gate", "rerank_skipped",
            "llm", "passages", "pool_size", "merged_description", "plan")
    out = {k: tr[k] for k in keep if k in tr}
    lt = getattr(chat, "LAST_TOOL", None) if not MOCK else None
    if lt:
        out["tools"] = {"route": lt.get("route"), "ms": lt.get("ms"), "style": lt.get("style"),
                        "blocks": [(k, len(b)) for k, b in lt.get("blocks") or []]}
    return out


HISTORY_CHARS = int(os.environ.get("HISTORY_CHARS", "6000"))


def trim_history(history, budget=HISTORY_CHARS):
    """v3 context management: the answer model gets the conversation, but not unbounded.
    Newest messages are kept whole; older assistant answers are cut to their first 400
    characters; whatever still doesn't fit is dropped, oldest first. (The planner gets its
    own compact view of the whole conversation from conv.py.)"""
    if not budget:
        return history
    out, used = [], 0
    for i, m in enumerate(reversed(history)):
        c = m["content"]
        if i >= 2 and m["role"] == "assistant" and len(c) > 400:
            c = c[:400].rsplit(" ", 1)[0] + " ..."
        if used + len(c) > budget and out:
            break
        out.append({"role": m["role"], "content": c})
        used += len(c)
    out.reverse()
    while out and out[0]["role"] != "user":
        out.pop(0)
    return out


_VERSIONS = None


def versions():
    """What produced an answer: code commit, settings fingerprint, models. Logged with every
    request and every piece of feedback, so a thumbs-down can be traced to a version."""
    global _VERSIONS
    if _VERSIONS is None:
        import subprocess
        try:
            git = subprocess.run(["git", "-C", str(RAG_DIR), "describe", "--always", "--dirty", "--tags"],
                                 capture_output=True, text=True, timeout=5).stdout.strip()
        except Exception:
            git = ""
        _VERSIONS = {"git": git, "fingerprint": site_cache.fingerprint() if not MOCK else "mock",
                     "model": MODEL, "conv": CONV_ON,
                     "prompt_version": getattr(chat, "PROMPT_VERSION", "v2") if not MOCK else "mock"}
    return _VERSIONS


def next_recent_titles(hits, previous):
    """What the next turn's "who directed it?" should resolve to. Mirrors
    chat.py: only move on when this turn actually retrieved something."""
    return [h["title"] for h in hits] if hits else list(previous or [])


def stream_answer(messages, think, cancel):
    """Yields ("thinking", text) and ("token", text) until done or cancelled.
    Leaving the loop closes the HTTP stream to Ollama, which stops generation
    on the GPU rather than finishing an answer nobody will read."""
    if MOCK:
        yield from _mock_stream(messages, think, cancel)
        return

    import persona                                   # round 27: PERSONA=cinemack (default off = unchanged)
    messages = persona.with_system(messages, MODEL)
    stream = ollama.chat(model=MODEL, messages=messages, stream=True,
                         think=think, keep_alive=KEEP_ALIVE, options=_opts())
    try:
        for chunk in stream:
            if cancel.is_set():
                break
            msg = chunk.get("message") if isinstance(chunk, dict) else chunk.message
            get = (lambda k: msg.get(k)) if isinstance(msg, dict) else (lambda k: getattr(msg, k, None))
            thought = get("thinking") or ""
            piece = get("content") or ""
            if thought:
                yield "thinking", thought
            if piece:
                yield "token", piece
    finally:
        close = getattr(stream, "close", None)
        if close:
            close()


def _rating(hit):
    """TMDB rating as the model saw it. retrieve.py doesn't carry it, so read
    it the way chat.context_for does - including its 50-vote minimum, so a
    chip never shows a rating the model wasn't given."""
    if "vote_average" in hit:
        va, vc = hit.get("vote_average"), hit.get("vote_count") or 50
    elif not MOCK and hit.get("id") is not None and hasattr(chat, "_CTX_DB"):
        row = chat._CTX_DB.execute(
            "SELECT vote_average, vote_count FROM movies WHERE id = ?", (hit["id"],)).fetchone()
        va, vc = row if row else (None, 0)
    else:
        return None
    try:
        return round(float(va), 1) if va and (vc or 0) >= 50 else None
    except (TypeError, ValueError):
        return None


def steps(hits):
    """retrieve()'s notes, turned into short lines for the page's Details view."""
    notes = (hits[0].get("_notes") if hits else None) or {}
    out = []
    if hits and hits[0].get("_cached"):
        out.append("Reused the search from an earlier question with the same wording")
    if notes.get("followup_resolved_to"):
        out.append(f'Follow-up: read "it" as {notes["followup_resolved_to"]}')
    f = notes.get("filters") or {}
    bits = []
    if f.get("year_min") is not None:
        lo, hi = f["year_min"], f.get("year_max")
        bits.append(str(lo) if lo == hi else f"{lo}–{hi}")
    if f.get("genres"):
        bits.append(", ".join(f["genres"]))
    if bits:
        out.append("Filtered to " + " · ".join(bits))
    if notes.get("title_match_injected") or notes.get("title_match_promoted"):
        out.append(f'Matched the title "{notes.get("title_match_injected") or notes.get("title_match_promoted")}" directly')
    if notes.get("soft_filters"):
        out.append("Treated the year/genre as a hint, not a hard filter (people often misremember dates)")
    if notes.get("multi_film"):
        out.append("Looked up each film you named: " + ", ".join(notes["multi_film"]))
    if notes.get("title_guesses_used"):
        out.append("Model's guesses that the database confirmed: " + ", ".join(notes["title_guesses_used"]))
    elif notes.get("title_guesses"):
        out.append("Model guessed " + ", ".join(notes["title_guesses"]) + " (none close enough to use)")
    if notes.get("reranked_to"):
        ms = notes.get("rerank_ms")
        out.append("Read the candidate plots and picked " + ", ".join(notes["reranked_to"])
                   + (f" ({ms / 1000:.1f}s)" if ms else ""))
    if notes.get("title_guesses_shown"):
        out.append("Also showed the reader its own guesses: " + ", ".join(notes["title_guesses_shown"]))
    if notes.get("second_look"):
        out.append("Nothing fit at first, so it read the next batch of candidates")
    if notes.get("kept_guess"):
        out.append(f'Kept the model\'s guess "{notes["kept_guess"]}" in 2nd place')
    if notes.get("backfilled"):
        out.append(f'Added {notes["backfilled"]} well-known films matching the filters')
    return out


def check(answer, hits, question):
    """Change 16: rule-based fact check of the finished answer against the films
    it was given (factcheck.py). {"ok", "flags", "checked"}; ok=None if skipped."""
    if MOCK:                                    # test mode: show what the badge looks like
        return {"ok": True, "flags": [], "checked": 3} if hits else {"ok": None, "flags": [], "checked": 0}
    if not hits or not answer.strip() or factcheck is None:
        return {"ok": None, "flags": [], "checked": 0}
    try:
        res = factcheck.fact_check(answer, hits, question)
    except Exception as e:                      # never break an answer over the check
        print(f"[movie-buff] fact check failed: {e!r}", flush=True)
        return {"ok": None, "flags": [], "checked": 0}
    # 8 Oct: the check only knows the film cards (movies.db), so with TOOLS=1 every budget, gross,
    # award or crew fact taken from facts.db is flagged as "Not backed by the database" - a false
    # alarm. Hide the warning on the page (ok=None shows no badge); the flags stay in requests.jsonl.
    # FACTCHECK_WARN=1 shows it again.
    if res.get("ok") is False and os.environ.get("FACTCHECK_WARN", "0") != "1":
        return {"ok": None, "flags": res.get("flags", []), "checked": res.get("checked", 0), "hidden": True}
    return res


def alternatives(hits):
    """Change 19 (simple version): for a plot description, the other films the
    search found, offered as "Not the one you meant?" buttons."""
    notes = (hits[0].get("_notes") if hits else None) or {}
    describing = bool(notes.get("reranked_to") or notes.get("title_guesses")
                      or (notes.get("plan") or {}).get("intent") == "identify")
    if not describing or len(hits) < 2:
        return []
    return [{"title": h.get("title"), "year": str(h.get("release_year") or "")[:4]}
            for h in hits[1:3] if h.get("title")]


def card(hit):
    """The fields the page shows for each retrieved film. Every field is
    optional - different versions of retrieve.py carry different columns."""
    cast = hit.get("top_cast") or ""
    rating = _rating(hit) if not (hit.get("_facts_only") or hit.get("_tv")) else None
    if hit.get("_tv") and not MOCK:                 # v7 D1: a TV show (id = -TMDB TV id, never a film id)
        try:
            import tv as _tv
            extra = _tv.card(hit)
        except Exception:  # noqa: BLE001
            extra = {"kind": "tv"}
        return dict({"id": hit.get("id"), "why": hit.get("_why_you"), "title": hit.get("title"),
                     "year": hit.get("years") or hit.get("release_year"), "director": hit.get("director"),
                     "cast": ", ".join(c.strip() for c in cast.split(",")[:3] if c.strip()) or None,
                     "genres": hit.get("genres"), "rating": None}, **extra)
    return {
        "id": hit.get("id"),
        "why": hit.get("_why_you"),
        "title": hit.get("title"),
        "year": hit.get("release_year"),
        "director": hit.get("director"),
        "cast": ", ".join(c.strip() for c in cast.split(",")[:3] if c.strip()) or None,
        "genres": hit.get("genres"),
        "rating": rating,
    }


# ------------------------------------------------------------ health + warmup

def health():
    if MOCK:
        return {"ok": True, "model": MODEL, "mock": True}
    try:
        ollama.show(MODEL)
    except Exception as e:  # Ollama down, or model not created
        return {"ok": False, "model": MODEL, "error": str(e)[:200]}
    if not (RAG_DIR / "movies.db").exists():
        return {"ok": False, "model": MODEL, "error": f"movies.db not found in {RAG_DIR}"}
    return {"ok": True, "model": MODEL}


def prewarm():
    """Load both models into VRAM at startup so the first visitor doesn't pay
    the load time. Same calls as chat.prewarm(), minus the printing."""
    if MOCK:
        return
    try:
        ollama.embed(model=EMBED_MODEL, input=["warmup"], keep_alive=KEEP_ALIVE)
        ollama.chat(model=MODEL, messages=[{"role": "user", "content": "hi"}],
                    options=_opts({"num_predict": 1}), keep_alive=KEEP_ALIVE)
        print("[movie-buff] models warm", flush=True)
    except Exception as e:
        print(f"[movie-buff] warmup failed: {e}", flush=True)


# ------------------------------------------------------------------- mock

_MOCK_FILMS = [
    {"id": 496243, "title": "Parasite", "release_year": "2019", "director": "Bong Joon Ho",
     "top_cast": "Song Kang-ho, Lee Sun-kyun, Cho Yeo-jeong, Choi Woo-shik",
     "genres": "Comedy, Thriller, Drama", "vote_average": 8.5},
    {"id": 900001, "title": "The Joneses", "release_year": "2009", "director": "Derrick Borte",
     "top_cast": "David Duchovny, Demi Moore, Amber Heard",
     "genres": "Comedy, Drama", "vote_average": 6.2},
    {"id": 900002, "title": "Six Degrees of Separation", "release_year": "1993", "director": "Fred Schepisi",
     "top_cast": "Stockard Channing, Will Smith, Donald Sutherland",
     "genres": "Drama, Comedy", "vote_average": 6.8},
]


def _mock_prepare(question):
    time.sleep(1.2 if len(question) < 60 else 3.5)
    if "cobalt hour" in question.lower():
        return question, []
    notes = {"title_guesses": ["The Parent Trap (1998)", "Parasite (2019)"],
             "title_guesses_used": ["Parasite (0.826)"],
             "reranked_to": ["Parasite", "Six Degrees of Separation", "The Joneses"],
             "rerank_ms": 1401}
    hits = [dict(f, _notes=notes) for f in _MOCK_FILMS]     # 900001/900002: made-up ids, mock only
    hits[0]["_plan"] = {"intent": "recommend" if "recommend" in question.lower() else "identify"}
    return question, hits


def _mock_stream(messages, think, cancel):
    if think:
        for w in ("The description mentions a poor family and a wealthy household. "
                  "Parasite's plot fits that best. ").split(" "):
            if cancel.is_set():
                return
            time.sleep(0.03)
            yield "thinking", w + " "
    text = ("That's **Parasite** (2019), directed by Bong Joon Ho. The Kim family, "
            "all unemployed, talk their way into jobs with the wealthy Park family "
            "one at a time, without letting on that they're related.\n\n"
            "It's a sharp, darkly funny thriller about class, and it won Best Picture. "
            "I'll stop there, since the second half is best seen cold.\n\n"
            "If you like it, try:\n- *Memories of Murder* (same director)\n- *Burning* (a slow-burn class thriller)")
    time.sleep(0.8)
    for w in text.split(" "):
        if cancel.is_set():
            return
        time.sleep(random.uniform(0.02, 0.06))
        yield "token", w + " "
