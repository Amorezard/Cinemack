"""
Fill the site cache with common questions, so the people who ask them next get an
instant answer. Sends each question to the RUNNING site, exactly as a browser
would (a new chat, first question), so the stored search and answer are the real
ones. Questions already cached come back instantly and cost nothing.

    cd ~/movie-buff/rag && source ~/venvs/movie-rag/bin/activate
    python web/warm_cache.py --top 30              # the 30 most-asked questions in logs/requests.jsonl
    python web/warm_cache.py --file my_questions.txt   # one question per line
    python web/warm_cache.py --top 30 --dry-run    # just list them

Uses MOVIE_BUFF_CODE from the environment (the site's access code) and PORT
(default 49151). Each new question takes as long as on the site (~5-20 s) and
uses the GPU, so run it when nobody else is using the GPU.
"""
import collections
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from site_cache import normalize_question, refers_back  # noqa: E402

LOG = HERE.parent / "logs" / "requests.jsonl"
URL = f"http://localhost:{os.environ.get('PORT', '49151')}/api/chat"


def top_questions(n):
    """Most-asked first questions of a conversation, one wording per question."""
    counts, wording = collections.Counter(), {}
    if not LOG.exists():
        print(f"(no {LOG} yet)")
        return []
    for line in LOG.open(encoding="utf-8"):
        try:
            r = json.loads(line)
        except ValueError:
            continue
        q = (r.get("question") or "").strip()
        if not q or r.get("history_turns") or r.get("think") or refers_back(q):
            continue
        k = normalize_question(q)
        counts[k] += 1
        wording.setdefault(k, q)
    return [(wording[k], c) for k, c in counts.most_common(n)]


def ask(question):
    body = json.dumps({"message": question, "history": [], "recent_titles": [], "think": False})
    req = urllib.request.Request(URL, body.encode(), {
        "Content-Type": "application/json", "X-Access-Code": os.environ.get("MOVIE_BUFF_CODE", "")})
    t0 = time.perf_counter()
    last = {}
    with urllib.request.urlopen(req, timeout=300) as resp:
        event = None
        for raw in resp:
            line = raw.decode("utf-8").rstrip("\n")
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:") and event in ("done", "error"):
                last = dict(json.loads(line[5:]), _event=event)
    return last, time.perf_counter() - t0


def main(args):
    qs = []
    if "--top" in args:
        qs += top_questions(int(args[args.index("--top") + 1]))
    if "--file" in args:
        path = args[args.index("--file") + 1]
        qs += [(q.strip(), None) for q in open(path, encoding="utf-8") if q.strip()]
    if not qs:
        sys.exit(__doc__)
    seen, todo = set(), []
    for q, c in qs:
        if normalize_question(q) not in seen:
            seen.add(normalize_question(q))
            todo.append((q, c))
    for i, (q, c) in enumerate(todo, 1):
        tag = f" (asked {c}x)" if c else ""
        if "--dry-run" in args:
            print(f"{i:3d}. {q[:90]}{tag}")
            continue
        try:
            r, s = ask(q)
        except Exception as e:  # noqa: BLE001
            print(f"{i:3d}. FAILED {e!r}: {q[:70]}")
            continue
        state = ("error: " + r.get("message", "")) if r.get("_event") == "error" else \
                ("already cached" if r.get("cached") else "stored")
        print(f"{i:3d}. {s:5.1f} s  {state:15s} {q[:70]}{tag}")


if __name__ == "__main__":
    main(sys.argv[1:])
