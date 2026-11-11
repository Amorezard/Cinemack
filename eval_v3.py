"""
v3 evaluation of answers beyond tip-of-the-tongue: factual, detail, comparison, reviews,
"why did it work", careers, rankings/trends, current releases, recommendations, honesty
(sets/analytic.json). stage_scores.py stays the retrieval benchmark; this measures what the
answer model is GIVEN (route, data, context size, time) and, with --answers, what it SAYS.

    python eval_v3.py --tag v2 TOOLS=0              # (set env vars before python, see below)
    TOOLS=0 python eval_v3.py --tag v2              # v2 prompts (no tools): the "before"
    TOOLS=1 python eval_v3.py --tag v3              # v3: route + tool blocks
    TOOLS=1 python eval_v3.py --tag v3 --answers    # + generate answers (movie-buff) and judge (gemma3:27b)
    python eval_v3.py --report results/v3eval_v3.json
    python eval_v3.py --compare results/v3eval_v2.json results/v3eval_v3.json

Level 1 (no answer generated; planner + tools only, ~5 s/question):
    route    the tools the question needed ran (expect_tasks)               TOOLS=1 only
    films    the route / retrieval is about the expected films (expect_films)
    data     the strings a correct answer needs are in the context (expect_in_context)
    spec     an aggregate question's spec has the expected fields (expect_spec)
    absent   films that must not be offered are not (expect_absent)
Level 2 (--answers): the answer, its time and length, then a judge (a different model
family) scores grounded / direct / specific / honest / concise, and for v3 also whether
numbers name their source and whether interpretation is kept apart from evidence.

Model calls are replayed from results/llm_cache.sqlite like stage_scores.py (--no-cache for timings).
"""
import contextlib
import io
import json
import os
import re
import statistics
import sys
import time

import chat
import retrieve as R

JUDGE = os.environ.get("JUDGE_MODEL", "gemma3:27b")

JUDGE_SYSTEM = """You are a strict evaluator of a movie chatbot. You see the CONTEXT the bot was given \
(its only allowed source of facts), the QUESTION, the bot's ANSWER, and NOTES describing what a good \
answer must do. Grade against the context and notes - not against your own film knowledge.

Scores, 1-5:
- grounded: 5 = every name, year, number and plot detail in the answer appears in the context. \
3 = one minor detail is not in the context. 1 = invents credits, numbers or plot, or contradicts the context.
- direct: 5 = the first sentence answers the question. 1 = the answer is buried or missing.
- specific: 5 = uses concrete details from the context. 1 = generic statements that fit any film.
- honest: 5 = if the context lacks what was asked, says so plainly (or the context had it). \
1 = fills a gap from memory and presents it as fact.
- concise: 5 = nothing unnecessary. 1 = padded. Longer is NOT better.
- sources: 5 = numbers and reported claims name where they come from (e.g. "per TMDB", "[Wikipedia]"). \
1 = none named. Use 0 if the answer contains no numbers or reported claims.
- inference: 5 = clearly separates what the data shows from the bot's own interpretation and states \
uncertainty where causes are inferred. 1 = presents speculation as fact. Use 0 if the question needs no interpretation.
answered: "yes" if it answers the question from the context, "partly", or "no".

Restating the user's own question is NOT an unsupported claim. List every specific fact in the answer that is \
NOT in the context under "unsupported_claims" (short phrases). verdict = "pass" if the answer meets the NOTES and \
invents nothing important, else "fail".
Reply with JSON only:
{"unsupported_claims": [], "grounded": 0, "direct": 0, "specific": 0, "honest": 0, "concise": 0, \
"sources": 0, "inference": 0, "answered": "yes", "verdict": "pass", "reason": "one sentence"}"""

KEYS = ("grounded", "direct", "specific", "honest", "concise", "sources", "inference")


def norm(s):
    return re.sub(r"[^a-z0-9]+", "", str(s or "").casefold())


def context_of(prompt):
    return prompt.split("\n\nQuestion:", 1)[0] if "\n\nQuestion:" in prompt else prompt


def run_one(q, answers):
    trace = {}

    def traced(*a, **kw):
        kw["trace"] = trace
        return R.retrieve(*a, **kw)

    chat.build_prompt.__globals__["retrieve"] = traced
    t0 = time.perf_counter()
    with contextlib.redirect_stdout(io.StringIO()):
        prompt, hits = chat.build_prompt(q["question"])
    prep_s = time.perf_counter() - t0
    tool = dict(chat.LAST_TOOL)
    route = tool.get("route") or {}
    ctx = context_of(prompt)
    titles = [h.get("title") for h in hits]
    res = {"id": q["id"], "category": q["category"], "question": q["question"], "notes": q.get("notes", ""),
           "hits": titles, "route": route, "blocks": [(k, len(b)) for k, b in tool.get("blocks") or []],
           "style": tool.get("style"), "prompt_chars": len(prompt), "context": ctx,
           "prompt": prompt,          # v4: the exact prompt, so judged answers can become training examples
           "timing": {"prepare_s": round(prep_s, 2), "plan_ms": trace.get("plan_ms"),
                      "search_ms": trace.get("search_ms"), "rerank_ms": trace.get("rerank_ms"),
                      "retrieve_ms": trace.get("total_ms"), "tools_ms": (tool.get("ms") or {}).get("total"),
                      "spec_ms": (tool.get("ms") or {}).get("spec")},
           "llm": trace.get("llm")}
    g = {}
    if "expect_tasks" in q and chat.TOOLS:
        g["route"] = all(t in (route.get("tasks") or []) for t in q["expect_tasks"])
    if q.get("expect_films"):
        have = {norm(t) for t in (route.get("films") or [])} | {norm(t) for t in titles}
        g["films"] = all(norm(f) in have for f in q["expect_films"])
    if q.get("expect_in_context"):
        low = ctx.casefold()
        g["data"] = all(s.casefold() in low for s in q["expect_in_context"])
    if q.get("expect_spec") and chat.TOOLS:
        spec = route.get("spec") or {}
        g["spec"] = all(spec.get(k) == v for k, v in q["expect_spec"].items())
    if q.get("expect_absent"):
        g["absent"] = not any(norm(t) == norm(a) for t in titles for a in q["expect_absent"])
    res["grades"] = g
    if answers:
        import ollama
        t1 = time.perf_counter()
        import persona                               # round 27: PERSONA=cinemack (default off = unchanged)
        r = ollama.chat(model=chat.MODEL, think=False, keep_alive=chat.KEEP_ALIVE,
                        options=R.llm_options(), messages=persona.with_system([{"role": "user", "content": prompt}], chat.MODEL))
        res["answer"] = (r["message"]["content"] or "").strip()
        res["timing"]["answer_s"] = round(time.perf_counter() - t1, 2)
        res["answer_tokens"] = r.get("eval_count") if hasattr(r, "get") else getattr(r, "eval_count", None)
        res["prompt_tokens"] = r.get("prompt_eval_count") if hasattr(r, "get") else getattr(r, "prompt_eval_count", None)
    return res


def judge_all(rows):
    import ollama
    # The judge (gemma3:27b, ~17 GB) only fits on the GPU once the answer model is gone. With
    # movie-buff still loaded (keep_alive 30m) Ollama splits the judge across GPU and CPU and
    # each judgment takes minutes, so unload everything else first.
    for m in (chat.MODEL, "qwen3:8b", "qwen3:4b", "llama3.2", "gemma3:27b"):
        if m != JUDGE:
            try:
                ollama.generate(model=m, prompt="", keep_alive=0)
            except Exception:  # noqa: BLE001 - not loaded / not installed
                pass
    for r in rows:
        if "answer" not in r:
            continue
        user = (f"CONTEXT:\n{r['context'][:14000]}\n\nQUESTION: {r['question']}\n\nANSWER:\n{r['answer']}"
                f"\n\nNOTES: {r['notes']}")
        v = None
        for _ in range(3):
            try:
                out = ollama.chat(model=JUDGE, format="json", options={"temperature": 0, "num_ctx": 8192},
                                  messages=[{"role": "system", "content": JUDGE_SYSTEM},
                                            {"role": "user", "content": user}])
                v = json.loads(out["message"]["content"])
                break
            except Exception:  # noqa: BLE001
                time.sleep(1)
        r["judge"] = v
        if v:
            print(f"  {r['id']} {r['category']:9s} " + " ".join(f"{k[:3]}{v.get(k, '?')}" for k in KEYS)
                  + f" {v.get('answered', '?'):6s} {str(v.get('verdict', '?')).upper()}")


def summarize(rows, label=""):
    cats = {}
    for r in rows:
        cats.setdefault(r["category"], []).append(r)
    cats["ALL"] = rows
    print(f"\n{label}  (pass rate per check; n = questions with that check)")
    print(f"{'category':10s} {'n':>3s} {'route':>7s} {'films':>7s} {'data':>7s} {'spec':>7s} {'absent':>7s} "
          f"{'prompt':>7s} {'prep s':>7s} {'ans s':>6s} {'grnd':>5s} {'hon':>5s} {'src':>5s} {'inf':>5s} {'pass':>6s}")
    for c, rs in cats.items():
        def rate(k):
            v = [r["grades"][k] for r in rs if k in r.get("grades", {})]
            return f"{sum(v)}/{len(v)}" if v else "-"
        def avg(f):
            v = [f(r) for r in rs]
            v = [x for x in v if isinstance(x, (int, float)) and x > 0]
            return statistics.mean(v) if v else None
        def fmt(x, p=1):
            return f"{x:.{p}f}" if x is not None else "-"
        js = [r["judge"] for r in rs if r.get("judge")]
        def j(k):
            v = [x.get(k) for x in js if isinstance(x.get(k), (int, float)) and x.get(k) > 0]
            return statistics.mean(v) if v else None
        passes = f"{sum(1 for x in js if x.get('verdict') == 'pass')}/{len(js)}" if js else "-"
        print(f"{c:10s} {len(rs):3d} {rate('route'):>7s} {rate('films'):>7s} {rate('data'):>7s} {rate('spec'):>7s} "
              f"{rate('absent'):>7s} {fmt(avg(lambda r: r['prompt_chars']), 0):>7s} "
              f"{fmt(avg(lambda r: r['timing']['prepare_s'])):>7s} {fmt(avg(lambda r: r['timing'].get('answer_s'))):>6s} "
              f"{fmt(j('grounded'), 2):>5s} {fmt(j('honest'), 2):>5s} {fmt(j('sources'), 2):>5s} {fmt(j('inference'), 2):>5s} "
              f"{passes:>6s}")


def compare(a_path, b_path):
    a = {r["id"]: r for r in json.load(open(a_path))["rows"]}
    b = {r["id"]: r for r in json.load(open(b_path))["rows"]}
    summarize(list(a.values()), os.path.basename(a_path))
    summarize(list(b.values()), os.path.basename(b_path))
    print("\nper question: data check and judge verdict, A -> B")
    for i in sorted(a):
        if i not in b:
            continue
        ga, gb = a[i].get("grades", {}).get("data"), b[i].get("grades", {}).get("data")
        va = (a[i].get("judge") or {}).get("verdict", "-")
        vb = (b[i].get("judge") or {}).get("verdict", "-")
        if ga != gb or va != vb:
            print(f"  {i} {a[i]['category']:9s} data {ga}->{gb}  verdict {va}->{vb}  {a[i]['question'][:60]}")


def main():
    args = sys.argv[1:]
    if "--report" in args:
        d = json.load(open(args[args.index("--report") + 1]))
        return summarize(d["rows"], d.get("tag", ""))
    if "--compare" in args:
        i = args.index("--compare")
        return compare(args[i + 1], args[i + 2])
    if "--rejudge" in args:                      # round 27: judge a saved run again (e.g. after claim_rewrite.py)
        path = args[args.index("--rejudge") + 1]
        d = json.load(open(path))
        judge_all(d["rows"])
        json.dump(d, open(path, "w"), indent=1, default=str, ensure_ascii=False)
        return summarize(d["rows"], d.get("tag", ""))
    tag = args[args.index("--tag") + 1] if "--tag" in args else ("v3" if chat.TOOLS else "v2")
    qfile = args[args.index("--questions") + 1] if "--questions" in args else "sets/analytic.json"
    answers = "--answers" in args
    cache = None
    if "--no-cache" not in args:
        import stage_scores
        cache = stage_scores.enable_cache()
    qs = json.load(open(qfile))["questions"]
    if "--ids" in args:
        want = set(args[args.index("--ids") + 1].split(","))
        qs = [q for q in qs if q["id"] in want]
    if "--category" in args:
        want = set(args[args.index("--category") + 1].split(","))
        qs = [q for q in qs if q["category"] in want]
    print(f"{len(qs)} questions, TOOLS={int(chat.TOOLS)} CONV={int(R.CONV)} prompt {chat.PROMPT_VERSION}, "
          f"answers={'yes' if answers else 'no'}")
    with contextlib.redirect_stdout(io.StringIO()):
        R.retrieve("warm up", k=3)
    rows = []
    for q in qs:
        r = run_one(q, answers)
        rows.append(r)
        g = " ".join(f"{k}={'ok' if v else 'NO'}" for k, v in r["grades"].items())
        print(f"{q['id']} {q['category']:9s} {r['timing']['prepare_s']:5.1f}s {r['prompt_chars']:6d}ch "
              f"tasks={','.join((r['route'] or {}).get('tasks') or []) or '-'}  {g}", flush=True)
    if answers and "--no-judge" not in args:
        print(f"\njudging with {JUDGE} ...")
        judge_all(rows)
    os.makedirs("results", exist_ok=True)
    out = f"results/v3eval_{tag}.json"
    json.dump({"tag": tag, "tools": chat.TOOLS, "conv": R.CONV, "prompt_version": chat.PROMPT_VERSION,
               "switches": getattr(R, "SWITCHES", {}), "cache": cache, "rows": rows},
              open(out, "w"), indent=1, default=str, ensure_ascii=False)
    summarize(rows, tag)
    if cache:
        print(f"\nModel calls: {cache['hit']} replayed, {cache['miss']} new")
    print(f"saved {out}")


if __name__ == "__main__":
    main()
