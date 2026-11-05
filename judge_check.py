#!/usr/bin/env python3
"""
E7 (v7, 27 Oct): how far to trust the gemma3:27b judge - 30 answers graded by hand, blind to the judge, then compared.
Reported in the write-up next to every judged number. Rule (written before grading): the judge is trusted for v7
claims when its pass/fail verdict agrees with the hand grade on >= 24/30 (80%) and Cohen's kappa >= 0.6; otherwise
judged results are reported with the measured agreement and the direction of the judge's errors (too lenient / strict).

    python judge_check.py sample results/v3eval_<tag>.json [more.json ...] [--n 30] [--seed 7]
        -> results/judge_check.csv + results/judge_check.html (open in a browser, grade, Export CSV; the judge's
           grades are NOT in the page or the CSV, they stay in results/judge_check_key.json)
    python judge_check.py agree results/judge_check_done.csv
        -> agreement, kappa, per-category table, every disagreement -> results/judge_check_report.txt

Sampling: one answer per (category, judge verdict) in turn until n, so pass and fail answers and every category are
represented; answers without a judge verdict are skipped.
"""
import collections
import csv
import json
import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS = os.path.join(HERE, "results")
COLS = ["n", "id", "category", "question", "notes", "answer", "your verdict (pass/fail)", "grounded (1-5)",
        "answered (yes/partly/no)", "invented facts (short)", "note"]


def load_rows(paths):
    out = []
    for p in paths:
        d = json.load(open(p, encoding="utf-8"))
        tag = d.get("tag") or os.path.basename(p)
        for r in d.get("rows", []):
            j = r.get("judge") or {}
            if r.get("answer") and str(j.get("verdict", "")).lower() in ("pass", "fail"):
                out.append(dict(r, _tag=tag))
    return out


def sample(rows, n, seed):
    rnd = random.Random(seed)
    strata = collections.defaultdict(list)
    for r in rows:
        strata[(r.get("category", "?"), r["judge"]["verdict"].lower())].append(r)
    for k in strata:
        rnd.shuffle(strata[k])
    keys = sorted(strata)
    rnd.shuffle(keys)
    pick, seen = [], set()
    while len(pick) < n and any(strata[k] for k in keys):
        for k in keys:
            while strata[k] and len(pick) < n:
                r = strata[k].pop()
                key = (r.get("id"), r["_tag"])
                if key in seen:
                    continue
                seen.add(key)
                pick.append(r)
                break
    rnd.shuffle(pick)
    return pick


def cmd_sample(paths, n, seed):
    rows = load_rows(paths)
    if not rows:
        sys.exit("no judged answers in " + ", ".join(paths))
    pick = sample(rows, n, seed)
    os.makedirs(RESULTS, exist_ok=True)
    key, data = {}, []
    with open(os.path.join(RESULTS, "judge_check.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(COLS)
        for i, r in enumerate(pick, 1):
            uid = f"{r['_tag']}:{r.get('id')}"
            key[uid] = {"verdict": r["judge"]["verdict"].lower(), "grounded": r["judge"].get("grounded"),
                        "answered": r["judge"].get("answered"), "unsupported": r["judge"].get("unsupported_claims"),
                        "reason": r["judge"].get("reason"), "category": r.get("category")}
            w.writerow([i, uid, r.get("category", ""), r.get("question", ""), r.get("notes", ""), r["answer"],
                        "", "", "", "", ""])
            data.append({"n": i, "id": uid, "cat": r.get("category", ""), "q": r.get("question", ""),
                         "notes": r.get("notes", ""), "a": r["answer"], "ctx": (r.get("context") or "")[:14000]})
    json.dump(key, open(os.path.join(RESULTS, "judge_check_key.json"), "w"), indent=1, ensure_ascii=False)
    page = os.path.join(RESULTS, "judge_check.html")
    open(page, "w", encoding="utf-8").write(PAGE.replace("__DATA__", json.dumps(data).replace("</", "<\\/")))
    c = collections.Counter(key[k]["verdict"] for k in key)
    print(f"{len(pick)} answers ({c['pass']} judge-pass, {c['fail']} judge-fail; "
          f"{len({k['category'] for k in key.values()})} categories) -> results/judge_check.csv\n"
          f"  grading page: {page}  (grade against the context shown, as the judge does; Export CSV when done)")


def kappa(a, b, labels):
    n = len(a)
    if not n:
        return 0.0
    po = sum(x == y for x, y in zip(a, b)) / n
    pe = sum((a.count(lab) / n) * (b.count(lab) / n) for lab in labels)
    return 1.0 if pe == 1 else (po - pe) / (1 - pe)


def cmd_agree(path):
    key = json.load(open(os.path.join(RESULTS, "judge_check_key.json"), encoding="utf-8"))
    rows = list(csv.DictReader(open(path, encoding="utf-8-sig")))
    vk = COLS[6]
    graded = [r for r in rows if (r.get(vk) or "").strip().lower() in ("pass", "fail") and r["id"] in key]
    lines = [f"E7 judge check: {len(graded)} of {len(rows)} answers graded by hand ({path})"]
    if not graded:
        print(lines[0])
        return
    h = [r[vk].strip().lower() for r in graded]
    j = [key[r["id"]]["verdict"] for r in graded]
    agree = sum(x == y for x, y in zip(h, j))
    k = kappa(h, j, ["pass", "fail"])
    lenient = sum(1 for x, y in zip(h, j) if y == "pass" and x == "fail")
    strict = sum(1 for x, y in zip(h, j) if y == "fail" and x == "pass")
    ok = agree >= 0.8 * len(graded) and k >= 0.6
    lines.append(f"verdict agreement {agree}/{len(graded)} = {agree / len(graded):.0%}, Cohen's kappa {k:.2f} "
                 f"(rule: >= 80% and >= 0.6) -> {'PASS: judge trusted' if ok else 'FAIL: report agreement with every judged number'}")
    lines.append(f"judge passed answers you failed (too lenient): {lenient}; judge failed answers you passed (too strict): {strict}")
    gh = [(int(r[COLS[7]]), key[r["id"]].get("grounded")) for r in graded
          if (r.get(COLS[7]) or "").strip().isdigit() and isinstance(key[r["id"]].get("grounded"), (int, float))]
    if gh:
        within = sum(abs(a - b) <= 1 for a, b in gh)
        mean_d = sum(b - a for a, b in gh) / len(gh)
        lines.append(f"grounded score: within 1 point on {within}/{len(gh)}; judge minus you on average {mean_d:+.2f}")
    ah = [(r[COLS[8]].strip().lower(), str(key[r["id"]].get("answered", "")).lower()) for r in graded
          if (r.get(COLS[8]) or "").strip()]
    if ah:
        lines.append(f"answered (yes/partly/no): same on {sum(a == b for a, b in ah)}/{len(ah)}")
    lines.append("\nper category (agree / graded):")
    per = collections.defaultdict(lambda: [0, 0])
    for r, x, y in zip(graded, h, j):
        per[r["category"]][1] += 1
        per[r["category"]][0] += x == y
    for c, (a, n) in sorted(per.items()):
        lines.append(f"  {c:12s} {a}/{n}")
    lines.append("\ndisagreements:")
    for r, x, y in zip(graded, h, j):
        if x != y:
            kk = key[r["id"]]
            lines.append(f"  #{r['n']} {r['id']} [{r['category']}] you {x}, judge {y}: {r['question'][:90]}")
            lines.append(f"      judge's reason: {kk.get('reason')}; judge's unsupported: {kk.get('unsupported')}")
            if (r.get(COLS[9]) or r.get("note") or "").strip():
                lines.append(f"      your note: {(r.get(COLS[9]) or '').strip()} {(r.get('note') or '').strip()}")
    out = "\n".join(lines)
    print(out)
    open(os.path.join(RESULTS, "judge_check_report.txt"), "w", encoding="utf-8").write(out + "\n")
    return ok


PAGE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Judge Check</title><style>
:root{--bg:#fafaf7;--fg:#1d1d1b;--mute:#6b6b66;--card:#fff;--line:#e3e1da;--on:#2f5d8a;--onfg:#fff}
@media (prefers-color-scheme:dark){:root{--bg:#161615;--fg:#ecebe6;--mute:#a09f99;--card:#20201e;--line:#34332f;--on:#8ab4dc;--onfg:#111}}
body{background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,sans-serif;margin:0 auto;padding:16px;max-width:900px}
h1{font-size:20px;margin:4px 0}.bar{position:sticky;top:0;background:var(--bg);padding:8px 0;border-bottom:1px solid var(--line);display:flex;gap:10px;flex-wrap:wrap;align-items:center;z-index:2}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px;margin:12px 0}
.meta{color:var(--mute);font-size:13px}.q{font-weight:600;margin:6px 0}.notes{font-size:13.5px;color:var(--mute);margin:4px 0 8px}
.ans{white-space:pre-wrap;border-left:3px solid var(--line);padding-left:10px;margin:8px 0}
details{margin:6px 0}summary{cursor:pointer;font-size:13px;color:var(--mute)}pre{white-space:pre-wrap;font-size:12px;max-height:340px;overflow:auto;background:var(--bg);padding:8px;border-radius:6px}
.row{display:flex;gap:6px;flex-wrap:wrap;align-items:center;margin:4px 0}.row b{width:150px;font-weight:600;font-size:13px}
button{font:inherit;font-size:13px;border:1px solid var(--line);background:transparent;color:var(--fg);border-radius:6px;padding:3px 10px;cursor:pointer}
button.on{background:var(--on);color:var(--onfg);border-color:var(--on)}input{font:inherit;flex:1;min-width:160px;border:1px solid var(--line);background:transparent;color:var(--fg);border-radius:6px;padding:4px}
.done{opacity:.65}</style></head><body>
<h1>Judge check</h1><p class="meta">Grade each answer the way the judge is told to: against the context the bot was given and the notes, not your own film knowledge. Pass = meets the notes and invents nothing important. The judge's grades are hidden until you export.</p>
<div class="bar"><span id="count"></span><button id="exp">Export CSV</button></div><div id="list"></div>
<script>
const DATA=__DATA__;const KEY="mb-judge-check-v1";let L={};try{L=JSON.parse(localStorage.getItem(KEY)||"{}")}catch(e){}
const F=[["v","Verdict",["pass","fail"]],["g","Grounded",["1","2","3","4","5"]],["a","Answered",["yes","partly","no"]]];
function save(){try{localStorage.setItem(KEY,JSON.stringify(L))}catch(e){}count()}
function count(){document.getElementById("count").textContent=`${DATA.filter(d=>L[d.id]&&L[d.id].v).length} of ${DATA.length} graded`}
function esc(s){return String(s??"").replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]))}
function render(){const box=document.getElementById("list");box.innerHTML="";
for(const d of DATA){const v=L[d.id]||{};const c=document.createElement("div");c.className="card"+(v.v?" done":"");
c.innerHTML=`<div class="meta">#${d.n} · ${esc(d.cat)}</div><div class="q">${esc(d.q)}</div><div class="notes">Notes: ${esc(d.notes)}</div>`+
`<div class="ans">${esc(d.a)}</div><details><summary>Context the bot was given</summary><pre>${esc(d.ctx)}</pre></details>`+
F.map(([k,lab,opts])=>`<div class="row"><b>${lab}</b>${opts.map(o=>`<button data-k="${k}" data-v="${o}" class="${v[k]===o?"on":""}">${o}</button>`).join("")}</div>`).join("")+
`<div class="row"><b>Invented facts</b><input data-k="i" value="${esc(v.i||"")}"></div><div class="row"><b>Note</b><input data-k="note" value="${esc(v.note||"")}"></div>`;
c.addEventListener("click",e=>{const b=e.target.closest("button");if(!b)return;L[d.id]=L[d.id]||{};L[d.id][b.dataset.k]=L[d.id][b.dataset.k]===b.dataset.v?"":b.dataset.v;save();render()});
c.querySelectorAll("input").forEach(inp=>inp.addEventListener("change",e=>{L[d.id]=L[d.id]||{};L[d.id][e.target.dataset.k]=e.target.value;save()}));box.appendChild(c)}count()}
document.getElementById("exp").onclick=()=>{const q=s=>'"'+String(s??"").replace(/"/g,'""')+'"';
const head=["n","id","category","question","notes","answer","your verdict (pass/fail)","grounded (1-5)","answered (yes/partly/no)","invented facts (short)","note"];
const rows=DATA.map(d=>{const v=L[d.id]||{};return [d.n,d.id,d.cat,d.q,d.notes,d.a,v.v||"",v.g||"",v.a||"",v.i||"",v.note||""].map(q).join(",")});
const blob=new Blob(["﻿"+head.map(q).join(",")+"\n"+rows.join("\n")],{type:"text/csv"});const a=document.createElement("a");a.href=URL.createObjectURL(blob);a.download="judge_check_done.csv";a.click()};
render();</script></body></html>"""


if __name__ == "__main__":
    a = sys.argv[1:]
    if a and a[0] == "sample" and len(a) > 1:
        paths = [x for x in a[1:] if x.endswith(".json")]
        n = int(a[a.index("--n") + 1]) if "--n" in a else 30
        seed = int(a[a.index("--seed") + 1]) if "--seed" in a else 7
        cmd_sample(paths, n, seed)
    elif a and a[0] == "agree" and len(a) > 1:
        sys.exit(0 if cmd_agree(a[1]) else 1)
    else:
        print(__doc__)
