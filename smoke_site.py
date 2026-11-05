#!/usr/bin/env python3
"""
4 Nov: ask the running site a few questions through its real API (server.py -> pipeline -> chat), the path the
evaluations never use, and check the new features show up. Run against a test copy:
    PORT=49152 bash start_site.sh            # terminal 1 (tmux)
    python smoke_site.py --port 49152        # terminal 2
Each question takes as long as a normal answer (~20-40 s). Prints what each check found; ends with a count.
"""
import argparse
import json
import sys
import time
import urllib.request
import uuid

CASES = [
    # (question, what to check, check function on (sources event, answer text))
    ("Who stars in Fargo?", "film-or-show buttons (CLARIFY)",
     lambda s, a: bool(s.get("choices"))),
    ("How many seasons does Breaking Bad have?", "answered from the show's data: 5 seasons (TV)",
     lambda s, a: "5" in a or "five" in a.lower()),
    ("I loved Game of Thrones, what fantasy series should I watch next?", "other shows recommended (TV)",
     lambda s, a: len([f for f in s.get("films", []) if "Game of Thrones" not in (f.get("title") or "")]) >= 2),
    ("recommend some animated short films", "only shorts in the cards (SHORTS)",
     lambda s, a: len(s.get("films", [])) >= 2 and all((f.get("runtime") or 0) <= 45 for f in s.get("films", []) if f.get("runtime"))),
    ("Who directed the short film Bao?", "Domee Shi named (SHORTS, normal path)",
     lambda s, a: "Domee Shi" in a),
    ("What did critics praise about Mad Max: Fury Road?", "reception answer with sources (RECEPTION)",
     lambda s, a: ("[" in a) and ("critic" in a.lower())),
    ("Who directed Heat (1995)?", "plain film fact still works",
     lambda s, a: "Michael Mann" in a),
]


def ask(base, code, user, q):
    body = json.dumps({"message": q, "history": [], "recent_titles": []}).encode()
    req = urllib.request.Request(base + "/api/chat", data=body, method="POST", headers={
        "content-type": "application/json", "x-access-code": code, "x-mb-user": user, "x-mb-private": "1"})
    sources, answer, ev = {}, "", None
    with urllib.request.urlopen(req, timeout=300) as r:
        for raw in r:
            line = raw.decode("utf-8", "replace").rstrip("\n")
            if line.startswith("event: "):
                ev = line[7:]
            elif line.startswith("data: ") and ev:
                d = json.loads(line[6:])
                if ev == "sources":
                    sources = d
                elif ev == "token":
                    answer += d.get("text", "")
                elif ev in ("done", "replace") and d.get("answer"):
                    answer = d["answer"]
                elif ev == "error":
                    answer = "ERROR: " + json.dumps(d)
    return sources, answer


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="49152")
    ap.add_argument("--code", default="movies")
    a = ap.parse_args()
    base = f"http://127.0.0.1:{a.port}"
    user = "smoke" + uuid.uuid4().hex[:8]
    ok = 0
    for q, what, check in CASES:
        t0 = time.time()
        try:
            s, ans = ask(base, a.code, user, q)
            good = bool(check(s, ans))
        except Exception as e:  # noqa: BLE001
            s, ans, good = {}, f"(failed: {e!r})", False
        ok += good
        films = ", ".join(f"{f.get('title')}{' [TV]' if f.get('kind') == 'tv' else ''}" for f in s.get("films", [])[:4])
        print(f"{'ok ' if good else 'BAD'} {time.time() - t0:5.1f}s  {q}\n      check: {what}\n      cards: {films or '-'}"
              + (f"\n      choices: {[c.get('label') for c in s.get('choices') or []]}" if s.get("choices") else "")
              + f"\n      answer: {' '.join(ans.split())[:220]}", flush=True)
    print(f"\nsmoke test: {ok}/{len(CASES)} checks passed")
    sys.exit(0 if ok == len(CASES) else 1)


if __name__ == "__main__":
    main()
