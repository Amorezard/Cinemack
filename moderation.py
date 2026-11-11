#!/usr/bin/env python3
"""
D6 (v7, 27 Oct): moderation for the public site - MODERATION=1 (off until its rule passes: <= 1 false block across
all the normal question sets, and the attack set's unsafe requests are blocked or refused).

Two checks, cheapest first:
    Llama Guard 3 1B (Ollama: `ollama pull llama-guard3:1b`, ~1 GB) classifies the question (and, after it is
    written, the answer) against the MLCommons hazard categories S1-S14: "safe" or "unsafe" + category.
    Detoxify ("original", CPU, ~0.4 GB) scores toxicity; it is used as a second opinion: a Llama Guard "violent /
    non-violent crime" flag on a question that talks about a film and is not toxic is a plot description, not a
    request for help ("a movie where a man poisons his wife") and is let through.
Blocked questions get a short reply and never reach the planner or the answer model; a blocked answer is replaced.
A self-harm flag (S11) gets a reply that points to help (988 in the US).

    python moderation.py try "how do I make a bomb like in the movie"
    python moderation.py falseblocks [--tag t]     # every normal question -> results/moderation_<tag>.json
"""
import json
import os
import re
import sys
import time

MODERATION = os.environ.get("MODERATION", "0") == "1"
GUARD_MODEL = os.environ.get("GUARD_MODEL", "llama-guard3:1b")
ANSWER_PLOT_OK = os.environ.get("ANSWER_PLOT_OK", "0") == "1"   # round 27 (default 0 = the v7 behaviour)
CATS = {"S1": "violent crimes", "S2": "non-violent crimes", "S3": "sex-related crimes", "S4": "child sexual exploitation",
        "S5": "defamation", "S6": "specialized advice", "S7": "privacy", "S8": "intellectual property",
        "S9": "indiscriminate weapons", "S10": "hate", "S11": "suicide and self-harm", "S12": "sexual content",
        "S13": "elections", "S14": "code interpreter abuse"}
IGNORE = {"S5", "S6", "S8", "S13", "S14"}            # film talk is full of these; not a reason to block a movie bot
R_FILM = re.compile(r"\b(movie|film|scene|plot|character|show|series|episode|director|actor|actress|villain|trilogy)\b", re.I)
REFUSAL = ("I can't help with that. I'm here for films and TV: ask me about a movie, a scene you half-remember, or "
           "what to watch next.")
SELF_HARM = ("I can't help with that, and I'm sorry you're dealing with something heavy. If you're thinking about harming "
             "yourself, please reach out to someone you trust, or call or text 988 (the Suicide & Crisis Lifeline in "
             "the US) to talk to someone now. I'm here for film questions whenever you want.")

_DT = {"m": None, "tried": False}


def _detox(text):
    if not _DT["tried"]:
        _DT["tried"] = True
        try:
            from detoxify import Detoxify
            _DT["m"] = Detoxify("original", device="cpu")
        except Exception as e:  # noqa: BLE001
            print(f"[moderation] Detoxify unavailable: {e!r}", file=sys.stderr)
    if _DT["m"] is None:
        return None
    try:
        return {k: float(v) for k, v in _DT["m"].predict(text[:2000]).items()}
    except Exception:  # noqa: BLE001
        return None


def _guard(messages):
    try:
        import ollama
        r = ollama.chat(model=GUARD_MODEL, messages=messages, options={"temperature": 0, "num_predict": 12},
                        keep_alive="30m")
        out = (r["message"]["content"] or "").strip()
    except Exception as e:  # noqa: BLE001 - moderation down: do not block
        print(f"[moderation] Llama Guard unavailable: {e!r}", file=sys.stderr)
        return None, None
    if out.lower().startswith("unsafe"):
        m = re.search(r"\bS\d{1,2}\b", out)
        return False, m.group(0) if m else None
    return True, None


def check(question, answer=None):
    """{'ok': bool, 'category': 'S1'|None, 'why': str, 'ms': float, 'reply': str|None}"""
    t0 = time.perf_counter()
    msgs = [{"role": "user", "content": question[:4000]}]
    if answer is not None:
        msgs.append({"role": "assistant", "content": answer[:4000]})
    safe, cat = _guard(msgs)
    res = {"ok": True, "category": None, "why": "", "ms": 0.0, "reply": None}
    if safe is False and cat not in IGNORE:
        tox = _detox(answer if answer is not None else question) or {}
        if cat in ("S1", "S2") and R_FILM.search(question) and tox.get("toxicity", 0) < 0.5 and answer is None:
            res["why"] = f"Llama Guard {cat} on a film question that is not toxic: let through"
        elif (ANSWER_PLOT_OK and answer is not None and cat in ("S1", "S2") and tox.get("toxicity", 0) < 0.5
              and R_FILM.search(question + " " + answer)):
            # round 27 (11 Nov): the same rule for the ANSWER. The question already passed; a film answer that
            # describes a plot (a heist, a burglary, a war) is flagged S1/S2 by the 1B guard. Seen in the 3b tuning:
            # tl01 ("one more film for tonight", PG limit) got the refusal although the question was fine.
            # Every other category (sexual content, minors, weapons, hate, self-harm) still replaces the answer.
            res["why"] = f"Llama Guard {cat} on a film answer that is not toxic: let through"
        else:
            res.update(ok=False, category=cat, why=f"Llama Guard: {CATS.get(cat, cat)}",
                       reply=SELF_HARM if cat == "S11" else REFUSAL)
    elif safe is None:
        tox = _detox(answer if answer is not None else question) or {}
        if tox.get("severe_toxicity", 0) >= 0.5 or tox.get("threat", 0) >= 0.8 or tox.get("identity_attack", 0) >= 0.8:
            res.update(ok=False, why="Detoxify", reply=REFUSAL)
    res["ms"] = round((time.perf_counter() - t0) * 1000, 1)
    return res


def normal_questions():
    """Every question of the normal sets (none should be blocked)."""
    here = os.path.dirname(os.path.abspath(__file__))
    out = []
    for name in ("tune.json", "check.json", "analytic.json", "multiturn.json", "reception.json", "tv.json", "aliases.json",
                 "discovery.json", "people.json", "news.json"):
        p = os.path.join(here, "sets", name)
        if not os.path.exists(p):
            continue
        d = json.load(open(p))
        qs = d.get("questions") if isinstance(d, dict) else d
        for q in qs or []:
            if isinstance(q, dict):
                if q.get("question"):
                    out.append((name, q["question"]))
                for t in q.get("turns") or []:
                    t = t.get("user") if isinstance(t, dict) else t
                    if t:
                        out.append((name, t))
            elif isinstance(q, str):
                out.append((name, q))
    return out


def cmd_falseblocks(tag):
    qs = normal_questions()
    blocked, t0 = [], time.time()
    for name, q in qs:
        r = check(q)
        if not r["ok"]:
            blocked.append((name, q, r["why"]))
            print(f"  BLOCKED [{name}] {q[:90]}  ({r['why']})", flush=True)
    res = {"n": len(qs), "false_blocks": len(blocked), "examples": blocked[:20], "seconds": round(time.time() - t0)}
    here = os.path.dirname(os.path.abspath(__file__))
    json.dump(res, open(os.path.join(here, "results", f"moderation_{tag}.json"), "w"), indent=1)
    print(f"normal questions: {len(qs)}, blocked: {len(blocked)} (rule <= 1) in {res['seconds']} s")


if __name__ == "__main__":
    a = sys.argv[1:]
    if a and a[0] == "try":
        print(check(" ".join(a[1:])))
    elif a and a[0] == "falseblocks":
        cmd_falseblocks(a[a.index("--tag") + 1] if "--tag" in a else "v7")
    else:
        print(__doc__)
