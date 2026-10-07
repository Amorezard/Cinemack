#!/usr/bin/env python3
"""
v3 data: the rest of each linked Wikipedia article (everything except the intro and plot,
which wiki.db already has) as passages in facts.db, plus the infobox's money figures.

    python wiki_sections.py build --corpus ~/movie-buff/data/trec_tot/corpus.jsonl.zip
    python wiki_sections.py embed            # GPU (Ollama mxbai-embed-large); resumable
    python wiki_sections.py infobox          # budget / box office from wiki.db's stored infoboxes
    python wiki_sections.py show "Oppenheimer"   # what a film has

Section kinds (a heading is matched by the words it contains, first rule wins):
    box_office   Box office, Commercial performance
    reception    Reception, Critical response, Audience response, Reviews
    accolades    Accolades, Awards, Nominations
    production   Production, Development, Writing, Casting, Filming, Design, Visual effects
    music        Music, Soundtrack, Score
    release      Release, Marketing, Distribution, Home media
    themes       Themes, Analysis, Interpretation, Style, Symbolism
    legacy       Legacy, Cultural impact, Influence, In popular culture, Sequel

Chunked like v2 (~180 words, 1-sentence overlap) so passages stay comparable. These
passages are NOT added to the tip-of-the-tongue search (wiki.db is unchanged); they feed
the analysis tools (why a film worked, production, reception) in tools.py.
"""
import argparse
import datetime
import json
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.request

import facts_db as F
import wiki_common as wc

RULES = [
    ("box_office", ("box office", "commercial", "gross", "financial")),
    ("reception", ("reception", "critical", "response", "reviews", "critics")),
    ("accolades", ("accolade", "award", "nomination", "honors", "honours")),
    ("music", ("music", "soundtrack", "score")),
    ("release", ("release", "marketing", "distribution", "home media", "premiere", "promotion")),
    ("themes", ("theme", "analysis", "interpretation", "style", "symbolism", "motif")),
    ("legacy", ("legacy", "impact", "influence", "popular culture", "sequel", "franchise", "remake")),
    ("production", ("production", "development", "writing", "casting", "filming", "photography",
                    "design", "effects", "pre-production", "post-production", "background",
                    "conception", "screenplay", "shooting", "location")),
]
SKIP = {"abstract", "plot", "cast", "references", "external links", "see also", "notes",
        "further reading", "bibliography", "sources", "footnotes", "citations"}


def kind_of(heading):
    h = heading.lower().strip()
    if h in SKIP or h in wc.PLOT_KEYS:
        return None
    for kind, words in RULES:
        if any(w in h for w in words):
            return kind
    return None


def cmd_build(a):
    wiki = sqlite3.connect(f"file:{a.wiki_db}?mode=ro", uri=True)
    docs = {str(d): f for d, f in wiki.execute("select doc_id, film_id from pages")}
    print(f"linked pages in wiki.db: {len(docs):,}")
    conn = F.connect(a.facts)
    conn.execute("delete from sections")
    conn.execute("delete from sec_vec")
    conn.commit()            # don't hold the write lock while reading the corpus (other builders wait on it)
    rows, seen, kinds = [], set(), {}
    t0 = time.time()
    for doc in wc.open_corpus(a.corpus):
        did = str(doc.get("doc_id"))
        if did not in docs or did in seen:
            continue
        seen.add(did)
        fid = docs[did]
        secs = doc.get("sections") or {}
        heads = set(secs)
        url = f"https://en.wikipedia.org/?curid={did}"
        for heading, raw in secs.items():
            k = kind_of(heading)
            if not k:
                continue
            text = wc.clean_section(raw, heads)
            if len(text.split()) < 25:
                continue
            parts = wc.chunk_text(text, a.target_words, a.max_words)
            for i, part in enumerate(parts, 1):
                rows.append((fid, did, heading, k, i, len(parts), part, len(part.split()), url, wc.SNAPSHOT))
                kinds[k] = kinds.get(k, 0) + 1
        if len(rows) > 20000:
            conn.executemany("insert into sections(tmdb_id, doc_id, section_key, kind, position, n_parts, "
                             "text, n_words, url, snapshot) values (?,?,?,?,?,?,?,?,?,?)", rows)
            conn.commit()
            rows = []
            print(f"  {len(seen):,} pages, {time.time() - t0:.0f}s", flush=True)
    if rows:
        conn.executemany("insert into sections(tmdb_id, doc_id, section_key, kind, position, n_parts, "
                         "text, n_words, url, snapshot) values (?,?,?,?,?,?,?,?,?,?)", rows)
    F.set_meta(conn, "sections_built_at", F.now())
    conn.commit()
    print(f"pages read: {len(seen):,} of {len(docs):,}")
    for k, n in sorted(kinds.items(), key=lambda x: -x[1]):
        print(f"  {k:12s} {n:,} passages")


def embed(url, model, texts):
    body = json.dumps({"model": model, "input": texts, "truncate": True}).encode()
    for attempt in range(5):
        try:
            req = urllib.request.Request(url + "/api/embed", body, {"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=300) as r:
                return json.load(r)["embeddings"]
        except (urllib.error.URLError, TimeoutError) as e:
            print(f"  embed retry {attempt + 1}: {e}", file=sys.stderr)
            time.sleep(5 * (attempt + 1))
    raise SystemExit("Ollama embedding kept failing")


def cmd_embed(a):
    import numpy as np
    conn = F.connect(a.facts)
    title = {}
    try:
        mconn = sqlite3.connect(f"file:{a.movies}?mode=ro", uri=True)
        title = dict(mconn.execute("select id, title from movies"))
    except sqlite3.Error:
        pass
    done = {r[0] for r in conn.execute("select sec_id from sec_vec")}
    rows = conn.execute("select s.sec_id, s.tmdb_id, s.kind, s.section_key, s.position, s.n_parts, s.text "
                        "from sections s left join films f on f.tmdb_id = s.tmdb_id "
                        "order by coalesce(f.vote_count, 0) desc, s.sec_id").fetchall()
    todo = [r for r in rows if r[0] not in done]
    print(f"sections: {len(rows):,} | embedded: {len(rows) - len(todo):,} | to do: {len(todo):,}")
    t0, n = time.time(), 0
    for s in range(0, len(todo), a.batch):
        part = todo[s:s + a.batch]
        texts = [wc.embed_header(title.get(r[1], ""), r[3], r[4], r[5]) + r[6] for r in part]
        vecs = embed(a.ollama, a.model, texts)
        conn.executemany("insert or replace into sec_vec values (?, ?)",
                         [(r[0], np.asarray(v, dtype=np.float32).tobytes()) for r, v in zip(part, vecs)])
        conn.commit()
        n += len(part)
        if (s // a.batch) % 50 == 0 or n == len(todo):
            rate = n / max(time.time() - t0, 1e-6)
            print(f"  {n:,}/{len(todo):,} {rate:.0f}/s eta {(len(todo) - n) / max(rate, 1e-6) / 60:.1f} min",
                  flush=True)
    F.set_meta(conn, "sections_embedder", a.model)
    F.set_meta(conn, "sections_embedded_at", F.now())
    conn.commit()


# ------------------------------------------------------------------ infobox money
_NUM = r"(\d{1,3}(?:[,\d]{0,12})(?:\.\d+)?)"
_MONEY = re.compile(r"(US\$|\$|£|€|₹|¥|CA\$|A\$|HK\$|NZ\$|RMB|CN¥)\s?" + _NUM +
                    r"\s*(?:[-–—]|to)?\s*(?:\$?\s?" + _NUM + r")?\s*(million|billion|crore|lakh|m\b|bn\b)?", re.I)
CUR = {"us$": "USD", "$": "USD", "£": "GBP", "€": "EUR", "₹": "INR", "¥": "JPY", "ca$": "CAD",
       "a$": "AUD", "hk$": "HKD", "nz$": "NZD", "rmb": "CNY", "cn¥": "CNY"}
SCALE = {"million": 1e6, "m": 1e6, "billion": 1e9, "bn": 1e9, "crore": 1e7, "lakh": 1e5, None: 1}


def parse_money(text):
    """First money amount in an infobox value -> (value, currency, is_range) or None.
    '$185 million' -> 185e6 USD; '$30–35 million' -> 30e6 (low end), is_range;
    '₹50 crore' -> 5e8 INR. Footnote markers and wiki markup are ignored."""
    if not text:
        return None
    t = re.sub(r"\[[^\]]*\]|\{\{|\}\}|\|", " ", str(text)).replace("\xa0", " ")
    m = _MONEY.search(t)
    if not m:
        return None
    cur = CUR.get(m.group(1).lower(), m.group(1))
    try:
        v = float(m.group(2).replace(",", ""))
    except ValueError:
        return None
    scale = (m.group(4) or "").lower() or None
    v *= SCALE.get(scale, 1)
    if v < 1000:          # "$5" with no scale word is never a film budget
        return None
    return v, cur, bool(m.group(3))


def cmd_infobox(a):
    wiki = sqlite3.connect(f"file:{a.wiki_db}?mode=ro", uri=True)
    conn = F.connect(a.facts)
    conn.execute("delete from facts where source = 'wikipedia_infobox'")
    ts = F.now()
    n = {"budget": 0, "box_office": 0}
    rows = []
    for did, fid, box, url in wiki.execute("select doc_id, film_id, infobox_json, url from pages"):
        try:
            box = json.loads(box or "{}")
        except ValueError:
            continue
        low = {str(k).lower().strip(): v for k, v in box.items()}
        for field, keys in (("budget", ("budget",)), ("box_office", ("gross", "box office", "box_office"))):
            raw = next((low[k] for k in keys if low.get(k)), None)
            p = parse_money(raw)
            if p:
                v, cur, rng = p
                rows.append((fid, field, v, str(raw)[:200], cur, "wikipedia_infobox", url, wc.SNAPSHOT, ts,
                             F.RELIABILITY["wikipedia_infobox"] - (0.1 if rng else 0)))
                n[field] += 1
    conn.executemany("insert into facts values (?,?,?,?,?,?,?,?,?,?)", rows)
    F.set_meta(conn, "infobox_parsed_at", ts)
    conn.commit()
    print(f"infobox money: {n}")


def cmd_show(a):
    conn = F.connect(a.facts, readonly=True)
    for fid, t, y in conn.execute("select tmdb_id, title, year from films where title like ? "
                                  "order by vote_count desc limit 1", (a.title,)):
        print(f"{t} ({y}) tmdb {fid}")
        for k, n in conn.execute("select kind, count(*) from sections where tmdb_id = ? group by kind", (fid,)):
            print(f"  {k}: {n} passages")
        for row in conn.execute("select field, value_num, currency, source, value_text from facts "
                                "where tmdb_id = ?", (fid,)):
            print("  fact", row)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["build", "embed", "infobox", "show"])
    ap.add_argument("title", nargs="?")
    ap.add_argument("--corpus")
    ap.add_argument("--wiki-db", default="wiki.db")
    ap.add_argument("--movies", default="movies.db")
    ap.add_argument("--facts")
    ap.add_argument("--model", default="mxbai-embed-large")
    ap.add_argument("--ollama", default="http://localhost:11434")
    ap.add_argument("--batch", type=int, default=32)
    ap.add_argument("--target-words", type=int, default=180)
    ap.add_argument("--max-words", type=int, default=260)
    a = ap.parse_args()
    if a.cmd == "build" and not a.corpus:
        sys.exit("--corpus path/to/corpus.jsonl.zip")
    t0 = time.time()
    {"build": cmd_build, "embed": cmd_embed, "infobox": cmd_infobox, "show": cmd_show}[a.cmd](a)
    print(f"took {(time.time() - t0) / 60:.1f} min")


if __name__ == "__main__":
    main()
