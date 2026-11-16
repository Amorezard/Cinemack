#!/usr/bin/env python3
"""v8.1 slice 2.3 (15 Nov 2026): does the TV query plan mean what its rule text says, on every real show?

For every case below and EVERY show in facts.db (the worktree copy):
  reference   plain Python written from the rule text in web/plan_sql_tv.py (no SQL, no site code)
  plan        web/query_plan.from_settings -> media_types ["tv"] -> web/plan_sql_tv.matching_show_ids
Rule (IMPLEMENTATION_PLAN_LOCKED.md, added 15 Nov before the first run): plan = reference on 100% of shows for every
case, AND a mixed film + show plan returns exactly the film answer plus the show answer as media ids (no id of one
kind ever stands for the other). Counts of shows with a missing fact are printed so "unknown fails" is visible.

Read-only: facts.db opened with mode=ro. No user data is read. Writes results/v81_tv_conformance.txt and .json.
    python tools/v81_tv_conformance.py [--db PATH]
"""
import argparse
import json
import os
import sqlite3
import sys
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(HERE, "web"))
sys.path.insert(0, HERE)
import plan_sql as PS  # noqa: E402
import plan_sql_tv as PT  # noqa: E402
import query_plan as QP  # noqa: E402

KINDS = ("flatrate", "free", "ads")
# rule text, typed again here on purpose (the reference must not import the tables it checks)
REF_GENRE = {"Action & Adventure": {"Action", "Adventure"}, "Sci-Fi & Fantasy": {"Science Fiction", "Fantasy"},
             "War & Politics": {"War"}, "Kids": {"Family"}}
REF_OK = {"G": {"TV-Y", "TV-Y7", "TV-Y7-FV", "TV-G"}}
REF_OK["PG"] = REF_OK["G"] | {"TV-PG"}
REF_OK["PG-13"] = REF_OK["PG"] | {"TV-14"}
REF_OK["R"] = REF_OK["PG-13"] | {"TV-MA"}
REF_OK["NC-17"] = REF_OK["R"]
REF_KNOWN = REF_OK["R"]

CASES = [
    {"id": "no_limits", "settings": {}},
    {"id": "year_from_2015", "settings": {"min_year": 2015}},
    {"id": "year_1990_1999_strict", "settings": {"min_year": 1990, "max_year": 1999, "strict": True}},
    {"id": "episodes_30_min", "settings": {"max_runtime": 30}},
    {"id": "episodes_30_min_strict", "settings": {"max_runtime": 30, "strict": True}},
    {"id": "comedy", "settings": {"only_genres": ["Comedy"]}},
    {"id": "action_any", "settings": {"only_genres": ["Action"]}},
    {"id": "scifi_and_fantasy_all", "settings": {"only_genres": ["Science Fiction", "Fantasy"], "genres_all": True}},
    {"id": "action_and_drama_all", "settings": {"only_genres": ["Action", "Drama"], "genres_all": True}},
    {"id": "crime_or_mystery", "settings": {"only_genres": ["Crime", "Mystery"]}},
    {"id": "never_family", "settings": {"never_genres": ["Family"]}},
    {"id": "never_war_strict", "settings": {"never_genres": ["War"], "strict": True}},
    {"id": "drama_never_crime", "settings": {"only_genres": ["Drama"], "never_genres": ["Crime"]}},
    {"id": "rated_g", "settings": {"max_cert": "G"}},
    {"id": "rated_pg_strict", "settings": {"max_cert": "PG", "strict": True}},
    {"id": "rated_pg13", "settings": {"max_cert": "PG-13"}},
    {"id": "rated_r_strict", "settings": {"max_cert": "R", "strict": True}},
    {"id": "on_netflix", "settings": {"services_only": True, "services": ["netflix"]}},
    {"id": "on_max_or_hulu", "settings": {"services_only": True, "services": ["max", "hulu"]}},
    {"id": "services_none_picked", "settings": {"services_only": True, "services": []}},
    {"id": "family_night", "settings": {"max_cert": "PG", "max_runtime": 45, "never_genres": ["Horror"], "strict": True}},
    {"id": "everything", "settings": {"min_year": 2000, "max_year": 2024, "max_runtime": 60, "only_genres": ["Drama", "Comedy"],
                                      "never_genres": ["Animation"], "max_cert": "PG-13", "services_only": True,
                                      "services": ["netflix", "prime", "disney"], "strict": True}},
    {"id": "documentary_strict", "settings": {"only_genres": ["Documentary"], "strict": True}},
    {"id": "western_or_war", "settings": {"only_genres": ["Western", "War"]}},
]


def load_shows(c):
    shows = {}
    for sid, y, rt, g in c.execute("select tmdb_id, first_year, episode_runtime, genres_json from tv_shows"):
        try:
            gs = json.loads(g) if g else []
            gs = [x for x in gs if isinstance(x, str)] if isinstance(gs, list) else []
        except ValueError:
            gs = []
        film = set()
        for x in gs:
            film |= REF_GENRE.get(x, {x})
        shows[sid] = {"year": y, "runtime": rt if rt and rt > 0 else None, "genres": film, "has_genres": bool(gs),
                      "rating": None, "on": set(), "listed": False}
    for sid, r in c.execute("select tmdb_id, rating from tv_ratings where region = 'US'"):
        r = (r or "").strip().upper()
        if sid in shows and r in REF_KNOWN:
            shows[sid]["rating"] = r
    for sid, name in c.execute("select tmdb_id, provider_name from tv_providers where region = 'US' and kind in (?,?,?)", KINDS):
        if sid in shows:
            shows[sid]["listed"] = True
            k = PS.service_of(name)
            if k:
                shows[sid]["on"].add(k)
    return shows


def reference(s, f):
    """The rule text, read literally."""
    strict = bool(s.get("strict"))

    def lim(known, ok):
        return ok if known else not strict

    y, rt = f["year"], f["runtime"]
    if s.get("min_year") is not None and not lim(y is not None, y is not None and y >= s["min_year"]):
        return False
    if s.get("max_year") is not None and not lim(y is not None, y is not None and y <= s["max_year"]):
        return False
    if s.get("max_runtime") is not None and not lim(rt is not None, rt is not None and rt <= s["max_runtime"]):
        return False
    only, never = set(s.get("only_genres") or []), set(s.get("never_genres") or [])
    if only or never:
        if not f["has_genres"]:
            if strict:
                return False
        else:
            if never & f["genres"]:
                return False
            if only:
                if s.get("genres_all") and len(only) > 1:
                    if not only <= f["genres"]:
                        return False
                elif not (only & f["genres"]):
                    return False
    if s.get("max_cert"):
        if f["rating"] is None:
            if strict:
                return False
        elif f["rating"] not in REF_OK[s["max_cert"]]:
            return False
    if s.get("services_only"):
        picked = set(s.get("services") or [])
        if picked:
            if not (picked & f["on"]):
                return False
        elif not f["listed"]:                 # no service named: any subscription / free listing counts
            return False
    return True


def tv_plan(settings, media=("tv",)):
    d = QP.from_settings(settings).model_dump()
    d["media_types"] = list(media)
    return QP.parse(d)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=os.environ.get("FACTS_DB", os.path.join(HERE, "facts.db")))
    ap.add_argument("--out", default=os.path.join(HERE, "results", "v81_tv_conformance"))
    a = ap.parse_args()
    t0 = time.time()
    c = sqlite3.connect(f"file:{a.db}?mode=ro", uri=True)
    shows = load_shows(c)
    ids = sorted(shows)
    n = len(ids)
    miss = {"no first year": sum(1 for f in shows.values() if f["year"] is None),
            "no episode length": sum(1 for f in shows.values() if f["runtime"] is None),
            "no genres": sum(1 for f in shows.values() if not f["has_genres"]),
            "no US TV rating": sum(1 for f in shows.values() if f["rating"] is None),
            "no US subscription / free listing": sum(1 for f in shows.values() if not f["listed"])}
    L = [f"TV plan conformance (v8.1 slice 2.3), {n:,} shows from {a.db}, {len(CASES)} cases", "",
         "shows with a missing fact: " + "; ".join(f"{k} {v:,} ({v / max(1, n):.1%})" for k, v in miss.items()), ""]
    out = {"shows": n, "missing": miss, "cases": []}
    all_ok = True
    for case in CASES:
        s = case["settings"]
        ref = {i for i in ids if reference(s, shows[i])}
        got = set(PT.matching_show_ids(c, tv_plan(s)))
        ok = got == ref
        all_ok &= ok
        out["cases"].append({"id": case["id"], "reference": len(ref), "plan": len(got), "ok": ok,
                             "plan_extra": sorted(got - ref)[:8], "plan_missing": sorted(ref - got)[:8]})
        L.append(f"  {case['id']:26s} reference {len(ref):6,d} | plan {len(got):6,d} {'OK' if ok else 'DIFFERENT'}"
                 + ("" if ok else f"  extra {sorted(got - ref)[:5]} missing {sorted(ref - got)[:5]}"))
    # mixed plans: film answer + show answer, as media ids
    mixed_ok = True
    for s in ({"min_year": 2010, "only_genres": ["Comedy"]}, {"max_cert": "PG", "strict": True}, {}):
        both = PT.matching_media_ids(c, tv_plan(s, ("movie", "tv")))
        films = ["movie:%d" % i for i in PS.matching_ids(c, tv_plan(s, ("movie",)))]
        tv = ["tv:%d" % i for i in PT.matching_show_ids(c, tv_plan(s))]
        same = both == films + tv and len(set(both)) == len(both)
        mixed_ok &= same
        L.append(f"  mixed plan {json.dumps(s)}: {len(films):,} films + {len(tv):,} shows = {len(both):,} media ids "
                 f"{'OK' if same else 'DIFFERENT'}")
    out["mixed_ok"] = mixed_ok
    rule = all_ok and mixed_ok
    out["rule"] = "PASS" if rule else "FAIL"
    L += ["", f"RULE plan = reference on every case AND mixed plans = films + shows: {out['rule']}",
          "Not compared (choices of the Pick Tonight TV list, not limits): under-40-vote shows, News / Talk / Soap / Reality, "
          "followed shows, the GENRE_MAIN main-genre rule.", f"{time.time() - t0:.0f} s"]
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    open(a.out + ".txt", "w").write("\n".join(L) + "\n")
    json.dump(out, open(a.out + ".json", "w"), indent=1)
    print("\n".join(L))
    return 0 if rule else 1


if __name__ == "__main__":
    sys.exit(main())
