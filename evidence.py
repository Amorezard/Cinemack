#!/usr/bin/env python3
"""
R14 (v7, 27 Oct): the reception evidence index - every critic statement, audience review passage, professional
review summary and analysis-video chapter as a citable unit, searched by keywords (BM25) AND vectors, filtered by
source / outlet / critic / date / sentiment / aspect, ranked with stated source weights, and diversified so an
answer sees critics and audiences.

    python evidence.py build                 # rebuild units whose text changed; embed the new ones (mxbai, Ollama)
    python evidence.py build --no-vectors    # units + keyword index only (no Ollama needed)
    python evidence.py search "Oppenheimer" "what did critics say about the pacing"
    python evidence.py stats
    python evidence.py hit sets/reception.json   # evidence hit@5 on the reception set (expect_evidence conditions)

Units (facts.db `evidence`; raw tables are never changed; rebuilt per unit when its text hash changes):
    source_type    from                                    one unit is                      weight (stated criteria)
    critic         critic_statements kind=critic           one sentence naming an outlet    1.00 named critic + outlet,
                   (Wikipedia, CC BY-SA)                   or critic, with its citation     0.95 outlet only
    aggregator     critic_statements kind=aggregator       Rotten Tomatoes / Metacritic     0.90
    critic_summary critic_statements kind=summary          "critics praised ..."            0.80
    audience_poll  critic_statements kind=audience         CinemaScore / PostTrak           0.85
    legacy         critic_statements in Legacy sections    later standing                   0.80
    guardian       critic_reviews source=guardian          the standfirst + star rating     0.95
    nyt            critic_reviews source=nyt               ONLY critic + date + link (E8)    0.50
    audience       reviews (TMDB users)                    a short review, or a paragraph   0.70 passed the quality
                                                           of a long one (<= ~700 chars)    filter, 0.55 not checked;
                                                                                            failed / bombing / joke: out
    video          yt_analysis (R11, allow-listed          one chapter of the description   0.60
                   analysis channels)
    tv_audience    tv_reviews (space = 'tv': TMDB TV ids, never joined to films)
  Weights rank sources by what they are (named critic > anonymous summary > single viewer), never by views or likes.

Search: BM25 (FTS5) and cosine similarity over the film's unit vectors, fused by reciprocal rank (k = 60), times the
weight; then diversity: at most 2 units per outlet / author, and critics and audiences interleaved unless the
question names only one side. Budget: well under 1 s (the vectors of one or two films only).

Vectors are made only for sources whose terms allow model processing (E8): Wikipedia statements, TMDB reviews,
video chapters. Guardian standfirsts are keyword-searchable only (EVIDENCE_VEC_GUARDIAN=1 after the terms check).
"""
import argparse
import hashlib
import json
import math
import os
import re
import sqlite3
import sys
import time

import facts_db as F

VERSION = "ev1"
EMBED_MODEL = os.environ.get("EMBED_MODEL", "mxbai-embed-large")
QUERY_PREFIX = "Represent this sentence for searching relevant passages: "
VEC_GUARDIAN = os.environ.get("EVIDENCE_VEC_GUARDIAN", "0") == "1"
CRITICS = ("critic", "aggregator", "critic_summary", "audience_poll", "legacy", "guardian", "nyt")
AUDIENCES = ("audience", "tv_audience", "video_comment")
ANALYSTS = ("video", "themes")
WEIGHT = {"critic": 1.0, "aggregator": 0.9, "critic_summary": 0.8, "audience_poll": 0.85, "legacy": 0.8,
          "guardian": 0.95, "nyt": 0.5, "audience": 0.7, "video": 0.6, "tv_audience": 0.7,
          "video_comment": 0.45, "themes": 0.8}
# 4 Nov: two new unit kinds, each searched only when its switch is on (so arms with it off see exactly the 3 Nov
# evidence): video_comment = top comments under the allow-listed channels' videos (youtube_analysis.py comments;
# YouTube API data: comment text is refreshed or deleted within 30 days), switch YT_COMMENTS=1; themes = paragraphs
# of a film's Wikipedia "Themes" / "Analysis" / "Controversy" sections (v7_data.py themes; CC BY-SA), switch ISSUES=1.
EXTRA_TYPES = {"video_comment": "YT_COMMENTS", "themes": "ISSUES"}


def _off_types():
    return [t for t, sw in EXTRA_TYPES.items() if os.environ.get(sw, "0") != "1"]

DDL = """
create table if not exists evidence(ev_id integer primary key, space text, tmdb_id integer, source_type text,
  outlet text, critic text, author text, date text, url text, text text, label text, aspects text, rating real,
  weight real, parent_key text, chunk integer, text_hash text, version text);
create index if not exists evidence_film on evidence(space, tmdb_id, source_type);
create unique index if not exists evidence_key on evidence(parent_key, chunk);
create table if not exists evidence_vec(ev_id integer primary key, tmdb_id integer, space text, emb blob);
create index if not exists evidence_vec_film on evidence_vec(space, tmdb_id);
create virtual table if not exists evidence_fts using fts5(text, outlet, critic, content='evidence',
  content_rowid='ev_id', tokenize='porter unicode61');
"""


def connect(path=None, readonly=False):
    if readonly:
        return F.connect(path, readonly=True)
    conn = F.connect(path)
    conn.executescript(DDL)
    return conn


def _q(conn, sql, p=()):
    try:
        return conn.execute(sql, p).fetchall()
    except sqlite3.Error:
        return []


def h(t):
    return hashlib.sha1((t or "").encode("utf-8", "replace")).hexdigest()[:16]


def wiki_link(pageid, revid):
    if not pageid:
        return None
    return f"https://en.wikipedia.org/w/index.php?curid={pageid}" + (f"&oldid={revid}" if revid else "")


def split_review(text, max_chars=700):
    t = (text or "").strip()
    if len(t) <= max_chars + 200:
        return [" ".join(t.split())]
    paras = [p for p in re.split(r"\n\s*\n|\r\n\r\n", t) if p.strip()] or [t]
    out = []
    for p in paras:
        p = " ".join(p.split())
        while len(p) > max_chars:
            cut = p.rfind(". ", 0, max_chars)
            cut = cut + 1 if cut > max_chars // 3 else max_chars
            out.append(p[:cut].strip())
            p = p[cut:].strip()
        if p:
            if out and len(out[-1]) + len(p) < max_chars // 2:
                out[-1] += " " + p
            else:
                out.append(p)
    return out[:6]


# ------------------------------------------------------------------ build
def units(conn):
    """Yield (parent_key, chunk, row-dict) for every evidence unit."""
    try:
        import review_filter
        ok = review_filter.usable_ids()
    except Exception:  # noqa: BLE001
        ok = None
    sent = {k: l for k, l in _q(conn, "select item_key, label from review_sentiment")}
    flags = {r: (b, j) for r, b, j in _q(conn, "select review_id, burst, joke from review_flags")}
    asp = {}
    for rid, a in _q(conn, "select review_id, aspect from review_aspects"):
        asp.setdefault(rid, []).append(a)
    kind_map = {"critic": "critic", "aggregator": "aggregator", "summary": "critic_summary", "audience": "audience_poll"}
    for sid, fid, pid, rev, sk, text, critic, outlet, url, date, kind in _q(conn, """select stmt_id, tmdb_id, pageid, revid,
            section_kind, text, critic, outlet, ref_url, ref_date, kind from critic_statements
            where kind in ('critic', 'aggregator', 'summary', 'audience') or section_kind = 'legacy'"""):
        st = "legacy" if sk == "legacy" and kind not in ("aggregator",) else kind_map.get(kind, "critic_summary")
        w = WEIGHT[st]
        if st == "critic":
            w = 1.0 if (critic and outlet) else 0.95
        yield f"cs:{sid}", 0, dict(space="film", tmdb_id=fid, source_type=st, outlet=outlet, critic=critic, author=None,
                                   date=date, url=url or wiki_link(pid, rev), text=text, label=sent.get(f"cs:{sid}"),
                                   aspects=None, rating=None, weight=w,
                                   wiki=wiki_link(pid, rev))
    for rk, src, fid, critic, stars, pub, url, summary, head in _q(conn, """select review_key, source, tmdb_id, critic,
            stars, published, url, summary, headline from critic_reviews where match >= 1"""):
        if src == "guardian" and summary:
            yield f"cr:{rk}", 0, dict(space="film", tmdb_id=fid, source_type="guardian", outlet="The Guardian",
                                      critic=critic, author=None, date=pub, url=url,
                                      text=" ".join(summary.split()) + (f" ({stars:g}/5 stars)" if stars is not None else ""),
                                      label=None, aspects=None, rating=None if stars is None else stars * 2,
                                      weight=WEIGHT["guardian"])
        elif src == "nyt":
            yield f"cr:{rk}", 0, dict(space="film", tmdb_id=fid, source_type="nyt", outlet="The New York Times",
                                      critic=critic, author=None, date=pub, url=url,
                                      text=f"The New York Times reviewed it" + (f" (critic {critic}" if critic else " (")
                                           + (f", {pub[:10]})" if pub else ")") + "; the review is at the link.",
                                      label=None, aspects=None, rating=None, weight=WEIGHT["nyt"])
    for rid, fid, author, rating, created, url, content in _q(conn, """select review_id, tmdb_id, author, rating,
            created_at, url, content from reviews"""):
        b, j = flags.get(rid, (0, 0))
        if b or j or (ok is not None and rid not in ok):
            continue
        w = WEIGHT["audience"] if ok is not None else 0.55
        for i, chunk in enumerate(split_review(content)):
            yield rid, i, dict(space="film", tmdb_id=fid, source_type="audience", outlet="TMDB", critic=None,
                               author=author, date=(created or "")[:10], url=url, text=chunk, label=sent.get(rid),
                               aspects=",".join(sorted(set(asp.get(rid, [])))) or None, rating=rating, weight=w)
    for rid, sid, author, rating, created, url, content in _q(conn, """select review_id, tmdb_id, author, rating,
            created_at, url, content from tv_reviews"""):
        for i, chunk in enumerate(split_review(content)):
            yield rid, i, dict(space="tv", tmdb_id=sid, source_type="tv_audience", outlet="TMDB", critic=None,
                               author=author, date=(created or "")[:10], url=url, text=chunk, label=sent.get(rid),
                               aspects=None, rating=rating, weight=WEIGHT["tv_audience"])
    for vid, fid, chap, start, title, text, channel, pub in _q(conn, """select video_key, tmdb_id, chapter, start_s,
            chapter_title, text, channel, published_at from yt_analysis_units where match_ok = 1"""):
        url = f"https://www.youtube.com/watch?v={vid}" + (f"&t={int(start)}s" if start else "")
        yield f"yt:{vid}", chap, dict(space="film", tmdb_id=fid, source_type="video", outlet=channel, critic=None,
                                      author=channel, date=(pub or "")[:10], url=url,
                                      text=(f"{title}: " if title else "") + (text or ""), label=None, aspects=None,
                                      rating=None, weight=WEIGHT["video"])
    # 4 Nov: comments under matched videos (one unit per comment; >= 8 words; the top 5 per video by likes)
    for cid, vid, fid, channel, vtitle, text, likes, pub in _q(conn, """select c.comment_id, c.video_key, u.tmdb_id,
            a.channel, a.title, c.text, c.likes, c.published_at from yt_analysis_comments c
            join (select distinct video_key, tmdb_id from yt_analysis_units where match_ok = 1) u on u.video_key = c.video_key
            join yt_analysis a on a.video_key = c.video_key
            where (select count(*) from yt_analysis_comments c2 where c2.video_key = c.video_key and c2.likes > c.likes) < 5"""):
        t = " ".join((text or "").split())
        if len(t.split()) < 8:
            continue
        yield f"ytc:{cid}", 0, dict(space="film", tmdb_id=fid, source_type="video_comment", outlet=channel, critic=None,
                                    author=None, date=(pub or "")[:10], url=f"https://www.youtube.com/watch?v={vid}",
                                    text=(t if len(t) <= 600 else t[:600].rsplit(" ", 1)[0] + " ..."),
                                    label=sent.get(f"ytc:{cid}"), aspects=None, rating=None, weight=WEIGHT["video_comment"])
    # 4 Nov: Wikipedia Themes / Analysis / Controversy paragraphs
    for fid, pid, rev, kind, heading, text in _q(conn, """select tmdb_id, pageid, revid, kind, heading, text
            from wiki_themes"""):
        for i, para in enumerate(split_review(text or "")):
            yield f"th:{pid}:{kind}:{heading}", i, dict(space="film", tmdb_id=fid, source_type="themes",
                                                        outlet=f"Wikipedia: {heading}", critic=None, author=None,
                                                        date=None, url=wiki_link(pid, rev), text=para, label=None,
                                                        aspects=kind, rating=None, weight=WEIGHT["themes"])


def needs_vector(st):
    return st in ("critic", "aggregator", "critic_summary", "audience_poll", "legacy", "audience", "tv_audience",
                  "video", "video_comment", "themes") or (st == "guardian" and VEC_GUARDIAN)


def embed(texts):
    import ollama
    out = []
    for s in range(0, len(texts), 64):
        r = ollama.embed(model=EMBED_MODEL, input=texts[s:s + 64], keep_alive="30m")
        out.extend(r["embeddings"])
    return out


def cmd_build(a, conn):
    import numpy as np
    have = {(pk, ch): (eid, th) for eid, pk, ch, th in _q(conn, "select ev_id, parent_key, chunk, text_hash from evidence")}
    seen, new, changed = set(), 0, 0
    t0 = time.time()
    rows = []
    from collections import Counter
    allu = list(units(conn))
    cnt = Counter((pk, ch) for pk, ch, _ in allu)
    dup = {k for k, n in cnt.items() if n > 1}
    skipped = 0
    if dup:
        print(f"evidence: {len(dup):,} keys used by more than one unit -> parent_key gets '#<tmdb_id>'", flush=True)
    for pk, ch, d in allu:
        if (pk, ch) in dup:
            pk = f"{pk}#{d.get('tmdb_id')}"
        if (pk, ch) in seen:
            skipped += 1
            continue
        seen.add((pk, ch))
        th = h(f"{d['text']}|{d['label']}|{d['weight']}|{d['url']}")
        old = have.get((pk, ch))
        if old and old[1] == th and not a.again:
            continue
        rows.append((pk, ch, d, th, old[0] if old else None))
    if skipped:
        print(f"evidence: {skipped:,} exact duplicate units skipped", flush=True)
    gone = [v[0] for k, v in have.items() if k not in seen]
    print(f"evidence: {len(seen):,} units; {len(rows):,} new or changed, {len(gone):,} gone", flush=True)
    for s in range(0, len(rows), 5000):
        part = rows[s:s + 5000]
        for pk, ch, d, th, old_id in part:
            if old_id:
                conn.execute("insert into evidence_fts(evidence_fts, rowid, text, outlet, critic) select 'delete', ev_id, "
                             "text, outlet, critic from evidence where ev_id = ?", (old_id,))
                conn.execute("delete from evidence where ev_id = ?", (old_id,))
                conn.execute("delete from evidence_vec where ev_id = ?", (old_id,))
                changed += 1
            else:
                new += 1
            cur = conn.execute("insert into evidence(space, tmdb_id, source_type, outlet, critic, author, date, url, text, "
                               "label, aspects, rating, weight, parent_key, chunk, text_hash, version) values "
                               "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                               (d["space"], d["tmdb_id"], d["source_type"], d["outlet"], d["critic"], d["author"],
                                d["date"], d["url"], d["text"], d["label"], d["aspects"], d["rating"], d["weight"], pk, ch,
                                th, VERSION))
            conn.execute("insert into evidence_fts(rowid, text, outlet, critic) values (?,?,?,?)",
                         (cur.lastrowid, d["text"], d["outlet"] or "", d["critic"] or ""))
        conn.commit()
    for eid in gone:
        conn.execute("insert into evidence_fts(evidence_fts, rowid, text, outlet, critic) select 'delete', ev_id, text, "
                     "outlet, critic from evidence where ev_id = ?", (eid,))
        conn.execute("delete from evidence where ev_id = ?", (eid,))
        conn.execute("delete from evidence_vec where ev_id = ?", (eid,))
    conn.commit()
    print(f"  units written: {new:,} new, {changed:,} changed ({time.time() - t0:.0f}s)", flush=True)
    if a.no_vectors:
        return cmd_stats(a, conn)
    todo = [(eid, fid, sp, st, t) for eid, fid, sp, st, t in _q(conn, """select e.ev_id, e.tmdb_id, e.space,
        e.source_type, e.text from evidence e left join evidence_vec v on v.ev_id = e.ev_id where v.ev_id is null""")
            if needs_vector(st)]
    if a.limit:
        todo = todo[:a.limit]
    print(f"  vectors to make: {len(todo):,} ({EMBED_MODEL})", flush=True)
    t1 = time.time()
    for s in range(0, len(todo), 512):
        part = todo[s:s + 512]
        try:
            vecs = embed([t[:2000] for _e, _f, _s, _st, t in part])
        except Exception as e:  # noqa: BLE001
            print(f"  embedding failed ({str(e)[:120]}); the rest is done next run", flush=True)
            break
        conn.executemany("insert or replace into evidence_vec values (?,?,?,?)",
                         [(eid, fid, sp, np.asarray(v, dtype=np.float16).tobytes())
                          for (eid, fid, sp, _st, _t), v in zip(part, vecs)])
        conn.commit()
        done = s + len(part)
        if (s // 512) % 10 == 0 or done == len(todo):
            rate = done / max(time.time() - t1, 1e-6)
            print(f"    {done:,}/{len(todo):,}  {rate:.0f}/s  eta {(len(todo) - done) / max(rate, 1e-6) / 60:.0f} min",
                  flush=True)
    F.set_meta(conn, "evidence_built_at", F.now())
    conn.commit()
    cmd_stats(a, conn)


def cmd_stats(a, conn):
    print("evidence units by source: " + ", ".join(f"{st} {n:,}" for st, n in _q(
        conn, "select source_type, count(*) from evidence group by source_type order by 2 desc")))
    print(f"  with vectors: {(_q(conn, 'select count(*) from evidence_vec') or [[0]])[0][0]:,}; films "
          f"{(_q(conn, 'select count(distinct tmdb_id) from evidence where space = ?', ('film',)) or [[0]])[0][0]:,}")


# ------------------------------------------------------------------ search
_STOP = set("""a about above after again all also am an and any are as at be because been before being both but by can
could did do does doing for from had has have having he her here hers him his how i if in into is it its just me more
most my no nor not of on once only or other our out over own same she should so some such than that the their them
then there these they this those through to too under until up very was we were what when where which while who whom
why will with would you your film movie movies films think thought say said people""".split())
R_AUD = re.compile(r"\b(audiences?|viewers?|fans?|moviegoers|people|users?|regular|ordinary|general public|"
                   r"public|imdb|letterboxd|tmdb)\b", re.I)
R_CRIT = re.compile(r"\b(critics?|reviewers?|critical|press|variety|hollywood reporter|ebert|new york times|nyt|"
                    r"guardian|rotten tomatoes|metacritic)\b", re.I)
R_ANAL = re.compile(r"\b(video essay\w*|youtubers?|analysts?|breakdowns?|essayists?|analysis channels?)\b", re.I)
R_NEG = re.compile(r"\b(complain\w*|critici[sz]\w*|disliked|hated?|problems?|flaws?|weak\w*|negative|panned|worst|"
                   r"didn'?t like|issues?|faults?)\b", re.I)
R_POS = re.compile(r"\b(prais\w*|loved?|liked|best|strengths?|positive|admired|acclaim\w*|what worked)\b", re.I)


def fts_query(text):
    words = [w for w in re.findall(r"[a-z0-9]+", (text or "").lower()) if w not in _STOP and len(w) > 2]
    return " OR ".join(dict.fromkeys(words)) if words else None


def question_filters(question):
    """What the question itself asks for: which side, which outlets, which tone."""
    q = question or ""
    f = {"side": None, "outlets": [], "label": None}
    a, c, v = bool(R_AUD.search(q)), bool(R_CRIT.search(q)), bool(R_ANAL.search(q))
    if v and not (a or c):
        f["side"] = "analysts"
    elif a and not c:
        f["side"] = "audiences"
    elif c and not a:
        f["side"] = "critics"
    try:
        import critics as C
        f["outlets"] = [o for o, _p in C.outlets_in_text(q) if o not in ("Rotten Tomatoes", "Metacritic")]
    except Exception:  # noqa: BLE001
        pass
    if R_NEG.search(q) and not R_POS.search(q):
        f["label"] = ("negative", "mixed")
    elif R_POS.search(q) and not R_NEG.search(q):
        f["label"] = ("positive", "mixed")
    return f


def search(question, fids, k=10, q_vec=None, conn=None, space="film", filters=None):
    """[{ev_id, source_type, outlet, critic, author, date, url, text, label, rating, score}] for these films."""
    import numpy as np
    conn = conn or connect(readonly=True)
    if conn is None or not fids:
        return []
    f = dict(question_filters(question))
    f.update(filters or {})
    marks = ",".join("?" * len(fids))
    where, args = [f"e.space = ? and e.tmdb_id in ({marks})"], [space, *fids]
    side = f.get("side")
    if side == "critics":
        where.append(f"e.source_type in ({','.join('?' * len(CRITICS))})")
        args += list(CRITICS)
    elif side == "audiences":
        where.append(f"e.source_type in ({','.join('?' * len(AUDIENCES))})")
        args += list(AUDIENCES)
    elif side == "analysts":
        where.append("e.source_type in ('video', 'critic', 'legacy', 'themes')")
    if f.get("outlets"):
        where.append(f"e.outlet in ({','.join('?' * len(f['outlets']))})")
        args += list(f["outlets"])
    if f.get("label"):
        where.append(f"(e.label is null or e.label in ({','.join('?' * len(f['label']))}))")
        args += list(f["label"])
    for col in ("source_type", "critic", "aspects"):
        if f.get(col):
            where.append(f"e.{col} like ?")
            args.append(f"%{f[col]}%")
    if f.get("date_from"):
        where.append("e.date >= ?")
        args.append(f["date_from"])
    if f.get("date_to"):
        where.append("e.date <= ?")
        args.append(f["date_to"])
    off = _off_types()
    if off:                                      # 4 Nov: unit kinds whose switch is off are not searched
        where.append(f"e.source_type not in ({','.join('?' * len(off))})")
        args += off
    w = " and ".join(where)
    rows = {r[0]: r for r in _q(conn, f"""select e.ev_id, e.source_type, e.outlet, e.critic, e.author, e.date, e.url,
        e.text, e.label, e.rating, e.weight, e.aspects from evidence e where {w}""", args)}
    if not rows and f.get("outlets"):            # the outlet is named but not in the index: drop that filter
        return search(question, fids, k, q_vec, conn, space, dict(filters or {}, outlets=[]))
    if not rows:
        return []
    ranks = {}
    fq = fts_query(question)
    if fq:
        kw = _q(conn, f"""select e.ev_id from evidence_fts join evidence e on e.ev_id = evidence_fts.rowid
            where evidence_fts match ? and {w} order by bm25(evidence_fts) limit 200""", [fq, *args])
        ranks["kw"] = {r[0]: i for i, r in enumerate(kw)}
    if q_vec is not None:
        vr = _q(conn, f"select v.ev_id, v.emb from evidence_vec v where v.space = ? and v.tmdb_id in ({marks})",
                [space, *fids])
        vr = [(eid, e) for eid, e in vr if eid in rows]
        if vr:
            M = np.stack([np.frombuffer(e, dtype=np.float16).astype(np.float32) for _i, e in vr])
            M /= np.linalg.norm(M, axis=1, keepdims=True) + 1e-9
            qv = np.asarray(q_vec, dtype=np.float32)
            qv /= np.linalg.norm(qv) + 1e-9
            order = np.argsort(-(M @ qv))
            ranks["vec"] = {vr[i][0]: r for r, i in enumerate(order[:200])}
    score = {}
    for eid, r in rows.items():
        s = sum(1.0 / (60 + rk[eid]) for rk in ranks.values() if eid in rk)
        if not ranks:
            s = 1.0 / 60
        score[eid] = s * (r[10] or 0.5)
    ordered = sorted(rows, key=lambda e: -score[e])
    # diversity: <= 2 per outlet/author; critics and audiences interleaved when the question names neither side
    per, out, buckets = {}, [], {"c": [], "a": [], "v": []}
    for eid in ordered:
        r = rows[eid]
        key = (r[2] or "") + "|" + (r[3] or r[4] or "")
        if r[1] == "audience":
            key = f"aud|{r[4]}"
        if per.get(key, 0) >= 2:
            continue
        per[key] = per.get(key, 0) + 1
        buckets["a" if r[1] in AUDIENCES else "v" if r[1] in ANALYSTS else "c"].append(eid)
    if side:
        seq = buckets["c"] + buckets["a"] + buckets["v"]
    else:
        seq, i = [], 0
        while len(seq) < k and any(len(b) > i for b in buckets.values()):
            for b in ("c", "a", "v"):
                if len(buckets[b]) > i:
                    seq.append(buckets[b][i])
            i += 1
    for eid in seq[:k]:
        r = rows[eid]
        out.append({"ev_id": eid, "source_type": r[1], "outlet": r[2], "critic": r[3], "author": r[4], "date": r[5],
                    "url": r[6], "text": r[7], "label": r[8], "rating": r[9], "aspects": r[11],
                    "score": round(score[eid], 5)})
    return out


SOURCE_NAME = {"critic": "Critic", "aggregator": "Review aggregator", "critic_summary": "Critics overall",
               "audience_poll": "Audience poll", "legacy": "Later standing", "guardian": "The Guardian review",
               "nyt": "The New York Times", "audience": "TMDB user review", "video": "Analysis video",
               "tv_audience": "TMDB user review", "video_comment": "YouTube viewer comment", "themes": "Wikipedia"}


def label_of(e):
    st = e["source_type"]
    if st in ("critic", "critic_summary", "aggregator", "audience_poll", "legacy"):
        who = ", ".join(x for x in (e.get("outlet"), e.get("critic")) if x)
        return f"{SOURCE_NAME[st]}{' - ' + who if who else ''}, via Wikipedia"
    if st == "guardian":
        return f"The Guardian review" + (f" by {e['critic']}" if e.get("critic") else "") + (
            f", {e['date'][:10]}" if e.get("date") else "")
    if st == "nyt":
        return "The New York Times review" + (f" by {e['critic']}" if e.get("critic") else "")
    if st in ("audience", "tv_audience"):
        return "TMDB user review" + (f", rated {e['rating']:g}/10" if e.get("rating") is not None else "") + (
            f", {e['date'][:7]}" if e.get("date") else "")
    if st == "video":
        return f"Analysis video - {e.get('outlet') or 'YouTube'}"
    if st == "video_comment":
        return f"YouTube viewer comment under a {e.get('outlet') or 'YouTube'} video" + (
            f", {e['date'][:7]}" if e.get("date") else "")
    if st == "themes":
        return f"{e.get('outlet') or 'Wikipedia'} section"
    return st


def evidence_block(title, ev, start=1):
    """('block text', {n: {label, url}}) - numbered so the answer can cite [n]."""
    if not ev:
        return None, {}
    lines, cites = [f"{title} - evidence (numbered; cite the number in square brackets, e.g. [{start}])"], {}
    for i, e in enumerate(ev, start):
        tone = f" ({e['label']})" if e.get("label") else ""
        text = e["text"] if len(e["text"]) <= 420 else e["text"][:420].rsplit(" ", 1)[0] + " ..."
        lines.append(f"[{i}] {label_of(e)}{tone}: \"{text}\"")
        cites[i] = {"label": label_of(e), "url": e.get("url"), "source_type": e["source_type"], "text": e["text"][:600],
                    "outlet": e.get("outlet")}
    return "\n".join(lines), cites


# ------------------------------------------------------------------ evaluation: evidence hit@5
def matches(e, cond):
    for k, v in cond.items():
        if k == "side":
            grp = "audiences" if e["source_type"] in AUDIENCES else "analysts" if e["source_type"] in ANALYSTS else "critics"
            if grp != v:
                return False
        elif k == "label":
            if e.get("label") not in (v if isinstance(v, list) else [v]):
                return False
        elif k == "text":
            if v.lower() not in (e.get("text") or "").lower():
                return False
        elif (str(e.get(k) or "")).lower() != str(v).lower() and not (k in ("aspects", "critic") and
                                                                     str(v).lower() in str(e.get(k) or "").lower()):
            return False
    return True


def cmd_hit(a, conn):
    import retrieve as R
    qs = json.load(open(a.path))["questions"]
    n = hit = 0
    t_ms = []
    for q in qs:
        conds = q.get("expect_evidence")
        if not conds:
            continue
        fids = []
        for t in q.get("expect_films") or []:
            r = conn.execute("select tmdb_id from films where title = ? collate nocase order by vote_count desc limit 1",
                             (t,)).fetchone()
            if r:
                fids.append(r[0])
        if not fids:
            continue
        try:
            qv = R.embed_query(q["question"])
        except Exception:  # noqa: BLE001
            qv = None
        t0 = time.perf_counter()
        ev = search(q["question"], fids, k=5, q_vec=qv, conn=conn)
        t_ms.append((time.perf_counter() - t0) * 1000)
        ok = any(matches(e, c) for e in ev for c in conds)
        n += 1
        hit += ok
        print(f"  {q['id']} {'HIT ' if ok else 'miss'} {q['question'][:70]}")
    if n:
        print(f"evidence hit@5: {hit}/{n} = {hit / n:.2f}; search time median "
              f"{sorted(t_ms)[len(t_ms) // 2]:.0f} ms, max {max(t_ms):.0f} ms (budget 1000 ms)")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["build", "search", "stats", "hit"])
    ap.add_argument("args", nargs="*")
    ap.add_argument("--facts")
    ap.add_argument("--again", action="store_true")
    ap.add_argument("--no-vectors", action="store_true")
    ap.add_argument("--limit", type=int)
    a = ap.parse_args()
    conn = connect(a.facts)
    if a.cmd == "search":
        title, question = a.args[0], " ".join(a.args[1:])
        r = conn.execute("select tmdb_id, title from films where title = ? collate nocase order by vote_count desc limit 1",
                         (title,)).fetchone()
        if not r:
            sys.exit("no such film")
        try:
            import retrieve as R
            qv = R.embed_query(question)
        except Exception:  # noqa: BLE001
            qv = None
        t0 = time.perf_counter()
        ev = search(question, [r[0]], k=10, q_vec=qv, conn=conn)
        b, _c = evidence_block(r[1], ev)
        print(b or "(nothing)")
        print(f"({(time.perf_counter() - t0) * 1000:.0f} ms; filters {question_filters(question)})")
    elif a.cmd == "hit":
        a.path = a.args[0] if a.args else "sets/reception.json"
        cmd_hit(a, conn)
    else:
        {"build": cmd_build, "stats": cmd_stats}[a.cmd](a, conn)


if __name__ == "__main__":
    main()
