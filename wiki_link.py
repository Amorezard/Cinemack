"""Step 2: link every film in movies.db to its Wikipedia page in the TREC corpus. Writes wiki.db.

python wiki_link.py --movies movies.db --corpus /path/corpus.jsonl.zip \
    --imdb-map /path/wikipage_id_to_imdb.json.zip --out wiki.db

Match order (method, confidence):
  1. wikidata id already in movies.db          (db_wikidata, 1.0)
  2. Wikidata SPARQL: TMDB id P4947 -> item    (sparql_tmdb, 1.0)
  3. IMDb id (movies.db or Wikidata P345) via the TREC page->IMDb map  (imdb, 0.95)
  4. same normalized title and year within 1, only if unique  (title_year, 0.6)
movies.db is opened read-only.
"""
import argparse
import collections
import json
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

import wiki_common as wc

SPARQL = "https://query.wikidata.org/sparql"
UA = os.environ.get("WIKI_UA", "MovieBuff/2.0 (Merrimack College student research project)")


def sparql_batch(tmdb_ids):
    values = " ".join(f'"{t}"' for t in tmdb_ids)
    q = ("SELECT ?tmdb ?item ?imdb WHERE { VALUES ?tmdb { %s } "
         "?item wdt:P4947 ?tmdb . OPTIONAL { ?item wdt:P345 ?imdb . } }" % values)
    data = urllib.parse.urlencode({"query": q, "format": "json"}).encode()
    for attempt in range(6):
        try:
            req = urllib.request.Request(SPARQL, data, {"User-Agent": UA,
                                                        "Accept": "application/sparql-results+json"})
            with urllib.request.urlopen(req, timeout=90) as r:
                rows = json.load(r)["results"]["bindings"]
            out = {}
            for b in rows:
                t = b["tmdb"]["value"]
                qid = b["item"]["value"].rsplit("/", 1)[-1]
                rec = out.setdefault(t, {"qid": qid, "imdb": []})
                if "imdb" in b and b["imdb"]["value"] not in rec["imdb"]:
                    rec["imdb"].append(b["imdb"]["value"])
            return out
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            wait = 5 * (attempt + 1)
            print(f"  sparql retry in {wait}s: {e}", file=sys.stderr)
            time.sleep(wait)
    raise SystemExit("Wikidata SPARQL kept failing; rerun later or use --no-sparql")


def load_imdb_map(path):
    """TREC file: Wikipedia doc_id -> IMDb id(s). Returns imdb -> doc_id."""
    if not path:
        return {}
    if path.endswith(".zip"):
        import zipfile
        zf = zipfile.ZipFile(path)
        name = next(n for n in zf.namelist() if n.endswith(".json"))
        raw = json.load(zf.open(name))
    else:
        raw = json.load(open(path, encoding="utf-8"))
    out = {}
    items = raw.items() if isinstance(raw, dict) else (
        (r.get("doc_id") or r.get("wikipedia_id"), r.get("imdb") or r.get("imdb_id")) for r in raw)
    for doc_id, imdbs in items:
        if str(doc_id).startswith("tt"):          # file keyed the other way round: imdb -> doc_id
            doc_id, imdbs = imdbs, doc_id
            if isinstance(doc_id, list):
                doc_id = doc_id[0] if doc_id else ""
        if isinstance(imdbs, str):
            imdbs = [imdbs]
        for i in imdbs or []:
            i = str(i).strip()
            if i.startswith("tt"):
                out.setdefault(i, str(doc_id))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--movies", default="movies.db")
    ap.add_argument("--corpus", required=True)
    ap.add_argument("--imdb-map", help="wikipage_id_to_imdb.json(.zip) from the TREC 2023 site")
    ap.add_argument("--out", default="wiki.db")
    ap.add_argument("--table")
    ap.add_argument("--col", action="append")
    ap.add_argument("--no-sparql", action="store_true")
    ap.add_argument("--sparql-cache", default="results/wiki_sparql_cache.json")
    ap.add_argument("--batch", type=int, default=200)
    a = ap.parse_args()

    table, mapping, movies = wc.load_movies(a.movies, a.table, wc.parse_overrides(a.col))
    print(f"movies: {len(movies)} from {table}")

    print("scanning corpus ...")
    t0 = time.time()
    pages = {}                       # doc_id -> (title, qid, year, is_film, has_plot)
    by_qid, by_title = {}, collections.defaultdict(list)
    for i, doc in enumerate(wc.open_corpus(a.corpus), 1):
        did = str(doc.get("doc_id"))
        qid = doc.get("wikidata_id") or ""
        film = wc.is_film(doc)
        pages[did] = (doc.get("page_title", ""), qid, wc.doc_year(doc), film, wc.has_plot(doc))
        if qid:
            by_qid[qid] = did
        if film:
            by_title[wc.norm_title(doc.get("page_title", ""))].append(did)
        if i % 50000 == 0:
            print(f"  {i} pages, {time.time() - t0:.0f}s")
    print(f"corpus pages: {len(pages)}")

    imdb_to_doc = load_imdb_map(a.imdb_map)
    print(f"imdb map entries: {len(imdb_to_doc)}")

    cache = {}
    if not a.no_sparql:
        os.makedirs(os.path.dirname(a.sparql_cache) or ".", exist_ok=True)
        if os.path.exists(a.sparql_cache):
            cache = json.load(open(a.sparql_cache))
        todo = [str(f) for f, m in movies.items() if not m["wikidata"] and str(f) not in cache]
        print(f"SPARQL lookups needed: {len(todo)} (cached: {len(cache)})")
        for s in range(0, len(todo), a.batch):
            part = todo[s:s + a.batch]
            got = sparql_batch(part)
            for t in part:
                cache[t] = got.get(t)          # None = not on Wikidata; don't ask again
            json.dump(cache, open(a.sparql_cache, "w"))
            print(f"  sparql {min(s + a.batch, len(todo))}/{len(todo)}")
            time.sleep(1.0)

    links, methods = {}, collections.Counter()
    for fid, m in movies.items():
        sp = cache.get(str(fid)) or {}
        found = None
        if m["wikidata"] and m["wikidata"] in by_qid:
            found = (by_qid[m["wikidata"]], "db_wikidata", 1.0)
        elif sp.get("qid") in by_qid:
            found = (by_qid[sp["qid"]], "sparql_tmdb", 1.0)
        else:
            for imdb in [m["imdb"]] + sp.get("imdb", []):
                if imdb and imdb in imdb_to_doc and imdb_to_doc[imdb] in pages:
                    found = (imdb_to_doc[imdb], "imdb", 0.95)
                    break
        if not found and m["year"]:
            cands = set()
            for t in {m["title"], m["original_title"]}:
                for did in by_title.get(wc.norm_title(t), []):
                    y = pages[did][2]
                    if y and abs(y - m["year"]) <= 1:
                        cands.add(did)
            if len(cands) == 1:
                found = (cands.pop(), "title_year", 0.6)
        if found:
            links[fid] = found

    # one page per film: if two films claim a page keep the stronger match, then the more voted film
    owner = {}
    for fid, (did, meth, conf) in links.items():
        cur = owner.get(did)
        key = (conf, movies[fid]["votes"])
        if cur is None or key > (links[cur][2], movies[cur]["votes"]):
            owner[did] = fid
    dropped = [fid for fid, (did, _, _) in links.items() if owner[did] != fid]
    for fid in dropped:
        del links[fid]
    for did, meth, conf in links.values():
        methods[meth] += 1

    out = sqlite3.connect(a.out)
    out.executescript("""
    drop table if exists links;
    create table links(film_id primary key, doc_id text unique, wiki_title text, wikidata_id text,
                       method text, conf real, has_plot integer);
    drop table if exists wiki_only;
    create table wiki_only(doc_id text primary key, title text, wikidata_id text, year integer, has_plot integer);
    """)
    out.executemany("insert into links values (?,?,?,?,?,?,?)",
                    [(fid, did, pages[did][0], pages[did][1], meth, conf, int(pages[did][4]))
                     for fid, (did, meth, conf) in links.items()])
    linked_docs = {did for did, _, _ in links.values()}
    out.executemany("insert into wiki_only values (?,?,?,?,?)",
                    [(did, p[0], p[1], p[2], int(p[4])) for did, p in pages.items()
                     if p[3] and did not in linked_docs])
    out.commit()

    n = len(movies)
    with_plot = sum(1 for did, _, _ in links.values() if pages[did][4])
    print("\n== match report")
    print(f"linked: {len(links)}/{n} ({100 * len(links) / n:.1f}%), with a plot section: {with_plot}")
    for meth, c in methods.most_common():
        print(f"  {meth:12s} {c}")
    print(f"conflicts dropped: {len(dropped)}")
    unl = sorted((m for f, m in movies.items() if f not in links), key=lambda m: -m["votes"])[:15]
    print("most-voted unlinked films:", [(m["title"], m["year"]) for m in unl])
    wo = out.execute("select count(*), sum(has_plot) from wiki_only").fetchone()
    print(f"Wikipedia-only film pages: {wo[0]} ({wo[1]} with plot) -> table wiki_only")
    print(f"wrote {a.out}")


if __name__ == "__main__":
    main()
