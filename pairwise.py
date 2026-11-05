#!/usr/bin/env python3
"""
3 Nov: head-to-head judging of two eval_v3 runs on the same questions (written before any result of it).

Why: on the reception set the off arm already answers well (v6 reviews / audience / Wikipedia blocks, named sources:
36/40 judge passes), so "on >= off + 6" could not be reached by any answer. When both answers pass, only a direct
comparison says which is better. The judge (gemma3:27b, temperature 0) sees the question, its NOTES, the two answers
and, for each, the claims the single-answer judge found unsupported by its context; it picks the answer that better
meets the notes. Each pair is judged in both orders; a win counts only if both orders agree, otherwise it is a tie.

    python pairwise.py results/v3eval_rec_off.json results/v3eval_rec_on.json --tag rec
        -> results/pairwise_rec.json; prints wins for A (first file) and B (second file)
    python v7_rules.py reception2 results/v3eval_rec_off.json results/v3eval_rec_on.json results/pairwise_rec.json
"""
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
JUDGE = os.environ.get("JUDGE_MODEL", "gemma3:27b")

PAIR_SYSTEM = """You compare two answers from a movie chatbot to the same question. You see the QUESTION, NOTES saying \
what a good answer must do, and the two answers. Under each answer is a list of claims a fact-checker found NOT \
supported by the data that answer was given - count those against it. Decide which answer better meets the NOTES, in \
this order: 1) invents nothing (fewer and less important unsupported claims); 2) does what the NOTES ask (for \
reception questions: names its sources, keeps critics and audiences apart, gives sample sizes when it generalises); \
3) answers the question directly and specifically; 4) concise - longer is NOT better. If they are about equally good, \
say tie.
Reply with JSON only: {"winner": "1" or "2" or "tie", "reason": "one sentence"}"""


def _ask(q, notes, a1, u1, a2, u2):
    import ollama
    user = (f"QUESTION: {q}\n\nNOTES: {notes}\n\nANSWER 1:\n{a1}\n\nUnsupported claims in answer 1: {u1 or 'none'}"
            f"\n\nANSWER 2:\n{a2}\n\nUnsupported claims in answer 2: {u2 or 'none'}")
    for _ in range(3):
        try:
            out = ollama.chat(model=JUDGE, format="json", options={"temperature": 0, "num_ctx": 8192},
                              messages=[{"role": "system", "content": PAIR_SYSTEM}, {"role": "user", "content": user}])
            w = str(json.loads(out["message"]["content"]).get("winner", "tie")).strip()
            return w if w in ("1", "2") else "tie"
        except Exception:  # noqa: BLE001
            time.sleep(1)
    return "tie"


def compare(path_a, path_b, tag):
    a = {r["id"]: r for r in json.load(open(path_a))["rows"] if r.get("answer")}
    b = {r["id"]: r for r in json.load(open(path_b))["rows"] if r.get("answer")}
    ids = [i for i in a if i in b]
    rows, wa, wb, ties = [], 0, 0, 0
    for i in ids:
        ra, rb = a[i], b[i]
        ua = "; ".join((ra.get("judge") or {}).get("unsupported_claims") or [])
        ub = "; ".join((rb.get("judge") or {}).get("unsupported_claims") or [])
        first = _ask(ra["question"], ra.get("notes", ""), ra["answer"], ua, rb["answer"], ub)    # A shown first
        second = _ask(ra["question"], ra.get("notes", ""), rb["answer"], ub, ra["answer"], ua)   # B shown first
        fa = {"1": "A", "2": "B"}.get(first, "tie")
        fb = {"1": "B", "2": "A"}.get(second, "tie")
        win = fa if fa == fb else "tie"
        wa += win == "A"
        wb += win == "B"
        ties += win == "tie"
        rows.append({"id": i, "category": ra.get("category"), "order1": fa, "order2": fb, "winner": win})
        print(f"  {i} {ra.get('category', ''):12s} A-first {fa:4s} B-first {fb:4s} -> {win}", flush=True)
    out = {"a": path_a, "b": path_b, "judge": JUDGE, "wins_a": wa, "wins_b": wb, "ties": ties, "n": len(ids),
           "rows": rows}
    os.makedirs(os.path.join(HERE, "results"), exist_ok=True)
    p = os.path.join(HERE, "results", f"pairwise_{tag}.json")
    json.dump(out, open(p, "w"), indent=1)
    print(f"{os.path.basename(path_a)} wins {wa}, {os.path.basename(path_b)} wins {wb}, ties {ties} (of {len(ids)}); "
          f"saved {p}")
    return out


def main():
    a = sys.argv[1:]
    if len(a) < 2:
        sys.exit(__doc__)
    tag = a[a.index("--tag") + 1] if "--tag" in a else "ab"
    try:                                                 # unload the answer model so the judge fits on the GPU
        import ollama
        for m in ("movie-buff", "llama3.2", "qwen3:8b"):
            try:
                ollama.generate(model=m, prompt="", keep_alive=0)
            except Exception:  # noqa: BLE001
                pass
    except Exception:  # noqa: BLE001
        pass
    compare(a[0], a[1], tag)


if __name__ == "__main__":
    main()
