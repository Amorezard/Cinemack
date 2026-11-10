#!/usr/bin/env python3
"""3b long-chat check (10 Nov 2026): does the final answer of a long chat still respect a limit set in turn 1?

Runs a set of long conversations through a running Cinemack server (the website's own /api/chat path: history cut to
12 messages / 6,000 characters, conv state for the planner), then scores the final answer from facts.db, no judge:
PASS = the answer names at least one of the films it was given (its "sources"), and every film it names meets every
limit (runtime, US age rating, genres to avoid, earliest year, services); a fact that is unknown fails the limit.

    python tools/long_eval.py --set sets/tune_v8_long.json --base http://localhost:49160 --tag tune_long_on
Item formats (both are read):
  tuning (tune_v8_long):  {"id", "turns": [{"user", "assistant"?}, ...], "limit": {...}} - the last turn is the scored
                          question; `limit` uses the chat-filter fields (max_runtime, max_cert, never_genres, min_year,
                          services) and is checked from facts.db as above.
  sealed (sealed_v8_long, written 8 Nov): {"id", "turns": [...], "question", "expect_films", "category", ...} - every
                          turn is history (a turn without an "assistant" text is answered by the server first) and
                          `question` is scored: PASS = the answer names at least one of `expect_films` (title match,
                          punctuation and case ignored). Decided 10 Nov ~16:00 from the set's structure only, before
                          any sealed run; the answer text is saved for the judge / a human to read.
Turn entries may be {"user": ..., "assistant": ...} or {"role", "content"} pairs or plain strings.
Writes results/long_<tag>.json and prints "long <tag>: N/M pass".
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.request
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
RAG = os.path.dirname(HERE)
sys.path[:0] = [os.path.join(RAG, "web"), RAG]
CERTS = ["G", "PG", "PG-13", "R", "NC-17"]


def chat(base, user, question, history, conv, timeout=900):
    body = json.dumps({"message": question, "history": history, "recent_titles": [], "conv": conv, "think": False, "filters": {}}).encode()
    req = urllib.request.Request(base + "/api/chat", data=body, headers={"Content-Type": "application/json", "X-Access-Code": os.environ.get("MOVIE_BUFF_CODE", "movies"),
                                                                       "X-MB-User": user, "X-MB-Conv": uuid.uuid4().hex[:16], "X-MB-Private": "1"})
    films, answer, conv_out, err = [], "", conv, None
    with urllib.request.urlopen(req, timeout=timeout) as r:
        buf = ""
        for raw in r:
            buf += raw.decode("utf-8", "replace")
            while "\n\n" in buf:
                block, buf = buf.split("\n\n", 1)
                ev, data = "message", ""
                for line in block.split("\n"):
                    if line.startswith("event:"):
                        ev = line[6:].strip()
                    elif line.startswith("data:"):
                        data += line[5:].strip()
                if not data:
                    continue
                d = json.loads(data)
                if ev == "sources":
                    films = d.get("films") or []
                elif ev == "token":
                    answer += d.get("text", "")
                elif ev == "replace":
                    answer = d.get("text") or ""
                elif ev == "done":
                    conv_out = d.get("conv") or conv_out
                elif ev == "error":
                    err = d.get("message")
    return films, answer, conv_out, err


def named(films, answer):
    """Source films the answer names, in the order it names them."""
    t = " " + re.sub(r"[^a-z0-9]+", " ", answer.lower()) + " "
    pos = []
    for f in films:
        if not f.get("title") or not isinstance(f.get("id"), int) or f["id"] <= 0:
            continue
        k = " " + re.sub(r"[^a-z0-9]+", " ", f["title"].lower()).strip() + " "
        i = t.find(k)
        if i >= 0:
            pos.append((i, f))
    return [f for _i, f in sorted(pos, key=lambda x: x[0])]


def _norm(s):
    return " " + re.sub(r"[^a-z0-9]+", " ", str(s or "").lower()).strip() + " "


def expect_titles(ex):
    out = []
    for x in ex if isinstance(ex, list) else [ex]:
        if isinstance(x, dict):
            x = x.get("title") or x.get("name")
        if x:
            out.append(str(x))
    return out


def turn_pairs(turns):
    """-> [(user text, assistant text or None)] from any of the turn formats."""
    out, pend = [], None
    for t in turns:
        if isinstance(t, str):
            out.append((t, None))
        elif "role" in t:
            if t["role"] == "user":
                if pend is not None:
                    out.append((pend, None))
                pend = t.get("content", "")
            elif pend is not None:
                out.append((pend, t.get("content", "")))
                pend = None
        else:
            out.append((t.get("user") or t.get("q") or t.get("question") or "", t.get("assistant", t.get("a"))))
    if pend is not None:
        out.append((pend, None))
    return out


def check(fid, lim):
    import user_prefs
    gs, year, rt = user_prefs._film_facts([fid]).get(fid, (set(), None, None))
    why = []
    if lim.get("max_runtime") and (not rt or rt > lim["max_runtime"]):
        why.append(f"runtime {rt or 'unknown'}")
    if lim.get("max_cert"):
        c = user_prefs.certifications([fid]).get(fid)
        if not c or CERTS.index(c) > CERTS.index(lim["max_cert"]):
            why.append(f"rated {c or 'unknown'}")
    if lim.get("never_genres") and (not gs or set(lim["never_genres"]) & gs):
        why.append(f"genres {sorted(gs) or 'unknown'}")
    if lim.get("min_year") and (not year or year < lim["min_year"]):
        why.append(f"year {year or 'unknown'}")
    if lim.get("services"):
        on = user_prefs.on_services({"services": lim["services"]}, [fid]).get(fid)
        if not on:
            why.append("not on " + "/".join(lim["services"]))
    return why


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", required=True)
    ap.add_argument("--base", default="http://localhost:49160")
    ap.add_argument("--tag", required=True)
    ap.add_argument("--build-conv", action="store_true", default=True, help="replay scripted turns into conv state, as the page would hold it")
    a = ap.parse_args()
    import conv as C
    d = json.load(open(a.set))
    out, n_pass = [], 0
    for q in d["questions"]:
        t0 = time.time()
        user = f"long-eval-{a.tag}-{q['id']}"
        pairs = turn_pairs(q["turns"])
        if q.get("question"):
            hist_pairs, last = pairs, q["question"]
        else:
            hist_pairs, last = pairs[:-1], pairs[-1][0]
        history, state = [], C.new()
        for u, ans in hist_pairs:
            if ans is None:
                _f, ans, state, err = chat(a.base, user, u, history, state)
                if err:
                    print(f"  {q['id']}: error in an earlier turn: {err}", flush=True)
            else:
                state = C.update(state, u, [], None, ans)
            history += [{"role": "user", "content": u}, {"role": "assistant", "content": ans or ""}]
        films, answer, _c, err = chat(a.base, user, last, history, state)
        lim = q.get("limit") or {}
        nm = named(films, answer)
        if lim:
            fails = {f["title"]: check(f["id"], lim) for f in nm[:3]}
            ok = bool(nm) and not any(fails.values()) and not err
        else:
            exp = expect_titles(q.get("expect_films"))
            hit = [t for t in exp if _norm(t) in _norm(answer)]
            fails = {} if hit else {"expected one of": exp}
            ok = bool(hit) and not err
        n_pass += ok
        out.append({"id": q["id"], "pass": ok, "limit": lim, "category": q.get("category"), "mode": "limit" if lim else "expect_films", "named": [f["title"] for f in nm[:3]], "fails": fails,
                    "sources": [f.get("title") for f in films], "answer": answer, "error": err, "history_chars": sum(len(m["content"]) for m in history),
                    "seconds": round(time.time() - t0, 1)})
        print(f"  {q['id']} {'PASS' if ok else 'FAIL'}  named {[f['title'] for f in nm[:3]]}  {'' if ok else fails or 'no source film named'}  ({out[-1]['seconds']}s)", flush=True)
    os.makedirs(os.path.join(RAG, "results"), exist_ok=True)
    p = os.path.join(RAG, "results", f"long_{a.tag}.json")
    json.dump({"set": a.set, "tag": a.tag, "env": {k: os.environ.get(k) for k in ("LONG_SUMMARY",)}, "pass": n_pass, "n": len(out), "items": out},
              open(p, "w"), indent=1)
    print(f"long {a.tag}: {n_pass}/{len(out)} pass -> {p}", flush=True)


if __name__ == "__main__":
    main()
