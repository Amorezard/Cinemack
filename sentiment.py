#!/usr/bin/env python3
"""
R3 + R5 + H7 (v7, 27 Oct): review sentiment with a confidence, joke / sarcasm and review-bombing flags, and the
labelling page for the 200-review test set.

    python sentiment.py run                    # every review / statement not yet scored (or whose text changed):
                                               # model features -> 3-class head fitted on TMDB star ratings -> labels
    python sentiment.py run --sources tmdb     # only TMDB audience reviews (also: tv, critic = Wikipedia statements)
    python sentiment.py flags                  # R5: irony model, review-bombing bursts, rating-text mismatch
    python sentiment.py sample 200             # H7: results/review_labels.csv + results/label_reviews.html
    python sentiment.py compare results/review_labels_done.csv   # rules: macro-F1 >= 0.80 and beats llama3.2;
                                               # flags precision >= 0.8 (needs the labelled file back)
    python sentiment.py stats
    python sentiment.py show "Oppenheimer"

Model. SENT_MODEL (default siebert/sentiment-roberta-large-english: RoBERTa-large fine-tuned on 15 English
sentiment sets, most of them reviews) scores each review as a whole (long reviews in ~400-token chunks, weighted
by length) and up to SENT_MAX_SENTS of its sentences one by one. A small 3-class head (softmax regression on 6
features: whole-review probability, share of clearly positive / negative sentences, sentence mean and spread,
length) turns that into positive / mixed / negative with a probability. The head is fitted on TMDB reviews that
carry a star rating (>= 7/10 positive, 5-6.5 mixed, <= 4.5 negative), on 80% of films, and tested on the other
20% (results/sentiment_head.json has both scores and the calibration error). The mixed class is the point of
the head: a binary model alone calls "gorgeous but too long" either positive or negative.

Terms (E8, 27 Oct). Only text whose terms allow model analysis is scored: TMDB user reviews (tmdb), TMDB TV
reviews (tv) and Wikipedia's critic statements (critic, CC BY-SA). NYT text is never analysed (NYT's terms bar
using its content to develop or ground AI systems without written consent). Guardian text waits until the
Open Platform terms are confirmed for this use (SENT_SOURCES=...,guardian turns it on after that). YouTube
comments are scored only with SENT_SOURCES including "ytc" (4 Nov: YouTube's derived metrics policy allows comment
sentiment as an aggregate satisfaction measure once the API project accepts the "Analytics & Reporting" use case;
it is labelled as Cinemack's, kept per film, and never used to profile viewers; comment text follows the 30-day
rule, the derived labels may be kept up to 36 months).

Tables (facts.db; raw rows are never edited):
    review_sentiment(item_key, tmdb_id, source_type, label, p_pos, p_mixed, p_neg, confidence, doc_p, frac_pos,
        frac_neg, sent_mean, sent_sd, n_sents, n_chars, rating, model, version, text_hash, created_at)
        source_type: audience (TMDB film reviews; tmdb_id = film), tv_audience (TMDB TV reviews; tmdb_id = TV id,
        never join to films), critic_stmt (critic_statements.stmt_id; tmdb_id = film)
    review_flags(review_id, tmdb_id, irony_p, joke, burst, burst_dir, mismatch, version, created_at)
    review_bursts(tmdb_id, start_day, end_day, n, mean_rating, film_mean, direction, share)
"""
import argparse
import collections
import csv
import datetime
import hashlib
import json
import math
import os
import random
import re
import sqlite3
import sys
import time

import facts_db as F

VERSION = "s1"
HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS = os.path.join(HERE, "results")
MODEL = os.environ.get("SENT_MODEL", "siebert/sentiment-roberta-large-english")
IRONY_MODEL = os.environ.get("IRONY_MODEL", "cardiffnlp/twitter-roberta-base-irony")
MAX_SENTS = int(os.environ.get("SENT_MAX_SENTS", "12"))
SOURCES = [s for s in os.environ.get("SENT_SOURCES", "tmdb,tv,critic").split(",") if s]
HEAD_PATH = os.path.join(RESULTS, "sentiment_head.json")
LABELS = ["negative", "mixed", "positive"]
POS_MIN, NEG_MAX = 7.0, 4.5                      # TMDB ratings (0-10) used as labels for the head

DDL = """
create table if not exists review_sentiment(item_key text primary key, tmdb_id integer, source_type text,
  label text, p_pos real, p_mixed real, p_neg real, confidence real, doc_p real, frac_pos real, frac_neg real,
  sent_mean real, sent_sd real, n_sents integer, n_chars integer, rating real, model text, version text,
  text_hash text, created_at text);
create index if not exists review_sentiment_film on review_sentiment(tmdb_id, source_type);
create table if not exists review_flags(review_id text primary key, tmdb_id integer, irony_p real, joke integer,
  burst integer, burst_dir text, mismatch integer, version text, created_at text);
create index if not exists review_flags_film on review_flags(tmdb_id);
create table if not exists review_bursts(tmdb_id integer, start_day text, end_day text, n integer,
  mean_rating real, film_mean real, direction text, share real);
"""


def connect(path=None):
    conn = F.connect(path)
    conn.executescript(DDL)
    return conn


_SENT = re.compile(r"(?<=[.!?])[\"')\]]*\s+(?=[\"'(\[]?[A-Z0-9])")


def sentences(text):
    t = " ".join((text or "").split())
    return [s.strip() for s in _SENT.split(t) if len(s.strip()) >= 12]


def text_hash(t):
    return hashlib.sha1((t or "").encode("utf-8", "replace")).hexdigest()[:16]


# ------------------------------------------------------------------ items
def items(conn, sources, again=False, limit=None):
    """[(item_key, tmdb_id, source_type, text, rating)] still to score."""
    have = {} if again else {k: (h, v) for k, h, v in conn.execute(
        "select item_key, text_hash, version from review_sentiment")}
    out = []

    def add(key, fid, st, text, rating):
        if not text or len(text.strip()) < 15:
            return
        if not again and have.get(key) == (text_hash(text), VERSION):
            return
        out.append((key, fid, st, text, rating))
    if "tmdb" in sources:
        for rid, fid, content, rating in conn.execute("select review_id, tmdb_id, content, rating from reviews"):
            add(rid, fid, "audience", content, rating)
    if "tv" in sources:
        try:
            for rid, sid, content, rating in conn.execute("select review_id, tmdb_id, content, rating from tv_reviews"):
                add(rid, sid, "tv_audience", content, rating)
        except sqlite3.Error:
            pass
    if "critic" in sources:
        try:
            for sid, fid, t in conn.execute("select stmt_id, tmdb_id, text from critic_statements "
                                            "where kind in ('critic', 'summary')"):
                add(f"cs:{sid}", fid, "critic_stmt", t, None)
        except sqlite3.Error:
            pass
    if "ytc" in sources:               # 4 Nov: YouTube comments - only with SENT_SOURCES=...,ytc, i.e. after the API
        try:                           # project accepted the "Analytics & Reporting" use case (derived metrics policy)
            for cid, fid, t in conn.execute("""select c.comment_id, u.tmdb_id, c.text from yt_analysis_comments c join
                    (select distinct video_key, tmdb_id from yt_analysis_units where match_ok = 1) u
                    on u.video_key = c.video_key"""):
                add(f"ytc:{cid}", fid, "yt_comment", t, None)
        except sqlite3.Error:
            pass
    if "guardian" in sources:          # only after the terms check says yes (E8)
        try:
            for rk, fid, t, stars in conn.execute("select review_key, tmdb_id, coalesce(body, summary), stars "
                                                  "from critic_reviews where source = 'guardian' and match >= 1"):
                add(f"cr:{rk}", fid, "guardian", t, None if stars is None else stars * 2)
        except sqlite3.Error:
            pass
    return out[:limit] if limit else out


# ------------------------------------------------------------------ model
class Scorer:
    def __init__(self, model=MODEL, device="auto", batch=32):
        import torch
        from transformers import AutoModelForSequenceClassification, AutoTokenizer
        self.torch = torch
        if device == "auto":
            device = "cpu"
            if torch.cuda.is_available():
                try:
                    free, _tot = torch.cuda.mem_get_info()
                    if free > 2.5e9:
                        device = "cuda"
                    else:
                        print(f"  GPU has only {free / 1e9:.1f} GB free: using the CPU", flush=True)
                except Exception:  # noqa: BLE001
                    device = "cuda"
        self.device = device
        self.tok = AutoTokenizer.from_pretrained(model)
        self.model = AutoModelForSequenceClassification.from_pretrained(model).to(device).eval()
        if device == "cuda":
            self.model.half()
        lab = {i: str(l).lower() for i, l in self.model.config.id2label.items()}
        self.pos = [i for i, l in lab.items() if l.startswith("pos") or l in ("label_1", "label_2") and len(lab) == 2]
        self.neg = [i for i, l in lab.items() if l.startswith("neg") or l == "label_0"]
        self.neu = [i for i, l in lab.items() if l.startswith("neu")]
        if len(lab) == 2 and not self.pos:
            self.pos, self.neg = [1], [0]
        if "irony" in model:
            self.pos = [i for i, l in lab.items() if l == "irony" or l == "label_1"] or [1]
            self.neg, self.neu = [i for i in lab if i not in self.pos], []
        self.batch = batch if device == "cuda" else max(4, batch // 4)
        torch.set_num_threads(max(1, min(8, (os.cpu_count() or 2) - 1)))
        print(f"  model {model} on {device}", flush=True)

    def p_pos(self, texts, max_len=512):
        """P(positive) for each text (for 3-label models: P(pos) + half of P(neutral))."""
        out = [None] * len(texts)
        order = sorted(range(len(texts)), key=lambda i: len(texts[i]))
        with self.torch.no_grad():
            for s in range(0, len(order), self.batch):
                idx = order[s:s + self.batch]
                enc = self.tok([texts[i] for i in idx], truncation=True, max_length=max_len, padding=True,
                               return_tensors="pt").to(self.device)
                prob = self.torch.softmax(self.model(**enc).logits.float(), dim=-1).cpu().numpy()
                for j, i in enumerate(idx):
                    p = float(prob[j][self.pos].sum())
                    if self.neu:
                        p += 0.5 * float(prob[j][self.neu].sum())
                    out[i] = p
        return out


def chunks(text, max_chars=1600):
    sents = sentences(text) or [text]
    out, cur = [], ""
    for s in sents:
        if cur and len(cur) + len(s) > max_chars:
            out.append(cur)
            cur = s
        else:
            cur = (cur + " " + s).strip()
    if cur:
        out.append(cur)
    return out[:6]


def features(scorer, batch_items):
    """[(doc_p, frac_pos, frac_neg, sent_mean, sent_sd, n_sents, n_chars)] for [(key, fid, st, text, rating)]."""
    docs, owner_d, sents, owner_s = [], [], [], []
    for k, it in enumerate(batch_items):
        text = it[3]
        for c in chunks(text):
            docs.append(c)
            owner_d.append((k, len(c)))
        ss = sentences(text)
        if len(ss) > MAX_SENTS:
            ss = ss[:MAX_SENTS // 2] + ss[-(MAX_SENTS - MAX_SENTS // 2):]
        for s in ss:
            sents.append(s)
            owner_s.append(k)
    pd = scorer.p_pos(docs)
    ps = scorer.p_pos(sents, max_len=128) if sents else []
    acc_d = collections.defaultdict(lambda: [0.0, 0])
    for (k, w), p in zip(owner_d, pd):
        acc_d[k][0] += p * w
        acc_d[k][1] += w
    acc_s = collections.defaultdict(list)
    for k, p in zip(owner_s, ps):
        acc_s[k].append(p)
    out = []
    for k, it in enumerate(batch_items):
        dp = acc_d[k][0] / acc_d[k][1] if acc_d[k][1] else 0.5
        sp = acc_s.get(k) or [dp]
        n = len(sp)
        mean = sum(sp) / n
        sd = math.sqrt(sum((x - mean) ** 2 for x in sp) / n)
        out.append((dp, sum(1 for x in sp if x >= 0.9) / n, sum(1 for x in sp if x <= 0.1) / n, mean, sd,
                    len(acc_s.get(k) or []), len(it[3])))
    return out


# ------------------------------------------------------------------ 3-class head (numpy softmax regression)
def _x(f):
    dp, fp, fn, sm, sd, ns, nc = f
    lg = lambda p: math.log(max(min(p, 1 - 1e-4), 1e-4) / (1 - max(min(p, 1 - 1e-4), 1e-4)))  # noqa: E731
    return [lg(dp) / 5, fp, fn, sm, sd, min(fp, fn) * 4, math.log1p(nc) / 8, 1.0]


def rating_label(r):
    if r is None:
        return None
    r = float(r)
    return 2 if r >= POS_MIN else 0 if r <= NEG_MAX else 1


def fit_head(X, y, epochs=600, lr=0.5, l2=1e-3):
    import numpy as np
    X, y = np.asarray(X, dtype=np.float64), np.asarray(y)
    W = np.zeros((X.shape[1], 3))
    counts = np.bincount(y, minlength=3).astype(float)
    cw = (len(y) / (3 * np.maximum(counts, 1)))[y]                # class-balanced: mixed is the rare class
    Y = np.eye(3)[y]
    for _ in range(epochs):
        Z = X @ W
        Z -= Z.max(1, keepdims=True)
        P = np.exp(Z)
        P /= P.sum(1, keepdims=True)
        G = X.T @ ((P - Y) * cw[:, None]) / len(y) + l2 * W
        W -= lr * G
    return W.tolist()


def head_probs(W, x):
    z = [sum(xi * W[i][c] for i, xi in enumerate(x)) for c in range(3)]
    m = max(z)
    e = [math.exp(v - m) for v in z]
    s = sum(e)
    return [v / s for v in e]


def macro_f1(y, p, k=3):
    f = []
    for c in range(k):
        tp = sum(1 for a, b in zip(y, p) if a == c and b == c)
        fp = sum(1 for a, b in zip(y, p) if a != c and b == c)
        fn = sum(1 for a, b in zip(y, p) if a == c and b != c)
        pr = tp / (tp + fp) if tp + fp else 0.0
        rc = tp / (tp + fn) if tp + fn else 0.0
        f.append(2 * pr * rc / (pr + rc) if pr + rc else 0.0)
    return sum(f) / k, f


def ece(conf, right, bins=10):
    tot, n = 0.0, len(conf)
    for b in range(bins):
        lo, hi = b / bins, (b + 1) / bins
        idx = [i for i, c in enumerate(conf) if lo <= c < hi or (b == bins - 1 and c == 1.0)]
        if idx:
            tot += len(idx) / n * abs(sum(right[i] for i in idx) / len(idx) - sum(conf[i] for i in idx) / len(idx))
    return tot


def calibrate(conn):
    """Fit the head on rated TMDB reviews (80% of films), test on the rest; save results/sentiment_head.json."""
    rows = conn.execute("select item_key, tmdb_id, doc_p, frac_pos, frac_neg, sent_mean, sent_sd, n_sents, n_chars, "
                        "rating from review_sentiment where source_type = 'audience' and rating is not null").fetchall()
    rows = [r for r in rows if rating_label(r[9]) is not None]
    if len(rows) < 60:
        print(f"  only {len(rows)} rated reviews scored: keeping the default head", flush=True)
        return None
    films = sorted({r[1] for r in rows})
    random.Random(42).shuffle(films)
    test_f = set(films[: max(1, len(films) // 5)])
    tr = [r for r in rows if r[1] not in test_f]
    te = [r for r in rows if r[1] in test_f]
    W = fit_head([_x(r[2:9]) for r in tr], [rating_label(r[9]) for r in tr])
    res = {"version": VERSION, "model": MODEL, "W": W, "labels": LABELS, "pos_min": POS_MIN, "neg_max": NEG_MAX,
           "n_train": len(tr), "n_test": len(te), "fitted_at": F.now()}
    for name, part in (("train", tr), ("test", te)):
        y = [rating_label(r[9]) for r in part]
        P = [head_probs(W, _x(r[2:9])) for r in part]
        pred = [max(range(3), key=lambda c: p[c]) for p in P]
        m, per = macro_f1(y, pred)
        # binary model alone (no mixed class) for comparison: >= 0.5 positive
        base = [2 if r[2] >= 0.5 else 0 for r in part]
        mb, _ = macro_f1(y, base)
        res[name] = {"macro_f1": round(m, 3), "f1_by_class": [round(x, 3) for x in per],
                     "binary_model_alone_macro_f1": round(mb, 3),
                     "ece": round(ece([max(p) for p in P], [int(a == b) for a, b in zip(y, pred)]), 3),
                     "class_counts": [y.count(c) for c in range(3)]}
    os.makedirs(RESULTS, exist_ok=True)
    json.dump(res, open(HEAD_PATH, "w"), indent=1)
    print(f"  head fitted on {len(tr):,} rated reviews; test ({len(te):,}, unseen films): macro-F1 "
          f"{res['test']['macro_f1']} (binary model alone {res['test']['binary_model_alone_macro_f1']}), "
          f"F1 neg/mixed/pos {res['test']['f1_by_class']}, ECE {res['test']['ece']}", flush=True)
    return W


DEFAULT_W = None          # no head yet: thresholds on the whole-review probability


def load_head():
    try:
        return json.load(open(HEAD_PATH))["W"]
    except Exception:  # noqa: BLE001
        return DEFAULT_W


def label_of(W, f):
    if W is None:
        dp = f[0]
        both = min(f[1], f[2]) >= 0.2
        p = [max(0.0, 1 - dp * 2) * (0.5 if both else 1), 0.0, max(0.0, dp * 2 - 1) * (0.5 if both else 1)]
        p[1] = max(0.0, 1 - p[0] - p[2])
        return p
    return head_probs(W, _x(f))


def relabel(conn, W):
    rows = conn.execute("select item_key, doc_p, frac_pos, frac_neg, sent_mean, sent_sd, n_sents, n_chars "
                        "from review_sentiment").fetchall()
    up = []
    for k, *f in rows:
        p = label_of(W, f)
        c = max(range(3), key=lambda i: p[i])
        up.append((LABELS[c], p[2], p[1], p[0], max(p), k))
    conn.executemany("update review_sentiment set label = ?, p_pos = ?, p_mixed = ?, p_neg = ?, confidence = ? "
                     "where item_key = ?", up)
    conn.commit()
    return len(up)


def cmd_run(a, conn):
    todo = items(conn, a.sources, a.again, a.limit)
    print(f"sentiment: {len(todo):,} texts to score (sources {','.join(a.sources)}; version {VERSION})", flush=True)
    if todo:
        sc = Scorer(a.model, a.device)
        t0 = time.time()
        step = 256
        for s in range(0, len(todo), step):
            part = todo[s:s + step]
            fs = features(sc, part)
            ts = F.now()
            conn.executemany(
                "insert or replace into review_sentiment(item_key, tmdb_id, source_type, doc_p, frac_pos, frac_neg, "
                "sent_mean, sent_sd, n_sents, n_chars, rating, model, version, text_hash, created_at) values "
                "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [(it[0], it[1], it[2], *f, it[4], a.model, VERSION, text_hash(it[3]), ts) for it, f in zip(part, fs)])
            conn.commit()
            done = min(s + step, len(todo))
            if (s // step) % 10 == 0 or done == len(todo):
                rate = done / max(time.time() - t0, 1e-6)
                print(f"  {done:,}/{len(todo):,}  {rate:.1f}/s  eta {(len(todo) - done) / max(rate, 1e-6) / 60:.0f} min",
                      flush=True)
    W = calibrate(conn) or load_head()
    n = relabel(conn, W)
    F.set_meta(conn, "sentiment_at", F.now())
    conn.commit()
    print(f"  labels written for {n:,} rows")
    cmd_stats(a, conn)


# ------------------------------------------------------------------ R5 flags
def _day(s):
    try:
        return datetime.date.fromisoformat(str(s)[:10])
    except ValueError:
        return None


def bursts(rows, window=7, min_n=5, extreme_share=0.8, min_share=0.3, gap=3.0):
    """rows = [(review_id, day, rating)] for one film -> [(start, end, ids, mean, film_mean, direction, share)].
    A burst: >= min_n reviews inside `window` days, >= 80% of them extreme (<= 2 or >= 9.5 of 10), making up
    >= 30% of the film's rated reviews, with a mean >= 3 points from the rest of the film's reviews."""
    rated = sorted([(d, r, rid) for rid, d, r in rows if d and r is not None])
    if len(rated) < min_n:
        return []
    out, used = [], set()
    j = 0
    for i in range(len(rated)):
        while rated[i][0] - rated[j][0] > datetime.timedelta(days=window - 1):
            j += 1
        win = rated[j:i + 1]
        if len(win) < min_n:
            continue
        ext = [x for x in win if x[1] <= 2 or x[1] >= 9.5]
        if len(ext) / len(win) < extreme_share or len(win) / len(rated) < min_share:
            continue
        ids = {x[2] for x in win}
        rest = [x[1] for x in rated if x[2] not in ids]
        wm = sum(x[1] for x in win) / len(win)
        rm = sum(rest) / len(rest) if rest else None
        if rm is not None and abs(wm - rm) < gap:
            continue
        if ids & used:
            out[-1] = (out[-1][0], win[-1][0], out[-1][2] | ids, wm, rm, out[-1][5], len(out[-1][2] | ids) / len(rated))
        else:
            out.append((win[0][0], win[-1][0], ids, wm, rm, "low" if wm < 5 else "high", len(win) / len(rated)))
        used |= ids
    return out


def cmd_flags(a, conn):
    rows = conn.execute("select review_id, tmdb_id, content, rating, created_at from reviews").fetchall()
    sent = {k: (lab, conf) for k, lab, conf in conn.execute(
        "select item_key, label, confidence from review_sentiment where source_type = 'audience'")}
    print(f"flags: {len(rows):,} TMDB reviews", flush=True)
    by_film = collections.defaultdict(list)
    for rid, fid, _c, r, d in rows:
        by_film[fid].append((rid, _day(d), r))
    burst_of, brows = {}, []
    for fid, rs in by_film.items():
        for st, en, ids, wm, rm, direction, share in bursts(rs):
            brows.append((fid, st.isoformat(), en.isoformat(), len(ids), round(wm, 2),
                          None if rm is None else round(rm, 2), direction, round(share, 3)))
            for i in ids:
                burst_of[i] = direction
    conn.execute("delete from review_bursts")
    conn.executemany("insert into review_bursts values (?,?,?,?,?,?,?,?)", brows)
    irony = {}
    if not a.no_irony:
        try:
            sc = Scorer(IRONY_MODEL, a.device)
            texts = [" ".join((c or "").split())[:600] for _r, _f, c, _x, _d in rows]
            for s in range(0, len(texts), 2048):
                ps = sc.p_pos(texts[s:s + 2048], max_len=160)
                for (rid, *_), p in zip(rows[s:s + 2048], ps):
                    irony[rid] = p
                print(f"  irony {min(s + 2048, len(texts)):,}/{len(texts):,}", flush=True)
        except Exception as e:  # noqa: BLE001 - no transformers / no model: flags without irony
            print(f"  irony model unavailable ({str(e)[:120]}): skipped", flush=True)
    out = []
    ts = F.now()
    for rid, fid, content, r, _d in rows:
        lab, conf = sent.get(rid, (None, 0.0))
        mismatch = int(r is not None and lab is not None and (conf or 0) >= 0.75 and
                       ((r >= 7 and lab == "negative") or (r <= 4 and lab == "positive")))
        ip = irony.get(rid)
        short = len(sentences(content or "")) <= 3
        emoji = bool(re.search(r"[\U0001F300-\U0001FAFF]|(\blol\b|\blmao\b|\bxd\b|\bjk\b|/s\b)", content or "", re.I))
        joke = int(ip is not None and ip >= 0.8 and (short or mismatch or emoji))
        out.append((rid, fid, ip, joke, int(rid in burst_of), burst_of.get(rid), mismatch, VERSION, ts))
    conn.executemany("insert or replace into review_flags values (?,?,?,?,?,?,?,?,?)", out)
    conn.commit()
    F.set_meta(conn, "review_flags_at", F.now())
    conn.commit()
    n_b = sum(1 for o in out if o[4])
    print(f"  bursts: {len(brows):,} on {len({b[0] for b in brows}):,} films ({n_b:,} reviews; "
          f"{sum(1 for b in brows if b[6] == 'low'):,} low = review bombing, {sum(1 for b in brows if b[6] == 'high'):,} high)")
    print(f"  rating-text mismatch: {sum(o[6] for o in out):,}; joke / sarcasm: {sum(o[3] for o in out):,}"
          + ("" if irony else " (no irony model)"))


# ------------------------------------------------------------------ H7: sample + labelling page
def cmd_sample(a, conn):
    rows = conn.execute("""select r.review_id, r.tmdb_id, f.title, f.year, r.rating, r.content, s.label, s.confidence,
            g.joke, g.burst, g.mismatch from reviews r left join films f on f.tmdb_id = r.tmdb_id
            left join review_sentiment s on s.item_key = r.review_id left join review_flags g on g.review_id = r.review_id
            where length(r.content) between 80 and 2500""").fetchall()
    try:
        import review_filter
        ok = review_filter.usable_ids()
    except Exception:  # noqa: BLE001
        ok = None
    rnd = random.Random(a.seed)
    strata = collections.defaultdict(list)
    for row in rows:
        r = row[4]
        band = "none" if r is None else "neg" if r <= 4.5 else "mid" if r < 7 else "pos"
        flag = "flag" if (row[8] or row[9] or row[10]) else ("fail" if ok is not None and row[0] not in ok else "ok")
        strata[(band, flag)].append(row)
    want, pick = a.n, []
    keys = sorted(strata)
    per = max(1, want // max(len(keys), 1))
    for k in keys:
        rnd.shuffle(strata[k])
        pick += strata[k][:per]
    rest = [r for k in keys for r in strata[k][per:]]
    rnd.shuffle(rest)
    pick = (pick + rest)[:want]
    rnd.shuffle(pick)
    os.makedirs(RESULTS, exist_ok=True)
    path = os.path.join(RESULTS, "review_labels.csv")
    cols = ["n", "who", "review_id", "film", "rating", "text", "sentiment (positive/mixed/negative)",
            "joke or sarcasm (y/n)", "troll, spam or bombing (y/n)", "usable as evidence (y/n)", "note"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for i, r in enumerate(pick, 1):
            w.writerow([i, "you" if i <= want // 2 else "claude", r[0], f"{r[2]} ({r[3]})", r[4] if r[4] is not None else "",
                        " ".join((r[5] or "").split()), "", "", "", "", ""])
    page = os.path.join(RESULTS, "label_reviews.html")
    data = [{"n": i, "who": "you" if i <= want // 2 else "claude", "id": r[0], "film": f"{r[2]} ({r[3]})",
             "rating": r[4], "text": " ".join((r[5] or "").split())} for i, r in enumerate(pick, 1)]
    open(page, "w", encoding="utf-8").write(LABEL_PAGE.replace("__DATA__", json.dumps(data)))
    print(f"{len(pick)} reviews -> {path}\n  labelling page: {page} (open it in a browser; it saves as you go and "
          f"exports the CSV)\n  rows 1-{want // 2}: you label; rows {want // 2 + 1}-{want}: Claude drafts, you approve")


LABEL_PAGE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Review Labels</title><style>
:root{--bg:#fafaf7;--fg:#1d1d1b;--mute:#6b6b66;--card:#fff;--line:#e3e1da;--acc:#2f5d8a;--on:#2f5d8a;--onfg:#fff}
@media (prefers-color-scheme:dark){:root{--bg:#161615;--fg:#ecebe6;--mute:#a09f99;--card:#20201e;--line:#34332f;--acc:#8ab4dc;--on:#8ab4dc;--onfg:#111}}
body{background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,sans-serif;margin:0;padding:16px;max-width:860px;margin:auto}
h1{font-size:20px;margin:4px 0}.bar{position:sticky;top:0;background:var(--bg);padding:8px 0;border-bottom:1px solid var(--line);display:flex;gap:10px;flex-wrap:wrap;align-items:center;z-index:2}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px;margin:12px 0}
.meta{color:var(--mute);font-size:13px}.text{margin:8px 0 10px;white-space:pre-wrap}
.row{display:flex;gap:6px;flex-wrap:wrap;align-items:center;margin:4px 0}.row b{width:170px;font-weight:600;font-size:13px}
button{font:inherit;font-size:13px;border:1px solid var(--line);background:transparent;color:var(--fg);border-radius:6px;padding:3px 10px;cursor:pointer}
button.on{background:var(--on);color:var(--onfg);border-color:var(--on)}input{font:inherit;width:100%;box-sizing:border-box;border:1px solid var(--line);background:transparent;color:var(--fg);border-radius:6px;padding:4px}
.done{opacity:.6}</style></head><body>
<h1>Label reviews</h1><p class="meta">Rows marked "you" are yours. Sentiment is what the review says about the film; "usable" means you would trust it as evidence of what an ordinary viewer thought.</p>
<div class="bar"><span id="count"></span><label><input type="checkbox" id="mine" style="width:auto" checked> only my rows</label><button id="exp">Export CSV</button></div>
<div id="list"></div>
<script>
const DATA=__DATA__;const KEY="mb-labels-v1";let L={};try{L=JSON.parse(localStorage.getItem(KEY)||"{}")}catch(e){}
const F=[["s","Sentiment",["positive","mixed","negative"]],["j","Joke or sarcasm",["y","n"]],["t","Troll, spam or bombing",["y","n"]],["u","Usable as evidence",["y","n"]]];
function save(){try{localStorage.setItem(KEY,JSON.stringify(L))}catch(e){}count()}
function count(){const mine=DATA.filter(d=>d.who==="you");const n=mine.filter(d=>L[d.id]&&L[d.id].s).length;document.getElementById("count").textContent=`${n} of ${mine.length} of your rows labelled`}
function esc(s){return String(s??"").replace(/[&<>]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;"}[c]))}
function render(){const only=document.getElementById("mine").checked;const box=document.getElementById("list");box.innerHTML="";
for(const d of DATA){if(only&&d.who!=="you")continue;const v=L[d.id]||{};const c=document.createElement("div");c.className="card"+(v.s?" done":"");
c.innerHTML=`<div class="meta">#${d.n} · ${esc(d.film)} · rating ${d.rating??"none"} · ${d.who}</div><div class="text">${esc(d.text)}</div>`+
F.map(([k,lab,opts])=>`<div class="row"><b>${lab}</b>${opts.map(o=>`<button data-k="${k}" data-v="${o}" class="${v[k]===o?"on":""}">${o}</button>`).join("")}</div>`).join("")+
`<div class="row"><b>Note</b><input data-k="note" value="${esc(v.note||"")}"></div>`;
c.addEventListener("click",e=>{const b=e.target.closest("button");if(!b)return;L[d.id]=L[d.id]||{};L[d.id][b.dataset.k]=L[d.id][b.dataset.k]===b.dataset.v?"":b.dataset.v;save();render()});
c.querySelector("input").addEventListener("change",e=>{L[d.id]=L[d.id]||{};L[d.id].note=e.target.value;save()});box.appendChild(c)}count()}
document.getElementById("mine").onchange=render;
document.getElementById("exp").onclick=()=>{const q=s=>'"'+String(s??"").replace(/"/g,'""')+'"';
const head=["n","who","review_id","film","rating","text","sentiment (positive/mixed/negative)","joke or sarcasm (y/n)","troll, spam or bombing (y/n)","usable as evidence (y/n)","note"];
const rows=DATA.map(d=>{const v=L[d.id]||{};return [d.n,d.who,d.id,d.film,d.rating??"",d.text,v.s||"",v.j||"",v.t||"",v.u||"",v.note||""].map(q).join(",")});
const blob=new Blob(["﻿"+head.map(q).join(",")+"\n"+rows.join("\n")],{type:"text/csv"});const a=document.createElement("a");a.href=URL.createObjectURL(blob);a.download="review_labels_done.csv";a.click()};
render();</script></body></html>"""


# ------------------------------------------------------------------ compare against the labels (rules)
def _llama_label(text, model="llama3.2", url="http://localhost:11434"):
    import urllib.request
    schema = {"type": "object", "properties": {"sentiment": {"type": "string", "enum": LABELS}}, "required": ["sentiment"]}
    body = json.dumps({"model": model, "stream": False, "format": schema, "options": {"temperature": 0, "num_predict": 30},
                       "messages": [{"role": "system", "content": "Classify the overall sentiment of this movie review "
                                     "toward the film: positive, mixed or negative. Reply with JSON."},
                                    {"role": "user", "content": text[:3000]}]}).encode()
    try:
        req = urllib.request.Request(url + "/api/chat", body, {"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.loads(json.load(r)["message"]["content"]).get("sentiment")
    except Exception:  # noqa: BLE001
        return None


def cmd_compare(a, conn):
    rows = list(csv.DictReader(open(a.path, encoding="utf-8-sig")))
    sk = "sentiment (positive/mixed/negative)"
    lab = [r for r in rows if (r.get(sk) or "").strip().lower() in LABELS]
    print(f"labelled: {len(lab)} of {len(rows)} rows have a sentiment label")
    if not lab:
        return
    ours = {k: l for k, l in conn.execute("select item_key, label from review_sentiment")}
    y = [LABELS.index(r[sk].strip().lower()) for r in lab]
    p_ours = [LABELS.index(ours.get(r["review_id"]) or "mixed") for r in lab]
    m_ours, f_ours = macro_f1(y, p_ours)
    print(f"  RoBERTa + head: macro-F1 {m_ours:.3f}  (F1 neg/mixed/pos {[round(x, 3) for x in f_ours]}; "
          f"mixed recall {sum(1 for a_, b in zip(y, p_ours) if a_ == b == 1) / max(1, y.count(1)):.2f})")
    texts = {k: t for k, t in conn.execute("select review_id, content from reviews")}
    p_llm = []
    for r in lab:
        g = _llama_label(texts.get(r["review_id"]) or r.get("text") or "", a.llm)
        p_llm.append(LABELS.index(g) if g in LABELS else 1)
    m_llm, f_llm = macro_f1(y, p_llm)
    print(f"  {a.llm}: macro-F1 {m_llm:.3f}  (F1 neg/mixed/pos {[round(x, 3) for x in f_llm]})")
    passed = m_ours >= 0.80 and m_ours > m_llm
    print(f"  RULE (macro-F1 >= 0.80 and beats {a.llm}): {'PASS -> use the RoBERTa labels' if passed else 'FAIL'}")
    stars = [(r, float(r["rating"])) for r in lab if (r.get("rating") or "").strip()]
    if stars:
        agree = sum(1 for (r, s), p in zip(stars, [ours.get(r['review_id']) for r, _s in stars])
                    if p == LABELS[rating_label(s)])
        print(f"  agreement of RoBERTa labels with the review's own star rating: {agree}/{len(stars)}")
    flags = {k: (j, b, m) for k, j, b, m in conn.execute("select review_id, joke, burst, mismatch from review_flags")}
    for col, idx, name in (("joke or sarcasm (y/n)", 0, "joke / sarcasm"), ("troll, spam or bombing (y/n)", 1,
                                                                              "bombing burst")):
        marked = [r for r in lab if (r.get(col) or "").strip().lower()[:1] in ("y", "n")]
        flagged = [r for r in marked if (flags.get(r["review_id"]) or (0, 0, 0))[idx]]
        tp = sum(1 for r in flagged if r[col].strip().lower().startswith("y"))
        if flagged:
            print(f"  {name} flag precision: {tp}/{len(flagged)} = {tp / len(flagged):.2f} (rule >= 0.8: "
                  f"{'PASS' if tp / len(flagged) >= 0.8 else 'FAIL'})")
        else:
            print(f"  {name} flag: none of the labelled rows were flagged (precision not measurable)")
    try:
        import review_filter
        ok = review_filter.usable_ids()
        col = "usable as evidence (y/n)"
        marked = [r for r in lab if (r.get(col) or "").strip().lower()[:1] in ("y", "n")]
        if ok is not None and marked:
            kept = [r for r in marked if r["review_id"] in ok]
            tp = sum(1 for r in kept if r[col].strip().lower().startswith("y"))
            print(f"  review_filter (H7): precision of 'usable' {tp}/{len(kept)} = {tp / max(1, len(kept)):.2f}; "
                  f"recall {tp}/{sum(1 for r in marked if r[col].strip().lower().startswith('y'))}")
    except Exception:  # noqa: BLE001
        pass


def cmd_stats(a, conn):
    for st, n, pos, mix, neg, conf in conn.execute(
            "select source_type, count(*), sum(label = 'positive'), sum(label = 'mixed'), sum(label = 'negative'), "
            "avg(confidence) from review_sentiment group by source_type"):
        print(f"  {st:12s} {n:7,}  positive {pos or 0:,}  mixed {mix or 0:,}  negative {neg or 0:,}  "
              f"mean confidence {conf or 0:.2f}")
    try:
        h = json.load(open(HEAD_PATH))
        print(f"  head: test macro-F1 {h['test']['macro_f1']} (binary model alone {h['test']['binary_model_alone_macro_f1']}), "
              f"ECE {h['test']['ece']}, fitted {h['fitted_at'][:16]}")
    except Exception:  # noqa: BLE001
        pass


def cmd_show(a, conn):
    row = conn.execute("select tmdb_id, title from films where title = ? collate nocase order by vote_count desc limit 1",
                       (a.title,)).fetchone()
    if not row:
        sys.exit("no such film")
    print(row[1])
    for st, n, pos, mix, neg in conn.execute("select source_type, count(*), sum(label='positive'), sum(label='mixed'), "
                                             "sum(label='negative') from review_sentiment where tmdb_id = ? and "
                                             "source_type != 'tv_audience' group by source_type", (row[0],)):
        print(f"  {st}: {n} scored - {pos} positive, {mix} mixed, {neg} negative")
    for b in conn.execute("select * from review_bursts where tmdb_id = ?", (row[0],)):
        print(f"  burst: {b}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["run", "flags", "sample", "compare", "stats", "show"])
    ap.add_argument("arg", nargs="?")
    ap.add_argument("--facts")
    ap.add_argument("--sources", default=",".join(SOURCES))
    ap.add_argument("--model", default=MODEL)
    ap.add_argument("--device", default="auto")
    ap.add_argument("--again", action="store_true")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--no-irony", action="store_true")
    ap.add_argument("--llm", default="llama3.2")
    ap.add_argument("--seed", type=int, default=11)
    a = ap.parse_args()
    a.sources = [s for s in a.sources.split(",") if s]
    if "nyt" in a.sources:
        sys.exit("NYT text is never analysed (E8: NYT terms bar using its content to develop or ground AI systems)")
    conn = connect(a.facts)
    if a.cmd == "sample":
        a.n = int(a.arg or 200)
    elif a.cmd == "compare":
        a.path = a.arg or os.path.join(RESULTS, "review_labels_done.csv")
    elif a.cmd == "show":
        a.title = a.arg
    {"run": cmd_run, "flags": cmd_flags, "sample": cmd_sample, "compare": cmd_compare, "stats": cmd_stats,
     "show": cmd_show}[a.cmd](a, conn)


if __name__ == "__main__":
    main()
