#!/usr/bin/env python3
"""
v3 P4 experiment: a small re-ranker trained for Movie Buff, to replace most of the 27B model's
re-ranking time (~8.7 s per hard question, E9).

E6 tried an off-the-shelf cross-encoder (bge-reranker-v2-m3) and it lost on accuracy: it was
never trained on "half-remembered description -> film". This trains one on exactly that, with
no test question involved:

  queries    the 57k tip-of-the-tongue descriptions llama3.2 wrote for 22k films (wiki.db,
             kind='question'; built for E3) - one per film, films in tune/check/final excluded
             + TREC 2023 *train* questions (sets/train.json), with their real answers
  positives  the film, shown as the re-ranker would see it (title, year, TMDB overview, the
             Wikipedia passage closest to the query)
  negatives  7 hard negatives per query from Movie Buff's own fused search (no LLM involved)
  loss       listwise: softmax over the 8 candidates, the right film should win

    python train_reranker.py data --n 4000          # build results/rr_train.jsonl (CPU + embeddings)
    python train_reranker.py train                  # fine-tune (GPU; unloads Ollama's models first)
    python train_reranker.py both --n 4000

Then (run_v3c.sh):
    RERANK_ENGINE=cross_llm RERANK_CROSS_MODEL=models/mb-reranker RERANK_CROSS_KEEP=10   (LLM reads 10, not 50)
    RERANK_ENGINE=cross     RERANK_CROSS_MODEL=models/mb-reranker                        (no LLM at all)
Needs torch + transformers (+ sentence-transformers to use it in retrieve.py), as E6 did.
"""
import argparse
import glob
import json
import os
import random
import sys
import time

BASE = os.environ.get("RR_BASE", "BAAI/bge-reranker-v2-m3")
# 6 Oct: the GPU is held by another job (20 of 23 GB), so training can also run on the CPU.
# RR_DEVICE=cpu|cuda ("" = cuda when it has room). On the CPU use a small base, e.g.
# RR_BASE=cross-encoder/ms-marco-MiniLM-L-6-v2 (22M parameters; bge-reranker-v2-m3 is 568M).
RR_DEVICE = os.environ.get("RR_DEVICE", "")
OUT_DIR = os.environ.get("RR_OUT", "models/mb-reranker")
DATA = "results/rr_train.jsonl"
GROUP = 8          # 1 positive + 7 negatives


def held_out_films():
    """Film ids in any evaluation set (tune, check, final), never trained on."""
    out = set()
    for f in glob.glob("sets/*.json"):
        if os.path.basename(f) in ("train.json", "analytic.json", "multiturn.json", "sections_synth.json"):
            continue
        try:
            for q in json.load(open(f)).get("questions", []):
                k = str(q.get("film_key") or q.get("expect_tmdb") or "")
                if k.isdigit():
                    out.add(int(k))
        except (ValueError, AttributeError):
            pass
    return out


def doc_text(R, conn, mid, q_vec):
    row = conn.execute("SELECT title, release_year, overview FROM movies WHERE id = ?", (mid,)).fetchone()
    if not row:
        return None
    t, y, o = row
    wp = R.wiki_passage(conn, mid, q_vec) if R._wiki_has(conn, "vec") else None
    body = f"{R._clip(o or '', 300)} {wp[1] if wp else ''}"
    return R._clip(f"{t} ({(y or '')[:4]}). {body}", R.RERANK_CROSS_CHARS)


def cmd_data(a):
    os.environ.setdefault("MB_CONFIG", "config.yaml")
    import retrieve as R
    conn = R.open_db("movies.db")
    held = held_out_films()
    rnd = random.Random(11)
    rows = conn.execute("SELECT c.film_id, c.text FROM wiki.chunks c WHERE c.kind = 'question'").fetchall()
    by_film = {}
    for fid, text in rows:
        if fid not in held:
            by_film.setdefault(fid, []).append(text)
    films = sorted(by_film)
    rnd.shuffle(films)
    queries = [(by_film[f][rnd.randrange(len(by_film[f]))], f, "synthetic") for f in films[:a.n]]
    if os.path.exists("sets/train.json"):
        for q in json.load(open("sets/train.json"))["questions"]:
            if q.get("source") == "trec" and q.get("expect_tmdb") and int(q["expect_tmdb"]) not in held:
                for _ in range(3):                                   # real questions count 3x
                    queries.append((q["turns"][-1], int(q["expect_tmdb"]), "trec_train"))
    print(f"{len(queries)} training queries ({sum(1 for q in queries if q[2] == 'trec_train') // 3} TREC train), "
          f"{len(held)} films held out")
    t0 = time.time()
    n_ok = 0
    with open(DATA, "w") as f:
        for i, (q, gold, src) in enumerate(queries, 1):
            try:
                v = R.embed_query(q)
                lists = [[m for m, _ in R.knn_multi(conn, v, 100)], R.wiki_question_ranking(conn, v, 100),
                         R.keyword_ranking(conn, q, 100), R.wiki_keyword_ranking(conn, q, 100)]
                fused = [m for m in R.rrf(*[x for x in lists if x])[:60] if m != gold]
            except Exception as e:  # noqa: BLE001
                print(f"  skip {i}: {e!r}", file=sys.stderr)
                continue
            if len(fused) < GROUP - 1:
                continue
            # hard negatives: mostly from the top, some from further down (like the re-ranker's 50)
            negs = fused[:4] + rnd.sample(fused[4:50], min(GROUP - 1 - 4, len(fused[4:50])))
            pos = doc_text(R, conn, gold, v)
            neg_docs = [d for d in (doc_text(R, conn, m, v) for m in negs) if d]
            if not pos or len(neg_docs) < GROUP - 1:
                continue
            f.write(json.dumps({"q": q, "pos": pos, "neg": neg_docs[:GROUP - 1], "src": src,
                                "gold_rank_in_search": (lists[0].index(gold) + 1) if gold in lists[0] else None}) + "\n")
            n_ok += 1
            if i % 200 == 0:
                print(f"  {i}/{len(queries)}  kept {n_ok}  {(time.time() - t0) / i:.2f}s/query", flush=True)
    print(f"wrote {n_ok} groups to {DATA}")


def cmd_train(a):
    import torch
    from transformers import AutoModelForSequenceClassification, AutoTokenizer
    try:                                      # free the GPU: Ollama's models would not leave room
        import ollama
        for m in ("movie-buff", "llama3.2", "mxbai-embed-large", "gemma3:27b"):
            try:
                ollama.generate(model=m, prompt="", keep_alive=0)
            except Exception:
                pass
    except ImportError:
        pass
    groups = [json.loads(line) for line in open(DATA)]
    random.Random(5).shuffle(groups)
    n_dev = max(50, len(groups) // 20)
    dev, train = groups[:n_dev], groups[n_dev:]
    tok = AutoTokenizer.from_pretrained(BASE)
    dev_name = RR_DEVICE
    if not dev_name:
        dev_name = "cpu"
        if torch.cuda.is_available():
            free, _total = torch.cuda.mem_get_info()
            dev_name = "cuda" if free > 6e9 else "cpu"
    gpu = dev_name == "cuda"
    if not gpu:
        torch.set_num_threads(max(1, (os.cpu_count() or 4) - 2))
    print(f"training on {dev_name}", flush=True)
    model = AutoModelForSequenceClassification.from_pretrained(BASE, num_labels=1, ignore_mismatched_sizes=True).to(dev_name)
    opt = torch.optim.AdamW(model.parameters(), lr=a.lr, weight_decay=0.01)
    steps = (len(train) + a.batch - 1) // a.batch * a.epochs
    sched = torch.optim.lr_scheduler.LambdaLR(opt, lambda s: min(1.0, (s + 1) / (0.1 * steps)) * max(0.0, 1 - s / steps))
    scaler = torch.cuda.amp.GradScaler(enabled=gpu)

    def scores(batch):
        qs, ds = [], []
        for g in batch:
            for d in [g["pos"]] + g["neg"]:
                qs.append(g["q"]); ds.append(d)
        enc = tok(qs, ds, truncation=True, max_length=a.max_len, padding=True, return_tensors="pt").to(dev_name)
        if gpu:
            with torch.autocast("cuda", dtype=torch.float16):
                return model(**enc).logits.view(len(batch), GROUP).float()
        return model(**enc).logits.view(len(batch), GROUP).float()

    def evaluate():
        model.eval()
        hit = 0
        with torch.no_grad():
            for i in range(0, len(dev), 8):
                s = scores(dev[i:i + 8])
                hit += int((s.argmax(1) == 0).sum())
        model.train()
        return hit / len(dev)

    print(f"base {BASE}: dev hit@1 among {GROUP} = {evaluate():.3f} ({len(dev)} groups); training on {len(train)}")
    model.train()
    t0, step = time.time(), 0
    for ep in range(a.epochs):
        random.Random(ep).shuffle(train)
        for i in range(0, len(train), a.batch):
            s = scores(train[i:i + a.batch])
            loss = torch.nn.functional.cross_entropy(s, torch.zeros(len(s), dtype=torch.long, device=dev_name))
            opt.zero_grad()
            scaler.scale(loss).backward()
            scaler.unscale_(opt)
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            scaler.step(opt); scaler.update(); sched.step()
            step += 1
            if step % 100 == 0:
                print(f"  step {step}/{steps} loss {loss.item():.3f} {time.time() - t0:.0f}s", flush=True)
        print(f"epoch {ep + 1}: dev hit@1 = {evaluate():.3f}")
    os.makedirs(OUT_DIR, exist_ok=True)
    model.save_pretrained(OUT_DIR)
    tok.save_pretrained(OUT_DIR)
    json.dump({"base": BASE, "device": dev_name, "groups": len(train), "epochs": a.epochs, "lr": a.lr, "max_len": a.max_len},
              open(os.path.join(OUT_DIR, "movie_buff_training.json"), "w"), indent=1)
    print(f"saved {OUT_DIR} ({(time.time() - t0) / 60:.1f} min)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["data", "train", "both"])
    ap.add_argument("--n", type=int, default=4000)
    ap.add_argument("--epochs", type=int, default=1)
    ap.add_argument("--batch", type=int, default=4, help="groups per step (x8 pairs)")
    ap.add_argument("--lr", type=float, default=2e-5)
    ap.add_argument("--max-len", type=int, default=384)
    a = ap.parse_args()
    if a.cmd in ("data", "both"):
        cmd_data(a)
    if a.cmd in ("train", "both"):
        cmd_train(a)


if __name__ == "__main__":
    main()
