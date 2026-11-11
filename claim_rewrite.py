#!/usr/bin/env python3
"""4c: fewer unsupported claims (v8 Should tier; built 11 Nov 2026, round 27). Switch: CLAIM_REWRITE=1 (default 0).

After an answer to a reception or analysis question is written, claimcheck.py (NLI) checks each factual sentence
against the evidence the answer model was given. Only the sentences it can't support are sent back to a small model
(REWRITE_MODEL, default llama3.2, temperature 0) with the evidence lines, to be rewritten so they say only what the
evidence says - or dropped. Each rewritten sentence is checked again; one that is still unsupported is removed. The
rest of the answer is never touched. The whole step stops at REWRITE_BUDGET_S (3 s): what is done by then is used.

    python claim_rewrite.py eval results/v3eval_<tag>.json --tag <new>   # rewrite saved answers -> results/v3eval_<new>.json
    python claim_rewrite.py try "<answer>" "<context>"

Rule (plan, 6 Nov): unsupported rate (claimcheck) down by at least 0.10, judge passes not lower, and at most 3 s added
per answer, on the analytic + reception sets.
"""
import json
import os
import re
import sys
import time

CLAIM_REWRITE = os.environ.get("CLAIM_REWRITE", "0") == "1"
MODEL = os.environ.get("REWRITE_MODEL", "llama3.2")
BUDGET = float(os.environ.get("REWRITE_BUDGET_S", "3"))
MAX_SENTENCES = 4
STYLES = {"analysis", "reception", "reception_list", "reviews"}     # where unsupported claims concentrate (v7 R16)
SYSTEM = ("You fix sentences in a movie chatbot's answer that the evidence does not support. For each numbered "
          "SENTENCE, write a version that says only what the EVIDENCE supports, keeping the meaning, the citations "
          "like [2] and the sources like (per TMDB) that the evidence backs. Do not add any fact that is not in the "
          "evidence. If the evidence supports none of it, return an empty string for that number. Reply with JSON "
          'only: {"1": "rewritten sentence or empty", "2": "..."}')


def applies(style):
    return CLAIM_REWRITE and (style or "") in STYLES


def _evidence(context, limit=5000):
    import claimcheck as CC
    ev, lines = CC.parse_context(context)
    out = [f"[{n}] {t}" for n, t in sorted(ev.items())] + lines
    s = "\n".join(out)
    return s[:limit]


def _ask(sentences, evidence, timeout):
    import ollama
    user = "EVIDENCE:\n" + evidence + "\n\n" + "\n".join(f"SENTENCE {i + 1}: {s}" for i, s in enumerate(sentences))
    cl = ollama.Client(timeout=max(0.5, timeout))
    r = cl.chat(model=MODEL, format="json", keep_alive="30m", options={"temperature": 0, "num_predict": 400},
                messages=[{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}])
    d = json.loads(r["message"]["content"] or "{}")
    return {int(k): str(v or "").strip() for k, v in d.items() if str(k).isdigit()}


def rewrite(answer, context, nli=None, budget=None):
    """(new answer, info). info: {checked, unsupported, rewritten, dropped, kept, seconds, timed_out}."""
    t0 = time.perf_counter()
    budget = BUDGET if budget is None else budget
    info = {"checked": 0, "unsupported": 0, "rewritten": 0, "dropped": 0, "kept": 0, "seconds": 0.0, "timed_out": False}
    if not answer or not context:
        return answer, info
    import claimcheck as CC
    m = nli or CC.nli()
    c = CC.check(answer, context, m)
    info["checked"] = c["factual"]
    bad = [r["text"] for r in c["sentences"] if r["kind"] == "factual" and r["best_entail"] < CC.NLI_MIN][:MAX_SENTENCES]
    info["unsupported"] = len(bad)
    if not bad:
        info["seconds"] = round(time.perf_counter() - t0, 2)
        return answer, info
    left = budget - (time.perf_counter() - t0)
    try:
        new = _ask(bad, _evidence(context), left) if left > 0.3 else {}
    except Exception as e:  # noqa: BLE001 - timeout or model down: keep the answer as it was
        info["timed_out"] = True
        info["error"] = repr(e)[:120]
        new = {}
    if not new:
        info["seconds"] = round(time.perf_counter() - t0, 2)
        info["timed_out"] = info["timed_out"] or (time.perf_counter() - t0) >= budget
        return answer, info
    ev, lines = CC.parse_context(context)
    prem = list(ev.values()) + lines
    out = answer
    for i, s in enumerate(bad):
        r = new.get(i + 1)
        if r is None:
            info["kept"] += 1                      # the model skipped it: leave it
            continue
        ok = False
        if r:
            hyp = CC.clean_hyp(r)
            probs = m.entail([(p, hyp) for p in prem]) if prem else []
            ok = bool(probs) and max(probs) >= CC.NLI_MIN
        if ok:
            out = _replace(out, s, r)
            info["rewritten"] += 1
        else:
            out = _replace(out, s, "")
            info["dropped"] += 1
    out = re.sub(r"(?m)^\s*([-*•]|\d+[.)])\s*$\n?", "", out)          # a list item whose sentence was dropped
    out = re.sub(r"[ \t]{2,}", " ", out)
    out = re.sub(r"\n{3,}", "\n\n", out).strip()
    if len(CC.sentences(out)) == 0:                # never leave an empty answer
        out = answer
        info["dropped"] = info["rewritten"] = 0
    info["seconds"] = round(time.perf_counter() - t0, 2)
    return out, info


def _replace(text, sentence, new):
    """Swap one sentence (as claimcheck split it: list markers and ** removed) in the original answer text."""
    if sentence in text:
        return text.replace(sentence, new, 1)
    words = sentence.split()
    if not words:
        return text
    b = r"(?:\*\*)?"
    pat = b + (b + r"\s+" + b).join(re.escape(w) for w in words) + b
    m = re.search(pat, text)
    return text[:m.start()] + new + text[m.end():] if m else text


def cmd_eval(path, tag):
    """Rewrite the answers of a saved eval_v3 run (same answers, so the comparison is only the rewrite)."""
    import claimcheck as CC
    d = json.load(open(path))
    m = CC.nli()
    t = []
    for r in d["rows"]:
        if not r.get("answer") or (r.get("style") or "") not in STYLES:
            continue
        new, info = rewrite(r["answer"], r.get("context") or "", m)
        r["answer_before_rewrite"], r["answer"], r["rewrite"] = r["answer"], new, info
        r.pop("judge", None)
        t.append(info["seconds"])
        print(f"  {r['id']}: unsupported {info['unsupported']}, rewritten {info['rewritten']}, dropped {info['dropped']}, "
              f"{info['seconds']:.2f}s{' TIMEOUT' if info['timed_out'] else ''}", flush=True)
    d["tag"] = tag
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results", f"v3eval_{tag}.json")
    json.dump(d, open(out, "w"), indent=1, default=str, ensure_ascii=False)
    if t:
        t.sort()
        print(f"rewrite seconds: mean {sum(t) / len(t):.2f}, p50 {t[len(t) // 2]:.2f}, p95 {t[int(len(t) * .95) - 1 if len(t) > 1 else 0]:.2f}; "
              f"answers {len(t)}")
    print(f"saved {out} (judge it: python eval_v3.py --rejudge {out})")


if __name__ == "__main__":
    a = sys.argv[1:]
    if a and a[0] == "eval":
        cmd_eval(a[1], a[a.index("--tag") + 1] if "--tag" in a else "rewrite")
    elif a and a[0] == "try":
        print(json.dumps(rewrite(a[1], a[2]), indent=1))
    else:
        print(__doc__)
