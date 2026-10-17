#!/usr/bin/env python3
"""
v4 (14 Oct): contextual chunk enrichment, the method from Jackson Higgins' Local Hybrid RAG.

Each Wikipedia passage is indexed today as "Title | Plot, part 3 of 5: <180 words>". A passage
from the middle of a plot often never names the film's setting, its main characters' roles or
what the film is about, so a description like "the one where the police chief is afraid of
the water" has little to match. Jackson's fix: before indexing, a small model writes for each
chunk
    context   one sentence placing the chunk in the whole film ("Part 3 of 5 of Jaws' plot:
              after a second attack, Brody, Hooper and Quint go to sea to hunt the shark.")
    topics    2-5 short themes ("shark hunt", "small beach town")
    keywords  3-8 search words a viewer might use, including ones the text does not contain
              ("great white", "police chief", "fishing boat")
and the chunk is embedded and keyword-indexed WITH those lines. The text shown to the
re-ranker and the answer model is unchanged (the original passage).

    python chunk_context.py build --sample 3000      # contexts for the films in the question sets
                                                     # + 3000 random others (~4 chunks per film)
    python chunk_context.py eval                     # SAMPLE TEST (no change to wiki.db): per retriever
                                                     # (vector, keyword, fused) hit@1/5/20, MRR,
                                                     # plain vs contextual, on the sample's films
    python chunk_context.py build --all              # every linked film (resumable; ~25k model calls)
    python chunk_context.py add                      # contextual copies as kinds plot_c / lead_c in wiki.db
    python chunk_context.py embed                    # their vectors into wiki.db vec_chunks (resumable)
    python chunk_context.py fts                      # wiki_ctx_fts: keyword index over text + context
    python chunk_context.py show "Jaws"
    python chunk_context.py status

Then in retrieve.py (no code change needed for the vectors): WIKI_KINDS=plot_c,lead_c searches the
contextual vectors and shows the same passages; WIKI_CTX_KW=1 uses wiki_ctx_fts instead of
wiki_fts for the Wikipedia keyword list. `python mb.py build-cache` afterwards (vec_cache).

Cost control: one model call per film (up to 6 chunks per call, structured output), not one per
chunk: ~25k calls for 104k passages. --workers N sends N calls at once (useful only when Ollama's
OLLAMA_NUM_PARALLEL > 1). Everything is resumable and versioned (VERSION).

Decision rule for the full run (written 14 Oct, before any result): run `build --all` + `add` +
`embed` only if, in the sample test, contextual fused hit@5 >= plain + 0.02 AND contextual MRR is
not lower, on the question-set films. Then adopt WIKI_KINDS=plot_c,lead_c only if stage_scores
tune and check each lose at most 1 question and gain overall (run_v4.sh ctx).
"""
import argparse
import concurrent.futures as cf
import datetime
import json
import os
import random
import re
import sqlite3
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
if os.environ.get("MOCK", "off").lower() not in ("", "off", "0"):
    import mock_llm
    mock_llm.install()
import ollama  # noqa: E402

import wiki_common as wc  # noqa: E402

VERSION = "cc1"
MODEL = os.environ.get("CTX_MODEL", "llama3.2")
EMBED_MODEL = os.environ.get("EMBED_MODEL", "mxbai-embed-large")
QUERY_PREFIX = "Represent this sentence for searching relevant passages: "
WIKI_DB = os.environ.get("WIKI_DB", os.path.join(HERE, "wiki.db"))
SAMPLE_DB = os.path.join(HERE, "results", "ctx_sample.db")
KINDS = ("plot", "lead")
PER_CALL = 6
CHUNK_CHARS = 1400            # characters of each chunk shown to the model (~ all of a 180-word chunk)

SYSTEM = """You prepare passages from a film's Wikipedia article for a search engine that people use \
to find a film they half remember. For EACH numbered passage write:
- context: ONE sentence (at most 35 words) that places the passage within the whole film: which part \
of the story it is, who the main people in it are (by role, e.g. "the police chief"), and where it happens. \
Use only what the film summary and the passages say.
- topics: 2-5 short themes of the passage (2-4 words each).
- keywords: 3-8 search words or short phrases a viewer might use to describe this part of the film, \
including plain everyday words (e.g. "shark", "beach town", "boat") even if the passage uses other words.
Do not invent events, names or places. Reply with JSON only."""
SCHEMA = {"type": "object", "properties": {"chunks": {"type": "array", "items": {
    "type": "object", "properties": {
        "part": {"type": "integer"},
        "context": {"type": "string"},
        "topics": {"type": "array", "items": {"type": "string"}},
        "keywords": {"type": "array", "items": {"type": "string"}}},
    "required": ["part", "context", "topics", "keywords"]}}}, "required": ["chunks"]}

DDL = """
create table if not exists chunk_ctx(
  chunk_id integer primary key, film_id integer, kind text, context text, topics_json text,
  keywords_json text, model text, version text, created_at text, ctx_chunk_id integer,
  embedded integer default 0);
create index if not exists chunk_ctx_film on chunk_ctx(film_id);
create table if not exists chunk_ctx_done(film_id integer primary key, version text, n integer, ok integer);
"""


def now():
    return datetime.datetime.now().isoformat(timespec="seconds")


def connect(path=WIKI_DB):
    db = sqlite3.connect(path, timeout=600)
    try:
        import sqlite_vec
        db.enable_load_extension(True)
        sqlite_vec.load(db)
        db.enable_load_extension(False)
    except Exception:  # noqa: BLE001
        pass
    db.executescript(DDL)
    return db


# ------------------------------------------------------------------ which films
def _questions(paths):
    """[(question text, title, year)] from stage_scores-format sets (single-turn only)."""
    out = []
    for p in paths:
        if not os.path.exists(p):
            print(f"  (no {p})")
            continue
        d = json.load(open(p))
        for q in (d if isinstance(d, list) else d.get("questions", [])):
            turns = q.get("turns") or []
            exp = q.get("expect_retrieved") or []
            if len(turns) != 1 or len(exp) != 1:
                continue
            t = turns[0] if isinstance(turns[0], str) else (turns[0].get("content") or turns[0].get("text") or "")
            out.append((t, exp[0], q.get("expect_year")))
    return out


def _film_of(db, title, year):
    nt = wc.norm_title(title)
    rows = db.execute("select distinct film_id, title, year from chunks where kind in ('plot','lead') "
                      "and title = ? collate nocase", (title,)).fetchall()
    if not rows:
        rows = [r for r in db.execute("select distinct film_id, title, year from chunks where kind = 'lead' "
                                      "and position = 1") if wc.norm_title(r[1]) == nt]
    if year:
        rows = [r for r in rows if str(r[2]) == str(year)[:4]] or rows
    return rows[0][0] if rows else None


QSETS = ["sets/tune.json", "sets/check.json"]


def sample_films(db, n_random, seed=7):
    qs = _questions([os.path.join(HERE, p) for p in QSETS])
    targets = {f for f in (_film_of(db, t, y) for _q, t, y in qs) if f is not None}
    allf = [r[0] for r in db.execute("select distinct film_id from chunks where kind = 'plot'")]
    rnd = random.Random(seed)
    others = [f for f in allf if f not in targets]
    rnd.shuffle(others)
    return sorted(targets), others[:n_random], qs


# ------------------------------------------------------------------ model calls
def film_chunks(db, fid):
    return db.execute("select chunk_id, kind, section, position, n_parts, text, title, year, genres_json "
                      "from chunks where film_id = ? and kind in (%s) order by kind desc, position"
                      % ",".join("?" * len(KINDS)), (fid, *KINDS)).fetchall()


def _prompt(rows, intro):
    t, y, g = rows[0][6], rows[0][7], ", ".join(json.loads(rows[0][8] or "[]"))
    head = f"Film: {t} ({y or '?'}){' - ' + g if g else ''}\nFilm summary (Wikipedia intro): {intro[:900]}\n"
    parts = []
    for i, r in enumerate(rows, 1):
        label = "Intro" if r[1] == "lead" else f"{(r[2] or 'plot').title()}, part {r[3]} of {r[4]}"
        parts.append(f"[{i}] ({label}) {r[5][:CHUNK_CHARS]}")
    return head + "\nPassages:\n" + "\n\n".join(parts)


def _clean_list(xs, n, words=4):
    out, seen = [], set()
    for x in xs or []:
        s = " ".join(str(x).split())[:60].strip(" .,;")
        if s and len(s.split()) <= words and s.casefold() not in seen:
            seen.add(s.casefold())
            out.append(s)
    return out[:n]


def ask(rows, intro, retries=2):
    """[(chunk_id, kind, context, topics, keywords)] for one call's rows; missing parts dropped."""
    prompt = _prompt(rows, intro)
    n_pred = 160 * len(rows) + 200
    for attempt in range(retries + 1):
        try:
            r = ollama.chat(model=MODEL, format=SCHEMA, keep_alive="30m",
                            options={"temperature": 0, "num_predict": n_pred, "num_ctx": 8192},
                            messages=[{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}])
            data = json.loads(r["message"]["content"])
            break
        except Exception as e:  # noqa: BLE001  (truncated JSON -> more room, then give up)
            n_pred = int(n_pred * 1.6)
            if attempt == retries:
                print(f"  model failed for {rows[0][6]!r}: {str(e)[:80]}", file=sys.stderr)
                return []
    out = []
    for c in data.get("chunks") or []:
        try:
            i = int(c.get("part"))
        except (TypeError, ValueError):
            continue
        if not 1 <= i <= len(rows):
            continue
        ctx = " ".join(str(c.get("context") or "").split())
        if len(ctx.split()) < 4:
            continue
        ctx = " ".join(ctx.split()[:45])
        out.append((rows[i - 1][0], rows[i - 1][1], ctx, _clean_list(c.get("topics"), 5),
                    _clean_list(c.get("keywords"), 8)))
    return out


def work_film(db_path, fid):
    db = sqlite3.connect(db_path, timeout=600)
    rows = film_chunks(db, fid)
    if not rows:
        return fid, []
    intro = next((r[5] for r in rows if r[1] == "lead"), "") or rows[0][5]
    got = []
    for s in range(0, len(rows), PER_CALL):
        got += ask(rows[s:s + PER_CALL], intro)
    db.close()
    return fid, got


def cmd_build(a):
    db = connect()
    if a.all:
        films = [r[0] for r in db.execute("select film_id from chunks where kind = 'plot' group by film_id "
                                          "order by max(popularity) desc")]
    else:
        targets, others, _ = sample_films(db, a.sample)
        films = targets + others
        print(f"sample: {len(targets)} question-set films + {len(others)} random films")
    # films whose model call failed (ok = 0) are tried again on the next run
    done = {r[0] for r in db.execute("select film_id from chunk_ctx_done where version = ? and ok = 1", (VERSION,))}
    todo = [f for f in films if f not in done]
    if a.limit:
        todo = todo[:a.limit]
    print(f"films: {len(films):,} | done: {len(films) - len(todo):,} | to do: {len(todo):,} | model {MODEL}, "
          f"{a.workers} at once")
    t0, n_chunks = time.time(), 0
    with cf.ThreadPoolExecutor(max_workers=max(1, a.workers)) as ex:
        futs = [ex.submit(work_film, WIKI_DB, f) for f in todo]
        for i, fu in enumerate(cf.as_completed(futs), 1):
            fid, got = fu.result()
            # upsert keeps ctx_chunk_id (a chunk already added to the index) and marks it for re-embedding
            db.executemany("insert into chunk_ctx(chunk_id, film_id, kind, context, topics_json, keywords_json, "
                           "model, version, created_at, embedded) values (?,?,?,?,?,?,?,?,?,0) "
                           "on conflict(chunk_id) do update set context = excluded.context, "
                           "topics_json = excluded.topics_json, keywords_json = excluded.keywords_json, "
                           "model = excluded.model, version = excluded.version, created_at = excluded.created_at, "
                           "embedded = 0",
                           [(cid, fid, k, ctx, json.dumps(tp), json.dumps(kw), MODEL, VERSION, now())
                            for cid, k, ctx, tp, kw in got])
            db.execute("insert or replace into chunk_ctx_done values (?,?,?,?)", (fid, VERSION, len(got), int(bool(got))))
            n_chunks += len(got)
            if i % 25 == 0 or i == len(todo):
                db.commit()
                rate = i / max(time.time() - t0, 1e-6)
                print(f"  {i:,}/{len(todo):,} films  {n_chunks:,} chunks  {rate:.2f} films/s  "
                      f"eta {(len(todo) - i) / max(rate, 1e-6) / 3600:.1f} h", flush=True)
    db.commit()


def ctx_text(row_ctx):
    """The lines added before the passage: context, topics, keywords."""
    ctx, tp, kw = row_ctx
    if not ctx:
        return ""
    s = ctx.rstrip(".") + "."
    tp, kw = json.loads(tp or "[]"), json.loads(kw or "[]")
    if tp:
        s += " Topics: " + ", ".join(tp) + "."
    if kw:
        s += " Keywords: " + ", ".join(kw) + "."
    return s


def embed_texts(texts, prefix=""):
    for attempt in range(5):
        try:
            return ollama.embed(model=EMBED_MODEL, input=[prefix + t for t in texts], truncate=True)["embeddings"]
        except TypeError:
            return ollama.embed(model=EMBED_MODEL, input=[prefix + t for t in texts])["embeddings"]
        except Exception as e:  # noqa: BLE001
            print(f"  embed retry {attempt + 1}: {str(e)[:80]}", file=sys.stderr)
            time.sleep(5 * (attempt + 1))
    raise SystemExit("embedding kept failing")


# ------------------------------------------------------------------ sample test (no wiki.db change)
def _bm25_rank(conn, table, text, n):
    toks = [w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) > 2][:40]
    if not toks:
        return []
    q = " OR ".join(f'"{w}"' for w in toks)
    try:
        return [r[0] for r in conn.execute(f"select film_id from {table} where {table} match ? "
                                           f"order by bm25({table}) limit ?", (q, n * 6))]
    except sqlite3.OperationalError:
        return []


def _dedupe(seq, n):
    out, seen = [], set()
    for f in seq:
        if f not in seen:
            seen.add(f)
            out.append(f)
            if len(out) >= n:
                break
    return out


def _rrf(lists, k=60):
    sc = {}
    for lst in lists:
        for i, f in enumerate(lst, 1):
            sc[f] = sc.get(f, 0.0) + 1.0 / (k + i)
    return [f for f, _ in sorted(sc.items(), key=lambda x: -x[1])]


def cmd_eval(a):
    import numpy as np
    db = connect()
    targets, others, qs = sample_films(db, a.sample)
    films = set(targets + others)
    have = {r[0] for r in db.execute("select film_id from chunk_ctx_done where version = ? and ok = 1", (VERSION,))}
    films &= have
    if not films:
        raise SystemExit("no films with contexts yet: run `build --sample N` first")
    rows = [r for r in db.execute("select c.chunk_id, c.film_id, c.kind, c.title, c.section, c.position, c.n_parts, "
                                  "c.text, x.context, x.topics_json, x.keywords_json from chunks c join chunk_ctx x "
                                  "on x.chunk_id = c.chunk_id where x.version = ? and c.kind in ('plot', 'lead')",
                                  (VERSION,)) if r[1] in films]
    print(f"sample: {len(films):,} films with contexts, {len(rows):,} passages")
    os.makedirs(os.path.dirname(SAMPLE_DB), exist_ok=True)
    sdb = sqlite3.connect(SAMPLE_DB)
    sdb.executescript("create table if not exists vec(chunk_id integer, variant text, emb blob, "
                      "primary key (chunk_id, variant));")
    have_vec = {(r[0], r[1]) for r in sdb.execute("select chunk_id, variant from vec")}
    plain_texts = {r[0]: wc.embed_header(r[3], r[4], r[5], r[6]) + r[7] for r in rows}
    ctx_texts = {r[0]: wc.embed_header(r[3], r[4], r[5], r[6]) + ctx_text(r[8:11]) + " " + r[7] for r in rows}
    for variant, texts in (("plain", plain_texts), ("ctx", ctx_texts)):
        todo = [cid for cid in texts if (cid, variant) not in have_vec]
        for s in range(0, len(todo), 32):
            part = todo[s:s + 32]
            vecs = embed_texts([texts[c] for c in part])
            sdb.executemany("insert or replace into vec values (?,?,?)",
                            [(c, variant, np.asarray(v, dtype=np.float32).tobytes()) for c, v in zip(part, vecs)])
            sdb.commit()
        print(f"  {variant}: {len(texts):,} vectors ({len(todo):,} new)")
    film_of = {r[0]: r[1] for r in rows}
    mats = {}
    for variant in ("plain", "ctx"):
        got = sdb.execute("select chunk_id, emb from vec where variant = ?", (variant,)).fetchall()
        got = [g for g in got if g[0] in film_of]
        ids = np.array([film_of[g[0]] for g in got])
        m = np.stack([np.frombuffer(g[1], dtype=np.float32) for g in got])
        m /= np.linalg.norm(m, axis=1, keepdims=True) + 1e-9
        mats[variant] = (ids, m)
    mem = sqlite3.connect(":memory:")
    mem.execute("create virtual table kw_plain using fts5(text, film_id unindexed)")
    mem.execute("create virtual table kw_ctx using fts5(text, film_id unindexed)")
    for r in rows:
        mem.execute("insert into kw_plain values (?, ?)", (r[7], r[1]))
        mem.execute("insert into kw_ctx values (?, ?)", (ctx_text(r[8:11]) + " " + r[7], r[1]))
    graded = [(q, _film_of(db, t, y)) for q, t, y in qs]
    graded = [(q, f) for q, f in graded if f in films]
    print(f"questions whose film is in the sample: {len(graded)}")
    if not graded:
        raise SystemExit("none of the question-set films have contexts")
    qv = embed_texts([q for q, _ in graded], prefix=QUERY_PREFIX)
    res = {}
    for (q, fid), v in zip(graded, qv):
        v = np.asarray(v, dtype=np.float32)
        v /= np.linalg.norm(v) + 1e-9
        lists = {}
        for variant, (ids, m) in mats.items():
            sim = m @ v
            order = np.argsort(-sim)
            lists[f"vector_{variant}"] = _dedupe((int(ids[i]) for i in order), 100)
        lists["keyword_plain"] = _dedupe(_bm25_rank(mem, "kw_plain", q, 100), 100)
        lists["keyword_ctx"] = _dedupe(_bm25_rank(mem, "kw_ctx", q, 100), 100)
        lists["fused_plain"] = _rrf([lists["vector_plain"], lists["keyword_plain"]])[:100]
        lists["fused_ctx"] = _rrf([lists["vector_ctx"], lists["keyword_ctx"]])[:100]
        for name, lst in lists.items():
            rk = lst.index(fid) + 1 if fid in lst else None
            res.setdefault(name, []).append(rk)
    lines = [f"Contextual chunk enrichment, sample test ({datetime.date.today()}, version {VERSION}, model {MODEL})",
             f"films in sample: {len(films):,} ({len(rows):,} passages); questions: {len(graded)} "
             f"(tune + check sets, single-turn, film in sample)",
             "Random distractors are easier than the full 24,854-film index; compare plain vs contextual, not "
             "with stage_scores numbers.", "",
             f"{'retriever':16s} {'hit@1':>6s} {'hit@5':>6s} {'hit@20':>7s} {'MRR':>6s} {'found@100':>9s}"]
    summ = {}
    for name in ("vector_plain", "vector_ctx", "keyword_plain", "keyword_ctx", "fused_plain", "fused_ctx"):
        rks = res[name]
        n = len(rks)
        h = lambda k: sum(1 for r in rks if r and r <= k) / n  # noqa: E731
        mrr = sum(1 / r for r in rks if r) / n
        summ[name] = {"hit1": h(1), "hit5": h(5), "hit20": h(20), "mrr": mrr}
        lines.append(f"{name:16s} {h(1):6.3f} {h(5):6.3f} {h(20):7.3f} {mrr:6.3f} {sum(1 for r in rks if r) / n:9.3f}")
    d5 = summ["fused_ctx"]["hit5"] - summ["fused_plain"]["hit5"]
    dm = summ["fused_ctx"]["mrr"] - summ["fused_plain"]["mrr"]
    ok = d5 >= 0.02 and dm >= 0
    lines += ["", f"rule (fused hit@5 +0.02 and MRR not lower): hit@5 {d5:+.3f}, MRR {dm:+.3f} -> "
              f"{'PASS: run build --all, add, embed, then run_v4.sh ctx' if ok else 'FAIL: keep plain passages'}"]
    out = os.path.join(HERE, "results", "chunk_context_sample.txt")
    open(out, "w").write("\n".join(lines) + "\n")
    json.dump({"summary": summ, "ranks": res, "pass": ok}, open(out.replace(".txt", ".json"), "w"))
    print("\n".join(lines))
    print(f"-> {out}")


# ------------------------------------------------------------------ full index
def cmd_add(_a):
    """A copy of EVERY lead / plot chunk as kind <kind>_c (same text, same metadata), so a search over
    plot_c,lead_c covers every film. Chunks without a context (model failed, or not built yet) get an
    empty one and are embedded like the plain passage; a later `build` fills them in and `embed`
    re-embeds just those."""
    db = connect()
    cols = [r[1] for r in db.execute("pragma table_info(chunks)") if r[1] != "chunk_id"]
    db.execute("insert or ignore into chunk_ctx(chunk_id, film_id, kind, context, topics_json, keywords_json, "
               "model, version, created_at, embedded) select chunk_id, film_id, kind, '', '[]', '[]', 'none', ?, ?, 0 "
               "from chunks where kind in (%s)" % ",".join("?" * len(KINDS)), (VERSION, now(), *KINDS))
    db.commit()
    todo = db.execute("select x.chunk_id from chunk_ctx x where x.ctx_chunk_id is null").fetchall()
    n = 0
    for (cid,) in todo:
        row = db.execute(f"select {','.join(cols)} from chunks where chunk_id = ?", (cid,)).fetchone()
        if not row:
            continue
        d = dict(zip(cols, row))
        d["kind"] = d["kind"] + "_c"
        cur = db.execute(f"insert into chunks({','.join(cols)}) values ({','.join('?' * len(cols))})",
                         [d[c] for c in cols])
        db.execute("update chunk_ctx set ctx_chunk_id = ? where chunk_id = ?", (cur.lastrowid, cid))
        n += 1
        if n % 5000 == 0:
            db.commit()
            print(f"  {n:,}")
    db.commit()
    print(f"added {n:,} contextual chunks (kinds {', '.join(k + '_c' for k in KINDS)})")


def cmd_embed(a):
    import sqlite_vec
    db = connect()
    if not db.execute("select 1 from sqlite_master where name = 'vec_chunks'").fetchone():
        raise SystemExit("wiki.db has no vec_chunks (run wiki_embed.py first)")
    model = db.execute("select value from manifest where key = 'embedder'").fetchone()[0]
    if model != EMBED_MODEL:
        raise SystemExit(f"vec_chunks was built with {model}, not {EMBED_MODEL}")
    done = {r[0] for r in db.execute("select chunk_id from vec_chunks where kind in ('plot_c', 'lead_c')")}
    rows = db.execute("select c.chunk_id, c.title, c.kind, c.section, c.position, c.n_parts, c.text, c.year, "
                      "c.language, c.is_animated, c.tier, c.film_id, x.context, x.topics_json, x.keywords_json, "
                      "x.embedded from chunk_ctx x join chunks c on c.chunk_id = x.ctx_chunk_id").fetchall()
    todo = [r for r in rows if r[0] not in done or not r[15]]
    stale = [r[0] for r in todo if r[0] in done]
    for s in range(0, len(stale), 500):          # context changed since it was embedded
        part = stale[s:s + 500]
        db.execute("delete from vec_chunks where chunk_id in (%s)" % ",".join("?" * len(part)), part)
    db.commit()
    print(f"contextual chunks: {len(rows):,} | embedded: {len(rows) - len(todo):,} | to do: {len(todo):,}")
    ins = ("insert into vec_chunks(chunk_id, embedding, kind, year, language, is_animated, tier, film_id) "
           "values (?,?,?,?,?,?,?,?)")
    t0 = time.time()
    for s in range(0, len(todo), a.batch):
        part = todo[s:s + a.batch]
        texts = [wc.embed_header(r[1], r[3], r[4], r[5]) + (ctx_text(r[12:15]) + " " if r[12] else "") + r[6]
                 for r in part]
        vecs = embed_texts(texts)
        db.executemany(ins, [(r[0], sqlite_vec.serialize_float32(v), r[2], int(r[7] or 0), r[8] or "",
                              int(r[9] or 0), int(r[10] or 0), int(r[11])) for r, v in zip(part, vecs)])
        db.executemany("update chunk_ctx set embedded = 1 where ctx_chunk_id = ?", [(r[0],) for r in part])
        db.commit()
        if (s // a.batch) % 50 == 0:
            n = s + len(part)
            rate = n / max(time.time() - t0, 1e-6)
            print(f"  {n:,}/{len(todo):,}  {rate:.0f}/s  eta {(len(todo) - n) / max(rate, 1e-6) / 60:.0f} min", flush=True)
    db.execute("insert or replace into manifest values ('ctx_embedded', ?)", (now(),))
    db.commit()
    print("done. Next: python mb.py build-cache, then run_v4.sh ctx")


def cmd_fts(_a):
    """wiki_ctx_fts: one row per film (rowid = film_id), its lead + plot text plus every context,
    topic and keyword line (retrieve.py WIKI_CTX_KW=1 searches it instead of wiki_fts)."""
    db = connect()
    db.executescript("drop table if exists wiki_ctx_fts; "
                     "create virtual table wiki_ctx_fts using fts5(title, body, tokenize='porter unicode61');")
    films = {}
    for fid, title, text, ctx, tp, kw in db.execute(
            "select c.film_id, c.title, c.text, x.context, x.topics_json, x.keywords_json from chunks c "
            "left join chunk_ctx x on x.chunk_id = c.chunk_id where c.kind in ('plot', 'lead') order by c.chunk_id"):
        f = films.setdefault(fid, [title, []])
        f[1].append(text)
        if ctx:
            f[1].append(ctx_text((ctx, tp, kw)))
    db.executemany("insert into wiki_ctx_fts(rowid, title, body) values (?,?,?)",
                   [(fid, t, " ".join(b)) for fid, (t, b) in films.items()])
    db.commit()
    print(f"wiki_ctx_fts: {len(films):,} films")


def cmd_show(a):
    db = connect()
    for fid, t, y in db.execute("select film_id, title, year from chunks where title like ? and kind = 'lead' "
                                "and position = 1 limit 3", (a.title,)):
        print(f"{t} ({y})  film {fid}")
        for kind, pos, n, text, ctx, tp, kw in db.execute(
                "select c.kind, c.position, c.n_parts, c.text, x.context, x.topics_json, x.keywords_json "
                "from chunks c left join chunk_ctx x on x.chunk_id = c.chunk_id where c.film_id = ? and c.kind in "
                "('plot','lead') order by c.kind desc, c.position", (fid,)):
            print(f"  [{kind} {pos}/{n}] {text[:110]}...")
            print(f"      context: {ctx or '-'}")
            if ctx:
                print(f"      topics: {', '.join(json.loads(tp))} | keywords: {', '.join(json.loads(kw))}")


def cmd_status(_a):
    db = connect()
    n_f = db.execute("select count(*), sum(ok) from chunk_ctx_done where version = ?", (VERSION,)).fetchone()
    n_c = db.execute("select count(*) from chunk_ctx where version = ? and context != ''", (VERSION,)).fetchone()[0]
    n_all = db.execute("select count(distinct film_id) from chunks where kind = 'plot'").fetchone()[0]
    n_added = db.execute("select count(*) from chunk_ctx where ctx_chunk_id is not null").fetchone()[0]
    print(f"films done {n_f[0]:,} of {n_all:,} (ok {n_f[1] or 0:,}); chunks with context {n_c:,}; "
          f"added as *_c kinds {n_added:,}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["build", "eval", "add", "embed", "fts", "show", "status"])
    ap.add_argument("title", nargs="?")
    ap.add_argument("--sample", type=int, default=3000, help="random films besides the question-set films")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=int(os.environ.get("CTX_WORKERS", "1")))
    ap.add_argument("--batch", type=int, default=32)
    a = ap.parse_args()
    {"build": cmd_build, "eval": cmd_eval, "add": cmd_add, "embed": cmd_embed, "fts": cmd_fts,
     "show": cmd_show, "status": cmd_status}[a.cmd](a)


if __name__ == "__main__":
    main()
