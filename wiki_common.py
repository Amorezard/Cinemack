"""Shared helpers for the Movie Buff v2 Wikipedia pipeline.

Used by wiki_inspect.py, wiki_link.py, wiki_chunks.py, wiki_embed.py, wiki_try.py.
Nothing here writes to movies.db; all new data goes to a separate wiki.db.
"""
import gzip
import html
import hashlib
import io
import json
import math
import re
import sqlite3
import zipfile

# Column names we look for in movies.db. First match wins; override with --col key=name.
CANDIDATES = {
    "id": ["tmdb_id", "movie_id", "id"],
    "title": ["title", "name"],
    "original_title": ["original_title"],
    "year": ["year", "release_year"],
    "release_date": ["release_date", "released", "date"],
    "genres": ["genres", "genre_names", "genre"],
    "language": ["original_language", "language", "lang"],
    "votes": ["vote_count", "votes", "num_votes"],
    "imdb": ["imdb_id", "imdb"],
    "wikidata": ["wikidata_id", "wikidata"],
}

# Wikipedia section keys (the TREC corpus lowercases them), in order of preference.
PLOT_KEYS = ["plot", "plot summary", "synopsis", "plot synopsis", "premise",
             "summary", "story", "storyline", "overview"]
EXTRA_KEYS = ["cast", "production", "release", "reception", "themes",
              "soundtrack", "music", "legacy"]
SNAPSHOT = "2023-01-01"


# ---------------------------------------------------------------- movies.db
def parse_overrides(pairs):
    out = {}
    for p in pairs or []:
        if "=" not in p:
            raise SystemExit(f"--col expects key=column, got {p!r}")
        k, v = p.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def list_plain_tables(con):
    rows = con.execute(
        "select name, coalesce(sql,'') from sqlite_master where type='table'").fetchall()
    return [n for n, sql in rows if not sql.upper().startswith("CREATE VIRTUAL")]


def table_columns(con, table):
    return [r[1] for r in con.execute(f"pragma table_info('{table}')")]


def find_movies_table(con, table=None):
    if table:
        return table
    best = None
    for name in list_plain_tables(con):
        try:
            cols = table_columns(con, name)
        except sqlite3.Error:
            continue
        if "title" not in cols:
            continue
        n = con.execute(f"select count(*) from '{name}'").fetchone()[0]
        if best is None or n > best[1]:
            best = (name, n)
    if not best:
        raise SystemExit("No table with a 'title' column found in movies.db; pass --table")
    return best[0]


def map_columns(con, table, overrides=None):
    cols = table_columns(con, table)
    lower = {c.lower(): c for c in cols}
    mapping = {}
    for key, names in CANDIDATES.items():
        if overrides and key in overrides:
            mapping[key] = overrides[key]
            continue
        mapping[key] = next((lower[n] for n in names if n in lower), None)
    if not mapping["id"] or not mapping["title"]:
        raise SystemExit(f"Could not find id/title columns in {table}: {cols}. Use --col id=... --col title=...")
    return mapping


def parse_genres(val):
    if val is None:
        return []
    if isinstance(val, (list, tuple)):
        items = val
    else:
        s = str(val).strip()
        if not s:
            return []
        if s[0] in "[{":
            try:
                items = json.loads(s)
            except json.JSONDecodeError:
                items = re.split(r"[,|]", s.strip("[]"))
        else:
            items = re.split(r"[,|]", s)
    out = []
    for g in items:
        if isinstance(g, dict):
            g = g.get("name", "")
        g = str(g).strip().strip("'\"")
        if g:
            out.append(g)
    return out


def parse_year(year_val, date_val):
    for v in (year_val, date_val):
        if v is None:
            continue
        m = re.search(r"(18|19|20)\d{2}", str(v))
        if m:
            return int(m.group(0))
    return 0


def load_movies(db_path, table=None, overrides=None):
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    table = find_movies_table(con, table)
    m = map_columns(con, table, overrides)
    wanted = [k for k, c in m.items() if c]
    sql = "select " + ", ".join(f'"{m[k]}"' for k in wanted) + f' from "{table}"'
    movies = {}
    for row in con.execute(sql):
        r = dict(zip(wanted, row))
        fid = r["id"]
        try:
            fid = int(fid)
        except (TypeError, ValueError):
            pass
        imdb = str(r.get("imdb") or "").strip()
        wd = str(r.get("wikidata") or "").strip()
        movies[fid] = {
            "id": fid,
            "title": r.get("title") or "",
            "original_title": r.get("original_title") or "",
            "year": parse_year(r.get("year"), r.get("release_date")),
            "genres": parse_genres(r.get("genres")),
            "language": (r.get("language") or "").strip().lower(),
            "votes": float(r.get("votes") or 0),
            "imdb": imdb if imdb.startswith("tt") else "",
            "wikidata": wd if wd.startswith("Q") else "",
        }
    con.close()
    return table, m, movies


# ---------------------------------------------------------------- corpus
def open_corpus(path):
    """Yield one dict per line from corpus.jsonl, .jsonl.gz or .zip."""
    if path.endswith(".zip"):
        zf = zipfile.ZipFile(path)
        member = next((n for n in zf.namelist() if n.endswith(".jsonl")), None)
        if member is None:
            raise SystemExit(f"No .jsonl inside {path}: {zf.namelist()[:10]}")
        fh = io.TextIOWrapper(zf.open(member), encoding="utf-8")
    elif path.endswith(".gz"):
        fh = gzip.open(path, "rt", encoding="utf-8")
    else:
        fh = open(path, encoding="utf-8")
    with fh:
        for line in fh:
            line = line.strip()
            if line:
                yield json.loads(line)


def class_names(doc):
    out = []
    for c in doc.get("wikidata_classes") or []:
        if isinstance(c, (list, tuple)) and len(c) > 1:
            out.append(str(c[1]).lower())
        elif isinstance(c, str):
            out.append(c.lower())
    return out


def is_film(doc):
    return any("film" in c for c in class_names(doc))


def film_infobox(doc):
    for box in doc.get("infoboxes") or []:
        if "film" in str(box.get("name", "")).lower():
            return box.get("params") or {}
    boxes = doc.get("infoboxes") or []
    return (boxes[0].get("params") or {}) if boxes else {}


def doc_year(doc):
    box = film_infobox(doc)
    for key in ("released", "release_date", "release"):
        m = re.search(r"(18|19|20)\d{2}", str(box.get(key, "")))
        if m:
            return int(m.group(0))
    secs = doc.get("sections") or {}
    m = re.search(r"\b(18|19|20)\d{2}\b", clean_section(secs.get("abstract", ""), set(secs))[:400])
    return int(m.group(0)) if m else 0


_HEADING = re.compile(r"^[^.!?]{1,60}\.$")


def clean_section(text, headings=None):
    """Drop the 'Plot.' style heading lines the corpus puts inside section text.

    headings: the page's section names (the corpus lowercases them as the keys of
    `sections`); a line is dropped only when it IS one of them, so a short real
    sentence ("He dies.") is kept. Without headings, only a first line of <= 3
    words that looks like a heading is dropped."""
    if not text:
        return ""
    heads = {h.strip().lower() for h in headings} if headings else None
    lines = []
    for i, ln in enumerate(str(text).split("\n")):
        s = ln.strip()
        if not s:
            continue
        if heads is not None:
            if s.rstrip(".").strip().lower() in heads:
                continue
        elif i == 0 and _HEADING.match(s) and len(s.split()) <= 3:
            continue
        lines.append(s)
    return html.unescape(re.sub(r"\s+", " ", " ".join(lines)).strip())   # "&amp;" -> "&"


def pick_plot(sections):
    heads = set(sections)
    for key in PLOT_KEYS:
        txt = clean_section(sections.get(key, ""), heads)
        if len(txt.split()) >= 30:
            return key, txt
    return None, ""


def has_plot(doc):
    return pick_plot(doc.get("sections") or {})[0] is not None


# ---------------------------------------------------------------- titles and chunking
def norm_title(t):
    t = re.sub(r"\s*\([^)]*\)\s*$", "", str(t or ""))  # drop "(1999 film)"
    t = t.lower().replace("&", "and")
    t = re.sub(r"[^a-z0-9 ]+", " ", t)
    t = re.sub(r"^(the|a|an) ", "", t.strip())
    return re.sub(r"\s+", " ", t).strip()


_SENT = re.compile(r"(?<=[.!?])[\"')\]]?\s+(?=[A-Z0-9\"'(\[])")


def split_sentences(text):
    return [s.strip() for s in _SENT.split(text) if s.strip()]


def chunk_text(text, target_words=180, max_words=260, overlap=1):
    """Greedy sentence packing to ~target_words, one sentence of overlap between chunks."""
    sents = split_sentences(text)
    if not sents:
        return []
    chunks, cur = [], []
    for s in sents:
        cur_words = sum(len(x.split()) for x in cur)
        if cur and cur_words + len(s.split()) > max_words or cur_words >= target_words:
            chunks.append(" ".join(cur))
            cur = cur[-overlap:] if overlap else []
        cur.append(s)
    if cur:
        tail = " ".join(cur)
        # fold a tiny last piece into the previous chunk
        if chunks and len(tail.split()) < 40:
            prev_sents = split_sentences(chunks[-1])
            extra = [x for x in cur if x not in prev_sents]
            chunks[-1] = " ".join([chunks[-1]] + extra)
        elif not chunks or tail != chunks[-1]:
            chunks.append(tail)
    return chunks


def embed_header(title, section, position, n_parts):
    if n_parts > 1:
        return f"{title} | {section.title()}, part {position} of {n_parts}: "
    return f"{title} | {section.title()}: "


def popularity(votes):
    return round(math.log1p(max(votes, 0)), 4)


def hashlib_sha1(text):
    return hashlib.sha1(text.encode()).hexdigest()
