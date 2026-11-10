#!/usr/bin/env python3
"""Chat latency benchmark (9 Nov). Two parts, both read-only for the answer pipeline:
  history  - every real request in logs/requests.jsonl: median / p95 of each stage the server already records.
  live     - N questions through the real /api/chat stream, timed from the client side: first byte, first status,
             sources (retrieval done), first answer token, done; plus the server's own stage numbers for that request.
    python tools/chat_bench.py history
    python tools/chat_bench.py live --tag baseline [--base http://localhost:49151] [--repeat 1]
Questions carry a short random tag in a trailing comment-free form?  No: they are sent as written; the answer cache is
avoided by the X-MB-Private header + a unique user id, and by reporting "cached" rows separately."""
import argparse, json, os, statistics as st, sys, time, urllib.request, uuid

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
QUESTIONS = [
    ("short_rec", "Recommend one thriller under 90 minutes."),
    ("short_rec", "A funny movie for tonight, something light."),
    ("short_rec", "Give me a good heist film from the 2000s."),
    ("fact", "Who directed Heat?"),
    ("fact", "How long is Oppenheimer?"),
    ("fact", "Where can I stream Arrival?"),
    ("identify", "The movie where a poor family slowly takes over jobs in a rich family's house."),
    ("identify", "Film where a guy relives the same day over and over reporting on a groundhog."),
    ("complex", "Compare how critics and audiences received Blade Runner 2049 and explain why it underperformed at the box office."),
    ("complex", "What are the main themes of Parasite and how does the house design support them?"),
]
MULTI = ["Recommend a sci-fi film like Arrival.", "Something shorter than that?", "Who directed the first one you suggested?"]


def pct(v, p):
    v = sorted(v)
    return v[min(len(v) - 1, int(round(p * (len(v) - 1))))] if v else float("nan")


def summ(name, v, unit="s"):
    v = [x for x in v if isinstance(x, (int, float))]
    if not v:
        return f"  {name:22s} n=0"
    return f"  {name:22s} n={len(v):4d}  median {st.median(v):7.2f}{unit}  p95 {pct(v, .95):7.2f}{unit}  max {max(v):7.2f}{unit}"


def history():
    p = os.path.join(ROOT, "web", "logs", "requests.jsonl")
    if not os.path.exists(p):
        p = os.path.join(ROOT, "logs", "requests.jsonl")
    rows = []
    for line in open(p, encoding="utf-8", errors="replace"):
        try:
            rows.append(json.loads(line))
        except ValueError:
            pass
    live = [r for r in rows if not r.get("cached") and not r.get("stopped") and (r.get("timings") or {}).get("total_s")]
    print(f"{p}: {len(rows):,} requests, {len(live):,} uncached and finished; newest {rows[-1].get('time') if rows else '-'}")
    for label, sel in (("all time", live), ("last 200", live[-200:])):
        print(f"--- {label}")
        T = lambda k: [r["timings"].get(k) for r in sel]
        print(summ("total", T("total_s"))); print(summ("retrieve (all)", T("retrieve_s")))
        print(summ("first answer text", T("first_text_s"))); print(summ("generate", T("generate_s")))
        S = lambda k: [(r.get("stages") or {}).get(k) / 1000 for r in sel if isinstance((r.get("stages") or {}).get(k), (int, float))]
        for k in ("plan_ms", "search_ms", "rerank_ms", "total_ms"):
            print(summ("  stage " + k[:-3], S(k)))
        tl = [((r.get("stages") or {}).get("tools") or {}).get("ms") for r in sel]
        print(summ("  stage tools", [x / 1000 for x in tl if isinstance(x, (int, float))]))
        wait = [r["timings"]["first_text_s"] - r["timings"]["retrieve_s"] for r in sel
                if r["timings"].get("first_text_s") and r["timings"].get("retrieve_s") is not None]
        print(summ("model: prompt -> 1st token", wait))
        print(summ("prompt chars", [r.get("prompt_chars") for r in sel], ""))
        print(summ("answer chars", [r.get("answer_chars") for r in sel], ""))
        rate = [r["answer_chars"] / max(0.1, r["timings"]["generate_s"] - (r["timings"]["first_text_s"] - r["timings"]["retrieve_s"]))
                for r in sel if r.get("answer_chars") and r["timings"].get("first_text_s") and r["timings"].get("generate_s")]
        print(summ("answer chars / s", rate, ""))
        share = [r["timings"]["retrieve_s"] / r["timings"]["total_s"] for r in sel if r["timings"].get("retrieve_s") is not None]
        print(summ("retrieve share of total", share, ""))
    paths = {}
    for r in live[-500:]:
        s = r.get("stages") or {}
        k = f"path={s.get('path')} planner={s.get('planner')} rerank_skipped={s.get('rerank_skipped')}"
        paths.setdefault(k, []).append(r["timings"]["retrieve_s"])
    print("--- retrieval by path (last 500)")
    for k, v in sorted(paths.items(), key=lambda x: -len(x[1])):
        print(f"  {k:60s} n={len(v):4d} median {st.median(v):6.2f}s p95 {pct(v, .95):6.2f}s")
    llm = [((r.get("stages") or {}).get("llm")) for r in live[-200:] if (r.get("stages") or {}).get("llm")]
    if llm:
        print("--- model calls inside retrieval (sample of last trace):", json.dumps(llm[-1])[:600])


def ask(base, code, user, q, hist, recent, conv, filters=None):
    body = json.dumps({"message": q, "history": hist, "recent_titles": recent, "conv": conv, "think": False,
                       "filters": filters or {}}).encode()
    req = urllib.request.Request(base + "/api/chat", data=body, headers={
        "Content-Type": "application/json", "X-Access-Code": code, "X-MB-User": user, "X-MB-Conv": uuid.uuid4().hex,
        "X-MB-Private": "1"})
    t0 = time.perf_counter()
    marks, ans, done, ev, n_tok = {}, [], {}, None, 0
    with urllib.request.urlopen(req, timeout=600) as r:
        marks["headers"] = time.perf_counter() - t0
        for raw in r:
            line = raw.decode("utf-8", "replace").rstrip("\n")
            marks.setdefault("first_byte", time.perf_counter() - t0)
            if line.startswith("event:"):
                ev = line[6:].strip()
            elif line.startswith("data:"):
                d = json.loads(line[5:].strip() or "{}")
                now = time.perf_counter() - t0
                if ev == "status":
                    marks.setdefault("status_" + str(d.get("stage")), now)
                elif ev == "sources":
                    marks["sources"] = now; marks["intent"] = d.get("intent"); marks["n_films"] = len(d.get("films") or [])
                elif ev == "token":
                    marks.setdefault("first_token", now); ans.append(d.get("text", "")); n_tok += 1
                elif ev == "done":
                    marks["done"] = now; done = d
    marks["tokens"] = n_tok
    return marks, "".join(ans), done


def live(a):
    code = os.environ.get("MOVIE_BUFF_CODE", "movies")
    out = []
    user = "bench-" + uuid.uuid4().hex[:10]
    FILT = {"brief": True} if a.brief else {}
    only = set(a.only.split(",")) if a.only else None
    for rep in range(a.repeat):
        for kind, q in QUESTIONS:
            if only and kind not in only:
                continue
            try:
                m, ans, done = ask(a.base, code, user, q, [], [], None, FILT)
            except Exception as e:  # noqa: BLE001
                print(f"  FAILED {q[:50]}: {e!r}"); continue
            out.append({"kind": kind, "q": q, "marks": m, "timings": done.get("timings"), "cached": done.get("cached"),
                        "words": len(ans.split()), "answer": ans})
            t = done.get("timings") or {}
            print(f"  {kind:9s} first status {m.get('status_searching', float('nan')):5.2f}s | sources {m.get('sources', float('nan')):6.2f}s | "
                  f"first token {m.get('first_token', float('nan')):6.2f}s | done {m.get('done', float('nan')):6.2f}s | "
                  f"{len(ans.split()):3d} words {m['tokens']:3d} tok | cached={done.get('cached')} | server retrieve {t.get('retrieve_s')} gen {t.get('generate_s')} | {q[:44]}", flush=True)
        hist, recent, conv = [], [], None
        for i, q in enumerate(MULTI if (not only or "multi" in only) else []):
            try:
                m, ans, done = ask(a.base, code, user, q, hist, recent, conv, FILT)
            except Exception as e:  # noqa: BLE001
                print(f"  FAILED multi {i}: {e!r}"); break
            out.append({"kind": "multi", "q": q, "marks": m, "timings": done.get("timings"), "cached": done.get("cached"),
                        "words": len(ans.split()), "answer": ans})
            print(f"  multi {i + 1}   sources {m.get('sources', float('nan')):6.2f}s | first token {m.get('first_token', float('nan')):6.2f}s | done {m.get('done', float('nan')):6.2f}s | {len(ans.split()):3d} words | {q}", flush=True)
            hist += [{"role": "user", "content": q}, {"role": "assistant", "content": ans}]
            recent, conv = done.get("recent_titles") or recent, done.get("conv") or conv
    fresh = [r for r in out if not r["cached"]]
    print(f"--- {a.tag}: {len(out)} questions ({len(fresh)} not cached)")
    for kind in ("short_rec", "fact", "identify", "complex", "multi", None):
        sel = [r for r in fresh if kind is None or r["kind"] == kind]
        if not sel:
            continue
        print(f" {kind or 'ALL'}")
        print(summ("first visible status", [r["marks"].get("status_searching") for r in sel]))
        print(summ("sources shown", [r["marks"].get("sources") for r in sel]))
        print(summ("first answer token", [r["marks"].get("first_token") for r in sel]))
        print(summ("done", [r["marks"].get("done") for r in sel]))
        print(summ("answer words", [r["words"] for r in sel], ""))
    os.makedirs(os.path.join(ROOT, "results"), exist_ok=True)
    p = os.path.join(ROOT, "results", f"chat_bench_{a.tag}.json")
    json.dump(out, open(p, "w"), indent=1)
    print("saved", p)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["history", "live"])
    ap.add_argument("--base", default="http://localhost:49151")
    ap.add_argument("--tag", default="baseline")
    ap.add_argument("--repeat", type=int, default=1)
    ap.add_argument("--brief", action="store_true", help="send the v8 page's default per-chat filter {brief: true}")
    ap.add_argument("--only", default="", help="comma list of kinds: short_rec,fact,identify,complex,multi")
    a = ap.parse_args()
    history() if a.mode == "history" else live(a)
