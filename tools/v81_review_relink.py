#!/usr/bin/env python3
"""v8.1 round 47 (16 Nov 2026): link stored Guardian / NYT reviews that were never matched to a film - no API calls.

Round 46 counted 9,678 of 25,032 Guardian reviews and 3,782 of 9,220 NYT reviews in critic_reviews with no film.
They cost calls once already; linking them is free. This tool PROPOSES a film for each, from the stored headline /
title guess and the publication date, and writes the proposals and a sample to grade. It changes no table.

Matching, in order (a proposal needs one of these; anything else is left unmatched):
  1. title + year window   a cleaned guess equals a film title and that film came out between 3 years before and
                           1 year after the review (wider than the nightly's -2..+1: DVD and festival reviews)
  2. only film with title  exactly ONE film has that title, and the review is not older than the film
Guesses from one review: the stored title guess and the headline, each cleaned of "review", star marks, "DVD:",
"Film of the week", trailing critic names after " – ", and "Lobster, The" -> "The Lobster"; accents folded.
Rule (written before the run): a 50-row random sample of the proposals, graded by a person or an AI rater recorded
as such, must be >= 90% right before any proposal is written to critic_reviews (a later, separate step).
    python tools/v81_review_relink.py [--db facts.db]
"""
import argparse
import csv
import json
import os
import random
import re
import sqlite3
import sys
import unicodedata

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LO, HI = -3, 1
STRIP = [r"\b(?:film|dvd|blu-?ray|dvd and blu-?ray|digital|streaming)\s*(?:review)?s?\s*:\s*", r"^\s*film of the week\s*[:\-–—]\s*",
         r"\s*[–—\-:|]\s*review\b.*$", r"\breview\b\s*[:\-–—]?\s*", r"[★☆*]+", r"\s*\(\d{4}\)\s*$", r"\s+[–—]\s+.*$"]


def norm(s):
    s = unicodedata.normalize("NFKD", str(s or ""))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", " ", s.casefold().replace("&", "and"))).strip()


def guesses(title_guess, headline):
    out = []
    for raw in (title_guess, headline):
        if not raw:
            continue
        s = re.sub(r"<[^>]+>", "", str(raw))
        for p in STRIP:
            s = re.sub(p, " ", s, flags=re.I)
        m = re.match(r"^(.*),\s*(the|a|an)\s*$", s.strip(), re.I)
        if m:
            s = f"{m.group(2)} {m.group(1)}"
        n = norm(s)
        if n and n not in out and len(n) >= 2:
            out.append(n)
    return out


def propose(idx, title_guess, headline, year):
    """(tmdb_id, how) or (None, None)."""
    for g in guesses(title_guess, headline):
        cands = idx.get(g) or []
        if not cands:
            continue
        if year:
            near = [c for c in cands if c[1] and year + LO <= c[1] <= year + HI]
            if near:
                return max(near, key=lambda c: c[2])[0], "title + year window"
        if len(cands) == 1 and (not year or not cands[0][1] or cands[0][1] <= year):
            return cands[0][0], "only film with the title"
    return None, None


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=os.environ.get("FACTS_DB", os.path.join(HERE, "facts.db")))
    ap.add_argument("--sample", type=int, default=50)
    ap.add_argument("--out", default=os.path.join(HERE, "results", "v81_review_relink"))
    a = ap.parse_args(argv)
    c = sqlite3.connect(f"file:{a.db}?mode=ro", uri=True)
    if not c.execute("select 1 from sqlite_master where name = 'critic_reviews'").fetchone():
        print("UNRUN: no critic_reviews table in this database")
        return 1
    idx, label = {}, {}
    for fid, t, ot, y, vc in c.execute("select tmdb_id, title, original_title, year, coalesce(vote_count, 0) from films"):
        label[fid] = f"{t} ({y})"
        for name in {t, ot}:
            if name:
                idx.setdefault(norm(name), []).append((fid, y, vc))
    rows = c.execute("select review_key, source, title_guess, headline, published, url from critic_reviews where tmdb_id is null").fetchall()
    props, by = [], {}
    for key, src, tg, hd, pub, url in rows:
        y = int(pub[:4]) if pub and str(pub)[:4].isdigit() else None
        fid, how = propose(idx, tg, hd, y)
        b = by.setdefault(src, {"unmatched": 0, "proposed": 0, "title + year window": 0, "only film with the title": 0})
        b["unmatched"] += 1
        if fid:
            b["proposed"] += 1
            b[how] += 1
            props.append((key, src, hd, (pub or "")[:10], url, fid, how))
    rnd = random.Random(47)
    pick = rnd.sample(props, min(a.sample, len(props)))
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    with open(a.out + "_check.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["source", "published", "headline", "proposed film", "how", "link", "right (y/n)", "note"])
        for key, src, hd, pub, url, fid, how in pick:
            w.writerow([src, pub, hd, label.get(fid, "?"), how, url, "", ""])
    L = [f"Unmatched stored reviews, proposed links (v8.1 round 47) - no API calls, no table changed"]
    for src, b in sorted(by.items()):
        L.append(f"  {src}: {b['unmatched']:,} unmatched -> {b['proposed']:,} proposed ({b['proposed'] / max(1, b['unmatched']):.0%}): "
                 f"{b['title + year window']:,} by title + year, {b['only film with the title']:,} as the only film with the title")
    L += [f"  sample to grade: {len(pick)} rows -> results/{os.path.basename(a.out)}_check.csv",
          "RULE (>= 90% right on that sample before anything is written): not known until it is graded", "", "examples:"]
    L += [f"  {src:8s} {pub} {hd[:60]!r} -> {label.get(fid, '?')} [{how}]" for key, src, hd, pub, url, fid, how in pick[:8]]
    with open(a.out + ".txt", "w", encoding="utf-8") as fh:
        fh.write("\n".join(L) + "\n")
    with open(a.out + ".json", "w") as fh:
        json.dump({"by_source": by, "proposals": len(props), "sample": len(pick)}, fh, indent=1)
    print("\n".join(L))
    return 0


if __name__ == "__main__":
    sys.exit(main())
