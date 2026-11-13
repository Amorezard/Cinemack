#!/usr/bin/env python3
"""v8.1 slice 2.2 (13 Nov 2026): do Cinemack's hard limits mean the same thing everywhere?

For every case in sets/v81_filters.json and EVERY film in facts.db, four answers to "does this film meet the limits?":
  reference   plain Python written from the rule text in web/plan_sql.py (no SQL, no site code)
  plan_sql    the compiled query plan (web/query_plan.from_settings -> web/plan_sql.matching_ids)
  site        web/user_prefs.excluded (website, Pick Tonight, chat after retrieval), GENRE_MAIN off
  home        web/discovery.match_genres (Home genre chips), genre-only cases
  chat        retrieve.passes (chat's parsed year / genre filter), year+genre cases
Rule (IMPLEMENTATION_PLAN_LOCKED.md, before the first run): plan_sql = reference on 100% of films for every case.
The other paths' differences are counted and explained (which fact was missing), never fixed silently.

Read-only: facts.db opened with mode=ro. No user data is read. Writes results/v81_filter_conformance.txt and .json.
    GENRE_MAIN=0 python tools/filter_conformance.py [--films N]
"""
import argparse
import json
import os
import random
import sqlite3
import sys
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(HERE, "web"))
sys.path.insert(0, HERE)
import facts_db as F  # noqa: E402
import plan_sql as PS  # noqa: E402
import query_plan as QP  # noqa: E402

CERTS = ["G", "PG", "PG-13", "R", "NC-17"]
KINDS = ("flatrate", "free", "ads")


def load_facts(c, ids=None):
    films = {}
    for fid, y, rt, g in c.execute("select tmdb_id, year, runtime, genres_json from films"):
        try:
            gs = json.loads(g) if g else []
            gs = set(gs) if isinstance(gs, list) else set()
        except ValueError:
            gs = set()
        films[fid] = {"year": y, "runtime": rt if rt and rt > 0 else None, "genres": gs, "cert": None, "on": set()}
    if ids is not None:
        films = {k: v for k, v in films.items() if k in ids}
    best = {}
    for fid, cert, rtype in c.execute("select tmdb_id, certification, release_type from certifications where region = 'US'"):
        if fid not in films:
            continue
        cert = (cert or "").strip().upper()
        if cert not in CERTS:
            continue
        key = (0 if rtype == 3 else (1 if rtype is not None else 2), rtype if rtype is not None else 0)   # theatrical, then by type, no type last
        if fid not in best or key < best[fid][0]:
            best[fid] = (key, cert)
    for fid, (_k, cert) in best.items():
        films[fid]["cert"] = cert
    names = {}
    for fid, name in c.execute("select tmdb_id, provider_name from watch_providers where region = 'US' and kind in (?,?,?)", KINDS):
        if fid in films:
            if name not in names:
                names[name] = PS.service_of(name)
            if names[name]:
                films[fid]["on"].add(names[name])
    return films


def reference(s, f):
    """The rule text, read literally."""
    strict = bool(s.get("strict"))

    def lim(known, ok):
        return ok if known else not strict

    y, rt, gs = f["year"], f["runtime"], f["genres"]
    if s.get("min_year") is not None and not lim(y is not None, y is not None and y >= s["min_year"]):
        return False
    if s.get("max_year") is not None and not lim(y is not None, y is not None and y <= s["max_year"]):
        return False
    if s.get("max_runtime") is not None and not lim(rt is not None, rt is not None and rt <= s["max_runtime"]):
        return False
    only, never = set(s.get("only_genres") or []), set(s.get("never_genres") or [])
    if only or never:
        if not gs:
            if strict:
                return False
        else:
            if never & gs:
                return False
            if only:
                if s.get("genres_all") and len(only) > 1:
                    if not only <= gs:
                        return False
                elif not (only & gs):
                    return False
    if s.get("max_cert"):
        if f["cert"] is None:
            if strict:
                return False
        elif CERTS.index(f["cert"]) > CERTS.index(s["max_cert"]):
            return False
    if s.get("services_only"):
        if not (set(s.get("services") or []) & f["on"]):
            return False
    return True


def describe(f):
    miss = [k for k in ("year", "runtime", "cert") if f[k] is None] + (["genres"] if not f["genres"] else [])
    return "missing " + "/".join(miss) if miss else "all facts known"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--films", type=int, default=0, help="sample this many films (0 = all)")
    ap.add_argument("--out", default=os.path.join(HERE, "results", "v81_filter_conformance"))
    a = ap.parse_args()
    os.environ["GENRE_MAIN"] = "0"        # plan v1.1 uses TMDB's genre lists; compare like with like
    t0 = time.time()
    c = sqlite3.connect(f"file:{F.PATH}?mode=ro", uri=True)
    films = load_facts(c)
    ids = sorted(films)
    if a.films:
        random.Random(13).shuffle(ids)
        ids = sorted(ids[:a.films])
        films = {i: films[i] for i in ids}
    cases = json.load(open(os.path.join(HERE, "sets", "v81_filters.json")))["cases"]
    site = home = chat = None
    notes = []
    try:
        import user_prefs as UP
        site = UP
    except Exception as e:  # noqa: BLE001
        notes.append(f"site path (user_prefs) not importable: {e!r}")
    try:
        import discovery as D
        home = D
    except Exception as e:  # noqa: BLE001
        notes.append(f"home path (discovery) not importable: {e!r}")
    try:
        import retrieve as R
        chat = R
    except Exception as e:  # noqa: BLE001
        notes.append(f"chat path (retrieve) not importable: {e!r}")
    lines = [f"Filter conformance (v8.1 slice 2.2), {len(films):,} films from {F.PATH}, {len(cases)} cases", ""]
    lines += notes
    out = {"films": len(films), "cases": []}
    all_ok = True
    for case in cases:
        s = case["settings"]
        ref = {i for i in ids if reference(s, films[i])}
        plan = QP.from_settings(s)
        got = set(PS.matching_ids(c, plan))
        got &= set(ids)
        ok = got == ref
        all_ok &= ok
        row = {"id": case["id"], "reference": len(ref), "plan_sql": len(got), "plan_sql_ok": ok,
               "plan_extra": sorted(got - ref)[:8], "plan_missing": sorted(ref - got)[:8]}
        line = f"{case['id']:30s} reference {len(ref):6,d} | plan_sql {len(got):6,d} {'OK' if ok else 'DIFFERENT'}"
        if site is not None:
            bad = set()
            for k in range(0, len(ids), 2000):
                chunk = [{"id": i} for i in ids[k:k + 2000]]
                bad |= site.excluded(dict(site.DEFAULTS, **s), chunk)
            site_ok = set(ids) - bad
            d1, d2 = sorted(site_ok - ref), sorted(ref - site_ok)
            row.update(site=len(site_ok), site_extra=len(d1), site_missing=len(d2),
                       site_extra_why=_why(d1, films), site_missing_why=_why(d2, films), site_examples=(d1 + d2)[:6])
            line += f" | site {len(site_ok):6,d} (+{len(d1)} -{len(d2)})"
        gonly = set(s) <= {"only_genres", "genres_all"} and s.get("only_genres")
        if home is not None and gonly:
            sel, mode, _every = home.genre_filter(s["only_genres"], "all" if s.get("genres_all") else "any")
            h = set()
            for k in range(0, len(ids), 2000):
                h |= set(home.match_genres(ids[k:k + 2000], sel, mode))
            row.update(home=len(h), home_extra=len(h - ref), home_missing=len(ref - h), home_missing_why=_why(sorted(ref - h), films))
            line += f" | home {len(h):6,d} (+{len(h - ref)} -{len(ref - h)})"
        yg = set(s) <= {"only_genres", "min_year", "max_year"} and not s.get("genres_all")
        if chat is not None and yg and s:
            flt = {}
            if s.get("min_year") or s.get("max_year"):
                flt = {"year_min": s.get("min_year") or 0, "year_max": s.get("max_year") or 9999}
            if s.get("only_genres"):
                flt["genres"] = list(s["only_genres"])
            ch = set()
            for i in ids:
                f = films[i]
                hit = {"release_year": f["year"], "genres": ", ".join(sorted(f["genres"]))}
                if chat.passes(hit, flt):
                    ch.add(i)
            row.update(chat=len(ch), chat_extra=len(ch - ref), chat_missing=len(ref - ch),
                       chat_extra_why=_why(sorted(ch - ref), films), chat_missing_why=_why(sorted(ref - ch), films))
            line += f" | chat {len(ch):6,d} (+{len(ch - ref)} -{len(ref - ch)})"
        lines.append(line)
        out["cases"].append(row)
    lines += ["", f"RULE plan_sql = reference on every case: {'PASS' if all_ok else 'FAIL'}",
              "(+n = films a path lets through that the reference removes; -n = films it removes that the reference keeps)",
              "", "Why the existing paths differ (count of films by which fact was missing):"]
    for row in out["cases"]:
        for k in ("site_extra_why", "site_missing_why", "home_missing_why", "chat_extra_why", "chat_missing_why"):
            if row.get(k):
                lines.append(f"  {row['id']:30s} {k:18s} {row[k]}")
    lines.append(f"\n{time.time() - t0:.0f} s")
    out["rule_pass"] = all_ok
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    open(a.out + ".txt", "w").write("\n".join(lines) + "\n")
    json.dump(out, open(a.out + ".json", "w"), indent=1)
    print("\n".join(lines))
    return 0 if all_ok else 1


def _why(id_list, films):
    out = {}
    for i in id_list:
        k = describe(films[i])
        out[k] = out.get(k, 0) + 1
    return out


if __name__ == "__main__":
    sys.exit(main())
