#!/usr/bin/env python3
"""
v6 (21 Oct): IMDb API (AWS Data Exchange, IMDb + Box Office Mojo trial) -> facts.db table imdb_boxoffice.

What it adds per film (joined on the IMDb id facts.db already has): production budget, lifetime gross worldwide /
domestic / international, the domestic opening weekend (gross + weekend end date), IMDb rating and votes, the US
certificate. Nothing else changes: the `money` table, the box-office model and the answers' money lines are untouched
(extra_data.py shows one sourced "Box Office Mojo via IMDb" line when EXTRA_DATA=1). Whether IMDb figures should feed
the money table and the model is a separate decision, after looking at how they compare (`compare` below).

Access (data.imdb.com/documentation/api-documentation/getting-access + calling-the-api):
  * an AWS account with the IMDb product subscribed in AWS Data Exchange, and AWS access keys
  * the API key IMDb emails you, and the data-set / revision / asset ids from AWS "Entitled data"
  Every call is AWS Data Exchange SendApiAsset (POST /v1, body {"query": <GraphQL>}, header x-api-key).

    pip install boto3                                   # once, in the movie-rag venv
    export AWS_ACCESS_KEY_ID=... AWS_SECRET_ACCESS_KEY=... AWS_DEFAULT_REGION=us-east-1
    export IMDB_API_KEY=... IMDB_DATA_SET_ID=... IMDB_REVISION_ID=... IMDB_ASSET_ID=...
    python fetch_imdb_api.py test                       # one query (Titanic), prints the reply
    python fetch_imdb_api.py fetch --limit 200          # small batch first; check the AWS bill page
    python fetch_imdb_api.py fetch                      # films with no budget or no gross first, then the rest
    python fetch_imdb_api.py compare                    # IMDb vs our money table: how often they agree
    python fetch_imdb_api.py coverage

Field names are the ones in IMDb's sample queries. Area enums other than INTERNATIONAL are not in the samples, so the
first batch tries each part and drops (and reports) any part the API refuses; the rest still arrives.
Terms: the trial is for evaluation; check the licence before the data is shown publicly or kept after the trial.
"""
import argparse
import json
import os
import sqlite3
import sys
import time

import facts_db as F

DDL = """
create table if not exists imdb_boxoffice(imdb_id text primary key, tmdb_id integer, budget real, budget_cur text,
  gross_ww real, gross_dom real, gross_intl real, gross_cur text, opening_dom real, opening_date text,
  rating real, votes integer, certificate text, status text, raw_json text, fetched_at text);
create index if not exists imdb_bo_film on imdb_boxoffice(tmdb_id);
"""
# GraphQL parts, each optional (a refused part is dropped and reported).
PARTS = {
    "budget": "productionBudget { budget { amount currency } }",
    "ww": "ww: lifetimeGross(boxOfficeArea: WORLDWIDE) { total { amount currency } }",
    "dom": "dom: lifetimeGross(boxOfficeArea: DOMESTIC) { total { amount currency } }",
    "intl": "intl: lifetimeGross(boxOfficeArea: INTERNATIONAL) { total { amount currency } }",
    "opening": "opening: openingWeekendGross(boxOfficeArea: DOMESTIC) { gross { total { amount currency } } weekendEndDate }",
    "rating": "ratingsSummary { aggregateRating voteCount }",
    "cert": "certificate { rating }",
}
BATCH = 25


def env(name):
    v = (os.environ.get(name) or "").strip().strip("'\"")
    if not v:
        sys.exit(f"export {name}=... first (see the top of this file)")
    return v


class Client:
    def __init__(self):
        try:
            import boto3
        except ImportError:
            sys.exit("pip install boto3   (in the movie-rag venv)")
        self.c = boto3.client("dataexchange", region_name=os.environ.get("AWS_DEFAULT_REGION", "us-east-1"))
        self.ids = dict(AssetId=env("IMDB_ASSET_ID"), DataSetId=env("IMDB_DATA_SET_ID"),
                        RevisionId=env("IMDB_REVISION_ID"))
        self.key = env("IMDB_API_KEY")
        self.calls = 0

    def query(self, q, retries=4):
        for attempt in range(retries):
            try:
                self.calls += 1
                r = self.c.send_api_asset(Method="POST", Path="/v1", Body=json.dumps({"query": q}),
                                          RequestHeaders={"x-api-key": self.key, "Content-Type": "application/json"},
                                          **self.ids)
                return json.loads(r["Body"])
            except Exception as e:  # noqa: BLE001 - throttling / network
                msg = str(e)
                if "AccessDenied" in msg or "UnrecognizedClient" in msg or "InvalidSignature" in msg:
                    sys.exit(f"AWS refused the call: {msg[:300]}")
                wait = 2 ** attempt * 2
                print(f"  call failed ({msg[:120]}); waiting {wait}s", flush=True)
                time.sleep(wait)
        return None


def build(ids, parts):
    body = " ".join(PARTS[p] for p in parts)
    return "query { titles(ids: [" + ",".join(json.dumps(i) for i in ids) + "]) { id " + body + " } }"


def probe(client):
    """Which parts this subscription accepts (one film, each part alone)."""
    ok = []
    for p in PARTS:
        d = client.query("query { title(id: \"tt0120338\") { id " + PARTS[p] + " } }")
        if d and not d.get("errors"):
            ok.append(p)
        else:
            err = (d or {}).get("errors") or [{"message": "no reply"}]
            print(f"  part '{p}' refused: {str(err[0].get('message'))[:160]}", flush=True)
    return ok


def _amt(x, *path):
    for k in path:
        x = (x or {}).get(k) if isinstance(x, dict) else None
    return x


def row_of(t, fid, ts):
    money = lambda node: (_amt(node, "total", "amount"), _amt(node, "total", "currency"))  # noqa: E731
    ww, cur = money(t.get("ww"))
    dom, _ = money(t.get("dom"))
    intl, _ = money(t.get("intl"))
    if ww is None and dom is not None and intl is not None:
        ww = dom + intl                     # worldwide = domestic + international when the API gives no WORLDWIDE
    op = t.get("opening") or {}
    return (t["id"], fid, _amt(t, "productionBudget", "budget", "amount"), _amt(t, "productionBudget", "budget", "currency"),
            ww, dom, intl, cur, _amt(op, "gross", "total", "amount"), op.get("weekendEndDate") if isinstance(op, dict) else None,
            _amt(t, "ratingsSummary", "aggregateRating"), _amt(t, "ratingsSummary", "voteCount"),
            _amt(t, "certificate", "rating"), "ok", json.dumps(t)[:4000], ts)


def cmd_test(a, conn):
    cl = Client()
    print(json.dumps(cl.query("query { title(id: \"tt0120338\") { id titleText { text } " + PARTS["rating"] + " "
                              + PARTS["budget"] + " } }"), indent=1)[:2000])


def cmd_fetch(a, conn):
    cl = Client()
    have = set() if a.again else {r[0] for r in conn.execute("select imdb_id from imdb_boxoffice where status = 'ok'")}
    try:
        m = {r[0]: (r[1], r[2]) for r in conn.execute("select tmdb_id, budget, gross from money")}
    except sqlite3.Error:
        m = {}
    rows = conn.execute("select tmdb_id, imdb_id, vote_count from films where imdb_id like 'tt%'").fetchall()
    todo = [(fid, iid, vc) for fid, iid, vc in rows if iid not in have]
    # films missing a budget or a gross first (where IMDb adds the most), then by votes
    todo.sort(key=lambda r: (bool(m.get(r[0], (None, None))[0]) and bool(m.get(r[0], (None, None))[1]), -(r[2] or 0)))
    if a.limit:
        todo = todo[:a.limit]
    print(f"imdb: {len(todo):,} films to fetch, {BATCH} per call", flush=True)
    parts = probe(cl)
    print(f"  parts accepted: {', '.join(parts) or 'none'}", flush=True)
    if not parts:
        sys.exit("no part of the query was accepted: check the subscription (Box Office product?) and ids")
    fid_of = {iid: fid for fid, iid, _vc in todo}
    got = 0
    for i in range(0, len(todo), BATCH):
        chunk = [iid for _f, iid, _v in todo[i:i + BATCH]]
        d = cl.query(build(chunk, parts))
        ts = F.now()
        titles = ((d or {}).get("data") or {}).get("titles") or []
        rows_ = [row_of(t, fid_of.get(t.get("id")), ts) for t in titles if t and t.get("id")]
        seen = {r[0] for r in rows_}
        rows_ += [(iid, fid_of.get(iid), None, None, None, None, None, None, None, None, None, None, None,
                   "missing", None, ts) for iid in chunk if iid not in seen and d is not None]
        conn.executemany("insert or replace into imdb_boxoffice values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows_)
        conn.commit()
        got += len(titles)
        if (i // BATCH) % 20 == 0:
            print(f"  {min(i + BATCH, len(todo)):,}/{len(todo):,} films, {got:,} answered, {cl.calls:,} calls", flush=True)
        time.sleep(a.sleep)
    F.set_meta(conn, "imdb_api_fetched_at", F.now())
    conn.commit()
    print(f"imdb done: {got:,} films answered in {cl.calls:,} calls")
    cmd_coverage(a, conn)


def cmd_compare(a, conn):
    rows = conn.execute("""select i.budget, m.budget, i.gross_ww, m.gross from imdb_boxoffice i join money m
        on m.tmdb_id = i.tmdb_id where i.status = 'ok' and (i.budget_cur is null or i.budget_cur = 'USD')""").fetchall()

    def agree(pairs):
        p = [(x, y) for x, y in pairs if x and y]
        if not p:
            return "no pairs"
        within = sum(1 for x, y in p if abs(x - y) / max(x, y) <= 0.10)
        return f"{len(p):,} films with both; within 10%: {100 * within / len(p):.0f}%"
    print("IMDb vs money table")
    print("  budget:", agree([(r[0], r[1]) for r in rows]))
    print("  worldwide gross:", agree([(r[2], r[3]) for r in rows]))
    new_b = conn.execute("""select count(*) from imdb_boxoffice i left join money m on m.tmdb_id = i.tmdb_id
        where i.budget is not null and (m.budget is null)""").fetchone()[0]
    new_g = conn.execute("""select count(*) from imdb_boxoffice i left join money m on m.tmdb_id = i.tmdb_id
        where i.gross_ww is not null and (m.gross is null)""").fetchone()[0]
    print(f"  films that would GAIN a budget: {new_b:,}; a worldwide gross: {new_g:,}")


def cmd_coverage(a, conn):
    one = lambda sql: conn.execute(sql).fetchone()[0]  # noqa: E731
    print("imdb_boxoffice coverage")
    n_all = one("select count(*) from imdb_boxoffice")
    n_ok = one("select count(*) from imdb_boxoffice where status = 'ok'")
    print(f"  fetched {n_all:,} (ok {n_ok:,})")
    for col, label in (("budget", "budget"), ("gross_ww", "worldwide gross"), ("gross_dom", "domestic gross"),
                       ("opening_dom", "domestic opening weekend"), ("rating", "rating")):
        print(f"  {label}: {one(f'select count(*) from imdb_boxoffice where {col} is not null'):,}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["test", "fetch", "compare", "coverage"])
    ap.add_argument("--limit", type=int)
    ap.add_argument("--again", action="store_true")
    ap.add_argument("--sleep", type=float, default=0.2)
    a = ap.parse_args()
    conn = F.connect()
    conn.executescript(DDL)
    {"test": cmd_test, "fetch": cmd_fetch, "compare": cmd_compare, "coverage": cmd_coverage}[a.cmd](a, conn)


if __name__ == "__main__":
    main()
