#!/usr/bin/env python3
"""
v3 data: awards, nominations, box office and production cost from Wikidata (CC0) into facts.db.

    python fetch_wikidata.py             # every film wiki.db links to a Wikidata item (~25k)
    python fetch_wikidata.py --limit 300 # smoke test
    python fetch_wikidata.py --from-facts  # 7 Oct: also every facts.db film with a Wikidata id
                                           # from TMDB (films outside the catalogue / wiki.db)

Uses the public SPARQL endpoint (query.wikidata.org), 150 films per query, with a
descriptive User-Agent as Wikidata asks. ~170 queries per kind; a few minutes in total.
Network only, no GPU.

Properties: P166 award received, P1411 nominated for (with P585 point in time),
P2142 box office, P2130 cost (with the currency unit; only US-dollar amounts and amounts
without a place qualifier, i.e. worldwide, are kept as numbers).
"""
import argparse
import json
import sqlite3
import sys
import time
import urllib.parse
import urllib.request

import facts_db as F

ENDPOINT = "https://query.wikidata.org/sparql"
UA = "MovieBuff-student-research/0.3 (Merrimack College course project; python-urllib)"
USD = "http://www.wikidata.org/entity/Q4917"

AWARDS_Q = """SELECT ?film ?prop ?award ?awardLabel ?time ?forLabel WHERE {
  VALUES ?film { %s }
  VALUES ?prop { p:P166 p:P1411 }
  ?film ?prop ?st .
  ?st ?ps ?award . FILTER(?ps IN (ps:P166, ps:P1411))
  OPTIONAL { ?st pq:P585 ?time }
  OPTIONAL { ?st pq:P1686 ?for }
  SERVICE wikibase:label { bd:serviceParam wikibase:language "en". }
}"""

MONEY_Q = """SELECT ?film ?kind ?amount ?unit ?place WHERE {
  VALUES ?film { %s }
  VALUES (?p ?psv ?kind) { (p:P2142 psv:P2142 "box_office") (p:P2130 psv:P2130 "budget") }
  ?film ?p ?st . ?st ?psv ?v .
  ?v wikibase:quantityAmount ?amount ; wikibase:quantityUnit ?unit .
  OPTIONAL { ?st pq:P3005 ?place }
}"""


def sparql(q, retries=5):
    data = urllib.parse.urlencode({"query": q, "format": "json"}).encode()
    for attempt in range(retries):
        try:
            req = urllib.request.Request(ENDPOINT, data, {"User-Agent": UA,
                                                          "Accept": "application/sparql-results+json"})
            with urllib.request.urlopen(req, timeout=120) as r:
                return json.load(r)["results"]["bindings"]
        except Exception as e:  # noqa: BLE001  (429 / 5xx / timeout)
            wait = 10 * (attempt + 1)
            print(f"  sparql retry {attempt + 1} in {wait}s: {str(e)[:100]}", file=sys.stderr)
            time.sleep(wait)
    return None


def qid(uri):
    return uri.rsplit("/", 1)[-1]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--wiki-db", default="wiki.db")
    ap.add_argument("--facts")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--batch", type=int, default=150)
    ap.add_argument("--from-facts", action="store_true")
    a = ap.parse_args()
    wiki = sqlite3.connect(f"file:{a.wiki_db}?mode=ro", uri=True)
    pairs = [(w, f) for w, f in wiki.execute("select wikidata_id, film_id from pages "
                                              "where wikidata_id like 'Q%'")]
    if a.from_facts:
        seen = {w for w, _f in pairs}
        fc = F.connect(a.facts, readonly=True)
        extra = [(w, f) for w, f in fc.execute("select wikidata_id, tmdb_id from films where wikidata_id like 'Q%'")
                 if w not in seen]
        print(f"films with a Wikidata id from TMDB only (not in wiki.db): {len(extra):,}")
        pairs += extra
    if a.limit:
        pairs = pairs[:a.limit]
    film_of = {w: f for w, f in pairs}
    print(f"films with a Wikidata id: {len(film_of):,}")
    conn = F.connect(a.facts)
    conn.execute("delete from awards")
    conn.execute("delete from facts where source = 'wikidata'")
    ts = F.now()
    qids = list(film_of)
    n_aw = n_money = 0
    t0 = time.time()
    for i in range(0, len(qids), a.batch):
        vals = " ".join(f"wd:{q}" for q in qids[i:i + a.batch])
        rows = sparql(AWARDS_Q % vals) or []
        aw = []
        for b in rows:
            q = qid(b["film"]["value"])
            status = "won" if b["prop"]["value"].endswith("P166") else "nominated"
            year = int(b["time"]["value"][:4]) if "time" in b and b["time"]["value"][:4].isdigit() else None
            aw.append((film_of.get(q), q, qid(b["award"]["value"]), b.get("awardLabel", {}).get("value"),
                       status, year, b.get("forLabel", {}).get("value"), ts))
        conn.executemany("insert into awards values (?,?,?,?,?,?,?,?)", aw)
        n_aw += len(aw)
        rows = sparql(MONEY_Q % vals) or []
        mo = []
        for b in rows:
            if "place" in b:          # domestic / one country only - keep worldwide figures
                continue
            q = qid(b["film"]["value"])
            unit = b["unit"]["value"]
            try:
                amt = float(b["amount"]["value"])
            except ValueError:
                continue
            mo.append((film_of.get(q), b["kind"]["value"], amt, None, "USD" if unit == USD else qid(unit),
                       "wikidata", f"https://www.wikidata.org/wiki/{q}", ts[:10], ts, F.RELIABILITY["wikidata"]))
        conn.executemany("insert into facts values (?,?,?,?,?,?,?,?,?,?)", mo)
        n_money += len(mo)
        conn.commit()
        done = min(i + a.batch, len(qids))
        print(f"  {done:,}/{len(qids):,} films  awards rows {n_aw:,}  money rows {n_money:,}  "
              f"{time.time() - t0:.0f}s", flush=True)
        time.sleep(1.0)
    F.set_meta(conn, "wikidata_fetched_at", ts)
    conn.commit()
    print("done")


if __name__ == "__main__":
    main()
