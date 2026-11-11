#!/usr/bin/env python3
"""Embedder fine-tuning, first slice (post-v8 workstream B; built 11 Nov 2026, round 27). GPU nights; nothing here
changes the live search: the fine-tuned model is measured on a SHADOW index (numpy, logs/embed_ft/) next to the base
model, and is only a candidate until it passes the gate below and is converted for Ollama (a later step).

    python tools/embed_ft.py data      # training triples + eval questions -> logs/embed_ft/{train,eval}.jsonl
    python tools/embed_ft.py base      # base model (HF mxbai-embed-large-v1): corpus vectors, hard negatives, scores
    python tools/embed_ft.py train     # fine-tune the top layers (in-batch + hard negatives, InfoNCE) -> logs/embed_ft/model
    python tools/embed_ft.py shadow    # corpus vectors with the fine-tuned model
    python tools/embed_ft.py eval      # Recall@5 / Recall@10 / MRR, base vs fine-tuned, per split -> results/embed_ft.txt
    python tools/embed_ft.py all       # every step in order

Data (no new labels):
  * training queries = the generated "tip of the tongue" descriptions in wiki.db (gen_questions, wiki_enrich.py),
    query prefix as at search time; positive = the film's own embed_text in movies.db; hard negative = the
    base model's best-ranked OTHER film for that query.
  * every film named in any question set (sets/*.json - tuning, check and sealed - and test_set.json) is left out of
    training, and 3% of the remaining films are held out as the "synthetic" split.
  * eval splits: "dev" = single-turn questions in sets/tune.json + sets/check.json with expect_retrieved (real,
    human-written; titles matched to movies.db), "synthetic" = held-out films' generated descriptions.
Gate (plan, 8 Nov: "Recall@5/10 and MRR gains set in advance"; written 11 Nov before any run): on "dev" the
fine-tuned model gains at least +0.03 Recall@10 and +0.02 MRR over the base model, and no metric is lower on
"synthetic". Same model size, so query time is unchanged (reported). Base and fine-tuned use the same HF code path.
No new dependency: torch + transformers only (already used by claimcheck.py).
"""
import json
import os
import random
import re
import sqlite3
import sys
import time
from pathlib import Path

RAG = Path(__file__).resolve().parent.parent
OUT = Path(os.environ.get("EMBED_FT_DIR", RAG / "logs" / "embed_ft"))
BASE_MODEL = os.environ.get("EMBED_FT_BASE", "mixedbread-ai/mxbai-embed-large-v1")
MOVIES = os.environ.get("MOVIES_DB", str(RAG / "movies.db"))
WIKI = os.environ.get("WIKI_DB", str(RAG / "wiki.db"))
PREFIX = "Represent this sentence for searching relevant passages: "
MAX_Q, MAX_D = 96, 384
N_TRAIN = int(os.environ.get("EMBED_FT_N", "30000"))
TRAIN_LAYERS = int(os.environ.get("EMBED_FT_LAYERS", "8"))     # only the top layers learn: fits next to other GPU jobs
BATCH = int(os.environ.get("EMBED_FT_BATCH", "16"))
LR = float(os.environ.get("EMBED_FT_LR", "2e-5"))
SCALE = 20.0                                                     # 1 / temperature 0.05
SEED = 27


def norm(s):
    return re.sub(r"[^a-z0-9]+", "", str(s or "").casefold())


def _sets_titles():
    titles = set()
    for p in list((RAG / "sets").glob("*.json")) + [RAG / "test_set.json"]:
        try:
            d = json.load(open(p))
        except (OSError, ValueError):
            continue
        qs = (d.get("questions") or d.get("attacks") or []) if isinstance(d, dict) else d
        for q in qs if isinstance(qs, list) else []:
            if not isinstance(q, dict):
                continue
            for k in ("expect_retrieved", "expect_films", "films", "expect", "expect_absent"):
                v = q.get(k)
                for t in (v if isinstance(v, list) else [v] if isinstance(v, str) else []):
                    if isinstance(t, str):
                        titles.add(norm(t))
    return titles


def cmd_data():
    OUT.mkdir(parents=True, exist_ok=True)
    m = sqlite3.connect(f"file:{MOVIES}?mode=ro", uri=True)
    films = {fid: (t, y) for fid, t, y in m.execute("select id, title, release_year from movies where embed_text is not null")}
    by_title = {}
    for fid, (t, y) in films.items():
        by_title.setdefault(norm(t), []).append(fid)
    banned_t = _sets_titles()
    banned = {fid for k in banned_t for fid in by_title.get(k, [])}
    w = sqlite3.connect(f"file:{WIKI}?mode=ro", uri=True)
    gq = [(fid, txt) for fid, txt in w.execute("select film_id, text from gen_questions where text is not null")
          if fid in films and fid not in banned and len(str(txt).split()) >= 4]
    rnd = random.Random(SEED)
    ids = sorted({f for f, _ in gq})
    held = set(rnd.sample(ids, max(1, len(ids) * 3 // 100)))
    train = [(f, t) for f, t in gq if f not in held]
    rnd.shuffle(train)
    syn = [(f, t) for f, t in gq if f in held]
    rnd.shuffle(syn)
    dev = []
    for p in ("sets/tune.json", "sets/check.json"):
        for q in json.load(open(RAG / p))["questions"]:
            turns = q.get("turns") or []
            exp = q.get("expect_retrieved") or []
            if len(turns) != 1 or len(exp) != 1:
                continue
            cand = by_title.get(norm(exp[0]), [])
            if q.get("expect_year"):
                cand = [c for c in cand if str(films[c][1] or "")[:4] == str(q["expect_year"])[:4]] or cand
            if cand:
                dev.append({"id": q["id"], "q": turns[0], "film": cand[0], "split": "dev"})
    with open(OUT / "train.jsonl", "w") as f:
        for fid, t in train[:N_TRAIN * 2]:
            f.write(json.dumps({"q": t, "film": fid}) + "\n")
    with open(OUT / "eval.jsonl", "w") as f:
        for d in dev:
            f.write(json.dumps(d) + "\n")
        for fid, t in syn[:600]:
            f.write(json.dumps({"q": t, "film": fid, "split": "synthetic"}) + "\n")
    print(f"films {len(films):,}; left out (named in a question set) {len(banned):,}; generated descriptions usable "
          f"{len(gq):,}; train {min(len(train), N_TRAIN * 2):,}; eval dev {len(dev)}, synthetic {min(len(syn), 600)} "
          f"(held-out films {len(held):,}) -> {OUT}")


# ------------------------------------------------------------------ model
class Enc:
    def __init__(self, path):
        import torch
        from transformers import AutoModel, AutoTokenizer
        self.torch = torch
        self.dev = "cuda" if torch.cuda.is_available() else "cpu"
        self.tok = AutoTokenizer.from_pretrained(path)
        self.m = AutoModel.from_pretrained(path).to(self.dev)

    def encode(self, texts, max_len, batch=32, grad=False):
        torch = self.torch
        out = []
        ctx = torch.enable_grad() if grad else torch.no_grad()
        with ctx:
            for s in range(0, len(texts), batch):
                enc = self.tok(texts[s:s + batch], truncation=True, max_length=max_len, padding=True,
                               return_tensors="pt").to(self.dev)
                with torch.autocast(self.dev, dtype=torch.float16, enabled=self.dev == "cuda"):
                    h = self.m(**enc).last_hidden_state[:, 0]           # CLS pooling (mxbai)
                v = torch.nn.functional.normalize(h.float(), dim=-1)
                out.append(v if grad else v.cpu())
        return torch.cat(out) if out else None


def _corpus():
    m = sqlite3.connect(f"file:{MOVIES}?mode=ro", uri=True)
    rows = m.execute("select id, embed_text from movies where embed_text is not null order by id").fetchall()
    return [r[0] for r in rows], [r[1] for r in rows]


def _embed_corpus(enc, name):
    import numpy as np
    ids, texts = _corpus()
    t0 = time.time()
    vecs = enc.encode(texts, MAX_D, batch=32).numpy().astype("float16")
    np.save(OUT / f"corpus_{name}.npy", vecs)
    json.dump(ids, open(OUT / "corpus_ids.json", "w"))
    print(f"corpus {len(ids):,} films embedded with {name} in {time.time() - t0:.0f} s")


def _load(path):
    return [json.loads(l) for l in open(path)]


def _scores(enc, name, rows):
    """Rank of each row's film among the corpus by cosine: [rank]."""
    import numpy as np
    ids = json.load(open(OUT / "corpus_ids.json"))
    pos = {f: i for i, f in enumerate(ids)}
    C = np.load(OUT / f"corpus_{name}.npy").astype("float32")
    t0 = time.time()
    Q = enc.encode([PREFIX + r["q"] for r in rows], MAX_Q, batch=64).numpy()
    q_ms = (time.time() - t0) * 1000 / max(1, len(rows))
    S = Q @ C.T
    ranks = []
    for i, r in enumerate(rows):
        j = pos.get(r["film"])
        ranks.append(int((S[i] > S[i, j]).sum()) + 1 if j is not None else None)
    return ranks, q_ms, S


def _metrics(ranks):
    rs = [r for r in ranks if r]
    n = len(rs) or 1
    return {"n": len(rs), "r5": sum(r <= 5 for r in rs) / n, "r10": sum(r <= 10 for r in rs) / n,
            "mrr": sum(1 / r for r in rs) / n}


def cmd_base():
    import numpy as np
    enc = Enc(BASE_MODEL)
    _embed_corpus(enc, "base")
    tr = _load(OUT / "train.jsonl")[:N_TRAIN]
    ids = json.load(open(OUT / "corpus_ids.json"))
    C = np.load(OUT / "corpus_base.npy").astype("float32")
    pos = {f: i for i, f in enumerate(ids)}
    hard = []
    for s in range(0, len(tr), 512):                         # hard negatives: best-ranked other film (base model)
        part = tr[s:s + 512]
        Q = enc.encode([PREFIX + r["q"] for r in part], MAX_Q, batch=64).numpy()
        S = Q @ C.T
        for i, r in enumerate(part):
            order = np.argsort(-S[i])[:5]
            neg = next((ids[j] for j in order if ids[j] != r["film"]), None)
            hard.append(dict(r, neg=neg, base_rank=int((S[i] > S[i, pos[r["film"]]]).sum()) + 1 if r["film"] in pos else None))
    with open(OUT / "train_hard.jsonl", "w") as f:
        for h in hard:
            f.write(json.dumps(h) + "\n")
    print(f"hard negatives for {len(hard):,} training queries; base model already ranks the film first for "
          f"{sum(1 for h in hard if h['base_rank'] == 1):,}")


def cmd_train():
    import torch
    enc = Enc(BASE_MODEL)
    m = enc.m
    layers = m.encoder.layer
    for p in m.parameters():
        p.requires_grad = False
    for lay in layers[-TRAIN_LAYERS:]:
        for p in lay.parameters():
            p.requires_grad = True
    try:                                     # frozen lower layers: non-reentrant checkpointing keeps the top layers' grads
        m.gradient_checkpointing_enable(gradient_checkpointing_kwargs={"use_reentrant": False})
    except TypeError:
        m.gradient_checkpointing_enable()
        m.enable_input_require_grads()
    m.train()
    _ids, texts = _corpus()
    doc = dict(zip(_ids, texts))
    rows = [r for r in _load(OUT / "train_hard.jsonl") if r["film"] in doc and r.get("neg") in doc]
    opt = torch.optim.AdamW([p for p in m.parameters() if p.requires_grad], lr=LR, weight_decay=0.01)
    steps = len(rows) // BATCH
    sched = torch.optim.lr_scheduler.LambdaLR(opt, lambda s: min(1.0, (s + 1) / max(1, steps // 10)) * max(0.0, 1 - s / max(1, steps)))
    scaler = torch.cuda.amp.GradScaler(enabled=enc.dev == "cuda")
    t0, tot = time.time(), 0.0
    for st in range(steps):
        b = rows[st * BATCH:(st + 1) * BATCH]
        q = enc.encode([PREFIX + r["q"] for r in b], MAX_Q, batch=BATCH, grad=True)
        d = enc.encode([doc[r["film"]] for r in b] + [doc[r["neg"]] for r in b], MAX_D, batch=BATCH * 2, grad=True)
        logits = SCALE * q @ d.T                              # in-batch positives + every hard negative
        loss = torch.nn.functional.cross_entropy(logits, torch.arange(len(b), device=logits.device))
        opt.zero_grad()
        scaler.scale(loss).backward()
        scaler.step(opt)
        scaler.update()
        sched.step()
        tot += loss.item()
        if (st + 1) % 100 == 0 or st + 1 == steps:
            print(f"  step {st + 1}/{steps} loss {tot / (st + 1):.4f} {time.time() - t0:.0f} s", flush=True)
    m.eval()
    (OUT / "model").mkdir(parents=True, exist_ok=True)
    m.save_pretrained(OUT / "model")
    enc.tok.save_pretrained(OUT / "model")
    json.dump({"base": BASE_MODEL, "train_layers": TRAIN_LAYERS, "batch": BATCH, "lr": LR, "steps": steps,
               "rows": len(rows), "seconds": round(time.time() - t0)}, open(OUT / "model" / "embed_ft.json", "w"))
    print(f"saved {OUT / 'model'}")


def cmd_shadow():
    _embed_corpus(Enc(str(OUT / "model")), "ft")


def cmd_eval():
    rows = _load(OUT / "eval.jsonl")
    res, lines = {}, []
    for name, path in (("base", BASE_MODEL), ("ft", str(OUT / "model"))):
        enc = Enc(path)
        ranks, q_ms, _S = _scores(enc, name, rows)
        for sp in ("dev", "synthetic"):
            res[(name, sp)] = _metrics([r for r, row in zip(ranks, rows) if row["split"] == sp])
        res[(name, "ms")] = q_ms
        del enc
    for sp in ("dev", "synthetic"):
        b, f = res[("base", sp)], res[("ft", sp)]
        lines.append(f"{sp:10s} n {b['n']:4d} | base R@5 {b['r5']:.3f} R@10 {b['r10']:.3f} MRR {b['mrr']:.3f} | "
                     f"ft R@5 {f['r5']:.3f} R@10 {f['r10']:.3f} MRR {f['mrr']:.3f}")
    b, f = res[("base", "dev")], res[("ft", "dev")]
    bs, fs = res[("base", "synthetic")], res[("ft", "synthetic")]
    ok = (f["r10"] - b["r10"] >= 0.03 and f["mrr"] - b["mrr"] >= 0.02
          and all(fs[k] >= bs[k] for k in ("r5", "r10", "mrr")))
    lines.append(f"query time: base {res[('base', 'ms')]:.1f} ms, ft {res[('ft', 'ms')]:.1f} ms per question (batched)")
    lines.append(f"GATE (dev +0.03 R@10 and +0.02 MRR; synthetic not lower): {'PASS' if ok else 'FAIL'}")
    txt = "\n".join(lines)
    print(txt)
    (RAG / "results").mkdir(exist_ok=True)
    open(RAG / "results" / "embed_ft.txt", "w").write(f"embed_ft eval {time.strftime('%Y-%m-%d %H:%M')}\n{txt}\n")


if __name__ == "__main__":
    steps = {"data": cmd_data, "base": cmd_base, "train": cmd_train, "shadow": cmd_shadow, "eval": cmd_eval}
    a = sys.argv[1:] or ["help"]
    todo = list(steps) if a[0] == "all" else a
    if not all(s in steps for s in todo):
        print(__doc__)
        sys.exit(0)
    for s in todo:
        print(f"=== embed_ft {s} {time.strftime('%H:%M')}", flush=True)
        steps[s]()
