"""
Persistent cache for the website. The evaluation scripts never use it
(stage_scores.py and run_eval.py call retrieve() directly), so it cannot change
any measured number.

Two tables in results/site_cache.sqlite:

  retrieval  question -> (prompt, films). Skips the planner, the search and the
             re-ranker (5-16 s) whenever a question has been asked before, in any
             conversation. The answer is still written fresh unless the answer
             table also has it. A follow-up ("who directed it?") is only reused
             for the same recent films.
  answers    the whole answer for a question at the same point in the same
             conversation (question + recent films + history), like the old
             in-memory cache, but it survives restarts.

Questions are matched after light normalizing: case, curly quotes, extra spaces,
a filler word in front ("hey,", "ok so"), "can you tell me" and "please"/"thanks" at the
ends, and the final ?/!/. are ignored. "Who directed Jaws?" and "hey, who directed
jaws" share one entry.

Every key includes a fingerprint of retrieve.py, chat.py, the settings they read
from the environment, movies.db / wiki.db and the movie-buff model, so changing any
of those never serves an old result. Only good results are stored: a question the
planner failed on (regex fallback) or that found nothing is not cached.

    python web/site_cache.py stats          # rows, reuse counts, most reused questions
    python web/site_cache.py clear          # empty both tables
SITE_CACHE=0 turns it off.
"""
import hashlib
import json
import os
import re
import sqlite3
import sys
import threading
import time
import unicodedata
from pathlib import Path

RAG_DIR = Path(__file__).resolve().parent.parent
PATH = RAG_DIR / "results" / "site_cache.sqlite"
ENABLED = os.environ.get("SITE_CACHE", "1") != "0" and os.environ.get("MOVIE_BUFF_MOCK") != "1"
MAX_ROWS = 5000                 # per table; least recently used rows go first
ENV_SKIP = {"NUM_BATCH"}        # settings that change speed, not results

_lock = threading.Lock()
_db = None
_fp = None

# ------------------------------------------------------------ normalizing

_GREETING = re.compile(r"^(?:(?:hey|ok|okay|so|um|uh)\b[\s,!.:-]*)+")
_ASKING = re.compile(r"^(?:(?:can|could|would) you (?:please )?(?:tell me|help me (?:find|figure out))\s+"
                     r"|please\s+|i want to know\s+|i'd like to know\s+)")
_TAIL = re.compile(r"(?:[\s,]*(?:please|thanks|thank you|thx|ty))+\s*[.!]*$")
# Same test as retrieve.py's follow-up resolution: pronouns and "the first one" style
# references. Only such questions depend on which films the conversation was about.
_REFERS_BACK = re.compile(
    r"\b(it|its|it's|that|this|those|these|them|they|he|she|him|her|hers|the film|the movie|"
    r"the one|the first|the second|the third|the last|the original|the sequel|the prequel|"
    r"the remake|the ending|the end|part (?:one|two|three|ii|iii|[1-6])|first one|other one)\b")


def normalize_question(q):
    q = unicodedata.normalize("NFKC", q or "").casefold()
    q = q.translate({0x2018: "'", 0x2019: "'", 0x201C: '"', 0x201D: '"', 0x2013: "-", 0x2014: "-"})
    q = " ".join(q.split())
    q = _GREETING.sub("", q)
    q = _ASKING.sub("", q)
    q = _TAIL.sub("", q.rstrip(" ?!."))
    return q.rstrip(" ?!.,").strip()


def refers_back(q):
    return bool(_REFERS_BACK.search(normalize_question(q)))


# ------------------------------------------------------------ fingerprint

def fingerprint():
    """Changes whenever the code, a setting, the data or the model changes."""
    global _fp
    if _fp is not None:
        return _fp
    h = hashlib.md5()
    names = set()
    for name in ("retrieve.py", "chat.py", "answer_style.py"):
        p = RAG_DIR / name
        if p.exists():
            src = p.read_bytes()
            h.update(src)
            names |= set(re.findall(rb'environ\.get\(\s*"([A-Z0-9_]+)"', src))
    for n in sorted(names):
        k = n.decode()
        if k not in ENV_SKIP:
            h.update(f"{k}={os.environ.get(k, '')};".encode())
    for db in ("movies.db", "wiki.db"):
        p = RAG_DIR / db
        if p.exists():
            st = p.stat()
            h.update(f"{db}:{st.st_size}:{int(st.st_mtime)};".encode())
    try:
        import ollama
        info = ollama.show("movie-buff")
        mod = info.get("modified_at") if isinstance(info, dict) else getattr(info, "modified_at", None)
        h.update(str(mod).encode())
    except Exception:
        pass
    _fp = h.hexdigest()[:12]
    return _fp


# ------------------------------------------------------------ storage

def _conn():
    global _db
    if _db is None:
        PATH.parent.mkdir(exist_ok=True)
        _db = sqlite3.connect(PATH, check_same_thread=False)
        _db.execute("PRAGMA journal_mode=WAL")
        for t in ("retrieval", "answers"):
            _db.execute(f"""CREATE TABLE IF NOT EXISTS {t} (
                key TEXT PRIMARY KEY, fp TEXT, question TEXT, a TEXT, b TEXT,
                created REAL, used REAL, reused INTEGER DEFAULT 0)""")
        _db.commit()
    return _db


def _key(*parts):
    return hashlib.sha1(json.dumps([fingerprint(), *parts], sort_keys=True,
                                   default=str).encode()).hexdigest()


def _get(table, key):
    if not ENABLED:
        return None
    try:
        with _lock:
            db = _conn()
            row = db.execute(f"SELECT a, b FROM {table} WHERE key = ?", (key,)).fetchone()
            if row:
                db.execute(f"UPDATE {table} SET used = ?, reused = reused + 1 WHERE key = ?",
                           (time.time(), key))
                db.commit()
        return (json.loads(row[0]), json.loads(row[1])) if row else None
    except Exception as e:                  # a cache problem must never break an answer
        print(f"[movie-buff] cache read failed: {e!r}", flush=True)
        return None


def _put(table, key, question, a, b):
    if not ENABLED:
        return
    try:
        now = time.time()
        with _lock:
            db = _conn()
            db.execute(f"INSERT OR REPLACE INTO {table} (key, fp, question, a, b, created, used, reused) "
                       f"VALUES (?, ?, ?, ?, ?, ?, ?, 0)",
                       (key, fingerprint(), question, json.dumps(a, default=str),
                        json.dumps(b, default=str), now, now))
            db.execute(f"DELETE FROM {table} WHERE key IN (SELECT key FROM {table} "
                       f"ORDER BY used DESC LIMIT -1 OFFSET ?)", (MAX_ROWS,))
            db.commit()
    except Exception as e:
        print(f"[movie-buff] cache write failed: {e!r}", flush=True)


# ------------------------------------------------------------ retrieval

def retrieval_key(question, recent_titles):
    recent = list(recent_titles or []) if refers_back(question) else []
    return _key("retrieval", normalize_question(question), recent)


def get_retrieval(question, recent_titles):
    """(prompt, hits) for this question, or None. The prompt is rewritten to carry
    this question's own wording."""
    got = _get("retrieval", retrieval_key(question, recent_titles))
    if not got:
        return None
    (prompt, asked), hits = got
    prompt = prompt.replace(f"Question: {asked}", f"Question: {question}", 1)
    for h in hits:
        h["_cached"] = True
    return prompt, hits


def put_retrieval(question, recent_titles, prompt, hits):
    notes = (hits[0].get("_notes") if hits else None) or {}
    if not hits or "plan" not in notes:     # found nothing, or the planner failed
        return
    clean = [{k: v for k, v in h.items() if k != "_cached"} for h in hits]
    _put("retrieval", retrieval_key(question, recent_titles), question, [prompt, question], clean)


# ------------------------------------------------------------ answers

def answer_key(question, recent_titles, history):
    return _key("answer", normalize_question(question), list(recent_titles or []), history)


def get_answer(key):
    return _get("answers", key)


def put_answer(key, question, src, done):
    _put("answers", key, question, src, done)


# ------------------------------------------------------------ command line

def _stats():
    db = _conn()
    fp = fingerprint()      # with THIS shell's settings; the site's may differ
    for t in ("retrieval", "answers"):
        n, reused = db.execute(f"SELECT COUNT(*), COALESCE(SUM(reused), 0) FROM {t}").fetchone()
        cur = db.execute(f"SELECT COUNT(*) FROM {t} WHERE fp = ?", (fp,)).fetchone()[0]
        print(f"{t:10s} {n:5d} rows ({cur} for the current code/settings), reused {reused} times")
    print("\nMost reused questions:")
    for q, r in db.execute("SELECT question, SUM(reused) s FROM (SELECT question, reused FROM retrieval "
                           "UNION ALL SELECT question, reused FROM answers) GROUP BY question "
                           "ORDER BY s DESC LIMIT 15"):
        print(f"  {r:4d}  {q[:90]}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "stats"
    if cmd == "clear":
        db = _conn()
        db.execute("DELETE FROM retrieval")
        db.execute("DELETE FROM answers")
        db.commit()
        print(f"Cleared {PATH}")
    else:
        _stats()
