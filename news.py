#!/usr/bin/env python3
"""
v4 (14 Oct): news at the time a film came out, and whether it connects to the film's success.

Question it serves: "Was there anything going on in the world when Jaws came out that helped
it?" / "Did 9/11 hurt Zoolander?" / "Why did Contagion do well?" (with NEWS=1 the "why" route
adds the news block).

    sources  NYT Archive API (every month since 1851: front-page headlines + abstract + URL;
             free key, NYT_API_KEY; 5 requests/min, 500/day, so a full back-fill takes ~3 days and
             resumes where it stopped), and Wikipedia's dated event lists (no key):
             "Portal:Current events/<Month YYYY>" (~1994 on), "<Month YYYY>" articles, then
             "<YYYY> in the United States" - used for months the NYT has not been fetched for.
    store    facts.db: news_items (date, headline, abstract, source, url), news_vec (mxbai vectors),
             news_months (what was fetched), news_film (per film), news_corpus (the corpus result)

What "connection" means here (all computed in code; the model only words it):
  1. related headlines   the window's headlines closest to the film (cosine between the film's
                         title + genres + keywords + overview and each headline + abstract)
  2. resonance           mean similarity of the film's 5 closest headlines in its release window
                         (release date -30 / +30 days) MINUS the same for control windows 6 months
                         before and after: is the news at release unusually on-topic FOR THIS FILM?
  3. themes              share of window headlines per news theme (war, economy, terrorism, disease,
                         disaster, politics, space, technology, crime, civil rights) vs the control
                         windows, for the themes the film itself touches (from its genres/keywords)
  4. documented          sentences in the film's own Wikipedia Box office / Release / Reception
                         passages that mention a news event (attacks, pandemic, recession, strike,
                         war, hurricane ...) - a link a source states, not one we infer
  5. corpus test         across every film with budget, gross and a release date: Spearman
                         correlation between resonance (and theme lift) and the film's return on
                         budget relative to films of the same genre and year, with a bootstrap 95%
                         CI and a permutation p-value. This says whether "on-topic news" goes with
                         success IN GENERAL; the film-level lines say what happened for one film.
A related headline is a coincidence of topics, not evidence the news moved the box office; only
(4) is a stated link, and (5) says how much the pattern holds across films. The block says so.

    python news.py fetch --source nyt [--films-only] [--max-months 450]   # one day's NYT quota
    python news.py fetch --source wiki --from 1990 --to 2026
    python news.py embed                        # vectors for new headlines
    python news.py films [--limit N]            # per-film windows, resonance, themes, documented
    python news.py analyze                      # corpus test -> results/news_link_summary.txt
    python news.py show "Jaws"                  # the block the answer model would see
    python news.py status
"""
import argparse
import datetime
import html
import json
import os
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
if os.environ.get("MOCK", "off").lower() not in ("", "off", "0"):
    import mock_llm
    mock_llm.install()
import numpy as np  # noqa: E402

import facts_db as F  # noqa: E402

VERSION = "nw2"     # 14 Oct: headlines about the film itself or about the film business are left out
EMBED_MODEL = os.environ.get("EMBED_MODEL", "mxbai-embed-large")
WINDOW = int(os.environ.get("NEWS_WINDOW_DAYS", "30"))
CONTROL_SHIFT = (-183, 183)          # control windows: the same length, 6 months before and after
TOPK = 5
UA = "MovieBuff/4 (student research project; Merrimack College) python-urllib"
NYT_PAGES = {"1", "A1"}
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December"]

THEMES = {
    "war":        r"\b(war|wars|wartime|invasion|troops|military|soldiers?|bomb\w*|missiles?|army|navy|combat|battle)\b",
    "terrorism":  r"\b(terror\w*|hijack\w*|9/11|september 11|al[- ]qaeda|isis|extremists?|suicide bomb\w*)\b",
    "economy":    r"\b(recession|depression|unemployment|jobless|inflation|stocks?|wall street|economy|economic|"
                  r"bankrupt\w*|layoffs?|gas prices|oil prices|financial crisis|market crash)\b",
    "politics":   r"\b(election|president\w*|congress|senate|campaign|impeach\w*|white house|vote|voters|"
                  r"parliament|scandal|watergate)\b",
    "disease":    r"\b(pandemic|epidemic|virus|outbreak|covid|coronavirus|aids|hiv|flu|influenza|quarantine|vaccine)\b",
    "disaster":   r"\b(hurricane|earthquake|tsunami|flood\w*|wildfires?|tornado\w*|disaster|explosion|crash|"
                  r"nuclear accident|oil spill)\b",
    "space":      r"\b(nasa|space|astronauts?|moon|mars|satellite|rocket|shuttle|orbit)\b",
    "technology": r"\b(computers?|internet|online|software|artificial intelligence|\bai\b|robots?|cyber\w*|"
                  r"smartphones?|social media|hackers?)\b",
    "crime":      r"\b(murder\w*|killings?|shooting|gunman|mob|mafia|gangs?|drugs?|cartel|police|serial killer|"
                  r"kidnap\w*|heist|robbery)\b",
    "civil_rights": r"\b(civil rights|racism|racial|segregation|protests?|riots?|discrimination|equality|"
                    r"feminis\w*|lgbt\w*|gay rights)\b",
}
THEME_RX = {k: re.compile(v, re.I) for k, v in THEMES.items()}
EVENT_RX = re.compile(
    r"\b(september 11|9/11|terror\w* attacks?|pandemic|covid|coronavirus|lockdowns?|recession|financial crisis|"
    r"great depression|depression era|strikes?|writers'? strike|actors'? strike|hurricane \w+|gulf war|iraq war|"
    r"vietnam war|world war|cold war|election|assassinat\w*|riots?|shootings?|tsunami|earthquake|oil crisis|"
    r"energy crisis|watergate|moon landing|columbine|aurora|boycott\w*|news events?|current events|"
    r"national mood|political climate|in the wake of|aftermath of)\b", re.I)

DDL = """
create table if not exists news_items(
  news_id integer primary key, source text, date text, month text, headline text, abstract text,
  section text, url text, key text unique, fetched_at text);
create index if not exists news_month on news_items(month);
create index if not exists news_date on news_items(date);
create table if not exists news_vec(news_id integer primary key, emb blob);
create table if not exists news_months(source text, month text, n integer, fetched_at text, status text,
  primary key (source, month));
create table if not exists news_film(
  tmdb_id integer primary key, release_date text, win_from text, win_to text, n_window integer,
  resonance real, top_sim real, related_json text, themes_json text, documented_json text,
  version text, computed_at text);
create table if not exists news_corpus(key text primary key, value text);
"""


def connect():
    c = F.connect()
    c.executescript(DDL)
    return c


def _get(url, tries=4, wait=12):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=120) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 429:
                print(f"  429 rate limit; waiting {wait * (i + 2)} s", file=sys.stderr)
                time.sleep(wait * (i + 2))
                continue
            if e.code in (404, 400):
                return None
            print(f"  HTTP {e.code} for {url[:90]}", file=sys.stderr)
            time.sleep(wait)
        except (urllib.error.URLError, TimeoutError, ValueError) as e:
            print(f"  {e!r} for {url[:90]}", file=sys.stderr)
            time.sleep(wait)
    return None


# ------------------------------------------------------------------ months wanted
def film_months(c, films_only=True):
    """Months (YYYY-MM) that release windows (and their control windows) need, the months most films
    need first (so a partial back-fill already covers the most films)."""
    rows = c.execute("select coalesce(f.us_release_date, f.release_date) from films f "
                     + ("join money m on m.tmdb_id = f.tmdb_id where m.gross > 0 and " if films_only else "where ")
                     + "coalesce(f.us_release_date, f.release_date) >= '1900'").fetchall()
    want = {}
    for (d,) in rows:
        try:
            d0 = datetime.date.fromisoformat(d[:10])
        except (TypeError, ValueError):
            continue
        for shift in (0,) + CONTROL_SHIFT:
            for off in (-WINDOW, 0, WINDOW):
                m = (d0 + datetime.timedelta(days=shift + off)).strftime("%Y-%m")
                want[m] = want.get(m, 0) + (3 if shift == 0 else 1)
    return sorted(want, key=lambda m: (-want[m], m), reverse=False)


# ------------------------------------------------------------------ NYT
def nyt_month(key, ym):
    y, m = ym.split("-")
    d = _get(f"https://api.nytimes.com/svc/archive/v1/{int(y)}/{int(m)}.json?api-key={urllib.parse.quote(key)}")
    if not d:
        return None
    docs = (d.get("response") or {}).get("docs") or []
    items = []
    for x in docs:
        hl = x.get("headline") or {}
        head = (hl.get("main") or hl.get("print_headline") or "").strip() if isinstance(hl, dict) else str(hl)
        if not head or head.lower() in ("no title", "untitled"):
            continue
        page = str(x.get("print_page") or "").strip()
        tom = str(x.get("type_of_material") or "")
        items.append({"date": str(x.get("pub_date") or "")[:10], "headline": head,
                      "abstract": (x.get("abstract") or x.get("snippet") or "").strip()[:500],
                      "section": x.get("section_name") or x.get("news_desk") or "", "url": x.get("web_url") or "",
                      "front": page in NYT_PAGES, "news": tom.lower() in ("news", "front page", "")})
    front = [i for i in items if i["front"]]
    keep = front if len(front) >= 40 else [i for i in items if i["news"]][:400]   # no print_page: first news items
    return keep


# ------------------------------------------------------------------ Wikipedia
class _LiParser(HTMLParser):
    """Collect (date, text) of leaf <li> items, each dated from the nearest heading / date line above
    it. A nested list's parent label is kept as a prefix ("2020 protests: Protesters march ...")."""
    DATE_RX = re.compile(r"(?:(" + "|".join(MONTHS) + r") (\d{1,2}),? (\d{4}))|(?:(\d{4}) (" + "|".join(MONTHS)
                         + r") (\d{1,2}))")

    def __init__(self):
        super().__init__()
        self.items, self.date, self.stack, self.skip, self.skip_tag = [], None, [], 0, None
        self.head_buf, self.in_head, self.head_tag = [], False, None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        cls = a.get("class") or ""
        if self.skip:
            if tag == self.skip_tag:
                self.skip += 1
            return
        if tag in ("sup", "style", "script") or "mw-editsection" in cls or "reference" in cls:
            self.skip, self.skip_tag = 1, tag
        elif not self.in_head and (tag in ("h2", "h3", "h4") or "current-events-title" in cls or
                                   (tag == "span" and "summary" in cls)):
            self.in_head, self.head_buf, self.head_tag = True, [], tag
        elif tag == "li":
            if self.stack:
                self.stack[-1][1] = True                  # the parent has a child list
            self.stack.append([[], False])

    def handle_endtag(self, tag):
        if self.skip:
            if tag == self.skip_tag:
                self.skip -= 1
            return
        if self.in_head and tag == self.head_tag:
            self._date_from(" ".join(self.head_buf))
            self.in_head = False
        elif tag == "li" and self.stack:
            buf, has_child = self.stack.pop()
            text = " ".join(" ".join(buf).split())
            if not has_child and len(text.split()) >= 6:
                labels = [" ".join(" ".join(b).split()) for b, _ in self.stack]
                labels = [x for x in labels if x and len(x.split()) <= 12]
                self.items.append((self.date, (": ".join(labels) + ": " if labels else "") + text))

    def _date_from(self, text):
        m = self.DATE_RX.search(text)
        if m:
            if m.group(1):
                mo, d, y = MONTHS.index(m.group(1)) + 1, int(m.group(2)), int(m.group(3))
            else:
                y, mo, d = int(m.group(4)), MONTHS.index(m.group(5)) + 1, int(m.group(6))
            try:
                self.date = datetime.date(y, mo, d).isoformat()
            except ValueError:
                pass

    def handle_data(self, data):
        if self.skip:
            return
        if self.in_head:
            self.head_buf.append(data)
        elif self.stack:
            # text of a parent item goes to its own buffer only until its child list starts
            top = self.stack[-1]
            top[0].append(data)


_LEAD_DATE = re.compile(r"^(" + "|".join(MONTHS) + r") (\d{1,2})(?:\s*[-\u2013\u2014:]\s*|,? )")


def _wiki_html(page):
    q = urllib.parse.urlencode({"action": "parse", "page": page, "prop": "text", "format": "json",
                                "formatversion": "2", "redirects": "1"})
    d = _get(f"https://en.wikipedia.org/w/api.php?{q}", tries=3, wait=5)
    return (d or {}).get("parse", {}).get("text") if d and "error" not in d else None


def wiki_items_from_html(text, ym):
    p = _LiParser()
    p.feed(text or "")
    out = []
    y = int(ym[:4])
    for date, t in p.items:
        m = _LEAD_DATE.match(t)            # year pages: "June 1 – The ..." under a "June" heading
        if m:
            try:
                date = datetime.date(y, MONTHS.index(m.group(1)) + 1, int(m.group(2))).isoformat()
                t = t[m.end():]
            except ValueError:
                pass
        if not date or not date.startswith(ym):
            continue
        t = re.sub(r"\[\d+\]|\(\s*[A-Z][\w .&]+\s*\)$", "", html.unescape(t)).strip()
        if len(t) > 15 and not t.lower().startswith(("see also", "retrieved", "archived")):
            out.append({"date": date, "headline": t[:300], "abstract": t[300:800], "section": "", "url": ""})
    return out


def wiki_month(ym):
    y, m = ym.split("-")
    name = f"{MONTHS[int(m) - 1]} {int(y)}"
    pages = [f"Portal:Current events/{name}", name]
    for page in pages:
        items = wiki_items_from_html(_wiki_html(page), ym)
        if len(items) >= 15:
            for i in items:
                i["url"] = "https://en.wikipedia.org/wiki/" + urllib.parse.quote(page.replace(" ", "_"))
            return items, page
        time.sleep(0.5)
    page = f"{int(y)} in the United States"
    items = wiki_items_from_html(_wiki_html(page), ym)
    for i in items:
        i["url"] = "https://en.wikipedia.org/wiki/" + urllib.parse.quote(page.replace(" ", "_"))
    return items, page


def store(c, source, ym, items):
    ts = F.now()
    n = 0
    for i in items:
        key = f"{source}|{i['date']}|{i['headline'][:200]}"
        cur = c.execute("insert or ignore into news_items(source, date, month, headline, abstract, section, url, key, "
                        "fetched_at) values (?,?,?,?,?,?,?,?,?)",
                        (source, i["date"], ym, i["headline"], i.get("abstract", ""), i.get("section", ""),
                         i.get("url", ""), key, ts))
        n += cur.rowcount
    c.execute("insert or replace into news_months values (?,?,?,?,?)", (source, ym, len(items), ts, "ok" if items else "empty"))
    c.commit()
    return n


def cmd_fetch(a):
    c = connect()
    if a.source == "nyt":
        key = os.environ.get("NYT_API_KEY", "")
        if not key:
            raise SystemExit("export NYT_API_KEY=... (developer.nytimes.com, enable the Archive API)")
    if a.months:
        months = a.months.split(",")
    elif a.year_from:
        months = [f"{y}-{m:02d}" for y in range(a.year_from, a.year_to + 1) for m in range(1, 13)]
    else:
        months = film_months(c, films_only=a.films_only)
    cur_m = datetime.date.today().strftime("%Y-%m")
    done = {r[0] for r in c.execute("select month from news_months where source = ? and status = 'ok'", (a.source,))}
    todo = [m for m in months if m not in done and m <= cur_m and m >= ("1851-09" if a.source == "nyt" else "1900-01")]
    todo = todo[:a.max_months]
    print(f"{a.source}: {len(months)} months wanted, {len(done)} fetched, doing {len(todo)} now")
    t0 = time.time()
    for i, ym in enumerate(todo, 1):
        if a.source == "nyt":
            items = nyt_month(os.environ["NYT_API_KEY"], ym)
            if items is None:
                print(f"  {ym}: no response (quota? stopping; rerun tomorrow)")
                break
            n = store(c, "nyt", ym, items)
            time.sleep(12.5)                                # 5 requests per minute
        else:
            items, page = wiki_month(ym)
            n = store(c, "wikipedia", ym, items)
            time.sleep(1.0)
        if i % 10 == 0 or i == len(todo):
            print(f"  {i}/{len(todo)} {ym}: +{n} items ({time.time() - t0:.0f} s)", flush=True)
    F.set_meta(c, f"news_{a.source}_fetched_at", F.now())
    c.commit()


# ------------------------------------------------------------------ vectors
def _embed(texts, prefix=""):
    import ollama
    for attempt in range(5):
        try:
            return ollama.embed(model=EMBED_MODEL, input=[prefix + t for t in texts])["embeddings"]
        except Exception as e:  # noqa: BLE001
            print(f"  embed retry {attempt + 1}: {str(e)[:80]}", file=sys.stderr)
            time.sleep(5 * (attempt + 1))
    raise SystemExit("embedding kept failing")


def _unit(v):
    v = np.asarray(v, dtype=np.float32)
    return v / (np.linalg.norm(v, axis=-1, keepdims=True) + 1e-9)


def cmd_embed(a):
    c = connect()
    todo = c.execute("select n.news_id, n.headline, n.abstract from news_items n left join news_vec v "
                     "on v.news_id = n.news_id where v.news_id is null").fetchall()
    print(f"headlines to embed: {len(todo):,}")
    t0 = time.time()
    for s in range(0, len(todo), a.batch):
        part = todo[s:s + a.batch]
        vecs = _unit(_embed([f"{h}. {ab}".strip(" .") for _i, h, ab in part]))
        c.executemany("insert or replace into news_vec values (?, ?)", [(r[0], v.tobytes()) for r, v in zip(part, vecs)])
        c.commit()
        if (s // a.batch) % 100 == 0:
            print(f"  {s + len(part):,}/{len(todo):,} ({time.time() - t0:.0f} s)", flush=True)


# ------------------------------------------------------------------ per film
def film_text(c, fid):
    r = c.execute("select title, year, genres_json, overview, tagline from films where tmdb_id = ?", (fid,)).fetchone()
    if not r:
        return None, None
    kws = [k for (k,) in c.execute("select name from keywords where tmdb_id = ? limit 20", (fid,))]
    g = ", ".join(json.loads(r[2] or "[]"))
    return r, f"{r[0]}. {g}. {', '.join(kws)}. {r[4] or ''} {r[3] or ''}".strip()


class Window:
    """Headlines + vectors by date range, loaded a month at a time and cached."""

    def __init__(self, c):
        self.c, self.cache = c, {}

    def month(self, ym):
        if ym not in self.cache:
            if len(self.cache) > 80:
                self.cache.pop(next(iter(self.cache)))
            rows = self.c.execute("select n.news_id, n.date, n.headline, n.source, n.url, v.emb from news_items n "
                                  "join news_vec v on v.news_id = n.news_id where n.month = ?", (ym,)).fetchall()
            mat = np.stack([np.frombuffer(r[5], dtype=np.float32) for r in rows]) if rows else np.zeros((0, 1))
            self.cache[ym] = ([r[:5] for r in rows], mat)
        return self.cache[ym]

    def range(self, d0, d1):
        rows, mats = [], []
        m = datetime.date(d0.year, d0.month, 1)
        while m <= d1:
            r, mat = self.month(m.strftime("%Y-%m"))
            keep = [i for i, x in enumerate(r) if d0.isoformat() <= (x[1] or "")[:10] <= d1.isoformat()
                    or not x[1]]
            if keep:
                rows += [r[i] for i in keep]
                mats.append(mat[keep])
            m = datetime.date(m.year + (m.month == 12), m.month % 12 + 1, 1)
        return rows, (np.concatenate(mats) if mats else np.zeros((0, 1), dtype=np.float32))


def theme_shares(heads):
    n = max(len(heads), 1)
    return {k: sum(1 for h in heads if rx.search(h)) / n for k, rx in THEME_RX.items()}


def film_themes(text):
    return [k for k, rx in THEME_RX.items() if rx.search(text or "")]


def documented(c, fid):
    out = []
    for kind, text in c.execute("select kind, text from sections where tmdb_id = ? and kind in "
                                "('box_office', 'release', 'reception', 'legacy', 'production')", (fid,)):
        for s in re.split(r"(?<=[.!?])\s+", text or ""):
            if EVENT_RX.search(s) and 6 <= len(s.split()) <= 70:
                out.append({"kind": kind, "sentence": s.strip()[:400], "event": EVENT_RX.search(s).group(0)})
    return out[:4]


# nw2 (14 Oct): a hit makes its own news ("Avatar passes $2 billion", "Oscar nominations announced"),
# which raises its similarity to the window's headlines BECAUSE it succeeded. Headlines that name the
# film or are about films / the film business are therefore dropped from the window and the controls.
ENT_RX = re.compile(r"\b(films?|movies?|box[- ]office|oscars?|academy awards?|hollywood|premieres?|sequels?|"
                    r"directors?|actors?|actress(es)?|cinemas?|theaters?|theatres?|studios?|blockbusters?|"
                    r"golden globes?|cannes|sundance|screenings?)\b", re.I)


def _norm(t):
    return re.sub(r"[^a-z0-9 ]+", " ", str(t or "").casefold()).split()


def drop_self(rows, mat, title):
    nt = " ".join(_norm(title))
    keep = []
    for i, r in enumerate(rows):
        h = r[2] or ""
        if ENT_RX.search(h):
            continue
        if len(nt) >= 4 and f" {nt} " in f" {' '.join(_norm(h))} ":
            continue
        keep.append(i)
    if len(keep) == len(rows):
        return rows, mat
    return [rows[i] for i in keep], (mat[keep] if len(keep) else mat[:0])


def compute_film(c, win, fid, fvec=None):
    row, text = film_text(c, fid)
    if not row:
        return None
    rd = c.execute("select coalesce(us_release_date, release_date) from films where tmdb_id = ?", (fid,)).fetchone()[0]
    try:
        d0 = datetime.date.fromisoformat((rd or "")[:10])
    except ValueError:
        return None
    if fvec is None:
        fvec = _unit(_embed([text])[0])
    w0, w1 = d0 - datetime.timedelta(days=WINDOW), d0 + datetime.timedelta(days=WINDOW)
    rows, mat = drop_self(*win.range(w0, w1), row[0])
    rec = {"tmdb_id": fid, "release_date": d0.isoformat(), "win_from": w0.isoformat(), "win_to": w1.isoformat(),
           "n_window": len(rows), "resonance": None, "top_sim": None, "related": [], "themes": {},
           "documented": documented(c, fid)}
    if len(rows) >= 10:
        sims = mat @ fvec
        order = np.argsort(-sims)
        top = float(np.mean(sims[order[:TOPK]]))
        ctrl, ctrl_heads = [], []
        for sh in CONTROL_SHIFT:
            cr, cm = drop_self(*win.range(w0 + datetime.timedelta(days=sh), w1 + datetime.timedelta(days=sh)), row[0])
            if len(cr) >= 10:
                cs = np.sort(cm @ fvec)[::-1]
                ctrl.append(float(np.mean(cs[:TOPK])))
                ctrl_heads += [x[2] for x in cr]
        rec["top_sim"] = round(top, 4)
        rec["resonance"] = round(top - float(np.mean(ctrl)), 4) if ctrl else None
        rec["related"] = [{"date": rows[i][1], "headline": rows[i][2], "source": rows[i][3], "url": rows[i][4],
                           "sim": round(float(sims[i]), 3)} for i in order[:8]]
        heads = [x[2] for x in rows]
        sh_w, sh_c = theme_shares(heads), theme_shares(ctrl_heads) if ctrl_heads else {}
        mine = film_themes(text)
        rec["themes"] = {"film": mine, "window": {k: round(v, 3) for k, v in sh_w.items()},
                         "control": {k: round(v, 3) for k, v in sh_c.items()},
                         "top": sorted(sh_w, key=lambda k: -sh_w[k])[:3]}
    return rec


def save_film(c, rec):
    c.execute("insert or replace into news_film values (?,?,?,?,?,?,?,?,?,?,?,?)",
              (rec["tmdb_id"], rec["release_date"], rec["win_from"], rec["win_to"], rec["n_window"], rec["resonance"],
               rec["top_sim"], json.dumps(rec["related"]), json.dumps(rec["themes"]), json.dumps(rec["documented"]),
               VERSION, F.now()))


def cmd_films(a):
    c = connect()
    fids = [r[0] for r in c.execute(
        "select f.tmdb_id from films f join money m on m.tmdb_id = f.tmdb_id where m.gross > 0 and "
        "coalesce(f.us_release_date, f.release_date) >= '1900' and f.tmdb_id not in "
        "(select tmdb_id from news_film where version = ?) order by coalesce(f.us_release_date, f.release_date)",
        (VERSION,))]
    if a.limit:
        fids = fids[:a.limit]
    print(f"films to compute: {len(fids):,}")
    win = Window(c)
    t0 = time.time()
    for s in range(0, len(fids), 64):
        part = fids[s:s + 64]
        texts = [film_text(c, f)[1] or "" for f in part]
        vecs = _unit(_embed(texts))
        for f, v in zip(part, vecs):
            rec = compute_film(c, win, f, v)
            if rec:
                save_film(c, rec)
        c.commit()
        if (s // 64) % 20 == 0:
            print(f"  {s + len(part):,}/{len(fids):,} ({time.time() - t0:.0f} s)", flush=True)


# ------------------------------------------------------------------ corpus test
def _rank(x):
    order = np.argsort(x, kind="mergesort")
    r = np.empty(len(x))
    r[order] = np.arange(len(x))
    return r


def spearman(x, y):
    rx, ry = _rank(np.asarray(x)), _rank(np.asarray(y))
    rx -= rx.mean()
    ry -= ry.mean()
    return float((rx * ry).sum() / (np.sqrt((rx ** 2).sum() * (ry ** 2).sum()) + 1e-12))


def corr_test(x, y, n_boot=1000, n_perm=1000, seed=0):
    x, y = np.asarray(x, dtype=float), np.asarray(y, dtype=float)
    rho = spearman(x, y)
    rng = np.random.default_rng(seed)
    boots = [spearman(x[i], y[i]) for i in (rng.integers(0, len(x), len(x)) for _ in range(n_boot))]
    perm = [spearman(x, rng.permutation(y)) for _ in range(n_perm)]
    p = (1 + sum(abs(v) >= abs(rho) for v in perm)) / (n_perm + 1)
    return {"n": len(x), "rho": round(rho, 4), "ci": [round(float(np.percentile(boots, 2.5)), 4),
                                                      round(float(np.percentile(boots, 97.5)), 4)], "p": round(p, 4)}


def success_residuals(c):
    """{tmdb_id: log(gross/budget) minus the median of films of the same first genre and year (5+ films),
    or of the same genre and decade when the year has fewer than 5}."""
    rows = c.execute("select f.tmdb_id, f.year, f.genres_json, m.roi from films f join money m on m.tmdb_id = f.tmdb_id "
                     "where m.roi > 0 and m.budget >= 100000").fetchall()
    by_year, by_decade = {}, {}
    for fid, y, g, roi in rows:
        gg = (json.loads(g or "[]") or ["?"])[0]
        by_year.setdefault((gg, y), []).append((fid, float(np.log(roi))))
        by_decade.setdefault((gg, (y or 0) // 10), []).append((fid, float(np.log(roi))))
    med_y = {k: float(np.median([v for _f, v in xs])) for k, xs in by_year.items() if len(xs) >= 5}
    med_d = {k: float(np.median([v for _f, v in xs])) for k, xs in by_decade.items() if len(xs) >= 5}
    out = {}
    for fid, y, g, roi in rows:            # same genre and year; else same genre and decade
        gg = (json.loads(g or "[]") or ["?"])[0]
        m = med_y.get((gg, y), med_d.get((gg, (y or 0) // 10)))
        if m is not None:
            out[fid] = float(np.log(roi)) - m
    return out


def cmd_analyze(a):
    c = connect()
    res = success_residuals(c)
    rows = c.execute("select tmdb_id, resonance, themes_json, n_window from news_film where version = ? "
                     "and resonance is not null and n_window >= ?", (VERSION, a.min_headlines)).fetchall()
    data = [(fid, r, json.loads(t or "{}")) for fid, r, t, n in rows if fid in res]
    lines = [f"News at release vs box-office success ({datetime.date.today()}, version {VERSION})",
             "success = log(gross / budget) minus the median of films of the same first genre and year (5+ films; "
             "else the same genre and decade);",
             f"window = release date +/- {WINDOW} days; control = the same window 6 months before and after;",
             f"resonance = mean similarity of the film's {TOPK} closest headlines in the window minus the controls'.", ""]
    out = {"version": VERSION, "at": F.now()}
    if len(data) < 30:
        lines.append(f"only {len(data)} films have both a resonance score and a success residual: fetch + embed more news")
    else:
        t = corr_test([d[1] for d in data], [res[d[0]] for d in data])
        out["resonance"] = t
        lines.append(f"ALL films: Spearman rho(resonance, success) = {t['rho']:+.3f}  95% CI [{t['ci'][0]:+.3f}, "
                     f"{t['ci'][1]:+.3f}]  permutation p = {t['p']:.3f}  (n = {t['n']:,})")
        out["themes"] = {}
        for th in THEMES:
            sub = [(d[0], (d[2].get("window") or {}).get(th, 0) - (d[2].get("control") or {}).get(th, 0))
                   for d in data if th in (d[2].get("film") or [])]
            if len(sub) >= 30:
                tt = corr_test([s[1] for s in sub], [res[s[0]] for s in sub], n_boot=500, n_perm=500)
                out["themes"][th] = tt
                lines.append(f"  {th:13s} films touching it: rho(theme share lift, success) = {tt['rho']:+.3f} "
                             f"[{tt['ci'][0]:+.3f}, {tt['ci'][1]:+.3f}] p = {tt['p']:.3f} (n = {tt['n']:,})")
        n_doc = c.execute("select count(*) from news_film where documented_json not in ('[]', '') and version = ?",
                          (VERSION,)).fetchone()[0]
        out["documented_films"] = n_doc
        lines += ["", f"films whose own Wikipedia box office / release / reception text names a news event: {n_doc:,}",
                  "Reading: a CI that includes 0 means no reliable link across films; a film's related headlines "
                  "are then a coincidence of topics unless a source states the link."]
    c.execute("insert or replace into news_corpus values ('summary', ?)", (json.dumps(out),))
    c.commit()
    path = os.path.join(HERE, "results", "news_link_summary.txt")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, "w").write("\n".join(lines) + "\n")
    print("\n".join(lines))
    print(f"-> {path}")


# ------------------------------------------------------------------ the answer block (tools.py)
SRC = {"nyt": "NYT Archive", "wikipedia": "Wikipedia: Current events"}


def news_block(fid, title, conn=None, compute=True):
    """What the answer model sees. Precomputed (news_film) when available, else computed now."""
    c = conn or F.connect(readonly=True)
    if c is None:
        return None
    try:
        row = c.execute("select release_date, win_from, win_to, n_window, resonance, top_sim, related_json, "
                        "themes_json, documented_json from news_film where tmdb_id = ?", (fid,)).fetchone()
    except sqlite3.Error:
        return None
    if row:
        rec = dict(zip(("release_date", "win_from", "win_to", "n_window", "resonance", "top_sim"), row[:6]))
        rec.update(related=json.loads(row[6] or "[]"), themes=json.loads(row[7] or "{}"),
                   documented=json.loads(row[8] or "[]"))
    elif compute:
        try:
            rec = compute_film(c, Window(c), fid)
        except (sqlite3.Error, SystemExit):
            rec = None
        if not rec:
            return None
    else:
        return None
    if not rec.get("n_window") and not rec.get("documented"):
        return None
    lines = [f"News around the release of {title} (released {rec['release_date']}; headlines {rec['win_from']} to "
             f"{rec['win_to']}: {rec['n_window']} stored)"]
    srcs = sorted({SRC.get(r.get("source"), r.get("source")) for r in rec["related"]})
    if rec["related"]:
        lines.append("Headlines closest in topic to the film (similarity 0-1 to its plot and keywords; "
                     "a topic match is not evidence of an effect):")
        for r in rec["related"][:5]:
            lines.append(f"- {r['date']}: \"{r['headline'][:160]}\" ({r['sim']:.2f}) [{SRC.get(r['source'], r['source'])}]")
    th = rec.get("themes") or {}
    if th.get("top"):
        w, ctl = th.get("window") or {}, th.get("control") or {}
        lines.append("Biggest news themes in the window: " + "; ".join(
            f"{k.replace('_', ' ')} {100 * w.get(k, 0):.0f}% of headlines (usual {100 * ctl.get(k, 0):.0f}%)"
            for k in th["top"]) + " [computed from the headlines]")
        mine = [k for k in th.get("film") or [] if w.get(k, 0) or ctl.get(k, 0)]
        if mine:
            lines.append("Themes the film shares with the news: " + "; ".join(
                f"{k.replace('_', ' ')} {100 * w.get(k, 0):.0f}% vs usual {100 * ctl.get(k, 0):.0f}%" for k in mine))
    if rec.get("resonance") is not None:
        pct = None
        try:
            allr = [r for (r,) in c.execute("select resonance from news_film where resonance is not null")]
            if len(allr) >= 30:
                pct = 100 * sum(1 for r in allr if r < rec["resonance"]) / len(allr)
        except sqlite3.Error:
            pass
        lines.append(f"Topic overlap with the news at release vs 6 months either side: {rec['resonance']:+.3f}"
                     + (f" (higher than {pct:.0f}% of films)" if pct is not None else "") + " [Movie Buff analysis]")
    for d in rec.get("documented") or []:
        lines.append(f"Stated link [Wikipedia: {d['kind'].replace('_', ' ').title()}]: \"{d['sentence'][:300]}\"")
    try:
        summ = json.loads((c.execute("select value from news_corpus where key = 'summary'").fetchone() or ["{}"])[0])
    except sqlite3.Error:
        summ = {}
    t = summ.get("resonance")
    if t:
        verdict = "no reliable link" if t["ci"][0] <= 0 <= t["ci"][1] else \
            ("a small positive link" if t["rho"] > 0 else "a small negative link")
        lines.append(f"Across {t['n']:,} films: on-topic news at release vs box-office return relative to similar films, "
                     f"Spearman rho {t['rho']:+.3f} (95% CI {t['ci'][0]:+.3f} to {t['ci'][1]:+.3f}) - {verdict} "
                     f"[Movie Buff analysis]")
    lines.append("How to use this: say what was in the news; call a connection 'stated' only when a source above "
                 "states it; otherwise it is a coincidence of topics, and the cross-film figure says how much such "
                 "coincidences matter in general. Do not claim the news caused the result."
                 + (" Headlines: " + ", ".join(srcs) + " (Data provided by The New York Times where marked)."
                    if srcs else ""))
    return "\n".join(lines)


def cmd_show(a):
    c = connect()
    r = c.execute("select tmdb_id, title from films where title like ? order by vote_count desc limit 1",
                  (a.title,)).fetchone()
    if not r:
        raise SystemExit("no such film in facts.db")
    print(news_block(r[0], r[1], c) or "(no news stored for that window)")


def cmd_status(_a):
    c = connect()
    for src, n, mn, mx in c.execute("select source, count(*), min(month), max(month) from news_items group by source"):
        print(f"{src:10s} {n:,} items, {mn} to {mx}")
    print(f"months fetched: {c.execute('select count(*) from news_months where status = ' + chr(39) + 'ok' + chr(39)).fetchone()[0]:,}"
          f"; vectors: {c.execute('select count(*) from news_vec').fetchone()[0]:,}"
          f"; films computed: {c.execute('select count(*) from news_film').fetchone()[0]:,}")
    s = c.execute("select value from news_corpus where key = 'summary'").fetchone()
    if s:
        print("corpus:", json.loads(s[0]).get("resonance"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["fetch", "embed", "films", "analyze", "show", "status"])
    ap.add_argument("title", nargs="?")
    ap.add_argument("--source", default="nyt", choices=["nyt", "wiki"])
    ap.add_argument("--from", dest="year_from", type=int)
    ap.add_argument("--to", dest="year_to", type=int, default=datetime.date.today().year)
    ap.add_argument("--months", help="comma list YYYY-MM")
    ap.add_argument("--films-only", action="store_true", default=True)
    ap.add_argument("--max-months", type=int, default=450)
    ap.add_argument("--batch", type=int, default=64)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--min-headlines", type=int, default=30)
    a = ap.parse_args()
    if a.source == "wiki":
        a.source = "wikipedia"
    {"fetch": cmd_fetch, "embed": cmd_embed, "films": cmd_films, "analyze": cmd_analyze, "show": cmd_show,
     "status": cmd_status}[a.cmd](a)


if __name__ == "__main__":
    main()
