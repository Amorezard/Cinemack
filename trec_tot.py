"""
Change 2, part A: the TREC 2023 Tip-of-the-Tongue benchmark as Movie Buff test
questions.

Real people describing films they can't name, from iRememberThisMovie.com.
The 2023 track released answers for 150 train and 150 dev questions; the 150
test answers were never made public, so only train and dev are usable.

    cd ~/movie-buff/rag && source ~/venvs/movie-rag/bin/activate
    python trec_tot.py                 # download, match to movies.db, report coverage
    python trec_tot.py --from-dir DIR  # use files you downloaded yourself

Steps:
  1. Downloads the train and dev files into data/trec_tot/ (skipped if there).
  2. Reads each question and its answer (Wikipedia page, Wikidata ID, IMDb link).
  3. Finds each answer's TMDB ID: first from its IMDb link through your local
     Kaggle TMDB csv (no network), then from Wikidata's TMDB ID (P4947) via the
     regular Wikidata API, then Wikidata's IMDb ID (P345) through the csv again.
  4. Looks the TMDB ID up in movies.db (movies.id is the TMDB ID).
  5. Writes trec_tot_2023.json in the test_set.json format, one entry per
     question whose film is in movies.db, plus trec_tot_2023_missing.json for
     the rest. The share found is your coverage number.

Every question gets a stable "bucket" (0-99, from its TMDB ID), so any later
split into tune / check / final keeps each film in one set.
"""

import argparse
import csv
import glob
import hashlib
import io
import json
import os
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile

SPLITS = {
    "train": "https://surfdrive.surf.nl/files/index.php/s/KJFwx32qMT7Tinh/download",
    "dev":   "https://surfdrive.surf.nl/files/index.php/s/F1mUvAW06gn7OdP/download",
}
DATA_DIR = "data/trec_tot"
OUT = "trec_tot_2023.json"
MISSING = "trec_tot_2023_missing.json"
UA = "MovieBuff-student-research/0.1 (Merrimack College; python urllib)"
WIKIDATA_API = "https://www.wikidata.org/w/api.php"
WIKI_API = "https://en.wikipedia.org/w/api.php"


# ------------------------------------------------------------------ download

def http_get(url, params=None, accept=None, tries=3):
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": UA, **({"Accept": accept} if accept else {})})
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if attempt == tries - 1:
                raise
            wait = int(e.headers.get("Retry-After") or 0) if e.code == 429 else 0
            wait = min(max(wait, 10 * (attempt + 1)), 120)
            print(f"    HTTP {e.code}, waiting {wait}s and retrying")
            time.sleep(wait)
        except Exception as e:
            if attempt == tries - 1:
                raise
            print(f"    retrying after: {e}")
            time.sleep(5 * (attempt + 1))


def download(split, url):
    """Fetch one split into data/trec_tot/<split>/ and unzip it if it's a zip."""
    folder = os.path.join(DATA_DIR, split)
    if glob.glob(os.path.join(folder, "**", "*.json*"), recursive=True):
        print(f"  {split}: already downloaded")
        return folder
    os.makedirs(folder, exist_ok=True)
    print(f"  {split}: downloading ...")
    blob = http_get(url)
    if zipfile.is_zipfile(io.BytesIO(blob)):
        zipfile.ZipFile(io.BytesIO(blob)).extractall(folder)
    else:
        with open(os.path.join(folder, "queries.jsonl"), "wb") as f:
            f.write(blob)
    print(f"  {split}: {len(blob) / 1e6:.1f} MB, files: "
          f"{[os.path.relpath(p, folder) for p in glob.glob(os.path.join(folder, '**', '*'), recursive=True) if os.path.isfile(p)]}")
    return folder


# ------------------------------------------------------------------- parsing

def read_split(folder):
    """Questions from the split's .jsonl file; answers from their own fields,
    or from a qrels file if the question lacks them."""
    files = [p for p in glob.glob(os.path.join(folder, "**", "*"), recursive=True)
             if os.path.isfile(p) and not os.path.basename(p).startswith("._")]
    qfiles = [p for p in files if p.endswith((".jsonl", ".json")) and "corpus" not in p.lower()]
    if not qfiles:
        sys.exit(f"No .jsonl question file found in {folder}. Files: {files}")
    questions = []
    for p in qfiles:
        with open(p, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    questions.append(json.loads(line))
    qrels = {}
    for p in files:
        if "qrel" in os.path.basename(p).lower():
            for line in open(p, encoding="utf-8"):
                parts = line.split()
                if len(parts) >= 4 and parts[3] != "0":
                    qrels[parts[0]] = parts[2]          # query id -> Wikipedia page id
    return questions, qrels


def qid_of(q):
    return str(q.get("id") or q.get("query_id"))


def imdb_of(q):
    m = re.search(r"(tt\d{5,9})", str(q.get("imdb_url") or ""))
    return m.group(1) if m else None


# -------------------------------------------------------------- id matching

def chunks(xs, n):
    xs = list(xs)
    for i in range(0, len(xs), n):
        yield xs[i:i + n]


def kaggle_csv():
    """The Kaggle TMDB file build_db.py uses; it has an imdb_id column."""
    for pattern in ("TMDB_movie_dataset_v*.csv", "TMDB_all_movies*.csv"):
        for folder in (".", "..", os.path.expanduser("~/movie-buff")):
            found = sorted(glob.glob(os.path.join(folder, pattern)))
            if found:
                return found[-1]
    return None


def tmdb_from_kaggle(imdb_ids):
    """IMDb ID -> TMDB ID from the local Kaggle file. No network."""
    path = kaggle_csv()
    if not path or not imdb_ids:
        if not path:
            print("   (no Kaggle TMDB csv found next to movies.db - skipping the IMDb route)")
        return {}
    csv.field_size_limit(10 ** 8)
    out = {}
    with open(path, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        if "imdb_id" not in (reader.fieldnames or []):
            print(f"   ({os.path.basename(path)} has no imdb_id column - skipping the IMDb route)")
            return {}
        for row in reader:
            i = (row.get("imdb_id") or "").strip()
            if i in imdb_ids and i not in out:
                try:
                    out[i] = int(row["id"])
                except (TypeError, ValueError):
                    pass
    return out


def wikidata_claims(qids):
    """Wikidata item -> (TMDB ID, IMDb ID) through the regular Wikidata API
    (wbgetentities, 50 items per request) - not the query service, which was
    rate-limited to 1 request/minute during its Sep 2026 outage."""
    out = {}
    for batch in chunks(sorted(qids), 50):
        raw = http_get(WIKIDATA_API, {"action": "wbgetentities", "ids": "|".join(batch),
                                      "props": "claims", "format": "json"})
        for qid, ent in json.loads(raw).get("entities", {}).items():
            claims = ent.get("claims", {})

            def first(prop):
                for c in claims.get(prop, []):
                    v = c.get("mainsnak", {}).get("datavalue", {}).get("value")
                    if v:
                        return str(v)
                return None
            tmdb = first("P4947")
            out[qid] = (int(tmdb) if tmdb and tmdb.isdigit() else None, first("P345"))
        time.sleep(1)
    return out


def wikidata_from_pageids(pageids):
    out = {}
    for batch in chunks(sorted(pageids), 50):
        raw = http_get(WIKI_API, {"action": "query", "pageids": "|".join(batch),
                                  "prop": "pageprops", "ppprop": "wikibase_item",
                                  "format": "json"})
        for pid, page in json.loads(raw).get("query", {}).get("pages", {}).items():
            item = page.get("pageprops", {}).get("wikibase_item")
            if item:
                out[pid] = item
        time.sleep(1)
    return out


def bucket(tmdb_id):
    return int(hashlib.sha1(str(tmdb_id).encode()).hexdigest(), 16) % 100


# ---------------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from-dir", help="folder holding train/ and dev/ you downloaded yourself")
    ap.add_argument("--db", default="movies.db")
    args = ap.parse_args()

    print("1. Files")
    folders = {}
    for split, url in SPLITS.items():
        if args.from_dir:
            folders[split] = os.path.join(args.from_dir, split)
        else:
            folders[split] = download(split, url)

    print("2. Questions")
    rows = []
    for split, folder in folders.items():
        qs, qrels = read_split(folder)
        for q in qs:
            qid = qid_of(q)
            rows.append({"split": split, "qid": qid, "text": (q.get("text") or "").strip(),
                         "post_title": q.get("title"), "wikidata": q.get("wikidata_id"),
                         "imdb": imdb_of(q),
                         "wikipedia_id": str(q.get("wikipedia_id") or qrels.get(qid) or "") or None,
                         "wikipedia_url": q.get("wikipedia_url")})
        print(f"   {split}: {len(qs)} questions, {sum(1 for r in rows if r['split'] == split and (r['wikidata'] or r['wikipedia_id']))} with an answer")

    print("3. Answers -> TMDB IDs")
    for r in rows:
        r["tmdb"], r["how"] = None, None

    def via_kaggle(label):
        want = {r["imdb"] for r in rows if not r["tmdb"] and r["imdb"]}
        found = tmdb_from_kaggle(want)
        for r in rows:
            if not r["tmdb"] and r["imdb"] in found:
                r["tmdb"], r["how"] = found[r["imdb"]], label
        print(f"   {label}: {len(found)}/{len(want)}")

    via_kaggle("IMDb link -> Kaggle csv")                    # no network

    left = [r for r in rows if not r["tmdb"]]
    need_wd = {r["wikipedia_id"] for r in left if not r["wikidata"] and r["wikipedia_id"]}
    if need_wd:
        found = wikidata_from_pageids(need_wd)
        for r in left:
            if not r["wikidata"] and r["wikipedia_id"] in found:
                r["wikidata"] = found[r["wikipedia_id"]]
        print(f"   Wikipedia page id -> Wikidata: {len(found)}/{len(need_wd)}")
    want = {r["wikidata"] for r in left if r["wikidata"]}
    if want:
        claims = wikidata_claims(want)
        n = 0
        for r in left:
            tmdb, imdb = claims.get(r["wikidata"], (None, None))
            if tmdb:
                r["tmdb"], r["how"] = tmdb, "Wikidata P4947"
                n += 1
            elif imdb and not r["imdb"]:
                r["imdb"] = imdb
        print(f"   Wikidata TMDB ID (P4947): {n}/{len(want)}")
        via_kaggle("Wikidata IMDb ID -> Kaggle csv")
    print(f"   TMDB ID found for {sum(1 for r in rows if r['tmdb'])}/{len(rows)}")

    print("4. In movies.db?")
    conn = sqlite3.connect(args.db)
    kept, missing = [], []
    for r in rows:
        film = conn.execute("SELECT title, release_year, vote_count FROM movies WHERE id = ?",
                            (r["tmdb"],)).fetchone() if r["tmdb"] else None
        if not film:
            r["why_missing"] = "no TMDB id on Wikidata" if not r["tmdb"] else "TMDB film not in movies.db"
            missing.append(r)
            continue
        title, year, votes = film
        kept.append({
            "id": f"trec-{r['split']}-{r['qid']}",
            "source": "trec", "split": r["split"], "category": "identify",
            "turns": [r["text"]],
            "expect_retrieved": [title], "expect_year": (year or "")[:4] or None,
            "expect_tmdb": r["tmdb"], "vote_count": votes, "bucket": bucket(r["tmdb"]),
            "wikidata": r["wikidata"], "wikipedia_url": r["wikipedia_url"],
            "matched_by": r["how"],
        })

    json.dump({"questions": kept}, open(OUT, "w"), indent=1, ensure_ascii=False)
    json.dump(missing, open(MISSING, "w"), indent=1, ensure_ascii=False)

    print("\nCoverage (answer film is in movies.db):")
    for split in SPLITS:
        n = sum(1 for r in rows if r["split"] == split)
        k = sum(1 for q in kept if q["split"] == split)
        print(f"   {split:5s} {k:3d}/{n}  ({k / n:.0%})" if n else f"   {split}: none")
    why = {}
    for r in missing:
        why[r["why_missing"]] = why.get(r["why_missing"], 0) + 1
    for w, n in why.items():
        print(f"   missing - {w}: {n}")
    if kept:
        v = sorted(q["vote_count"] or 0 for q in kept)
        print(f"   votes of the films found: median {v[len(v) // 2]:,}, lowest {v[0]:,}")
    print(f"\nWrote {OUT} ({len(kept)} questions) and {MISSING} ({len(missing)}).")
    print("Next: python stage_scores.py --questions trec_tot_2023.json --split dev --limit 5")


if __name__ == "__main__":
    main()
