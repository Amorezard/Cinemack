"""
D11 (v7, 27 Oct): semantic answer cache - SEMCACHE=1 on the site (off until its rule passes: zero wrong reuses on the
near-miss list below; paraphrase reuses are reported, not required).

The site cache (site_cache.py) replays an answer only for the same question after light normalising. This adds a
second, strict lookup for single-turn questions (no history, no recent films, no "it"): the new question's embedding
is compared with earlier single-turn questions asked under the same settings, and an earlier answer is reused only
when ALL of these hold:
    cosine similarity >= SEM_MIN (0.95)
    the same named things: capitalised names / titles, numbers and years, quoted text (so "Alien" != "Aliens",
    "Heat (1995)" != "Heat (1986)")
    the same content words after stemming and a small synonym list (direct / director, cast / stars, make / gross,
    score / music ...), and no extra limiting word on one side ("but funnier", "without", "not", "only", "before")
The reused answer is the stored one for that earlier question (its key includes the code / settings fingerprint, so
a code or data change never serves an old answer).

    python web/semcache.py check          # the near-miss and paraphrase lists (needs Ollama for the embeddings)
"""
import hashlib
import os
import re
import sqlite3
import sys
import threading
import time
from pathlib import Path

SEMCACHE = os.environ.get("SEMCACHE", "0") == "1"
SEM_MIN = float(os.environ.get("SEM_MIN", "0.95"))
EMBED_MODEL = os.environ.get("EMBED_MODEL", "mxbai-embed-large")
PREFIX = "Represent this sentence for searching relevant passages: "
PATH = Path(__file__).resolve().parent.parent / "results" / "semcache.sqlite"
_lock = threading.Lock()
_C = {"c": None}
_STOP = set("""a an the of to in on for and or is are was were be been it its this that these those what which who whom
how why when where do does did can could would should will i me my you your we our please tell show give any some
about with from by at as movie movies film films""".split())
R_LIMIT = re.compile(r"\b(but|without|not|no|only|except|before|after|instead|unlike|than|less|more|over|under|"
                     r"funnier|darker|lighter|shorter|longer|newer|older|recent|kids?|family|scarier)\b", re.I)


def _conn():
    if _C["c"] is None:
        PATH.parent.mkdir(parents=True, exist_ok=True)
        c = sqlite3.connect(str(PATH), check_same_thread=False, timeout=30)
        c.execute("pragma journal_mode=WAL")
        c.execute("create table if not exists sem(id integer primary key, question text, tag text, emb blob, "
                  "answer_key text, entities text, words text, created real)")
        c.execute("create index if not exists sem_tag on sem(tag)")
        _C["c"] = c
    return _C["c"]


def entities(q):
    """The named things a question is about: capitalised sequences (not the first word alone), numbers, quotes."""
    q = q or ""
    out = set(x.lower() for x in re.findall(r"\d+", q))
    out |= {x.lower().strip() for x in re.findall(r"[\"“'‘]([^\"”'’]{2,60})[\"”'’]", q)}
    lead = _STOP | {"who", "what", "what's", "which", "recommend", "suggest", "is", "are", "tell", "was", "were", "when",
                    "where", "how", "did", "does", "do", "can", "best", "a", "an"}
    for m in re.finditer(r"\b[A-Z][\w'’:-]*(?:\s+(?:(?:of|the|and|in|on|a|to|vs\.?)\b|[A-Z0-9][\w'’:-]*))*", q):
        ws = m.group(0).strip().split()
        while ws and ws[0].lower().strip("'’") in lead:
            ws = ws[1:]
        while ws and ws[-1].lower() in ("of", "the", "and", "in", "on", "a", "to"):
            ws = ws[:-1]
        if ws:
            out.add(" ".join(ws).lower())
    return out


def _stem(w):
    for suf in ("ing", "ers", "er", "ed", "es", "s"):
        if len(w) > len(suf) + 3 and w.endswith(suf):
            return w[: -len(suf)]
    return w


SYN = {"directed": "direct", "director": "direct", "directs": "direct", "helmed": "direct", "made": "direct?",
       "stars": "star", "starring": "star", "cast": "star", "actors": "star", "actor": "star",
       "released": "release", "release": "release", "come": "release", "came": "release", "out": "",
       "long": "length", "runtime": "length", "length": "length", "minutes": "length",
       "plot": "about", "story": "about", "about": "about", "what's": "",
       "music": "music", "score": "music", "soundtrack": "music", "composed": "music", "composer": "music",
       "earn": "gross", "earned": "gross", "make": "gross", "gross": "gross", "grossed": "gross", "money": "gross",
       "cost": "budget", "budget": "budget",
       "recommend": "recommend", "suggest": "recommend", "watching": "watch", "seeing": "watch", "see": "watch",
       "watch": "watch", "worth": "worth", "good": "good", "great": "good", "film": "", "films": "", "movie": "",
       "movies": "", "much": "", "was": "", "is": "", "who": "", "set": ""}


def words(q):
    out = set()
    for w in re.findall(r"[a-z0-9']+", (q or "").lower()):
        w = SYN.get(w, w)
        if w and w not in _STOP and len(w) > 2:
            out.add(_stem(w))
    return out


def same_meaning(q1, q2, cos):
    if cos < SEM_MIN:
        return False
    if entities(q1) != entities(q2):
        return False
    w1, w2 = words(q1), words(q2)
    if not w1 or w1 != w2:                       # every content word the same (after synonyms): one changed word
        return False                             # ("critics" / "audiences", "make" / "cost") is a different question
    l1, l2 = set(R_LIMIT.findall((q1 or "").lower())), set(R_LIMIT.findall((q2 or "").lower()))
    return l1 == l2


def _embed(text):
    import ollama
    import numpy as np
    v = np.asarray(ollama.embed(model=EMBED_MODEL, input=[PREFIX + text])["embeddings"][0], dtype=np.float32)
    return v / (np.linalg.norm(v) + 1e-9)


def lookup(question, tag=""):
    """An earlier single-turn question's answer key with the same meaning, or None."""
    if not SEMCACHE:
        return None
    try:
        import numpy as np
        v = _embed(question)
        with _lock:
            rows = _conn().execute("select question, emb, answer_key from sem where tag = ? order by id desc limit 3000",
                                   (tag or "",)).fetchall()
        best = None
        for q, e, k in rows:
            cos = float(np.frombuffer(e, dtype=np.float32) @ v)
            if same_meaning(question, q, cos) and (best is None or cos > best[0]):
                best = (cos, k, q)
        return best[1] if best else None
    except Exception as e:  # noqa: BLE001 - the cache must never break an answer
        print(f"[movie-buff] semantic cache lookup failed: {e!r}", flush=True)
        return None


def add(question, tag, answer_key):
    if not SEMCACHE:
        return
    try:
        v = _embed(question)
        with _lock:
            _conn().execute("insert into sem(question, tag, emb, answer_key, entities, words, created) values "
                            "(?,?,?,?,?,?,?)", (question, tag or "", v.tobytes(), answer_key,
                                                "|".join(sorted(entities(question))), "|".join(sorted(words(question))),
                                                time.time()))
            _conn().commit()
    except Exception as e:  # noqa: BLE001
        print(f"[movie-buff] semantic cache add failed: {e!r}", flush=True)


NEAR_MISS = [  # must NOT share an answer
    ("Who directed Alien?", "Who directed Aliens?"), ("movies like Heat", "movies like Heat but funnier"),
    ("Is Dune worth watching?", "Is Dune Part Two worth watching?"), ("Who directed Heat (1995)?", "Who directed Heat (1986)?"),
    ("best horror movies of the 80s", "best horror movies of the 90s"), ("How long is The Irishman?", "How long is The Godfather?"),
    ("recommend a comedy for kids", "recommend a comedy for adults"), ("Who stars in Top Gun?", "Who stars in Top Gun: Maverick?"),
    ("What did critics think of Joker?", "What did audiences think of Joker?"), ("a sci-fi movie without aliens", "a sci-fi movie with aliens"),
    ("When did Toy Story come out?", "When did Toy Story 3 come out?"), ("Is Barbie a musical?", "Is Wicked a musical?"),
    ("How much did Avatar make?", "How much did Avatar cost?"), ("films like Inception", "films like Interstellar"),
    ("Who wrote the music for Interstellar?", "Who wrote the script for Interstellar?"),
    ("a romantic comedy from 1999", "a romantic comedy from 2009"), ("Where can I stream Heat?", "Where can I rent Heat?"),
    ("Was Cats a flop?", "Was Dogs a flop?"), ("movies about dogs", "movies about cats"),
    ("the best Pixar movie", "the worst Pixar movie"),
]
PARAPHRASE = [  # may share an answer (reported only)
    ("Who directed Jaws?", "who was the director of Jaws"), ("How long is The Irishman?", "what is the runtime of The Irishman"),
    ("When did Inception come out?", "When was Inception released?"), ("Who stars in Heat (1995)?", "who is in the cast of Heat (1995)"),
    ("recommend a good heist movie", "suggest a good heist film"), ("Is Parasite worth watching?", "is Parasite worth seeing"),
    ("What is Arrival about?", "what's the plot of Arrival"), ("Who composed the score for Dune?", "who did the music for Dune"),
    ("How much did Titanic make?", "how much money did Titanic earn"), ("best Studio Ghibli movies", "the best Studio Ghibli films"),
]


def check():
    import numpy as np
    wrong, reuse = [], 0
    for a, b in NEAR_MISS:
        cos = float(_embed(a) @ _embed(b))
        if same_meaning(a, b, cos):
            wrong.append((a, b, round(cos, 3)))
    for a, b in PARAPHRASE:
        cos = float(_embed(a) @ _embed(b))
        ok = same_meaning(a, b, cos)
        reuse += ok
        print(f"  paraphrase {'reused ' if ok else 'missed '} cos {cos:.3f}  {a!r} ~ {b!r}")
    for a, b, cos in wrong:
        print(f"  WRONG REUSE cos {cos}: {a!r} ~ {b!r}")
    print(f"near-miss pairs wrongly reused: {len(wrong)}/{len(NEAR_MISS)} (rule: 0) -> {'PASS' if not wrong else 'FAIL'}; "
          f"paraphrases reused {reuse}/{len(PARAPHRASE)} (reported)")
    return not wrong


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "check":
        sys.exit(0 if check() else 1)
    print(__doc__)
