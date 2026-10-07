#!/usr/bin/env python3
"""
v3 hierarchical summaries (film -> section -> passages) and the experiment that decides
whether retrieval should use them.

    python summarize.py build --films 800           # section summaries for the most-voted films (llama3.2)
    python summarize.py embed                       # embed the summaries (mxbai, like the passages)
    python summarize.py make-queries --n 200        # synthetic questions, one per sampled passage
    python summarize.py eval                        # flat passages vs summary-first, two tasks
    python summarize.py show "Jaws"

Representation (raw passages are never replaced; summaries only point at them):
    film ─► section kind (production / reception / box_office / themes / ...)
              ├── summary: 2-3 sentences of that section, from its passages only
              └── passages: the original ~180-word chunks (sections table), used for the answer

The experiment (written before running it). Queries: llama3.2 reads one sampled passage and
writes the question a curious viewer might ask that this passage answers, without naming the
film; the passage is the gold. Two tasks:
  A. across all films ("movies whose production went badly over budget"): rank films.
       flat           cosine over every passage, film = best passage
       summary-first  cosine over section summaries -> top 20 (film, section) -> their passages
     measured: film hit@1 / hit@5, passages scored per query (cost), ms
  B. within the named film ("how did they film X?" once X is known): pick the passage.
       flat           best passage of the film
       summary-first  best section summary of the film -> best passage inside that section
     measured: gold passage hit@1, section-kind accuracy
Adopt summary-first for a task only if it is not worse on hit@1 and is cheaper; otherwise keep
flat passages (tools.sections_block already does B with flat passages + a kind bonus).
Limitations stated up front: synthetic queries are worded like the passage they came from,
which favours flat passage matching; summaries cover only the films built.
"""
import argparse
import hashlib
import json
import random
import sys
import time
import urllib.request

import numpy as np

import facts_db as F

VERSION = "sum1"
KINDS = ("reception", "production", "box_office", "release", "themes", "legacy", "accolades", "music")


def chat(url, model, system, text, n=180, fmt=None):
    body = {"model": model, "stream": False, "keep_alive": "30m",
            "options": {"temperature": 0, "num_predict": n},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": text}]}
    if fmt:
        body["format"] = fmt
    for attempt in range(3):
        try:
            req = urllib.request.Request(url + "/api/chat", json.dumps(body).encode(),
                                         {"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=180) as r:
                return json.load(r)["message"]["content"].strip()
        except Exception as e:  # noqa: BLE001
            print(f"  retry {attempt + 1}: {str(e)[:100]}", file=sys.stderr)
            time.sleep(3 * (attempt + 1))
    return ""


def embed(url, model, texts, prefix=""):
    body = json.dumps({"model": model, "input": [prefix + t for t in texts], "truncate": True}).encode()
    req = urllib.request.Request(url + "/api/embed", body, {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as r:
        return json.load(r)["embeddings"]


SUM_SYSTEM = ("Summarise this section of a film's Wikipedia article in 2-3 plain sentences (at most 70 words). "
              "Keep the specific facts that matter: names, numbers, places, what went well or badly. Use only "
              "the text given; no opinions of your own. Do not start with the film's title.")


def cmd_build(a):
    conn = F.connect(a.facts)
    titles = dict(conn.execute("select tmdb_id, title from films"))
    fids = [r[0] for r in conn.execute("select s.tmdb_id from sections s left join films f on f.tmdb_id = s.tmdb_id "
                                       "group by s.tmdb_id order by max(coalesce(f.vote_count, 0)) desc limit ?",
                                       (min(a.films, int(__import__("os").environ.get("SUM_FILMS_MAX", "120"))),))]
    todo = []
    for fid in fids:
        for kind in KINDS:
            rows = conn.execute("select sec_id, text from sections where tmdb_id = ? and kind = ? order by sec_id",
                                (fid, kind)).fetchall()
            if not rows:
                continue
            text = " ".join(t for _i, t in rows)[:6000]
            key = hashlib.sha1(f"{VERSION}|{a.model}|{text}".encode()).hexdigest()
            if conn.execute("select 1 from summaries where key = ?", (key,)).fetchone():
                continue
            todo.append((key, fid, kind, text, [i for i, _t in rows]))
    print(f"{len(todo):,} section summaries to write ({len(fids)} films)")
    t0 = time.time()
    for i, (key, fid, kind, text, ids) in enumerate(todo, 1):
        s = chat(a.ollama, a.model, SUM_SYSTEM, f"Film: {titles.get(fid, '')}\nSection: {kind}\n\n{text}")
        if s:
            conn.execute("insert or replace into summaries values (?,?,?,?,?,?,?,?,?)",
                         (key, fid, kind, "section", a.model, VERSION, s, json.dumps(ids), F.now()))
        if i % 25 == 0 or i == len(todo):
            conn.commit()
            rate = i / max(time.time() - t0, 1e-6)
            print(f"  {i:,}/{len(todo):,} {rate:.2f}/s eta {(len(todo) - i) / max(rate, 1e-6) / 60:.0f} min", flush=True)
    conn.commit()


def cmd_embed(a):
    conn = F.connect(a.facts)
    titles = dict(conn.execute("select tmdb_id, title from films"))
    done = {r[0] for r in conn.execute("select key from sum_vec")}
    rows = [r for r in conn.execute("select key, tmdb_id, kind, text from summaries where level = 'section'")
            if r[0] not in done]
    print(f"{len(rows):,} summaries to embed")
    for s in range(0, len(rows), 32):
        part = rows[s:s + 32]
        vecs = embed(a.ollama, a.embed_model, [f"{titles.get(r[1], '')} | {r[2].replace('_', ' ').title()} summary: {r[3]}"
                                               for r in part])
        conn.executemany("insert or replace into sum_vec values (?, ?)",
                         [(r[0], np.asarray(v, dtype=np.float32).tobytes()) for r, v in zip(part, vecs)])
        conn.commit()
    print("done")


Q_SYSTEM = ("Read this passage from a film's Wikipedia article. Write ONE question a curious viewer might ask that "
            "this passage answers. Do not name the film, its title, or its main actors; describe it only by what "
            "the passage is about (e.g. 'Which war movie had its budget double during filming?'). Reply with the "
            "question only.")


def cmd_queries(a):
    conn = F.connect(a.facts)
    have = {r[0] for r in conn.execute("select distinct tmdb_id from summaries")}
    rows = [r for r in conn.execute("select s.sec_id, s.tmdb_id, s.kind, s.text from sections s "
                                    "join sec_vec v on v.sec_id = s.sec_id") if r[1] in have]
    random.Random(7).shuffle(rows)
    out = []
    for sid, fid, kind, text in rows[:min(a.n, int(__import__("os").environ.get("SUM_QUERIES_MAX", "120")))]:
        q = chat(a.ollama, a.model, Q_SYSTEM, text, n=60).split("\n")[0].strip()
        if q:
            out.append({"sec_id": sid, "tmdb_id": fid, "kind": kind, "query": q})
    json.dump(out, open("sets/sections_synth.json", "w"), indent=1)
    print(f"wrote {len(out)} queries to sets/sections_synth.json")


def _unit(m):
    m = m.astype(np.float32)
    return m / (np.linalg.norm(m, axis=1, keepdims=True) + 1e-9)


def cmd_eval(a):
    conn = F.connect(a.facts)
    qs = json.load(open("sets/sections_synth.json"))
    have = {r[0] for r in conn.execute("select distinct tmdb_id from summaries")}
    P = conn.execute("select s.sec_id, s.tmdb_id, s.kind, v.emb from sections s join sec_vec v on v.sec_id = s.sec_id")
    p_ids, p_film, p_kind, p_vec = [], [], [], []
    for sid, fid, kind, emb in P:
        if fid in have:                                    # same films for both methods
            p_ids.append(sid); p_film.append(fid); p_kind.append(kind); p_vec.append(np.frombuffer(emb, dtype=np.float32))
    PV = _unit(np.stack(p_vec)).astype(np.float16)
    p_film, p_kind, p_ids = np.array(p_film), np.array(p_kind), np.array(p_ids)
    S = conn.execute("select s.tmdb_id, s.kind, v.emb from summaries s join sum_vec v on v.key = s.key "
                     "where s.level = 'section'").fetchall()
    s_film = np.array([r[0] for r in S]); s_kind = np.array([r[1] for r in S])
    SV = _unit(np.stack([np.frombuffer(r[2], dtype=np.float32) for r in S])).astype(np.float16)
    print(f"{len(qs)} queries | {len(p_ids):,} passages | {len(S):,} section summaries | {len(have)} films")
    qv = _unit(np.array(embed(a.ollama, a.embed_model, [q["query"] for q in qs],
                              prefix="Represent this sentence for searching relevant passages: ")))
    res = {"A_flat": [0, 0, 0.0, 0], "A_sum": [0, 0, 0.0, 0], "B_flat": [0, 0], "B_sum": [0, 0]}
    for q, v in zip(qs, qv):
        v16 = v.astype(np.float16)
        # ---- A: across films
        t0 = time.perf_counter()
        sims = PV @ v16
        best = {}
        for i in np.argsort(-sims)[:400]:
            best.setdefault(int(p_film[i]), float(sims[i]))
        films_flat = list(best)[:5]
        res["A_flat"][2] += time.perf_counter() - t0
        res["A_flat"][3] += len(p_ids)
        t0 = time.perf_counter()
        ss = SV @ v16
        top = np.argsort(-ss)[:20]
        cand = {(int(s_film[i]), str(s_kind[i])) for i in top}
        mask = np.array([(int(f), str(k)) in cand for f, k in zip(p_film, p_kind)])
        idx = np.nonzero(mask)[0]
        sims2 = PV[idx] @ v16
        best2 = {}
        for j in np.argsort(-sims2):
            best2.setdefault(int(p_film[idx[j]]), float(sims2[j]))
        films_sum = list(best2)[:5]
        res["A_sum"][2] += time.perf_counter() - t0
        res["A_sum"][3] += len(S) + len(idx)
        for name, fl in (("A_flat", films_flat), ("A_sum", films_sum)):
            res[name][0] += bool(fl and fl[0] == q["tmdb_id"])
            res[name][1] += q["tmdb_id"] in fl
        # ---- B: within the film
        fm = np.nonzero(p_film == q["tmdb_id"])[0]
        if len(fm):
            fsims = PV[fm] @ v16
            res["B_flat"][0] += int(p_ids[fm[int(np.argmax(fsims))]] == q["sec_id"])
            sm = np.nonzero(s_film == q["tmdb_id"])[0]
            if len(sm):
                k = str(s_kind[sm[int(np.argmax(SV[sm] @ v16))]])
                res["B_sum"][1] += k == q["kind"]
                km = fm[np.array([p_kind[i] == k for i in fm])]
                if len(km):
                    res["B_sum"][0] += int(p_ids[km[int(np.argmax(PV[km] @ v16))]] == q["sec_id"])
            res["B_flat"][1] += str(p_kind[fm[int(np.argmax(fsims))]]) == q["kind"]
    n = len(qs)
    lines = [f"Summary-first vs flat passages, {n} synthetic queries (sets/sections_synth.json)",
             "A. across films      film hit@1   hit@5   vectors scored/query   ms/query",
             f"   flat              {res['A_flat'][0] / n:8.2f} {res['A_flat'][1] / n:7.2f} {res['A_flat'][3] / n:14.0f} "
             f"{res['A_flat'][2] / n * 1000:10.1f}",
             f"   summary-first     {res['A_sum'][0] / n:8.2f} {res['A_sum'][1] / n:7.2f} {res['A_sum'][3] / n:14.0f} "
             f"{res['A_sum'][2] / n * 1000:10.1f}",
             "B. within the film   passage hit@1   section-kind right",
             f"   flat              {res['B_flat'][0] / n:8.2f} {res['B_flat'][1] / n:12.2f}",
             f"   summary-first     {res['B_sum'][0] / n:8.2f} {res['B_sum'][1] / n:12.2f}"]
    print("\n".join(lines))
    open("results/summaries_eval.txt", "w").write("\n".join(lines) + "\n")


def cmd_show(a):
    conn = F.connect(a.facts, readonly=True)
    for fid, t in conn.execute("select tmdb_id, title from films where title like ? order by vote_count desc limit 1",
                               (a.title,)):
        print(t)
        for kind, text in conn.execute("select kind, text from summaries where tmdb_id = ?", (fid,)):
            print(f"  [{kind}] {text}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["build", "embed", "make-queries", "eval", "show"])
    ap.add_argument("title", nargs="?")
    ap.add_argument("--facts")
    ap.add_argument("--films", type=int, default=800)
    ap.add_argument("--n", type=int, default=200)
    ap.add_argument("--model", default="llama3.2")
    ap.add_argument("--embed-model", default="mxbai-embed-large")
    ap.add_argument("--ollama", default="http://localhost:11434")
    a = ap.parse_args()
    {"build": cmd_build, "embed": cmd_embed, "make-queries": cmd_queries, "eval": cmd_eval,
     "show": cmd_show}[a.cmd](a)


if __name__ == "__main__":
    main()
