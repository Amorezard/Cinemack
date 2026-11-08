#!/usr/bin/env python3
"""
pf5 pre-release inputs (8 Nov 2026). Every value is dated BEFORE the film's US release (or its release date when no
US date is known); nothing here reads reviews, grosses or anything published after release.

    python pf5_data.py people            # A: cast/director in the stored headlines (news_items), CPU only, minutes
    python pf5_data.py press             # B: Guardian articles in the film section, 120 days before release
                                         #    GUARDIAN_API_KEY; resumable; stops at PRESS_MAX calls (default 4,800/day)
    python pf5_data.py macro             # C: FRED CPIAUCNS / UNRATE / UMCSENT monthly series (FRED_API_KEY)
    python pf5_data.py status            # coverage of each table, test years 2016-2019 first

A  people_news (one row per film x person) + film_people_news (per film):
   people = the director(s) and the first 5 billed cast. A headline mentions a person when the full name appears
   as consecutive words (case-sensitive); names held by two people in the people table, and one-word names, are
   skipped. n_pre = mentions in the 90 days before release; base90 = their average per 90 days in the 3 years
   before that window, so the change in coverage, not fame, is the new signal. Tone from the headline words:
   scandal (arrest, lawsuit, allegation, charged, scandal, divorce, accused, sued) and awards (Oscar, Golden
   Globe, Bafta, Emmy, nominated, nomination, wins, award). Headlines = whatever news.py has stored (NYT front
   pages, Wikipedia current events); coverage is printed.
B  film_press_done (one row per film: total matches) + film_press (up to 50 articles: date, headline, trail):
   Guardian /search with q = "<title>" (as a phrase), section = film, from release - 120 days to release - 1 day.
   One call per film; order: 2016-2019 (the test years), 2020-2023, then 2015 back to 2000 (the archive starts
   in 1999). The free key allows 5,000 calls a day; the run stops at PRESS_MAX and continues on the next run.
   Text is kept for counting only (the free key is non-commercial).
C  macro (series, month, value): the whole monthly series. performance.py uses the value of release month - 2,
   which had been published before the film opened. CPIAUCNS is never revised; UNRATE and UMCSENT get small
   revisions after first publication, which this does not undo (noted in the write-up).
"""
import argparse
import bisect
import datetime
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import facts_db as F  # noqa: E402

VERSION = "pf5d1"
UA = "Cinemack-research/1.0 (academic project)"
G_API = "https://content.guardianapis.com/search"
FRED_API = "https://api.stlouisfed.org/fred/series/observations"
FRED_SERIES = ("CPIAUCNS", "UNRATE", "UMCSENT")
PRE_DAYS, BASE_YEARS, PRESS_DAYS, N_CAST = 90, 3, 120, 5
R_SCANDAL = re.compile(r"\b(arrest(ed)?|lawsuit|sued|su(es|ing)|alleg(ation|ations|ed|es)|accus(ed|es|ation)|charged|"
                       r"scandal|divorc(e|es|ing)|misconduct|indicted|rehab)\b", re.I)
R_AWARD = re.compile(r"\b(oscars?|academy awards?|golden globes?|baftas?|emmys?|nominat(ed|ion|ions)|wins|won|"
                     r"award|awards|honou?red)\b", re.I)
R_TOKEN = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿ0-9][A-Za-zÀ-ÖØ-öø-ÿ0-9'.\-]*")

DDL = """
create table if not exists people_news(tmdb_id integer, person_id integer, role text, n_pre integer, base90 real,
  n_scandal integer, n_award integer, version text, primary key (tmdb_id, person_id, role));
create table if not exists film_people_news(tmdb_id integer primary key, people integer, covered integer,
  pre_total integer, base90_total real, ratio real, max_person_ratio real, scandal integer, award integer,
  version text, computed_at text);
create table if not exists film_press_done(tmdb_id integer primary key, query text, from_date text, to_date text,
  n_total integer, status text, fetched_at text);
create table if not exists film_press(tmdb_id integer, article_id text, pub_date text, headline text, trail text,
  section text, primary key (tmdb_id, article_id));
create table if not exists macro(series text, month text, value real, fetched_at text, primary key (series, month));
"""


def connect():
    c = F.connect()
    c.executescript(DDL)
    return c


def _d(s):
    try:
        return datetime.date.fromisoformat(str(s)[:10])
    except (TypeError, ValueError):
        return None


def model_films(c):
    """Films the box-office model uses (budget and gross known), with their release date."""
    out = []
    for fid, title, rel in c.execute(
            "select f.tmdb_id, f.title, coalesce(f.us_release_date, f.release_date) from films f "
            "join money m on m.tmdb_id = f.tmdb_id where m.gross > 0 and m.budget > 0"):
        d = _d(rel)
        if d and d.year >= 1900 and title:
            out.append((fid, title, d))
    return out


# ------------------------------------------------------------------ A: people in the news
def tokens(text):
    return [t.rstrip(".,'") if not t.endswith(("Jr.", "Sr.")) else t for t in R_TOKEN.findall((text or "").replace("’", "'"))]


def name_index(c, pids):
    """name -> person_id for names that belong to exactly one person and have 2-4 words."""
    names = {}
    for pid, name in c.execute("select person_id, name from people"):
        if name:
            names.setdefault(name.replace("’", "'").strip(), set()).add(pid)
    out = {}
    for name, ps in names.items():
        k = len(tokens(name))
        if len(ps) == 1 and 2 <= k <= 4:
            pid = next(iter(ps))
            if pid in pids:
                out[" ".join(tokens(name))] = pid
    return out


def mentions(c, idx):
    """person_id -> sorted list of (date ordinal, scandal, award) from every stored headline (+ abstract)."""
    got = {}
    n = 0
    for date, head, abstract in c.execute("select date, headline, coalesce(abstract, '') from news_items"):
        d = _d(date)
        if not d:
            continue
        n += 1
        text = f"{head or ''} {abstract or ''}"
        tk = tokens(text)
        seen = set()
        for i in range(len(tk)):
            for k in (2, 3, 4):
                if i + k <= len(tk):
                    pid = idx.get(" ".join(tk[i:i + k]))
                    if pid is not None and pid not in seen:
                        seen.add(pid)
        if seen:
            sc, aw = bool(R_SCANDAL.search(head or "")), bool(R_AWARD.search(head or ""))
            for pid in seen:
                got.setdefault(pid, []).append((d.toordinal(), sc, aw))
    for v in got.values():
        v.sort()
    print(f"  headlines read: {n:,}; people mentioned at least once: {len(got):,}", flush=True)
    return got


def count(ms, lo, hi):
    """mentions with lo <= date <= hi (ordinals) -> (n, scandal, award)."""
    if not ms:
        return 0, 0, 0
    days = [m[0] for m in ms]
    i, j = bisect.bisect_left(days, lo), bisect.bisect_right(days, hi)
    part = ms[i:j]
    return len(part), sum(1 for m in part if m[1]), sum(1 for m in part if m[2])


def cmd_people(_a):
    c = connect()
    films = model_films(c)
    fids = {f for f, _t, _d in films}
    crew = {}
    for fid, pid, kind, job, ord_ in c.execute("select tmdb_id, person_id, kind, job, ord from credits"):
        if fid not in fids:
            continue
        if kind == "crew" and job == "Director":
            crew.setdefault(fid, []).append((pid, "director"))
        elif kind == "cast" and ord_ is not None and ord_ < N_CAST:
            crew.setdefault(fid, []).append((pid, "cast"))
    pids = {p for v in crew.values() for p, _r in v}
    idx = name_index(c, pids)
    print(f"films: {len(films):,}; people: {len(pids):,}; usable unique names: {len(idx):,}", flush=True)
    ms = mentions(c, idx)
    usable = set(idx.values())
    span = BASE_YEARS * 365 / PRE_DAYS
    rows, frows, ts = [], [], F.now()
    for fid, _t, d in films:
        rel = d.toordinal()
        tot_pre = tot_base = sc = aw = covered = 0
        best = 0.0
        ppl = crew.get(fid, [])
        for pid, role in ppl:
            m = ms.get(pid)
            if pid not in usable:
                continue
            n_pre, s, w = count(m, rel - PRE_DAYS, rel - 1)
            n_b, _s, _w = count(m, rel - PRE_DAYS - BASE_YEARS * 365, rel - PRE_DAYS - 1)
            base = n_b / span
            rows.append((fid, pid, role, n_pre, round(base, 4), s, w, VERSION))
            covered += 1 if (m and (n_pre or n_b)) else 0
            tot_pre, tot_base, sc, aw = tot_pre + n_pre, tot_base + base, sc + s, aw + w
            best = max(best, (n_pre + 1) / (base + 1))
        frows.append((fid, len(ppl), covered, tot_pre, round(tot_base, 4), round((tot_pre + 1) / (tot_base + 1), 4),
                      round(best, 4), sc, aw, VERSION, ts))
    c.execute("delete from people_news")
    c.execute("delete from film_people_news")
    c.executemany("insert or replace into people_news values (?,?,?,?,?,?,?,?)", rows)
    c.executemany("insert or replace into film_people_news values (?,?,?,?,?,?,?,?,?,?,?)", frows)
    c.commit()
    n_any = sum(1 for r in frows if r[2])
    print(f"film_people_news: {len(frows):,} films; with any person in the headlines: {n_any:,}; "
          f"with a mention in the 90 days before release: {sum(1 for r in frows if r[3]):,}", flush=True)


# ------------------------------------------------------------------ B: Guardian articles before release
def api(url, label):
    """GET JSON -> dict, or one of: None (404), ("bad", code, body) for a refused query (400), ("key", code, body)
    for a refused key (401/403), ("quota", 429, body) when rate-limited after waiting, ("failed", None, error)."""
    last = None
    for attempt in range(4):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=40) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read()[:300].decode("utf-8", "replace")
            except Exception:  # noqa: BLE001
                pass
            if e.code == 404:
                return None
            if e.code == 400:
                return ("bad", 400, body)
            if e.code in (401, 403):
                return ("key", e.code, body)
            last = ("quota", e.code, body) if e.code == 429 else ("failed", e.code, body)
            wait = 2 ** attempt * (15 if e.code == 429 else 2)
            print(f"  {label} HTTP {e.code}; waiting {wait} s", flush=True)
            time.sleep(wait)
        except Exception as e:  # noqa: BLE001
            last = ("failed", None, repr(e))
            print(f"  {label} {e!r}", flush=True)
            time.sleep(3 * (attempt + 1))
    return last


def _safe(url):
    return re.sub(r"api[-_]key=[^&]+", "api-key=***", url)


def phrase(title):
    """The title as a Guardian phrase query. The Guardian parser refuses some punctuation even inside quotes
    (HTTP 400 "Invalid search query syntax", 8 Nov), so only letters, digits and spaces are kept, '&' becomes
    'and', and the words AND / OR / NOT are lower-cased so they are not read as operators."""
    t = (title or "").replace("&", " and ")
    t = re.sub(r"[^\w\s]|_", " ", t)
    t = " ".join(w.lower() if w in ("AND", "OR", "NOT") else w for w in t.split())
    return '"' + t + '"'


def press_order(films):
    def rank(f):
        y = f[2].year
        if 2016 <= y <= 2019:
            return (0, -y)
        if 2020 <= y <= 2023:
            return (1, y)
        return (2, -y)
    return sorted((f for f in films if f[2].year >= 2000), key=rank)


def cmd_press(a):
    key = (os.environ.get("GUARDIAN_API_KEY") or "").strip().strip("'\"")
    if not key:
        sys.exit("export GUARDIAN_API_KEY=... first (the same free key fetch_free_data.py guardian uses)")
    c = connect()
    done = {r[0] for r in c.execute("select tmdb_id from film_press_done where status = 'ok'")}
    if a.retry_bad:
        done -= {r[0] for r in c.execute("select tmdb_id from film_press_done where status = 'bad_query'")}
    else:
        done |= {r[0] for r in c.execute("select tmdb_id from film_press_done where status = 'bad_query'")}
    todo = [f for f in press_order(model_films(c)) if f[0] not in done]
    cap = a.max or int(os.environ.get("PRESS_MAX", "4800"))
    print(f"press: {len(done):,} films done, {len(todo):,} to do; this run stops after {cap:,} calls", flush=True)
    calls, t0, bad_run = 0, time.time(), 0
    for fid, title, d in todo:
        if calls >= cap:
            print(f"  reached {cap:,} calls: run again tomorrow (the free key allows 5,000 a day)", flush=True)
            break
        lo, hi = d - datetime.timedelta(days=PRESS_DAYS), d - datetime.timedelta(days=1)
        q = phrase(title)
        url = G_API + "?" + urllib.parse.urlencode({"q": q, "section": "film", "from-date": lo.isoformat(),
                                                     "to-date": hi.isoformat(), "page-size": 50, "order-by": "oldest",
                                                     "show-fields": "trailText", "api-key": key})
        r = api(url, "Guardian")
        calls += 1
        if isinstance(r, tuple):
            kind, code, body = r
            if kind in ("key", "quota"):
                print(f"  stopped after {calls:,} calls: HTTP {code} ({'key refused' if kind == 'key' else 'rate / daily limit'}): "
                      f"{body[:200]}\n  query: {_safe(url)}", flush=True)
                break
            c.execute("insert or replace into film_press_done values (?,?,?,?,?,?,?)",
                      (fid, q, lo.isoformat(), hi.isoformat(), None, "bad_query" if kind == "bad" else "failed", F.now()))
            c.commit()
            bad_run = bad_run + 1 if kind == "bad" else 0
            if bad_run <= 3 or bad_run % 100 == 0:
                print(f"  {title} ({d.year}): HTTP {code} {body[:160]}\n    query: {_safe(url)}", flush=True)
            if bad_run >= 25:
                print("  stopped: 25 refused queries in a row - something is wrong with the request, not one title", flush=True)
                break
            continue
        bad_run = 0
        resp = (r or {}).get("response") or {}
        rows = []
        for it in resp.get("results") or []:
            pub = (it.get("webPublicationDate") or "")[:10]
            if not pub or pub > hi.isoformat():            # belt and braces: nothing on or after release day
                continue
            trail = re.sub(r"<[^>]+>", "", ((it.get("fields") or {}).get("trailText") or ""))[:300]
            rows.append((fid, it.get("id"), pub, (it.get("webTitle") or "")[:300], trail, it.get("sectionId")))
        c.executemany("insert or replace into film_press values (?,?,?,?,?,?)", rows)
        c.execute("insert or replace into film_press_done values (?,?,?,?,?,?,?)",
                  (fid, q, lo.isoformat(), hi.isoformat(), int(resp.get("total") or 0), "ok", F.now()))
        if calls % 50 == 0:
            c.commit()
            print(f"  {calls:,} calls, {time.time() - t0:.0f} s; last {title} ({d.year}): {resp.get('total', 0)} articles",
                  flush=True)
        time.sleep(0.25)
    c.commit()
    cmd_status(a, only="press")


# ------------------------------------------------------------------ C: FRED economy series
def cmd_macro(_a):
    key = (os.environ.get("FRED_API_KEY") or "").strip().strip("'\"")
    if not key:
        sys.exit("export FRED_API_KEY=... first (free: https://fred.stlouisfed.org/docs/api/api_key.html)")
    c = connect()
    for s in FRED_SERIES:
        url = FRED_API + "?" + urllib.parse.urlencode({"series_id": s, "api_key": key, "file_type": "json",
                                                       "observation_start": "1960-01-01"})
        r = api(url, "FRED")
        if not isinstance(r, dict):
            print(f"  {s}: not fetched ({r})", flush=True)
            continue
        rows = [(s, o["date"][:7], float(o["value"]), F.now()) for o in r.get("observations") or []
                if o.get("value") not in (None, "", ".")]
        c.executemany("insert or replace into macro values (?,?,?,?)", rows)
        c.commit()
        print(f"  {s}: {len(rows):,} months ({rows[0][1] if rows else '-'} to {rows[-1][1] if rows else '-'})", flush=True)
        time.sleep(1)


# ------------------------------------------------------------------ status
def cmd_status(_a, only=None):
    c = connect()
    films = model_films(c)
    test = {f for f, _t, d in films if 2016 <= d.year <= 2019}
    train = {f for f, _t, d in films if d.year <= 2015}

    def cov(sql):
        have = {r[0] for r in c.execute(sql)}
        return f"test {len(have & test):,}/{len(test):,}, train {len(have & train):,}/{len(train):,}"
    if only in (None, "people"):
        print("A people in the news:  ", cov("select tmdb_id from film_people_news where covered > 0"))
    if only in (None, "press"):
        print("B Guardian pre-release:", cov("select tmdb_id from film_press_done where status = 'ok'"),
              "| films with 1+ article:", cov("select tmdb_id from film_press_done where n_total > 0"))
        for st, n in c.execute("select status, count(*) from film_press_done group by status"):
            print(f"    {st}: {n:,}")
    if only is None:
        for s, n, lo, hi in c.execute("select series, count(*), min(month), max(month) from macro group by series"):
            print(f"C {s}: {n:,} months {lo} to {hi}")
        if not c.execute("select 1 from macro limit 1").fetchone():
            print("C macro: not fetched yet (FRED_API_KEY)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["people", "press", "macro", "status"])
    ap.add_argument("--max", type=int, default=0, help="press: calls this run (default PRESS_MAX or 4,800)")
    ap.add_argument("--retry-bad", action="store_true", help="press: ask again for films whose query was refused")
    a = ap.parse_args()
    {"people": cmd_people, "press": cmd_press, "macro": cmd_macro, "status": cmd_status}[a.cmd](a)


if __name__ == "__main__":
    main()
