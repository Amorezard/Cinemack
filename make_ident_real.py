#!/usr/bin/env python3
"""9 Nov (4b): the real-film half of the 4b check. From the tuning sets tune.json + check.json, take single-turn
questions that describe ONE expected film without naming it (no recommendations), and write them in eval_v3 form:
sets/tune_v8_ident_real_ev.json (at most 40, in file order). Tuning sets only - never sealed ones."""
import collections
import json
import re

REC = re.compile(r"\b(similar|recommend|suggest|something like|films like|movies like|movies|films|shows)\b", re.I)


def norm(s):
    s = re.sub(r"\s*\(\d{4}\)\s*$", "", s or "").casefold()
    return re.sub(r"^(the|a|an) ", "", re.sub(r"[^\w]+", " ", s).strip())


out, seen, cats_all, cats = [], set(), collections.Counter(), collections.Counter()
for path in ("sets/tune.json", "sets/check.json"):
    d = json.load(open(path))
    for q in (d["questions"] if isinstance(d, dict) else d):
        cats_all[q.get("category", "?")] += 1
        turns = q.get("turns") or [q.get("question")]
        if len(turns) != 1:
            continue
        text = turns[0] if isinstance(turns[0], str) else (turns[0] or {}).get("user", "")
        exp = q.get("expect_retrieved") or q.get("expect_films") or []
        exp = [e if isinstance(e, str) else (e or {}).get("title", "") for e in exp]
        if len(exp) != 1 or not text or not exp[0]:
            continue
        title = re.sub(r"\s*\(\d{4}\)\s*$", "", exp[0])
        if f" {norm(title)} " in f" {norm(text)} " or REC.search(text) or norm(text) in seen:
            continue
        seen.add(norm(text))
        cats[q.get("category", "?")] += 1
        out.append({"id": f"rid_{q.get('id', len(out))}", "category": "real_ident", "question": text,
                    "expect_films": [title],
                    "notes": f"A real film: {title}. PASS only if the answer names {title} as the film described; "
                             f"FAIL if it says no film matches or names a different film."})
out = out[:40]
json.dump({"about": "9 Nov 4b: real film descriptions from tune.json + check.json (tuning), eval_v3 form",
           "questions": out}, open("sets/tune_v8_ident_real_ev.json", "w"), indent=1)
print(f"categories in tune+check: {dict(cats_all)}")
print(f"picked {len(out)} (by category before the 40 cap: {dict(cats)}) -> sets/tune_v8_ident_real_ev.json")
for q in out[:5]:
    print(f"  {q['id']}: {q['question'][:80]!r} -> {q['expect_films'][0]}")
