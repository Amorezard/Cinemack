#!/usr/bin/env python3
"""
6 Nov: Letterboxd ratings from public research datasets (no scraping, no Letterboxd API) -> facts.db letterboxd.

Two datasets, either or both:
  kaggle  "Letterboxd Movie Ratings Data" by samlearner (Kaggle, scraped ~2021: users / ratings / movie data).
          Download it yourself (Kaggle account) and pass the .zip or the unzipped folder:
              kaggle datasets download -d samlearner/letterboxd-movie-ratings-data      # needs ~/.kaggle/kaggle.json
          The loader finds the ratings file (a user column, a movie column and a rating column) and the movie file
          (movie id + title, and TMDB / IMDb ids or a year if present) by their column names and prints what it used.
          Ratings on a 1-10 scale (half-stars doubled) are halved to Letterboxd's 0.5-5.
  hf      "letterboxd-10m-movies-ratings-2025" by titouann (Hugging Face, Dec 2025, CC BY-NC-SA 4.0: research use,
          attribution, share-alike): 10.5M ratings from the 6,519 most popular members. Titles only, no ids or years,
          so a film is matched only when exactly one catalogue film with 50+ TMDB votes has that title (match 0.5).

Matching (match column): TMDB id or IMDb id 1.0; title + year within -2/+1 years 1.0; title only, unique among
films with 50+ votes 0.5. performance.py uses match 1.0 only. Each film keeps one row per source.

What the numbers are: the mean rating of the dataset's members, who are a self-selected, film-heavy group, at the
time the dataset was collected (years after release for most films). Post-release only; not "audiences".

    python letterboxd_data.py load --kaggle ~/letterboxd-movie-ratings-data.zip
    python letterboxd_data.py load --hf                  # downloads with huggingface_hub (or --file a local .parquet/.csv)
    python letterboxd_data.py report                     # coverage + agreement with IMDb / RT / Metacritic / MovieLens
    python letterboxd_data.py show "Heat"
"""
import argparse
import csv
import io
import json
import math
import os
import re
import sys
import unicodedata
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import facts_db as F  # noqa: E402

HF_REPO = "titouann/letterboxd-10m-movies-ratings-2025"
SUMMARY = os.path.join(HERE, "results", "letterboxd_summary.txt")
LABEL = {"kaggle": "Letterboxd (Kaggle 'Letterboxd Movie Ratings Data', ~2021)",
         "hf": "Letterboxd (Hugging Face letterboxd-10m-movies-ratings-2025, CC BY-NC-SA 4.0)"}
DDL = """
create table if not exists letterboxd(source text, lb_key text, tmdb_id integer, title text, n integer, mean real,
  sd real, share_hi real, share_lo real, match real, loaded_at text, primary key (source, lb_key));
create index if not exists letterboxd_film on letterboxd(tmdb_id);
"""
csv.field_size_limit(10 ** 7)


def norm(t):
    """Title key (same rule as fetch_free_data.py): no accents, punctuation, case or leading article."""
    t = unicodedata.normalize("NFKD", str(t or "")).encode("ascii", "ignore").decode()
    t = t.lower().replace("&", " and ")
    t = re.sub(r"[^a-z0-9 ]+", " ", t)
    t = re.sub(r"^(the|a|an) ", "", t.strip())
    return re.sub(r"\s+", " ", t).strip()


def connect():
    c = F.connect()
    c.executescript(DDL)
    return c


# ------------------------------------------------------------------ reading the files
class Source:
    """CSV files from a .zip or a folder, opened by name."""

    def __init__(self, path):
        self.path = os.path.expanduser(path)
        if zipfile.is_zipfile(self.path):
            self.z = zipfile.ZipFile(self.path)
            self.names = [n for n in self.z.namelist() if n.lower().endswith(".csv")]
        elif os.path.isdir(self.path):
            self.z = None
            self.names = [os.path.join(r, f) for r, _d, fs in os.walk(self.path) for f in fs if f.lower().endswith(".csv")]
        else:
            raise SystemExit(f"{path}: not a .zip or a folder")

    def open(self, name):
        if self.z:
            return io.TextIOWrapper(self.z.open(name), encoding="utf-8", errors="replace", newline="")
        return open(name, encoding="utf-8", errors="replace", newline="")

    def header(self, name):
        with self.open(name) as f:
            return [h.strip().lower() for h in next(csv.reader(f), [])]


def _pick(cols, *pats, avoid=()):
    for p in pats:
        for i, c in enumerate(cols):
            if re.search(p, c) and not any(re.search(a, c) for a in avoid):
                return i
    return None


def find_files(src):
    """(ratings file, its columns {user, movie, rating}), (movie file, its columns {movie, title, tmdb, imdb, year})."""
    rat = mov = None
    for n in sorted(src.names, key=lambda x: -len(x)):
        h = src.header(n)
        r = _pick(h, r"^rating(_val(ue)?)?$", r"rating", avoid=(r"count", r"avg", r"average", r"vote"))
        u = _pick(h, r"^user(_id|name)?$", r"user")
        m = _pick(h, r"^movie_id$", r"^film_id$", r"movie|film|slug")
        t = _pick(h, r"^movie_title$", r"^title$", r"title|name", avoid=(r"original",))
        if r is not None and u is not None and m is not None and rat is None:
            rat = (n, {"user": u, "movie": m, "rating": r, "title": t})
        elif m is not None and t is not None and u is None and mov is None:
            mov = (n, {"movie": m, "title": t, "tmdb": _pick(h, r"^tmdb_id$", r"tmdb(?!.*link)"),
                       "imdb": _pick(h, r"^imdb_id$", r"imdb(?!.*link)"),
                       "year": _pick(h, r"^year(_released)?$", r"year", r"release_date")})
    return rat, mov


def aggregate(rows, scale=None):
    """rows: iterable of (movie key, title or None, rating). -> {key: [title, n, sum, sumsq, hi, lo]}, scale used."""
    agg, buf = {}, []
    it = iter(rows)
    if scale is None:                       # look at the first 200k ratings: anything above 5 means a 1-10 scale
        for _ in range(200000):
            try:
                buf.append(next(it))
            except StopIteration:
                break
        vals = [r for _k, _t, r in buf if r is not None]
        scale = 0.5 if vals and max(vals) > 5 else 1.0

    def add(k, t, r):
        if r is None or not k:
            return
        v = r * scale
        if not 0.5 <= v <= 5:
            return
        a = agg.get(k)
        if a is None:
            a = agg[k] = [t, 0, 0.0, 0.0, 0, 0]
        if t and not a[0]:
            a[0] = t
        a[1] += 1
        a[2] += v
        a[3] += v * v
        a[4] += v >= 4.0
        a[5] += v <= 2.0
    for k, t, r in buf:
        add(k, t, r)
    for k, t, r in it:
        add(k, t, r)
    return agg, scale


def _float(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def read_kaggle(path):
    src = Source(path)
    rat, mov = find_files(src)
    if not rat:
        raise SystemExit(f"no ratings file found among {src.names} (need user, movie and rating columns)")
    rn, rc = rat
    print(f"ratings: {os.path.basename(rn)} columns user={src.header(rn)[rc['user']]} movie={src.header(rn)[rc['movie']]} "
          f"rating={src.header(rn)[rc['rating']]}")

    def rows():
        with src.open(rn) as f:
            rd = csv.reader(f)
            next(rd, None)
            for r in rd:
                if len(r) > max(rc["movie"], rc["rating"]):
                    yield r[rc["movie"]].strip(), (r[rc["title"]] if rc.get("title") is not None and len(r) > rc["title"] else None), \
                        _float(r[rc["rating"]])
    agg, scale = aggregate(rows())
    print(f"  {sum(a[1] for a in agg.values()):,} ratings for {len(agg):,} films (scale x{scale})")
    meta = {}
    if mov:
        mn, mc = mov
        h = src.header(mn)
        print(f"movies: {os.path.basename(mn)} columns " + ", ".join(f"{k}={h[i]}" for k, i in mc.items() if i is not None))
        with src.open(mn) as f:
            rd = csv.reader(f)
            next(rd, None)
            for r in rd:
                g = lambda k: (r[mc[k]].strip() if mc.get(k) is not None and len(r) > mc[k] else "")  # noqa: E731
                meta[g("movie")] = {"title": g("title"), "tmdb": g("tmdb"), "imdb": g("imdb"), "year": g("year")}
    else:
        print("movies: no movie file found - matching on the titles in the ratings file only (match 0.5)")
    return agg, meta


def read_hf(path=None):
    if not path:
        from huggingface_hub import hf_hub_download, list_repo_files
        files = [f for f in list_repo_files(HF_REPO, repo_type="dataset") if f.endswith((".parquet", ".csv"))]
        pq = [f for f in files if f.endswith(".parquet")] or files
        if not pq:
            raise SystemExit(f"no data files in {HF_REPO}")
        paths = [hf_hub_download(HF_REPO, f, repo_type="dataset") for f in pq]
        print(f"downloaded: {', '.join(pq)}")
    else:
        paths = [os.path.expanduser(path)]

    def rows():
        for p in paths:
            if p.endswith(".parquet"):
                import pyarrow.parquet as pq_
                pf = pq_.ParquetFile(p)
                for b in pf.iter_batches(batch_size=200000, columns=["movie_id", "title", "rating"]):
                    d = b.to_pydict()
                    yield from zip((str(x) for x in d["movie_id"]), d["title"], d["rating"])
            else:
                with open(p, encoding="utf-8", errors="replace", newline="") as f:
                    for r in csv.DictReader(f):
                        yield str(r.get("movie_id") or ""), r.get("title"), _float(r.get("rating"))
    agg, scale = aggregate(rows(), scale=1.0)
    print(f"  {sum(a[1] for a in agg.values()):,} ratings for {len(agg):,} films")
    return agg, {}


# ------------------------------------------------------------------ matching
def film_index(c):
    by_id, by_imdb, by_title = set(), {}, {}
    for fid, title, otitle, year, imdb, votes in c.execute(
            "select tmdb_id, title, original_title, year, imdb_id, vote_count from films"):
        by_id.add(fid)
        if imdb:
            by_imdb[imdb] = fid
        for t in {norm(title), norm(otitle)} - {""}:
            by_title.setdefault(t, []).append((fid, year, votes or 0))
    return by_id, by_imdb, by_title


def _year(v):
    m = re.search(r"(18|19|20)\d\d", str(v or ""))
    return int(m.group(0)) if m else None


def match(title, info, idx):
    by_id, by_imdb, by_title = idx
    info = info or {}
    t = str(info.get("tmdb") or "").split(".")[0]
    if t.isdigit() and int(t) in by_id:
        return int(t), 1.0
    im = re.search(r"tt\d{6,9}", str(info.get("imdb") or ""))
    if im and im.group(0) in by_imdb:
        return by_imdb[im.group(0)], 1.0
    cands = by_title.get(norm(info.get("title") or title), [])
    y = _year(info.get("year"))
    if y:
        near = {f for f, fy, _v in cands if fy and -2 <= fy - y <= 1}
        if len(near) == 1:
            return near.pop(), 1.0
    pop = {f for f, _fy, v in cands if v >= 50}
    if len(pop) == 1:
        return pop.pop(), 0.5
    return None, 0.0


def cmd_load(a):
    c = connect()
    jobs = ([("kaggle", lambda: read_kaggle(a.kaggle))] if a.kaggle else []) + \
           ([("hf", lambda: read_hf(a.file))] if a.hf else [])
    if not jobs:
        raise SystemExit("say which: --kaggle PATH and/or --hf")
    idx = film_index(c)
    for name, job in jobs:
        agg, meta = job()
        out, unmatched, half = [], 0, 0
        for key, (title, n, s, ss, hi, lo) in agg.items():
            info = meta.get(key) or {}
            fid, m = match(title, info, idx)
            if fid is None:
                unmatched += 1
                continue
            half += m < 1
            mean = s / n
            sd = math.sqrt(max(ss / n - mean * mean, 0.0))
            out.append((name, key, fid, info.get("title") or title, n, round(mean, 4), round(sd, 4),
                        round(hi / n, 4), round(lo / n, 4), m, F.now()))
        c.execute("delete from letterboxd where source = ?", (name,))
        c.executemany("insert or replace into letterboxd values (?,?,?,?,?,?,?,?,?,?,?)", out)
        F.set_meta(c, f"letterboxd_{name}_loaded_at", F.now())
        c.commit()
        print(f"{name}: matched {len(out):,} films ({len(out) - half:,} by id or title + year, {half:,} by title only), "
              f"{unmatched:,} not in the catalogue")


# ------------------------------------------------------------------ report
def _rank(x):
    order = sorted(range(len(x)), key=lambda i: x[i])
    r = [0.0] * len(x)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and x[order[j + 1]] == x[order[i]]:
            j += 1
        for k in range(i, j + 1):
            r[order[k]] = (i + j) / 2
        i = j + 1
    return r


def spearman(a, b):
    if len(a) < 10:
        return None
    ra, rb = _rank(a), _rank(b)
    ma, mb = sum(ra) / len(ra), sum(rb) / len(rb)
    num = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    den = math.sqrt(sum((x - ma) ** 2 for x in ra) * sum((y - mb) ** 2 for y in rb))
    return num / den if den else None


def _has(c, t):
    return bool(c.execute("select 1 from sqlite_master where type in ('table','view') and name = ?", (t,)).fetchone())


def cmd_report(_a):
    c = connect()
    L = ["Letterboxd ratings (letterboxd_data.py) - descriptive only", ""]
    srcs = [s for (s,) in c.execute("select distinct source from letterboxd order by source")]
    if not srcs:
        raise SystemExit("nothing loaded: python letterboxd_data.py load ...")
    other = {}
    if _has(c, "imdb_ratings"):
        other["IMDb rating"] = dict(c.execute("select f.tmdb_id, i.rating from films f join imdb_ratings i on i.imdb_id = f.imdb_id "
                                              "where i.votes >= 100"))
    if _has(c, "critic_scores"):
        other["Rotten Tomatoes %"] = {f: v for f, v in c.execute("select tmdb_id, rt_pct from critic_scores") if v is not None}
        other["Metacritic"] = {f: v for f, v in c.execute("select tmdb_id, mc_score from critic_scores") if v is not None}
    if _has(c, "ml_stats"):
        other["MovieLens mean"] = {f: m for f, n, m in c.execute("select tmdb_id, n_ratings, mean from ml_stats") if n >= 20}
    other["TMDB rating"] = {f: v for f, v, n in c.execute("select tmdb_id, vote_average, vote_count from films") if (n or 0) >= 50}
    for s in srcs:
        rows = c.execute("select tmdb_id, n, mean, match from letterboxd where source = ?", (s,)).fetchall()
        good = {f: m for f, n, m, mt in rows if n >= 20 and mt >= 1.0}
        L.append(f"{LABEL.get(s, s)}")
        L.append(f"  films matched {len(rows):,} (exact {sum(1 for r in rows if r[3] >= 1):,}); with 20+ ratings and an exact "
                 f"match: {len(good):,}; ratings in matched films {sum(r[1] for r in rows):,}")
        if not good:
            L.append("")
            continue
        vals = sorted(good.values())
        L.append(f"  mean rating: median {vals[len(vals) // 2]:.2f} / 5 (10th {vals[len(vals) // 10]:.2f}, "
                 f"90th {vals[9 * len(vals) // 10]:.2f})")
        for name, d in other.items():
            ks = [f for f in good if f in d]
            r = spearman([good[f] for f in ks], [d[f] for f in ks])
            if r is not None:
                L.append(f"  Spearman with {name:18s} {r:+.3f}  (n={len(ks):,})")
        if "IMDb rating" in other:            # where this group differs most from IMDb voters (z-scores, 500+ ratings)
            big = {f: m for f, n, m, mt in rows if n >= 500 and mt >= 1.0 and f in other["IMDb rating"]}
            if len(big) >= 50:
                def z(d):
                    v = list(d.values())
                    mu = sum(v) / len(v)
                    sd = math.sqrt(sum((x - mu) ** 2 for x in v) / len(v)) or 1
                    return {k: (x - mu) / sd for k, x in d.items()}
                zl, zi = z(big), z({f: other["IMDb rating"][f] for f in big})
                gap = sorted(big, key=lambda f: zl[f] - zi[f])
                title = dict(c.execute("select tmdb_id, title || ' (' || coalesce(year, '?') || ')' from films where tmdb_id in ("
                                       + ",".join(str(f) for f in gap[:8] + gap[-8:]) + ")"))
                L.append("  rated higher here than by IMDb voters: " + "; ".join(title.get(f, str(f)) for f in reversed(gap[-8:])))
                L.append("  rated lower here than by IMDb voters:  " + "; ".join(title.get(f, str(f)) for f in gap[:8]))
        L.append("")
    L.append("Limits: dataset members are a self-selected, film-heavy group; ratings were collected years after release "
             "for most films; the Hugging Face set has titles only, so title-only matches (0.5) are left out above.")
    text = "\n".join(L)
    os.makedirs(os.path.dirname(SUMMARY), exist_ok=True)
    open(SUMMARY, "w").write(text + "\n")
    print(text)


def cmd_show(a):
    c = connect()
    fid = c.execute("select tmdb_id from films where title = ? collate nocase order by vote_count desc limit 1", (a.title,)).fetchone()
    if not fid:
        raise SystemExit("no such film")
    for r in c.execute("select source, n, mean, sd, share_hi, share_lo, match from letterboxd where tmdb_id = ?", fid):
        print(f"{LABEL.get(r[0], r[0])}: {r[2]:.2f}/5 from {r[1]:,} ratings (sd {r[3]:.2f}; 4+ stars {100 * r[4]:.0f}%, "
              f"2 or less {100 * r[5]:.0f}%; match {r[6]})")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["load", "report", "show"])
    ap.add_argument("title", nargs="?")
    ap.add_argument("--kaggle", help="the Kaggle .zip or unzipped folder")
    ap.add_argument("--hf", action="store_true", help="the Hugging Face set (downloads it unless --file)")
    ap.add_argument("--file", help="a local copy of the Hugging Face .parquet / .csv")
    a = ap.parse_args()
    {"load": cmd_load, "report": cmd_report, "show": cmd_show}[a.cmd](a)


if __name__ == "__main__":
    main()
