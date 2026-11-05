#!/usr/bin/env python3
"""
R16 (v7, 27 Oct): claim check - which sentences of an answer (or of a reception summary) are supported by the
evidence it was given. A small NLI model (natural-language inference: does the premise entail the sentence?)
reads each factual sentence against its cited evidence, or against the whole context when nothing is cited.

    python claimcheck.py eval results/v3eval_<tag>.json      # per answer + totals -> results/claimcheck_<tag>.json
    python claimcheck.py summaries --limit 400               # R13 rule: >= 95% of summary sentences supported
    python claimcheck.py try "premise" "hypothesis"

Measures (the reception set's rule uses the first):
    unsupported rate     factual sentences no premise entails / factual sentences   (the hallucination rate)
    citation precision   cited sentences whose cited evidence entails them / cited sentences
    citation recall      supported factual sentences that carry a citation [n] or a source tag [..] / supported ones
    sources used         distinct evidence numbers cited
A sentence is "factual" when it reports something (a number, a quote, a named source, a reported verb such as
praised / called / rated). Sentences marked as interpretation ("this suggests", "likely", "may", "perhaps") are not
checked: the answer is allowed to interpret, as long as it says so.

Model: NLI_MODEL (default cross-encoder/nli-deberta-v3-small); entailment probability >= NLI_MIN (0.5) = supported.
"""
import argparse
import json
import os
import re
import sqlite3
import sys

NLI_MODEL = os.environ.get("NLI_MODEL", "cross-encoder/nli-deberta-v3-small")
NLI_MIN = float(os.environ.get("NLI_MIN", "0.5"))
HERE = os.path.dirname(os.path.abspath(__file__))

R_INTERP = re.compile(r"\b(this suggests|suggests|likely|probably|perhaps|maybe|may have|might|could (have|be)|"
                      r"i think|in my view|arguably|it seems|seems to|appears to|my (read|guess|interpretation)|"
                      r"interpretation)\b", re.I)
R_FACTUAL = re.compile(r"(\d|[\"“”]|\[\d+\]|\b(critics?|reviewers?|audiences?|viewers|users?|fans|"
                       r"praised|criticized|criticised|called|described|rated|gave|wrote|said|scored|grossed|earned|"
                       r"rotten tomatoes|metacritic|imdb|movielens|cinemascore|tmdb|variety|guardian|reporter|ebert|"
                       r"times|percent|stars?)\b)", re.I)
_SPLIT = re.compile(r"(?<=[.!?])[\"”')\]]*\s+(?=[\"“(\[]?[A-Z0-9*])")


def sentences(answer):
    t = re.sub(r"\n\s*\*Sources:.*$", "", answer or "", flags=re.S)        # the code-added sources footer
    t = re.sub(r"\*\*([^*]+)\*\*", r"\1", t)
    out = []
    for line in t.split("\n"):
        line = re.sub(r"^\s*([-*•]|\d+[.)])\s+", "", line).strip()
        if not line or line.endswith(":") and len(line) < 60:
            continue
        out += [s.strip() for s in _SPLIT.split(line) if len(s.split()) >= 5]
    return out


def cited(sentence):
    nums = []
    for m in re.finditer(r"\[(\d{1,2}(?:\s*[,–-]\s*\d{1,2})*)\]", sentence):
        for part in re.split(r"\s*,\s*", m.group(1)):
            if re.match(r"^\d+\s*[–-]\s*\d+$", part):
                a, b = [int(x) for x in re.split(r"\s*[–-]\s*", part)]
                nums += list(range(a, b + 1)) if 0 < b - a < 10 else []
            elif part.strip().isdigit():
                nums.append(int(part))
    return nums


def clean_hyp(s):
    s = re.sub(r"\[\d{1,2}(?:\s*[,–-]\s*\d{1,2})*\]", "", s)
    s = re.sub(r"\[[A-Z][^\]]{2,80}\]", "", s)                   # [TMDB], [Wikipedia: Reception]
    return " ".join(s.split())


def parse_context(ctx):
    """(evidence {n: text}, other premise lines) from a prompt's context."""
    ev, lines = {}, []
    for raw in (ctx or "").split("\n"):
        line = raw.strip()
        m = re.match(r"^\[(\d{1,2})\]\s+(.*)$", line)
        if m:
            ev[int(m.group(1))] = m.group(2)
        elif len(line.split()) >= 4:
            for part in re.split(r"\s\|\s", line) if " | " in line and len(line) > 300 else [line]:
                lines.append(part)
    return ev, lines


class NLI:
    def __init__(self, model=NLI_MODEL):
        import torch
        from transformers import AutoModelForSequenceClassification, AutoTokenizer
        self.torch = torch
        self.dev = "cuda" if torch.cuda.is_available() and torch.cuda.mem_get_info()[0] > 1.5e9 else "cpu"
        self.tok = AutoTokenizer.from_pretrained(model)
        self.m = AutoModelForSequenceClassification.from_pretrained(model).to(self.dev).eval()
        lab = {i: l.lower() for i, l in self.m.config.id2label.items()}
        self.ent = next(i for i, l in lab.items() if l.startswith("entail"))

    def entail(self, pairs, batch=32):
        out = []
        with self.torch.no_grad():
            for s in range(0, len(pairs), batch):
                p = pairs[s:s + batch]
                enc = self.tok([a for a, _b in p], [b for _a, b in p], truncation=True, max_length=320, padding=True,
                               return_tensors="pt").to(self.dev)
                pr = self.torch.softmax(self.m(**enc).logits.float(), -1)[:, self.ent].cpu().tolist()
                out += pr
        return out


_NLI = {"m": None}


def nli():
    if _NLI["m"] is None:
        _NLI["m"] = NLI()
    return _NLI["m"]


_R_NUM = re.compile(r"(?<![\w.])(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)(\s*%|\s*/\s*\d+)?")
_NUM = [0, 0]   # summaries: numbers in factual sentences, numbers found in the inputs
_NUMBAD = []


def _nums(s):
    """Number tokens with their unit: ('8.4', '/10'), ('95', '%'), ('46295', ''). Bare single digits skipped."""
    out = []
    for m in _R_NUM.finditer(s or ""):
        v = m.group(1).replace(",", "")
        u = (m.group(2) or "").replace(" ", "")
        if not u and len(v) == 1:
            continue
        out.append((v, u))
    return out


def _ctx_nums(context):
    have = set()
    for v, u in _nums(context):
        have.add(v + u)
        have.add(v)
    return have


def _num_missing(s, have):
    """A number with a unit must appear with the same unit; a bare number may appear with any unit."""
    return [v + u for v, u in _nums(s) if (v + u) not in have]


def check(answer, context, model=None):
    """{'sentences': [...], 'factual', 'unsupported', 'cited', 'cited_ok', 'supported', 'supported_tagged',
    'sources_used'}"""
    m = model or nli()
    ev, lines = parse_context(context)
    have_nums = _ctx_nums(context or "")
    rows = []
    for s in sentences(answer):
        if R_INTERP.search(s):
            rows.append({"text": s, "kind": "interpretation"})
            continue
        if not R_FACTUAL.search(s):
            rows.append({"text": s, "kind": "other"})
            continue
        c = [n for n in cited(s) if n in ev]
        hyp = clean_hyp(s)
        prem_c = [ev[n] for n in c]
        prem_all = list(ev.values()) + lines
        pairs = [(p, hyp) for p in prem_c] + [(p, hyp) for p in prem_all]
        probs = m.entail(pairs) if pairs else []
        pc, pa = probs[:len(prem_c)], probs[len(prem_c):]
        best_all = max(pa) if pa else 0.0
        rows.append({"text": s, "kind": "factual", "n_nums": len(_nums(s)), "nums_missing": _num_missing(s, have_nums), "cited": c, "cited_entail": round(max(pc), 3) if pc else None,
                     "best_entail": round(max(best_all, max(pc) if pc else 0.0), 3),
                     "tagged": bool(c) or bool(re.search(r"\[[A-Z][^\]]{2,80}\]|\bper [A-Z]", s))})
    fact = [r for r in rows if r["kind"] == "factual"]
    sup = [r for r in fact if r["best_entail"] >= NLI_MIN]
    cit = [r for r in fact if r["cited"]]
    return {"sentences": rows, "factual": len(fact), "unsupported": len(fact) - len(sup), "supported": len(sup),
            "cited": len(cit), "cited_ok": sum(1 for r in cit if (r["cited_entail"] or 0) >= NLI_MIN),
            "supported_tagged": sum(1 for r in sup if r["tagged"]),
            "sources_used": len({n for r in cit for n in r["cited"]}),
            "nums": sum(r["n_nums"] for r in fact), "nums_ok": sum(r["n_nums"] - len(r["nums_missing"]) for r in fact)}


def totals(results):
    f = sum(r["factual"] for r in results)
    u = sum(r["unsupported"] for r in results)
    c = sum(r["cited"] for r in results)
    co = sum(r["cited_ok"] for r in results)
    s = sum(r["supported"] for r in results)
    st = sum(r["supported_tagged"] for r in results)
    return {"answers": len(results), "factual": f, "unsupported_rate": round(u / f, 3) if f else None,
            "citation_precision": round(co / c, 3) if c else None, "citation_recall": round(st / s, 3) if s else None,
            "mean_sources_used": round(sum(r["sources_used"] for r in results) / max(1, len(results)), 2)}


def cmd_eval(a):
    d = json.load(open(a.path))
    m = nli()
    res = []
    for r in d["rows"]:
        if not r.get("answer"):
            continue
        c = check(r["answer"], r.get("context") or "", m)
        c["id"] = r["id"]
        res.append(c)
        print(f"  {r['id']}: factual {c['factual']}, unsupported {c['unsupported']}, cited {c['cited']} "
              f"(ok {c['cited_ok']}), sources {c['sources_used']}", flush=True)
    t = totals(res)
    tag = d.get("tag") or os.path.basename(a.path)
    out = os.path.join(HERE, "results", f"claimcheck_{tag}.json")
    json.dump({"tag": tag, "model": NLI_MODEL, "min": NLI_MIN, "totals": t, "answers": res}, open(out, "w"), indent=1)
    print(f"{tag}: {json.dumps(t)}\nsaved {out}")


_NOTES_COUNT = [0, 0]  # [list-form sources_json, empty notes]


def _notes_text(sj):
    try:
        v = json.loads(sj or "{}") or {}
    except ValueError:
        v = {}
    if isinstance(v, dict):
        v = v.get("inputs") or ""
    else:
        _NOTES_COUNT[0] += 1
    if isinstance(v, list):
        parts = []
        for x in v:
            if isinstance(x, str):
                parts.append(x)
            elif isinstance(x, dict):
                parts.append(" ".join(str(x[k]) for k in ("text", "quote", "summary", "inputs") if x.get(k)))
        v = "\n".join(p for p in parts if p)
    if not isinstance(v, str):
        v = ""
    if not v:
        _NOTES_COUNT[1] += 1
    return v


def cmd_summaries(a):
    import facts_db as F
    conn = F.connect(readonly=True)
    rows = conn.execute("select key, tmdb_id, level, text, sources_json from summaries where kind = 'reception' "
                        "order by random() limit ?", (a.limit,)).fetchall()
    m = nli()
    sup = tot = 0
    bad = []
    for key, fid, level, text, sj in rows:
        notes = _notes_text(sj)
        c = check(text, notes, m)
        tot += c["factual"]
        sup += c["supported"]
        _NUM[0] += c["nums"]; _NUM[1] += c["nums_ok"]
        _NUMBAD.extend((fid, r["text"], r["nums_missing"]) for r in c["sentences"] if r.get("nums_missing"))
        bad += [(fid, level, r["text"]) for r in c["sentences"] if r["kind"] == "factual" and r["best_entail"] < NLI_MIN]
    share = sup / tot if tot else 0
    print(f"summaries with list-form sources_json: {_NOTES_COUNT[0]}, with no source text: {_NOTES_COUNT[1]}")
    print(f"numbers in factual sentences found in the inputs (same unit): {_NUM[1]}/{_NUM[0]} = "
          f"{(_NUM[1] / _NUM[0] if _NUM[0] else 0):.1%}")
    for fid, s, miss in _NUMBAD[:12]:
        print(f"  number not in inputs (film {fid}): {miss}  <- {s[:150]}")
    print(f"summary sentences supported: {sup}/{tot} = {share:.1%} over {len(rows)} summaries (rule >= 95%: "
          f"{'PASS' if share >= 0.95 else 'FAIL'})")
    for fid, level, s in bad[:15]:
        print(f"  unsupported (film {fid}, level {level}): {s[:160]}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["eval", "summaries", "try"])
    ap.add_argument("args", nargs="*")
    ap.add_argument("--limit", type=int, default=400)
    a = ap.parse_args()
    if a.cmd == "eval":
        a.path = a.args[0]
        cmd_eval(a)
    elif a.cmd == "summaries":
        cmd_summaries(a)
    else:
        print(nli().entail([(a.args[0], a.args[1])]))


if __name__ == "__main__":
    main()
