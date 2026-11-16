#!/usr/bin/env python3
"""v8.1 round 46 (16 Nov 2026): a second opinion on which film a YouTube review / essay video is about.

The 11 Nov hand check (50 confident matches of youtube_analysis.py, rater: Claude, an AI) found 15 wrong, 30%.
Every error was one of five patterns. This module looks at each confident match again and KEEPS it, SWITCHES it to
another film, or DROPS it. It changes no table: it reads the facts.db copy and writes two result files.

Rules, in this order (written with the 50 checked rows in view - so those rows cannot grade it; see the rule):
  G  not a film      the video title says it is a game ("Game Review", "(PS5)", "gameplay") or television
                     ("Episode(s)", "Season 2", "S01E02", "TV series", "series finale")                    -> DROP
  N  sequel number   the matched title is followed by a number or roman numeral that is not part of the film's own
                     title ("Transformers 4"): a film called exactly "<title> <number>" -> SWITCH, else     -> DROP
  W  another title   a one-word title that opens the video title (or a part after ":" "-" "|") and is followed,
                     with no punctuation between, by only one or two more capitalised words that are not review
                     words ("Best of the Worst: Wicked World")                                              -> DROP
  F  series name     the matched title is the name of a series (two or more other films carry it in their title)
                     and the video does not treat it as a film title: it is not at the start of the video title or
                     of a part after ":" "-" "|", or it runs on into another capitalised word
                     ("Star Wars Standalone Details Emerge")                                                -> DROP
  S  subtitle        a series name followed by ":" or "-" and a capitalised phrase that is not a review word
                     ("Star Wars: Rogue One - ..."): a film whose title starts with or contains that phrase
                     -> SWITCH (nearest to the upload year), none found                                     -> DROP
  Y  same title      several films share the title (remakes): the one released within a year of the upload wins
                     over the most-voted one ("Halloween - Trailer Review" uploaded 2018 -> Halloween 2018) -> SWITCH
  A match whose title is quoted or carries its year in the video title is trusted and only rule G applies.
Rule for calling it better (before any run): on a NEW random sample of its confident matches (videos outside the 50
already checked), graded by a person - or by an AI rater recorded as such - right film >= 90%; AND of the 35 matches
the first check found right, at least 32 are still kept (>= 90%). The numbers this tool prints for the 50 checked
rows are labelled "seen sample": they show the rules do what they were written to do, nothing more.
    python tools/v81_yt_match.py [--db facts.db] [--min-score 0.6] [--sample 60]
"""
import argparse
import csv
import json
import os
import random
import re
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

GENERIC = set("""review reviews reviewed reviewing trailer trailers teaser movie movies film films explained explanation
analysis breakdown ending endings reaction reactions thoughts spoiler spoilers spoilercast official video essay re is was
are has had have and by with vs versus in on at of for from to the a an how what why when who where which did does do can
could should would will it its this that these those scene scenes clip clips featurette commentary retrospective revisited
anniversary cast interview interviews podcast discussion director directors cut edition extended remastered imax 4k blu ray
dvd unboxing soundtrack score first second final full honest quick non free my our your i we you not no""".split())
R_GAME = re.compile(r"\bgame\s+review\b|\bgameplay\b|\bvideo\s+game\b|\bDLC\b|\((?:ps[1-5]|xbox[^)]*|switch|pc|nintendo[^)]*)\)", re.I)
R_TV = re.compile(r"\bepisodes?\b|\bseason\s+\d|\bS\d{1,2}\s?E\d{1,2}\b|\bseries\s+(?:review|finale|premiere|recap)\b|"
                  r"\btv\s+(?:series|show|review)\b|\bminiseries\b", re.I)
R_TOKEN = re.compile(r"[^\W_]+|&")
R_BREAK = re.compile(r"[:\-–—|(\[/]")
ROMAN = {"ii", "iii", "iv", "v", "vi", "vii", "viii", "ix", "x"}


def norm(s):
    import unicodedata
    s = unicodedata.normalize("NFKD", str(s or ""))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", " ", s.casefold().replace("&", "and"))).strip()


def tokens(raw):
    """[(normalised token, raw token, start, end)] of a video title."""
    out = []
    for m in R_TOKEN.finditer(raw or ""):
        n = norm(m.group(0))
        if n:
            out.append((n, m.group(0), m.start(), m.end()))
    return out


def find(toks, n):
    """Index of the first place the normalised title n appears as whole tokens, or -1."""
    want = n.split()
    have = [t[0] for t in toks]
    for i in range(len(have) - len(want) + 1):
        if have[i:i + len(want)] == want:
            return i
    return -1


def is_series(n, idx):
    """True when two or more OTHER indexed titles carry this title inside theirs."""
    k = 0
    for m in idx:
        if m != n and f" {n} " in f" {m} ":
            k += 1
            if k >= 2:
                return True
    return False


def title_like(raw_tok):
    return raw_tok[:1].isupper() and raw_tok.lower() not in GENERIC and not raw_tok.isdigit()


def by_year(cands, year):
    """The candidate released within a year of `year` (most-voted among those), or None."""
    if not year:
        return None
    near = [c for c in cands if c[1] and abs(c[1] - year) <= 1]
    return max(near, key=lambda c: c[2]) if near else None


def decide(title, pub_year, fid, how, idx, rev):
    """(action, tmdb_id, rule, why) for one confident match. idx = {normalised title: [(id, year, votes)]},
    rev = {id: normalised title}."""
    t = title or ""
    if R_GAME.search(t):
        return "drop", None, "G", "the video title says it is a game"
    if R_TV.search(t):
        return "drop", None, "G", "the video title says it is television"
    n = rev.get(fid)
    if not n or how.startswith("quoted") or how.startswith("title in the description"):
        return "keep", fid, "-", "trusted as matched"
    toks = tokens(t)
    i = find(toks, n)
    if i < 0:
        return "keep", fid, "-", "title not found as whole words (left as matched)"
    k = len(n.split())
    nxt = toks[i + k] if i + k < len(toks) else None
    sep_after = t[toks[i + k - 1][3]:nxt[2]] if nxt else t[toks[i + k - 1][3]:]
    sep_before = t[toks[i - 1][3]:toks[i][2]] if i > 0 else ""
    at_start = i == 0 or bool(R_BREAK.search(sep_before))
    brk = bool(R_BREAK.search(sep_after))
    cands = idx.get(n, [])
    # N: a sequel number that is not part of this film's title
    if nxt and not brk and (nxt[0] in ROMAN or (nxt[0].isdigit() and 2 <= int(nxt[0]) <= 20)):
        sib = idx.get(f"{n} {nxt[0]}")
        if sib:
            best = by_year(sib, pub_year) or max(sib, key=lambda c: c[2])
            return "switch", best[0], "N", f"\"{n} {nxt[0]}\" is its own film"
        return "drop", None, "N", f"followed by \"{nxt[1]}\": a sequel this title does not name"
    one_word = k == 1
    # W: a one-word title running on into another capitalised word
    if one_word and nxt and not brk and at_start:
        rest, j = [], i + k
        while j < len(toks) and not (j > i + k and R_BREAK.search(t[toks[j - 1][3]:toks[j][2]])):
            rest.append(toks[j][1])
            j += 1
        if len(rest) <= 2 and all(title_like(x) for x in rest):
            return "drop", None, "W", f"\"{toks[i][1]} {' '.join(rest)}\" reads as a different title"
    if is_series(n, idx):
        if not at_start:
            return "drop", None, "F", "a series name inside the video title, not the film as its subject"
        if nxt and not brk and title_like(nxt[1]):
            return "drop", None, "F", f"a series name running on into \"{nxt[1]}\""
        after = toks[i + k + 1] if nxt and nxt[0] in ("the", "a", "an") and i + k + 1 < len(toks) else nxt
        if nxt and brk and title_like(after[1]):      # "Star Wars: The Mandalorian and Grogu": the article is part of the subtitle
            # S: the phrase after the break, up to the next break
            j = i + k
            phrase = []
            while j < len(toks) and len(phrase) < 5:
                if phrase and R_BREAK.search(t[toks[j - 1][3]:toks[j][2]]):
                    break
                phrase.append(toks[j][0])
                j += 1
            for ln in range(len(phrase), 1, -1):
                p = " ".join(phrase[:ln])
                hits = [c for m, cs in idx.items() if m != n and (m == p or m.startswith(p + " ") or f" {p} " in f" {m} ")
                        for c in cs]
                if hits:
                    best = by_year(hits, pub_year) or max(hits, key=lambda c: c[2])
                    return "switch", best[0], "S", f"the subtitle \"{p}\" names another film of the series"
            return "drop", None, "S", "a series name with a subtitle that is no film in the index"
    # Y: remakes - the film released around the upload
    if len(cands) > 1:
        best = by_year(cands, pub_year)
        if best and best[0] != fid:
            return "switch", best[0], "Y", f"same title, released {best[1]}, uploaded {pub_year}"
    return "keep", fid, "-", "kept"


def rematch(videos, idx, match_films, min_score=0.6):
    """For every video: the first-pass confident matches and what the rules above do with each.
    videos = [(video_key, title, description, published_at)] -> [dict]."""
    rev = {}
    for n, cs in idx.items():
        for fid, _y, _v in cs:
            rev.setdefault(fid, n)
    out = []
    for key, title, desc, pub in videos:
        year = int(pub[:4]) if pub and str(pub)[:4].isdigit() else None
        for fid, score, how in match_films(title, desc, idx):
            if score < min_score:
                continue
            action, new, rule, why = decide(title, year, fid, how, idx, rev)
            out.append({"video": key, "title": title, "published": (pub or "")[:10], "v1": fid, "score": score, "how": how,
                        "action": action, "v2": new, "rule": rule, "why": why})
    return out


def seen_sample(path, rows):
    """How the 50 checked rows fare: {right_kept, right_lost, wrong_fixed_or_dropped, wrong_unchanged, missing}."""
    by = {}
    for r in rows:
        by.setdefault(r["video"], []).append(r)
    res = {"right_kept": 0, "right_lost": [], "wrong_dropped": 0, "wrong_switched": 0, "wrong_unchanged": [], "not_found": 0}
    for r in csv.DictReader(open(path, encoding="utf-8-sig")):
        key = r["video"].rsplit("/", 1)[-1]
        ans = (r.get("right (y/n)") or "").strip().lower()[:1]
        film = r.get("matched film", "")
        mine = [m for m in by.get(key, []) if m.get("v1_label") == film] or by.get(key, [])
        if not mine or ans not in ("y", "n"):
            res["not_found"] += 1
            continue
        m = mine[0]
        if ans == "y":
            if m["action"] == "keep":
                res["right_kept"] += 1
            else:
                res["right_lost"].append(f"{r['video title'][:60]} -> {m['action']} ({m['rule']})")
        elif m["action"] == "drop":
            res["wrong_dropped"] += 1
        elif m["action"] == "switch":
            res["wrong_switched"] += 1
        else:
            res["wrong_unchanged"].append(r["video title"][:60])
    return res


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=os.environ.get("FACTS_DB", os.path.join(HERE, "facts.db")))
    ap.add_argument("--min-score", type=float, default=0.6)
    ap.add_argument("--sample", type=int, default=60)
    ap.add_argument("--checked", default=None, help="the 11 Nov graded CSV (default: looked for in results/)")
    ap.add_argument("--out", default=os.path.join(HERE, "results", "v81_yt_match"))
    a = ap.parse_args(argv)
    import sqlite3
    import youtube_analysis as Y
    c = sqlite3.connect(f"file:{a.db}?mode=ro", uri=True)
    if not c.execute("select 1 from sqlite_master where name = 'yt_analysis'").fetchone():
        print("UNRUN: this facts.db has no yt_analysis table")
        return 1
    idx = Y.title_index(c, 300)
    label = {fid: f"{t} ({y})" for fid, t, y in c.execute("select tmdb_id, title, year from films")}
    videos = c.execute("select video_key, title, description, published_at from yt_analysis").fetchall()
    rows = rematch(videos, idx, Y.match_films, a.min_score)
    for r in rows:
        r["v1_label"] = label.get(r["v1"], "?")
        r["v2_label"] = label.get(r["v2"], "") if r["v2"] else ""
    n = len(rows)
    by_action = {k: sum(1 for r in rows if r["action"] == k) for k in ("keep", "switch", "drop")}
    by_rule = {}
    for r in rows:
        if r["action"] != "keep":
            by_rule[r["rule"]] = by_rule.get(r["rule"], 0) + 1
    L = [f"YouTube video-film matches, second opinion (v8.1 round 46): {len(videos):,} videos, {n:,} confident first-pass matches (score >= {a.min_score})",
         f"  kept {by_action['keep']:,} ({by_action['keep'] / max(1, n):.1%}) | switched to another film {by_action['switch']:,} | dropped {by_action['drop']:,}",
         "  changed, by rule: " + ", ".join(f"{k} {v:,}" for k, v in sorted(by_rule.items())), ""]
    res = {"videos": len(videos), "confident": n, "actions": by_action, "by_rule": by_rule}
    checked = a.checked
    for p in (os.path.join(HERE, "results", "video_match_check_claude.csv"),
              os.path.expanduser("~/movie-buff/rag/results/video_match_check_claude.csv")):
        if not checked and os.path.exists(p):
            checked = p
    seen_keys = set()
    if checked and os.path.exists(checked):
        s = seen_sample(checked, rows)
        seen_keys = {r["video"].rsplit("/", 1)[-1] for r in csv.DictReader(open(checked, encoding="utf-8-sig"))}
        res["seen_sample"] = s
        wrong = s["wrong_dropped"] + s["wrong_switched"] + len(s["wrong_unchanged"])
        right = s["right_kept"] + len(s["right_lost"])
        L += [f"seen sample (the 50 rows the rules were written from - NOT a test): of {right} right matches {s['right_kept']} kept; "
              f"of {wrong} wrong matches {s['wrong_dropped']} dropped, {s['wrong_switched']} switched, {len(s['wrong_unchanged'])} unchanged; "
              f"{s['not_found']} rows not found",
              f"  rule part 2 (>= 32 of the 35 right matches kept): {'ok' if s['right_kept'] >= 32 else 'NOT ok'}"]
        L += [f"    right match lost: {x}" for x in s["right_lost"]] + [f"    wrong match unchanged: {x}" for x in s["wrong_unchanged"]]
    else:
        L.append("seen sample: the 11 Nov graded CSV was not found - not compared")
    rnd = random.Random(46)
    pool = [r for r in rows if r["action"] != "drop" and r["video"] not in seen_keys]
    pick = rnd.sample(pool, min(a.sample, len(pool)))
    drops = [r for r in rows if r["action"] == "drop" and r["video"] not in seen_keys]
    dpick = rnd.sample(drops, min(20, len(drops)))
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    with open(a.out + "_check2.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["part", "video", "uploaded", "video title", "film after the rules", "first-pass film", "rule", "right (y/n)", "note"])
        for r in pick:
            w.writerow(["match", f"https://youtu.be/{r['video']}", r["published"], r["title"], r["v2_label"], r["v1_label"], r["rule"], "", ""])
        for r in dpick:
            w.writerow(["dropped (y = the first-pass film was wrong)", f"https://youtu.be/{r['video']}", r["published"], r["title"], "",
                        r["v1_label"], r["rule"], "", ""])
    res["new_sample"] = {"matches": len(pick), "dropped": len(dpick), "file": os.path.basename(a.out) + "_check2.csv"}
    L += ["", f"new sample to grade: {len(pick)} matches after the rules + {len(dpick)} dropped ones -> results/{os.path.basename(a.out)}_check2.csv",
          "RULE part 1 (right film >= 90% on the new sample): not known until that file is graded", "",
          "examples of each rule:"]
    for rule in sorted(by_rule):
        for r in [x for x in rows if x["rule"] == rule and x["action"] != "keep"][:3]:
            L.append(f"  {rule} {r['action']:6s} {r['title'][:70]!r}: {r['v1_label']} -> {r['v2_label'] or '(none)'}")
    with open(a.out + ".txt", "w", encoding="utf-8") as fh:
        fh.write("\n".join(L) + "\n")
    with open(a.out + ".json", "w") as fh:
        json.dump(res, fh, indent=1)
    print("\n".join(L))
    return 0


if __name__ == "__main__":
    sys.exit(main())
