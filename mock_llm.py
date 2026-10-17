#!/usr/bin/env python3
"""
Full-pipeline mock model client (Future Work: "a stand-in for Ollama that returns believable
model responses without running a model"), so the pipeline, the tools, the review scripts,
the new v4 scripts and the website run on a laptop with no GPU and no Ollama.

    MOCK=off      (default) real Ollama
    MOCK=replay   every ollama.chat / ollama.embed call is looked up in results/llm_cache.sqlite
                  (the 1 GB of real calls recorded by stage_scores.py / eval_v3.py, same key), and
                  only calls never recorded are faked. Any question already run in an evaluation
                  replays offline with the real model's outputs.
    MOCK=fake     everything faked: embeddings from a hash of the text (same text -> same vector),
                  structured-output calls get a valid object for their JSON schema, the answer
                  model returns a template answer naming the films in its prompt.
    MOCK=record   real calls, saved to the cache as they run (= stage_scores.enable_cache), so
                  the replay set grows with every run on the server.

retrieve.py installs it when MOCK is set, so chat.py, tools.py, eval_v3.py, stage_scores.py and
web/server.py all use it. Scripts that call Ollama over HTTP (reviews_analyze.py, wiki_embed.py,
summarize.py) can use the fake HTTP server instead:

    python mock_llm.py serve --port 11435        # then: --ollama http://localhost:11435
    python mock_llm.py stats                     # how many calls the cache holds, by kind
    python mock_llm.py selftest                  # fake chat / schema / embed / stream round trip

What the mock tests: prompts are built, JSON is parsed, questions reach the right tool, review
filters behave, conversation state carries between turns, the web UI works end to end.
What it cannot test: answer quality, ranking quality with fake embeddings (meaningless), speed.
Every faked call is logged to logs/mock_calls.jsonl.
"""
import hashlib
import json
import math
import os
import re
import sqlite3
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.environ.get("MOCK_CACHE", os.path.join(HERE, "results", "llm_cache.sqlite"))
LOG = os.path.join(HERE, "logs", "mock_calls.jsonl")
DIM = int(os.environ.get("MOCK_DIM", "1024"))          # mxbai-embed-large
_STATE = {"installed": None, "db": None, "stats": {"replayed": 0, "faked": 0}}


# ------------------------------------------------------------------ fakes
def fake_vector(text, dim=DIM):
    """Deterministic unit vector from the text: identical text -> identical vector; texts sharing
    words get somewhat similar vectors (bag of hashed words), so cosine tests behave sensibly."""
    v = [0.0] * dim
    words = re.findall(r"[a-z0-9]+", str(text).lower()) or ["empty"]
    for w in words:
        h = hashlib.sha1(w.encode()).digest()
        for j in range(4):
            idx = int.from_bytes(h[j * 4:(j + 1) * 4], "little") % dim
            v[idx] += 1.0 if h[16 + j] & 1 else -1.0
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


def _last_user(messages):
    for m in reversed(messages or []):
        if m.get("role") == "user":
            return str(m.get("content") or "")
    return ""


_IDX = {"i": 0}      # position inside a faked array (so integer ids come out 1, 2, 3)


def _value_for(name, schema, text):
    t = schema.get("type")
    if "enum" in schema:
        return schema["enum"][0]
    if t == "object":
        return fake_object(schema, text)
    if t == "array":
        item = schema.get("items") or {"type": "string"}
        n = max(3 if item.get("type") == "object" else 2, int(schema.get("minItems") or 0))
        out = []
        for i in range(n):
            _IDX["i"] = i
            out.append(_value_for(name, item, text))
        _IDX["i"] = 0
        return out
    if t in ("integer", "number"):
        lo = schema.get("minimum", 1) + _IDX["i"]
        return lo if t == "integer" else float(lo)
    if t == "boolean":
        return False
    words = re.findall(r"[A-Za-z]{4,}", text)[_IDX["i"] * 3:_IDX["i"] * 3 + 6] or re.findall(r"[A-Za-z]{4,}", text)[:6]
    if name in ("search_query", "query"):
        return " ".join(text.split()[-25:])
    if name in ("quote",):
        return " ".join(text.split()[:6])
    return " ".join(words) or f"mock {name}"


def fake_object(schema, text=""):
    out = {}
    props = schema.get("properties") or {}
    for k, s in props.items():
        out[k] = _value_for(k, s, text)
    return out


def fake_answer(messages):
    prompt = _last_user(messages)
    titles = re.findall(r"^(?:Title: )?([A-Z][^\n|()]{1,60}) \((\d{4})\)", prompt, re.M)
    seen, names = set(), []
    for t, y in titles:
        if t not in seen:
            seen.add(t)
            names.append(f"**{t.strip()}** ({y})")
    q = re.search(r"Question: (.+)", prompt)
    body = ("[mock answer] " + (f"For \"{q.group(1).strip()[:120]}\": " if q else "")
            + (("the context names " + ", ".join(names[:3]) + ".") if names else "the context names no films."))
    return body


def _log(kind, model, why):
    _STATE["stats"]["faked"] += 1
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(LOG, "a") as f:
            f.write(json.dumps({"t": time.strftime("%Y-%m-%d %H:%M:%S"), "kind": kind, "model": model,
                                "why": why}) + "\n")
    except OSError:
        pass


def fake_chat(**kw):
    fmt = kw.get("format")
    msgs = kw.get("messages") or []
    if isinstance(fmt, dict):
        content = json.dumps(fake_object(fmt, _last_user(msgs)))
    elif fmt == "json":
        content = "{}"
    else:
        content = fake_answer(msgs)
    return {"model": kw.get("model"), "message": {"role": "assistant", "content": content}, "done": True}


def fake_embed(**kw):
    inp = kw.get("input")
    texts = [inp] if isinstance(inp, str) else list(inp or [])
    return {"model": kw.get("model"), "embeddings": [fake_vector(t) for t in texts]}


def _stream(content):
    for i, tok in enumerate(re.findall(r"\S+\s*", content)):
        yield {"message": {"role": "assistant", "content": tok}, "done": False}
    yield {"message": {"role": "assistant", "content": ""}, "done": True}


# ------------------------------------------------------------------ replay
def _db():
    if _STATE["db"] is None and os.path.exists(CACHE):
        _STATE["db"] = sqlite3.connect(f"file:{CACHE}?mode=ro", uri=True, check_same_thread=False)
    return _STATE["db"]


def cache_key(kind, kw):
    """Exactly stage_scores.enable_cache's key, so recorded calls are found."""
    kw = {k: v for k, v in kw.items() if k != "keep_alive"}
    return hashlib.sha1(json.dumps([kind, kw], sort_keys=True, default=str).encode()).hexdigest()


def lookup(kind, kw):
    db = _db()
    if db is None:
        return None
    row = db.execute("SELECT v FROM c WHERE k = ?", (cache_key(kind, kw),)).fetchone()
    return json.loads(row[0]) if row else None


# ------------------------------------------------------------------ install
def install(mode=None):
    """Patch the ollama module in place. Idempotent. Returns the mode in effect."""
    mode = (mode or os.environ.get("MOCK", "off")).lower()
    if mode in ("", "off", "0") or _STATE["installed"] == mode:
        return _STATE["installed"] or "off"
    if mode == "record":
        import stage_scores
        stage_scores.enable_cache(CACHE)
        _STATE["installed"] = mode
        return mode
    if mode not in ("fake", "replay"):
        raise SystemExit(f"MOCK must be off, fake, replay or record, not {mode!r}")
    import ollama

    def chat(*args, **kw):
        if args:
            kw = dict(zip(("model", "messages"), args), **kw)
        stream = kw.get("stream")
        hit = None
        if mode == "replay":
            hit = lookup("chat", {k: v for k, v in kw.items() if k != "stream"} if not stream else kw)
        if hit is not None:
            _STATE["stats"]["replayed"] += 1
            return _stream(hit["message"]["content"]) if stream else hit
        _log("chat", kw.get("model"), "not in cache" if mode == "replay" else "fake")
        r = fake_chat(**kw)
        return _stream(r["message"]["content"]) if stream else r

    def embed(*args, **kw):
        if args:
            kw = dict(zip(("model", "input"), args), **kw)
        hit = lookup("embed", kw) if mode == "replay" else None
        if hit is not None:
            _STATE["stats"]["replayed"] += 1
            return hit
        _log("embed", kw.get("model"), "not in cache" if mode == "replay" else "fake")
        return fake_embed(**kw)

    def generate(*args, **kw):
        if args:
            kw = dict(zip(("model", "prompt"), args), **kw)
        r = fake_chat(model=kw.get("model"), messages=[{"role": "user", "content": kw.get("prompt", "")}],
                      format=kw.get("format"))
        _log("generate", kw.get("model"), "fake")
        return {"response": r["message"]["content"], "done": True}

    def show(model=None, *a, **k):
        return {"modified_at": "mock", "modelfile": f"FROM {model}\n", "details": {"family": "mock"}}

    def ps(*a, **k):
        return {"models": []}

    def list_(*a, **k):
        return {"models": [{"name": n, "model": n} for n in ("movie-buff", "llama3.2", "mxbai-embed-large",
                                                              "gemma3:27b")]}

    ollama.chat, ollama.embed, ollama.generate = chat, embed, generate
    ollama.show, ollama.ps, ollama.list = show, ps, list_
    _STATE["installed"] = mode
    print(f"[mock_llm] MOCK={mode}: model calls are {'replayed from ' + CACHE + ' or ' if mode == 'replay' else ''}"
          f"faked (logged to {LOG})", file=sys.stderr)
    return mode


def stats():
    return dict(_STATE["stats"])


# ------------------------------------------------------------------ fake HTTP server
def serve(port=11435):
    """A fake Ollama over HTTP for scripts that use urllib (reviews_analyze.py, wiki_embed.py ...)."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, obj, code=200):
            b = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)

        def do_GET(self):
            if self.path.startswith("/api/tags"):
                return self._send({"models": [{"name": "movie-buff"}, {"name": "llama3.2"},
                                              {"name": "mxbai-embed-large"}]})
            if self.path.startswith("/api/ps"):
                return self._send({"models": []})
            self._send({"error": "not found"}, 404)

        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n) or b"{}")
            if self.path.startswith("/api/chat"):
                _log("chat", body.get("model"), "http fake")
                return self._send(fake_chat(**body))
            if self.path.startswith("/api/generate"):
                r = fake_chat(model=body.get("model"), format=body.get("format"),
                              messages=[{"role": "user", "content": body.get("prompt", "")}])
                return self._send({"response": r["message"]["content"], "done": True})
            if self.path.startswith("/api/embed"):
                _log("embed", body.get("model"), "http fake")
                return self._send(fake_embed(**body))
            if self.path.startswith("/api/embeddings"):
                return self._send({"embedding": fake_vector(body.get("prompt", ""))})
            if self.path.startswith("/api/show"):
                return self._send({"modelfile": f"FROM {body.get('model') or body.get('name')}\n",
                                   "modified_at": "mock"})
            self._send({"error": "not found"}, 404)

    srv = ThreadingHTTPServer(("127.0.0.1", port), H)
    print(f"fake Ollama on http://localhost:{port} (Ctrl-C to stop)")
    srv.serve_forever()


def _selftest():
    install("fake")
    import ollama
    schema = {"type": "object", "properties": {"intent": {"type": "string", "enum": ["identify", "named_film"]},
                                               "films": {"type": "array", "items": {"type": "object", "properties": {
                                                   "title": {"type": "string"}}}},
                                               "search_query": {"type": "string"}}, "required": ["intent"]}
    r = ollama.chat(model="movie-buff", format=schema, messages=[{"role": "user", "content": "that movie with a shark"}])
    plan = json.loads(r["message"]["content"])
    assert plan["intent"] == "identify" and isinstance(plan["films"], list), plan
    e = ollama.embed(model="mxbai-embed-large", input=["a shark", "a shark", "space"])["embeddings"]
    assert e[0] == e[1] and len(e[0]) == DIM
    cos = sum(a * b for a, b in zip(e[0], e[2]))
    assert abs(cos) < 0.5
    a = ollama.chat(model="movie-buff", messages=[{"role": "user", "content": "Jaws (1975)\nQuestion: who directed it?"}])
    assert "Jaws" in a["message"]["content"], a
    toks = "".join(c["message"]["content"] for c in ollama.chat(model="movie-buff", stream=True,
                                                                messages=[{"role": "user", "content": "hi"}]))
    assert toks.startswith("[mock answer]"), toks
    print("selftest ok:", plan, "| answer:", a["message"]["content"][:80])


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "serve":
        port = int(sys.argv[sys.argv.index("--port") + 1]) if "--port" in sys.argv else 11435
        serve(port)
    elif cmd == "stats":
        db = _db()
        if db is None:
            print(f"no cache at {CACHE}")
        else:
            print(f"{CACHE}: {db.execute('select count(*) from c').fetchone()[0]:,} recorded calls")
    elif cmd == "selftest":
        _selftest()
    else:
        print(__doc__)
