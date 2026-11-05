#!/usr/bin/env python3
"""
D8 (v7, 27 Oct): the discovery fix - recommendations and browse lists leave out what is not a released feature film.
REC_CLEAN=1 (off until its rule passes: the 10 discovery prompts have no bad item in their top 10, and tune / check
lose <= 1). Applies to every recommendation (DISCOVER on or off), and to the backfill that tops a list up.

A film is left out (and the reason logged in the retrieve notes) when:
    not a feature   IMDb title type is not movie / tvMovie (tvSpecial, video, short, tvSeries, tvMiniSeries, ...)
    unreleased      TMDB status is not Released, or the release date is after today
    performance     a concert film or stand-up special (TMDB keywords concert film / concert / live performance /
                    stand-up comedy / music video, or genres Music + Documentary) - unless the question asks for one
    short           runtime under 50 minutes
    obscure         fewer than REC_MIN_VOTES (40) TMDB votes (the obscure mode's own floor is higher already)
    off-genre       the question names genres and the film has none of them

    python rec_clean.py check sets/discovery.json      # retrieve() each prompt; bad items in the top 10 (rule: 0)
    python rec_clean.py why "Queen Rock Montreal"       # why one film would be left out
"""
import datetime
import json
import os
import re
import sqlite3
import sys

import facts_db as F

REC_CLEAN = os.environ.get("REC_CLEAN", "0") == "1"
MIN_VOTES = int(os.environ.get("REC_MIN_VOTES", "40"))
FEATURE_TYPES = {"movie", "tvMovie"}
PERF_KW = re.compile(r"^(concert film|concert|live performance|music video|stand-up comedy|stand-up special|"
                     r"comedy special|live concert|concert tour|music festival)$", re.I)
R_WANTS_PERF = re.compile(r"\b(concerts?|stand-?up|comedy specials?|live (shows?|performances?)|music documentar\w*|"
                          r"tours?)\b", re.I)
UNRELEASED = {"Planned", "In Production", "Post Production", "Rumored", "Canceled"}

_DB = {"conn": None, "tried": False}


def db():
    if not _DB["tried"]:
        _DB["tried"] = True
        _DB["conn"] = F.connect(readonly=True)
    return _DB["conn"]


def _q(sql, p=()):
    c = db()
    if c is None:
        return []
    try:
        return c.execute(sql, p).fetchall()
    except sqlite3.Error:
        return []


def info(ids):
    """{tmdb_id: dict(status, release, runtime, genres, votes, imdb_type, keywords)} for these films."""
    ids = [int(i) for i in ids if i is not None and int(i) > 0]
    out = {}
    for s in range(0, len(ids), 500):
        part = ids[s:s + 500]
        marks = ",".join("?" * len(part))
        for fid, st, rd, urd, rt, g, vc, imdb in _q(f"select tmdb_id, status, release_date, us_release_date, runtime, "
                                                    f"genres_json, vote_count, imdb_id from films where tmdb_id in "
                                                    f"({marks})", part):
            out[fid] = {"status": st, "release": urd or rd, "runtime": rt, "genres": json.loads(g or "[]"),
                        "votes": vc or 0, "imdb": imdb, "type": None, "kw": []}
        for fid, kw in _q(f"select tmdb_id, name from keywords where tmdb_id in ({marks})", part):
            if fid in out:
                out[fid]["kw"].append(kw)
    imdbs = {v["imdb"]: k for k, v in out.items() if v["imdb"]}
    keys = list(imdbs)
    for s in range(0, len(keys), 500):
        part = keys[s:s + 500]
        for tc, typ in _q(f"select tconst, title_type from imdb_titles where tconst in ({','.join('?' * len(part))})", part):
            out[imdbs[tc]]["type"] = typ
    return out


def reason(d, filters=None, question="", mode=None):
    """Why this film should not be recommended, or None."""
    if d is None:
        return None                                    # not in facts.db: keep (nothing to judge it by)
    if d["type"] and d["type"] not in FEATURE_TYPES:
        return f"not a feature film (IMDb: {d['type']})"
    today = datetime.date.today().isoformat()
    if (d["status"] and d["status"] in UNRELEASED) or (d["release"] and d["release"][:10] > today):
        return "unreleased"
    if not R_WANTS_PERF.search(question or ""):
        if any(PERF_KW.match(k or "") for k in d["kw"]) or ({"Music", "Documentary"} <= set(d["genres"])):
            return "concert / stand-up special"
    if d["runtime"] and d["runtime"] < 50:
        return "short"
    if mode != "obscure" and d["votes"] < MIN_VOTES:
        return "too few votes"
    want = (filters or {}).get("genres") or []
    if want and d["genres"] and not any(g.casefold() in " ".join(d["genres"]).casefold() for g in want):
        return "off-genre"
    return None


def clean(hits, filters=None, question="", mode=None):
    """(kept hits, [(title, reason)]) - order kept."""
    if not hits:
        return hits, []
    meta = info([h.get("id") for h in hits if not h.get("_tv")])
    kept, dropped = [], []
    for h in hits:
        r = None if h.get("_tv") else reason(meta.get(h.get("id")), filters, question, mode)
        if r:
            dropped.append((h.get("title"), r))
        else:
            kept.append(h)
    return kept, dropped


def cmd_check(path):
    import retrieve as R
    qs = json.load(open(path))["questions"]
    total_bad = 0
    for q in qs:
        hits = R.retrieve(q["question"], k=10)
        meta = info([h["id"] for h in hits])
        bad = [(h["title"], reason(meta.get(h["id"]), R.parse_filters(q["question"]), q["question"])) for h in hits[:10]]
        bad = [b for b in bad if b[1]]
        total_bad += len(bad)
        print(f"{q['id']} {len(hits)} films, {len(bad)} bad  {q['question'][:60]}"
              + "".join(f"\n      {t}: {r}" for t, r in bad))
        print("      top 5: " + "; ".join(f"{h['title']} ({h.get('release_year')})" for h in hits[:5]))
    print(f"bad items in top 10, all prompts: {total_bad} (rule: 0) -> {'PASS' if total_bad == 0 else 'FAIL'}")
    return total_bad


if __name__ == "__main__":
    a = sys.argv[1:]
    if a and a[0] == "check":
        cmd_check(a[1] if len(a) > 1 else "sets/discovery.json")
    elif a and a[0] == "why":
        rows = _q("select tmdb_id, title, year from films where title = ? collate nocase order by vote_count desc",
                  (" ".join(a[1:]),))
        for fid, t, y in rows:
            print(f"{t} ({y}): {reason(info([fid]).get(fid)) or 'kept'}")
    else:
        print(__doc__)
