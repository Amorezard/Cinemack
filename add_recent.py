#!/usr/bin/env python3
"""
v4 (14 Oct): make recent films searchable - including little-known ones - with live Wikipedia text.

The search catalogue (movies.db, 30,856 films) and the Wikipedia passages (wiki.db, the TREC 2023
snapshot) stop around 2023. Newer films were in facts.db only (TMDB data, answerable when named),
never findable from a description ("the 2025 one where ..."). This adds them to the SAME indexes the
old films use, so every search path works for them unchanged: TMDB record + plot vectors, keyword
search, Wikipedia lead + plot passages (vectors and keywords), generated "tip of the tongue"
descriptions, and the non-plot Wikipedia sections the tools quote (production, reception, box office).

    python fetch_tmdb.py extra --year-from 2023 --min-votes 3 --pages 25   # 1. recent films into facts.db
                                                                           #    (TMDB votes, any number of reviews)
    python add_recent.py wiki          # 2. live Wikipedia page for each (Wikidata link, else search)  ~1 s/film
    python add_recent.py catalog       # 3. into movies.db: record, record vectors, plot vectors, keyword index
    python add_recent.py passages      # 4. into wiki.db: pages, lead + plot chunks, keyword index; facts.db sections
    python wiki_embed.py --wiki-db wiki.db --kinds lead,plot                # 5. passage vectors (only the new ones)
    python wiki_enrich.py --n 3 && python wiki_embed.py --wiki-db wiki.db --kinds question   # 6. descriptions
    python wiki_sections.py embed      # 7. section vectors (only the new ones)
    python mb.py build-cache           # 8. vec_cache
    python add_recent.py eval --n 100  # 9. can the pipeline find them? (see the rule below)
    python add_recent.py status | remove

Every added row is recorded (recent_added in facts.db; doc_id 'live:<pageid>' in wiki.db and facts.db
sections), so `remove` takes them all out again; backups/*_v3sealed.db keep the sealed versions.
Wikipedia text is CC BY-SA and dated (snapshot = the day it was fetched); answers already credit
Wikipedia.

Decision rule (written 14 Oct, before any result): keep the added films only if stage_scores on
sets/tune.json and sets/check.json each lose at most 1 question against the same settings without
them (new films are new distractors for old questions), and `eval` finds the added films' own
descriptions in the top 3 at least half as often as it finds the catalogue films' (reference group).
"""
import argparse
import datetime
import hashlib
import json
import os
import random
import re
import sqlite3
import sys
import time
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
if os.environ.get("MOCK", "off").lower() not in ("", "off", "0"):
    import mock_llm
    mock_llm.install()

import facts_db as F  # noqa: E402
import wiki_common as wc  # noqa: E402
import wiki_sections as WS  # noqa: E402

MOVIES = os.path.join(HERE, "movies.db")
WIKI = os.path.join(HERE, "wiki.db")
UA = "MovieBuff/4 (student research project; Merrimack College) python-urllib"
TODAY = datetime.date.today().isoformat()
EMBED_MODEL = os.environ.get("EMBED_MODEL", "mxbai-embed-large")
EMBED_MAX_CHARS = 1900

DDL = """
create table if not exists recent_wiki(tmdb_id integer primary key, pageid integer, title text, url text,
  method text, extract text, fetched_at text, status text);
create table if not exists recent_added(tmdb_id integer primary key, added_at text, has_wiki integer);
"""


def fconn():
    c = F.connect()
    c.executescript(DDL)
    return c


def _get(url, tries=5):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.load(r)
        except Exception as e:  # noqa: BLE001
            print(f"  {str(e)[:80]} ({url[:70]})", file=sys.stderr)
            wait = 3 * (i + 1)
            if getattr(e, "code", None) == 429:          # 29 Oct: rate limited -> honour Retry-After, back off longer
                try:
                    wait = max(wait, int(e.headers.get("Retry-After") or 0))
                except (TypeError, ValueError, AttributeError):
                    pass
                wait = max(wait, 20 * (i + 1))
            time.sleep(wait)
    return None


def candidates(c, year_from):
    """Films in facts.db, released from year_from on, not in the search catalogue, with a TMDB plot."""
    cat = set()
    if os.path.exists(MOVIES):
        m = sqlite3.connect(f"file:{MOVIES}?mode=ro", uri=True)
        cat = {r[0] for r in m.execute("select id from movies")}
        m.close()
    rows = c.execute("select tmdb_id, title, year, wikidata_id from films where year >= ? and status = 'Released' "
                     "and overview is not null and length(overview) > 40 order by popularity desc",
                     (year_from,)).fetchall()
    return [r for r in rows if r[0] not in cat]


# ------------------------------------------------------------------ 2. live Wikipedia
def enwiki_titles(qids):
    out = {}
    for s in range(0, len(qids), 50):
        part = [q for q in qids[s:s + 50] if q]
        if not part:
            continue
        d = _get("https://www.wikidata.org/w/api.php?" + urllib.parse.urlencode(
            {"action": "wbgetentities", "ids": "|".join(part), "props": "sitelinks", "sitefilter": "enwiki",
             "format": "json"}))
        for q, ent in ((d or {}).get("entities") or {}).items():
            t = ((ent.get("sitelinks") or {}).get("enwiki") or {}).get("title")
            if t:
                out[q] = t
        time.sleep(0.5)
    return out


def search_title(title, year):
    d = _get("https://en.wikipedia.org/w/api.php?" + urllib.parse.urlencode(
        {"action": "query", "list": "search", "srsearch": f"{title} {year} film", "srlimit": 5, "format": "json"}))
    nt = wc.norm_title(title)
    for hit in ((d or {}).get("query") or {}).get("search") or []:
        if wc.norm_title(hit["title"]) == nt and ("film" in hit.get("snippet", "").lower() or str(year) in hit.get("snippet", "")):
            return hit["title"]
    return None


def page_extract(title):
    d = _get("https://en.wikipedia.org/w/api.php?" + urllib.parse.urlencode(
        {"action": "query", "prop": "extracts|info", "explaintext": 1, "exsectionformat": "wiki", "inprop": "url",
         "titles": title, "redirects": 1, "format": "json", "formatversion": 2}))
    pages = ((d or {}).get("query") or {}).get("pages") or []
    if not pages or pages[0].get("missing"):
        return None
    p = pages[0]
    return {"pageid": p.get("pageid"), "title": p.get("title"), "url": p.get("fullurl"), "extract": p.get("extract") or ""}


def looks_like_the_film(extract, year):
    lead = (extract or "")[:600].lower()
    return "film" in lead and (str(year) in lead or str(int(year) - 1) in lead or str(int(year) + 1) in lead)


def cmd_wiki(a):
    c = fconn()
    films = candidates(c, a.year_from)
    done = {r[0] for r in c.execute("select tmdb_id from recent_wiki")}
    todo = [f for f in films if f[0] not in done][: a.limit or None]
    print(f"recent films not in the catalogue: {len(films):,}; pages to look up now: {len(todo):,}")
    by_q = enwiki_titles([f[3] for f in todo if f[3]])
    stats = {"wikidata": 0, "search": 0, "none": 0, "not_a_film_page": 0}
    for i, (fid, title, year, qid) in enumerate(todo, 1):
        wt, method = by_q.get(qid), "wikidata"
        if not wt:
            wt, method = search_title(title, year), "search"
        page = page_extract(wt) if wt else None
        if page and not looks_like_the_film(page["extract"], year or 0):
            stats["not_a_film_page"] += 1
            page = None
        if page:
            stats[method] += 1
            c.execute("insert or replace into recent_wiki values (?,?,?,?,?,?,?,?)",
                      (fid, page["pageid"], page["title"], page["url"], method, page["extract"], F.now(), "ok"))
        else:
            stats["none"] += 1
            c.execute("insert or replace into recent_wiki values (?,?,?,?,?,?,?,?)",
                      (fid, None, None, None, method, None, F.now(), "none"))
        if i % 25 == 0 or i == len(todo):
            c.commit()
            print(f"  {i:,}/{len(todo):,}  " + ", ".join(f"{k} {v}" for k, v in stats.items()), flush=True)
        time.sleep(0.3)
    c.commit()


def split_sections(extract):
    """{'abstract': lead, '<heading lowercased>': text} from a plain-text extract with == headings ==.
    Subsections are folded into their level-2 section."""
    secs, cur, buf = {}, "abstract", []
    for line in (extract or "").split("\n"):
        m = re.match(r"^(=+)\s*(.+?)\s*\1\s*$", line.strip())
        if m:
            if len(m.group(1)) == 2:
                secs[cur] = "\n".join(buf).strip()
                cur, buf = m.group(2).strip().lower(), []
            continue
        buf.append(line)
    secs[cur] = "\n".join(buf).strip()
    return {k: re.sub(r"\s+", " ", v).strip() for k, v in secs.items() if v.strip()}


# ------------------------------------------------------------------ 3. catalogue
def film_record(c, fid):
    r = c.execute("select title, year, overview, tagline, genres_json, vote_average, vote_count, popularity "
                  "from films where tmdb_id = ?", (fid,)).fetchone()
    if not r:
        return None
    title, year, ov, tag, g, va, vc, pop = r
    genres = ", ".join(json.loads(g or "[]"))
    director = ", ".join(n for (n,) in c.execute(
        "select p.name from credits k join people p on p.person_id = k.person_id where k.tmdb_id = ? "
        "and k.job = 'Director' order by k.ord", (fid,)))
    cast = [(n, ch) for n, ch in c.execute(
        "select p.name, k.character from credits k join people p on p.person_id = k.person_id where k.tmdb_id = ? "
        "and k.kind = 'cast' order by k.ord limit 5", (fid,))]
    from embed_layout import clean_keywords
    kws = clean_keywords(", ".join(n for (n,) in c.execute("select name from keywords where tmdb_id = ?", (fid,))))
    lead = (c.execute("select extract from recent_wiki where tmdb_id = ? and status = 'ok'", (fid,)).fetchone() or [None])[0]
    summary = " ".join(split_sections(lead).get("abstract", "").split()[:120]) if lead else ""
    starring = ", ".join(f"{n} as {ch}" if ch else n for n, ch in cast)
    parts = [f"{title} ({year or 'unknown year'})"]          # the layout rebuild_embeddings.py writes
    if ov:
        parts.append(f"Plot: {ov}")
    if summary:
        parts.append(f"Summary: {summary}")
    if kws:
        parts.append(f"Keywords: {kws}")
    if genres:
        parts.append(f"Genres: {genres}")
    if director:
        parts.append(f"Director: {director}")
    if starring:
        parts.append(f"Starring: {starring}")
    if tag:
        parts.append(f"Tagline: {tag}")
    return {"id": fid, "title": title, "overview": ov or "", "genres": genres, "director": director or None,
            "top_cast": ", ".join(n for n, _ in cast) or None, "release_year": str(year or ""),
            "vote_average": va, "vote_count": vc or 0, "popularity": pop, "wiki_summary": summary or None,
            "embed_text": "\n".join(parts)[:EMBED_MAX_CHARS]}


def _vec_conn(path):
    import sqlite_vec
    db = sqlite3.connect(path, timeout=600)
    db.enable_load_extension(True)
    sqlite_vec.load(db)
    db.enable_load_extension(False)
    return db


def _embed(texts):
    import ollama
    for attempt in range(5):
        try:
            return ollama.embed(model=EMBED_MODEL, input=texts)["embeddings"]
        except Exception as e:  # noqa: BLE001
            print(f"  embed retry {attempt + 1}: {str(e)[:80]}", file=sys.stderr)
            time.sleep(5 * (attempt + 1))
    raise SystemExit("embedding kept failing")


def cmd_catalog(a):
    import sqlite_vec
    c = fconn()
    films = candidates(c, a.year_from)[: a.limit or None]
    m = _vec_conn(MOVIES)
    cols = [r[1] for r in m.execute("pragma table_info(movies)")]
    recs = [r for r in (film_record(c, f[0]) for f in films) if r]
    print(f"adding {len(recs):,} films to {MOVIES} (columns: {', '.join(cols)})")
    use = [k for k in ("id", "title", "overview", "genres", "director", "top_cast", "release_year", "vote_average",
                       "vote_count", "popularity", "wiki_summary", "embed_text") if k in cols]
    m.executemany(f"insert or replace into movies ({','.join(use)}) values ({','.join('?' * len(use))})",
                  [tuple(r[k] for k in use) for r in recs])
    m.commit()
    have_rec = {r[0] for r in m.execute("select movie_id from movie_vectors")}
    try:
        have_plot = {r[0] for r in m.execute("select movie_id from movie_plot_vectors")}
    except sqlite3.OperationalError:
        have_plot = None
    todo = [r for r in recs if r["id"] not in have_rec]
    for s in range(0, len(todo), 64):
        part = todo[s:s + 64]
        vecs = _embed([r["embed_text"] for r in part])
        m.executemany("insert or replace into movie_vectors(movie_id, embedding) values (?, ?)",
                      [(r["id"], sqlite_vec.serialize_float32(v)) for r, v in zip(part, vecs)])
        if have_plot is not None:
            pv = _embed([f"{r['title']} ({r['release_year'] or 'unknown year'})\nPlot: {r['overview']}" for r in part])
            m.executemany("insert or replace into movie_plot_vectors(movie_id, embedding) values (?, ?)",
                          [(r["id"], sqlite_vec.serialize_float32(v)) for r, v in zip(part, pv)])
        m.commit()
        print(f"  vectors {s + len(part):,}/{len(todo):,}", flush=True)
    try:
        m.executemany("delete from movies_fts where rowid = ?", [(r["id"],) for r in recs])
        m.executemany("insert into movies_fts(rowid, title, body) values (?,?,?)",
                      [(r["id"], r["title"], r["embed_text"]) for r in recs])
        m.commit()
    except sqlite3.OperationalError as e:
        print(f"  (movies_fts not updated: {e})")
    c.executemany("insert or replace into recent_added values (?,?,?)",
                  [(r["id"], F.now(), int(bool(r["wiki_summary"]))) for r in recs])
    c.executemany("update films set in_catalog = 1 where tmdb_id = ?", [(r["id"],) for r in recs])
    c.commit()
    print(f"catalogue: +{len(recs):,} films ({sum(1 for r in recs if r['wiki_summary']):,} with a Wikipedia summary)")


# ------------------------------------------------------------------ 4. passages + sections
def cmd_passages(a):
    c = fconn()
    w = sqlite3.connect(WIKI, timeout=600)
    rows = c.execute("select r.tmdb_id, r.pageid, r.title, r.url, r.method, r.extract, f.title, f.year, "
                     "f.original_language, f.genres_json, f.vote_count, f.wikidata_id from recent_wiki r "
                     "join films f on f.tmdb_id = r.tmdb_id join recent_added a on a.tmdb_id = r.tmdb_id "
                     "where r.status = 'ok'").fetchall()
    have = {r[0] for r in w.execute("select doc_id from pages where doc_id like 'live:%'")}
    # 16 Oct: pages already in wiki.db whose facts.db sections are missing (the 15 Oct night run crashed after
    # writing pages + chunks, before sections) get their sections now; only these and new pages are touched.
    have_secs = {r[0] for r in c.execute("select distinct doc_id from sections where doc_id like 'live:%'")}
    done = set()
    cols =["film_id", "doc_id", "title", "kind", "section", "position", "n_parts", "is_ending", "year", "decade",
            "language", "is_animated", "genres_json", "tier", "popularity", "source", "url", "snapshot",
            "link_method", "link_conf", "text", "text_hash", "n_words"]
    n_pages = n_chunks = n_secs = 0
    sec_rows = []
    for fid, pid, wtitle, url, method, extract, title, year, lang, g, vc, qid in rows:
        did = f"live:{pid}"
        if did in done:
            continue                    # two TMDB films linked to the same Wikipedia page: the first keeps it
        done.add(did)
        new_page = did not in have
        if not new_page and did in have_secs:
            continue
        secs = split_sections(extract)
        if not new_page:                # page + chunks exist: sections only
            for heading, text in secs.items():
                k = WS.kind_of(heading)
                if not k or len(text.split()) < 25:
                    continue
                parts = wc.chunk_text(text, 180, 260)
                for i, part in enumerate(parts, 1):
                    sec_rows.append((fid, did, heading, k, i, len(parts), part, len(part.split()), url, TODAY))
            continue
        lead = secs.get("abstract", "")
        plot_key, plot = wc.pick_plot(secs)
        genres = json.loads(g or "[]")
        conf = 1.0 if method == "wikidata" else 0.8
        extra = {k: secs[k] for k in wc.EXTRA_KEYS if secs.get(k)}
        w.execute("insert or replace into pages values (?,?,?,?,?,?,?,?,?)",
                  (did, fid, title, qid or "", url, TODAY, plot_key,
                   json.dumps({"abstract": lead, "plot": plot, **extra}, ensure_ascii=False), "{}"))
        try:
            w.execute("insert or ignore into links(film_id, doc_id, wiki_title, wikidata_id, method, conf, has_plot) "
                      "values (?,?,?,?,?,?,?)", (fid, did, wtitle, qid or "", "live-" + method, conf, int(bool(plot))))
        except sqlite3.OperationalError:
            pass
        base = dict(film_id=fid, doc_id=did, title=title, year=year or 0, decade=((year or 0) // 10) * 10,
                    language=(lang or "").lower(), is_animated=int(any(x.lower() == "animation" for x in genres)),
                    genres_json=json.dumps(genres), tier=0, popularity=wc.popularity(vc or 0),
                    source="wikipedia-live", url=url, snapshot=TODAY, link_method="live-" + method, link_conf=conf)
        for kind, section, text in (("lead", "intro", lead), ("plot", plot_key or "plot", plot)):
            if not text:
                continue
            parts = wc.chunk_text(text, 180, 260)
            for i, part in enumerate(parts, 1):
                rec = dict(base, kind=kind, section=section, position=i, n_parts=len(parts),
                           is_ending=int(kind == "plot" and i == len(parts) and len(parts) > 1), text=part,
                           text_hash=hashlib.sha1(part.encode()).hexdigest(), n_words=len(part.split()))
                w.execute(f"insert into chunks({','.join(cols)}) values ({','.join('?' * len(cols))})",
                          [rec[k] for k in cols])
                n_chunks += 1
        for heading, text in secs.items():
            k = WS.kind_of(heading)
            if not k or len(text.split()) < 25:
                continue
            parts = wc.chunk_text(text, 180, 260)
            for i, part in enumerate(parts, 1):
                sec_rows.append((fid, did, heading, k, i, len(parts), part, len(part.split()), url, TODAY))
        n_pages += 1
        if n_pages % 200 == 0:
            w.commit()
    w.commit()
    try:                                       # keyword index over intro + plot (wiki_fts.py's layout)
        # 16 Oct: one row per film, built here (the old INSERT ... SELECT per page wrote the same rowid twice
        # when two films shared a page -> IntegrityError, which ended the 15 Oct night chain).
        fts = {}
        for fid, title, sj in w.execute("select film_id, title, sections_json from pages where doc_id like 'live:%'"):
            s = json.loads(sj or "{}")
            body = " ".join(x for x in (s.get("abstract", ""), s.get("plot", "")) if x)
            if body:
                fts[int(fid)] = (title, body)
        w.executemany("delete from wiki_fts where rowid = ?", [(i,) for i in fts])
        w.executemany("insert into wiki_fts(rowid, title, body) values (?,?,?)",
                      [(i, t, b) for i, (t, b) in fts.items()])
        w.commit()
        print(f"  wiki_fts: {len(fts):,} recent films indexed")
    except sqlite3.Error as e:
        w.rollback()
        print(f"  (wiki_fts not updated: {e})")
    # 16 Oct: replace only the sections of the pages handled in this run (was: every 'live:' section, so a
    # second run deleted the first run's sections and their vectors' rows).
    touched = sorted({r[1] for r in sec_rows})
    if c.execute("select 1 from sqlite_master where name = 'sec_vec'").fetchone():
        c.executemany("delete from sec_vec where sec_id in (select sec_id from sections where doc_id = ?)",
                      [(d,) for d in touched])
    c.executemany("delete from sections where doc_id = ?", [(d,) for d in touched])
    c.executemany("insert into sections(tmdb_id, doc_id, section_key, kind, position, n_parts, text, n_words, url, "
                  "snapshot) values (?,?,?,?,?,?,?,?,?,?)", sec_rows)
    n_secs = len(sec_rows)
    c.commit()
    print(f"wiki.db: +{n_pages:,} pages, +{n_chunks:,} lead/plot chunks; facts.db: {n_secs:,} section passages. "
          f"Next: wiki_embed.py --kinds lead,plot, wiki_enrich.py, wiki_embed.py --kinds question, "
          f"wiki_sections.py embed, mb.py build-cache")


# ------------------------------------------------------------------ 9. eval
QSYS = ("You write the message someone types when they half-remember a film but not its title. Use 1-3 plain "
        "sentences about what happens or what they remember seeing. Never give the title, the actors' or "
        "characters' names, or the year. Reply with the message only.")


def cmd_eval(a):
    """Can the full pipeline find the added films from a vague description? Descriptions are written by a
    DIFFERENT model (EVAL_QMODEL, default gemma3:27b) than the one that wrote the indexed descriptions
    (llama3.2), from the film's Wikipedia plot. The same is done for random catalogue films as reference."""
    import ollama
    import retrieve as R
    c = fconn()
    w = sqlite3.connect(f"file:{WIKI}?mode=ro", uri=True)
    rnd = random.Random(11)
    added = [r[0] for r in c.execute("select tmdb_id from recent_added where has_wiki = 1")]
    old = [r[0] for r in w.execute("select distinct film_id from chunks where kind = 'plot' and source != 'wikipedia-live'")]
    rnd.shuffle(added)
    rnd.shuffle(old)
    model = os.environ.get("EVAL_QMODEL", "gemma3:27b")
    # all descriptions first, then all searches: gemma3:27b and movie-buff do not fit on the GPU together,
    # so alternating would reload a 17 GB model twice per question
    qs = []
    for group, ids in (("added", added[:a.n]), ("catalogue", old[:a.n])):
        for fid in ids:
            plot = " ".join(t for (t,) in w.execute("select text from chunks where film_id = ? and kind = 'plot' "
                                                    "order by position limit 2", (fid,)))
            title = (w.execute("select title from chunks where film_id = ? limit 1", (fid,)).fetchone() or [""])[0]
            if not plot or not title:
                continue
            r = ollama.chat(model=model, options={"temperature": 0.7, "num_predict": 120, "seed": int(fid)},
                            messages=[{"role": "system", "content": QSYS}, {"role": "user", "content": plot[:2500]}])
            q = r["message"]["content"].strip()
            if q and not (wc.norm_title(title) and wc.norm_title(title) in wc.norm_title(q)):
                qs.append((group, fid, title, q))
    json.dump(qs, open(os.path.join(HERE, "results", "add_recent_eval_questions.json"), "w"), indent=1)
    try:
        ollama.generate(model=model, prompt="", keep_alive=0)          # free the GPU for the pipeline
    except Exception:  # noqa: BLE001
        pass
    out = {g: {"n": 0, "h1": 0, "h3": 0} for g in ("added", "catalogue")}
    for group, fid, title, q in qs:
        got = [wc.norm_title(h["title"]) for h in R.retrieve(q, k=3)]
        o = out[group]
        o["n"] += 1
        o["h1"] += got[:1] == [wc.norm_title(title)]
        o["h3"] += wc.norm_title(title) in got
        print(f"  {group:9s} {'HIT' if wc.norm_title(title) in got else '   '} {title[:40]:40s} <- {q[:70]}", flush=True)
    out = {g: {"n": v["n"], "hit@1": v["h1"] / max(v["n"], 1), "hit@3": v["h3"] / max(v["n"], 1)} for g, v in out.items()}
    ok = out["catalogue"]["hit@3"] and out["added"]["hit@3"] >= 0.5 * out["catalogue"]["hit@3"]
    text = (f"add_recent eval {F.now()}: descriptions by {model}\n" +
            "\n".join(f"  {g:9s} n {v['n']:3d}  hit@1 {v['hit@1']:.2f}  hit@3 {v['hit@3']:.2f}" for g, v in out.items()) +
            f"\nrule part 2 (added hit@3 >= half of catalogue's): {'PASS' if ok else 'FAIL'}")
    path = os.path.join(HERE, "results", "add_recent_eval.txt")
    open(path, "w").write(text + "\n")
    print(text)


# ------------------------------------------------------------------ status / remove
def cmd_status(a):
    c = fconn()
    print(f"recent films (from {a.year_from}) not yet in the catalogue: {len(candidates(c, a.year_from)):,}")
    for st, n in c.execute("select status, count(*) from recent_wiki group by status"):
        print(f"  Wikipedia lookups {st}: {n:,}")
    n, nw = c.execute("select count(*), sum(has_wiki) from recent_added").fetchone()
    print(f"added to the catalogue: {n or 0:,} ({nw or 0:,} with Wikipedia text)")
    for y, k in c.execute("select f.year, count(*) from recent_added a join films f on f.tmdb_id = a.tmdb_id "
                          "group by f.year order by f.year"):
        print(f"    {y}: {k:,}")


def cmd_remove(_a):
    c = fconn()
    ids = [r[0] for r in c.execute("select tmdb_id from recent_added")]
    if not ids:
        print("nothing added")
        return
    m = _vec_conn(MOVIES)
    for t in ("movies", "movie_vectors", "movie_plot_vectors", "movies_fts"):
        key = "rowid" if t == "movies_fts" else ("movie_id" if "vectors" in t else "id")
        try:
            m.executemany(f"delete from {t} where {key} = ?", [(i,) for i in ids])
        except sqlite3.OperationalError:
            pass
    m.commit()
    w = _vec_conn(WIKI)
    cids = [r[0] for r in w.execute("select chunk_id from chunks where doc_id like 'live:%'")]
    for s in range(0, len(cids), 500):
        part = cids[s:s + 500]
        for t in ("vec_chunks",):
            w.execute(f"delete from {t} where chunk_id in ({','.join('?' * len(part))})", part)
    w.execute("delete from chunks where doc_id like 'live:%'")
    w.execute("delete from chunks where kind = 'question' and film_id in (" + ",".join(str(int(i)) for i in ids) + ")")
    w.execute("delete from pages where doc_id like 'live:%'")
    for t, col in (("links", "doc_id"),):
        try:
            w.execute(f"delete from {t} where {col} like 'live:%'")
        except sqlite3.OperationalError:
            pass
    try:
        w.executemany("delete from wiki_fts where rowid = ?", [(i,) for i in ids])
        w.execute("delete from gen_questions where film_id in (%s)" % ",".join(str(int(i)) for i in ids))
    except sqlite3.OperationalError:
        pass
    w.commit()
    c.execute("delete from sec_vec where sec_id in (select sec_id from sections where doc_id like 'live:%')")
    c.execute("delete from sections where doc_id like 'live:%'")
    c.executemany("update films set in_catalog = 0 where tmdb_id = ?", [(i,) for i in ids])
    c.execute("delete from recent_added")
    c.commit()
    print(f"removed {len(ids):,} added films from movies.db, wiki.db and facts.db; run python mb.py build-cache")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["wiki", "catalog", "passages", "eval", "status", "remove"])
    ap.add_argument("--year-from", type=int, default=2023)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--n", type=int, default=100)
    a = ap.parse_args()
    {"wiki": cmd_wiki, "catalog": cmd_catalog, "passages": cmd_passages, "eval": cmd_eval,
     "status": cmd_status, "remove": cmd_remove}[a.cmd](a)


if __name__ == "__main__":
    main()
