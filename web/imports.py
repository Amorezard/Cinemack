"""
4 Nov: import a person's own Letterboxd export into their Cinemack taste profile.

Letterboxd has no open API (access by request only) and its terms forbid scraping, but every member can download
their own data: letterboxd.com -> Settings -> Data -> Export your data (a zip of CSV files). The person unzips it and
picks ratings.csv / watched.csv / watchlist.csv (diary.csv works too) on the site; the page sends the files' text.

    ratings.csv / diary.csv   Date, Name, Year, Letterboxd URI, Rating (0.5-5 stars)
    watched.csv               Date, Name, Year, Letterboxd URI
    watchlist.csv             Date, Name, Year, Letterboxd URI

What each row becomes (film_info.choose, the same as the taste buttons on a film card):
    rating >= 3.5   liked        rating <= 2   not for me        2.5-3   seen, no signal
    watchlist       want to watch (unless they rated it)          watched only: seen, no signal
A film the person already marked on the site keeps their site choice. Films are matched to the catalogue by title
and year (+-1); unmatched rows are counted and a few are shown.
"""
import csv
import io
import sqlite3
import unicodedata

MAX_ROWS = 5000
MAX_CHARS = 6_000_000


def _norm(s):
    s = unicodedata.normalize("NFKD", str(s or ""))
    s = "".join(c for c in s if not unicodedata.combining(c)).casefold().replace("&", " and ")
    return " ".join("".join(c if c.isalnum() else " " for c in s).split())


def parse_letterboxd(files):
    """files: {filename: csv text} -> {"rated": [(name, year, stars)], "watched": [...], "watchlist": [...]}"""
    out = {"rated": [], "watched": [], "watchlist": []}
    total = 0
    for fname, text in (files or {}).items():
        text = str(text or "")[:MAX_CHARS]
        rows = list(csv.DictReader(io.StringIO(text.lstrip("﻿"))))
        if not rows:
            continue
        cols = {c.strip().casefold() for c in rows[0].keys() if c}
        if "name" not in cols:
            continue
        kind = ("watchlist" if "watchlist" in fname.casefold() else
                "rated" if "rating" in cols else "watched")
        for r in rows:
            r = {str(k).strip().casefold(): (v or "").strip() for k, v in r.items() if k}
            name = r.get("name")
            if not name:
                continue
            try:
                year = int(r.get("year") or 0) or None
            except ValueError:
                year = None
            stars = None
            if kind == "rated":
                try:
                    stars = float(r.get("rating") or "")
                except ValueError:
                    stars = None
                if stars is None:                    # a diary row without a rating: watched
                    out["watched"].append((name, year, None))
                    total += 1
                    continue
            out[kind].append((name, year, stars))
            total += 1
            if total >= MAX_ROWS:
                return out
    return out


class Catalogue:
    """Title -> [(year, tmdb_id, title, votes)] over facts.db films in the catalogue."""
    def __init__(self, facts_path):
        self.by = {}
        try:
            c = sqlite3.connect(f"file:{facts_path}?mode=ro", uri=True)
            rows = c.execute("select tmdb_id, title, year, coalesce(vote_count, 0) from films where in_catalog = 1").fetchall()
            try:
                rows += [(i, t, y, v) for i, t, y, v in c.execute(
                    "select tmdb_id, original_title, year, coalesce(vote_count, 0) from films where in_catalog = 1 "
                    "and original_title is not null and original_title != title")]
            except sqlite3.Error:
                pass
            c.close()
        except sqlite3.Error:
            rows = []
        for fid, t, y, v in rows:
            self.by.setdefault(_norm(t), []).append((y, fid, t, v))

    def find(self, name, year):
        cands = self.by.get(_norm(name)) or []
        if year:
            near = [c for c in cands if c[0] and abs(int(c[0]) - year) <= 1]
            cands = sorted(near, key=lambda c: (abs(int(c[0]) - year), -c[3]))
        else:
            cands = sorted(cands, key=lambda c: -c[3])
        return (cands[0][1], cands[0][2]) if cands else None


def apply(user, parsed, facts_path, choose, existing):
    """Record the matched rows for `user`. choose(user, fid, title, choice) stores one choice; existing(user, ids)
    -> {fid: current choice}. Returns counts and up to 8 unmatched names."""
    cat = Catalogue(facts_path)
    plan = {}                                        # fid -> (choice or None, title); ratings win over the watchlist
    unmatched, n_rows, n_unmatched = [], 0, 0
    for kind in ("watchlist", "watched", "rated"):    # later kinds overwrite earlier ones
        for name, year, stars in parsed.get(kind, []):
            n_rows += 1
            m = cat.find(name, year)
            if not m:
                n_unmatched += 1
                if len(unmatched) < 8:
                    unmatched.append(f"{name} ({year})" if year else name)
                continue
            fid, title = m
            if kind == "rated":
                choice = "liked" if stars >= 3.5 else "not" if stars <= 2.0 else None
            elif kind == "watchlist":
                choice = "want"
            else:
                choice = plan.get(fid, (None,))[0]       # watched: keeps a watchlist entry out, adds no signal
                if choice == "want":
                    choice = None
            plan[fid] = (choice, title)
    have = existing(user, list(plan)) if plan else {}
    counts = {"rows": n_rows, "matched": len(plan), "liked": 0, "not": 0, "want": 0, "seen_only": 0, "kept_site": 0,
              "unmatched": n_unmatched}
    for fid, (choice, title) in plan.items():
        if have.get(fid):
            counts["kept_site"] += 1
            continue
        if not choice:
            counts["seen_only"] += 1
            continue
        choose(user, fid, title, choice)
        counts[choice] += 1
    counts["unmatched_examples"] = unmatched
    return counts
