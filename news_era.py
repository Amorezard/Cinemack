#!/usr/bin/env python3
"""
v4 (14 Oct): era and stance - "did anti-war films do well while a war dominated the news?"

news.py compares the news in a film's release month with the same film's news six months either
side. A war, a pandemic or a recession that lasts years is in both, so it cancels out: that test
only sees short spikes. This adds the two things the question needs:

  era     how much of a year's news was about each theme, against that theme's usual share in the
          same KIND of source (NYT front pages, Wikipedia current-events portal, Wikipedia month
          articles, "<year> in the United States" pages - their styles differ, so each is compared
          only with itself), as a z-score per year. 1968 and 2003-07 come out as war years.
  stance  for every film that touches a theme, which side it takes:
              war        critical = anti-war (war as tragic or wrong)   celebratory = heroic / patriotic
              disease    critical = bleak, institutions fail             celebratory = hope, heroes win
              economy    critical = greed / hardship / inequality         celebratory = aspirational success
              politics   critical = corruption, satire                    celebratory = idealistic, patriotic
              terrorism  critical = causes and costs                      celebratory = action, revenge
              disaster   critical = human error, institutions fail        celebratory = heroic survival
              space      critical = danger, cost                          celebratory = triumph of exploration
              technology critical = threat, dystopia                      celebratory = wonder, progress
              civil_rights critical = injustice unresolved                celebratory = triumph over injustice
          from TMDB keywords where they say it (e.g. "anti-war"), otherwise llama3.2 (structured output,
          one call per film and theme, resumable), plus "neutral" when it takes no side.
  test    per theme, a difference-in-differences on return relative to similar films (news.py's
          success residual): (critical films in heavy-coverage years - critical films in light years)
          minus (the same for celebratory films). Heavy / light = top / bottom third of the era z-scores
          of that theme's films. Bootstrap 95% CI (1,000 resamples within cells).
          Rule, written 14 Oct before any result: a theme shows a stance effect only if the CI excludes
          0 and every cell has 30+ films; otherwise "no evidence" (not "no effect").

    python news_era.py era                 # per-theme yearly shares + z-scores (seconds)
    python news_era.py stance [--limit N]  # film stance labels (keywords, then llama3.2; ~1-2 h for all)
    python news_era.py analyze             # results/news_era_summary.txt (+ news_corpus 'era')
    python news_era.py show "Apocalypse Now"
"""
import argparse
import datetime
import json
import os
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
if os.environ.get("MOCK", "off").lower() not in ("", "off", "0"):
    import mock_llm
    mock_llm.install()
import numpy as np  # noqa: E402

import news as N  # noqa: E402

VERSION = "ne1"
MODEL = os.environ.get("STANCE_MODEL", "llama3.2")
THEMES = [t for t in N.THEMES if t != "crime"]        # crime films are too many kinds to have one stance
MEANING = {
    "war": ("anti-war: war shown as tragic, futile or wrong", "heroic or patriotic: war as duty, victory or adventure"),
    "disease": ("bleak: institutions fail, people suffer", "hopeful: heroes or science overcome it"),
    "economy": ("critical of greed, inequality or hardship", "aspirational: wealth and success as goals"),
    "politics": ("critical: corruption, abuse of power, satire", "idealistic or patriotic about politics"),
    "terrorism": ("examines causes and human costs", "action or revenge: defeating the terrorists"),
    "disaster": ("human error or institutions fail", "heroic survival and rescue"),
    "space": ("danger, cost or failure of space travel", "triumph and wonder of exploration"),
    "technology": ("technology as threat or dystopia", "technology as wonder or progress"),
    "civil_rights": ("injustice shown as unresolved or bleak", "triumph over injustice"),
}
KEYWORD_STANCE = {           # TMDB keywords that settle the stance without a model call
    "war": ({"anti war", "anti-war", "antiwar", "pacifism", "pacifist", "horrors of war", "war crimes",
             "conscientious objector", "futility of war", "shell shock"},
            {"patriotism", "propaganda", "heroism", "war hero", "military hero", "victory"}),
}
SCHEMA = {"type": "object", "properties": {
    "stance": {"type": "string", "enum": ["critical", "celebratory", "neutral"]},
    "reason": {"type": "string"}}, "required": ["stance", "reason"]}

DDL = """
create table if not exists news_era(theme text, year integer, share real, z real, n integer,
  primary key (theme, year));
create table if not exists news_stance(tmdb_id integer, theme text, stance text, reason text, source text,
  version text, primary key (tmdb_id, theme));
"""


def connect():
    c = N.connect()
    c.executescript(DDL)
    return c


def kind_of(source, url):
    if source == "nyt":
        return "nyt"
    u = url or ""
    if "Portal:Current_events" in u or "Portal%3ACurrent_events" in u:
        return "portal"
    if "_in_the_United_States" in u:
        return "year_page"
    return "month_page"


# ------------------------------------------------------------------ era
def cmd_era(_a):
    """Per theme and year: share of headlines on the theme, as a z-score within each kind of source
    (then averaged over the kinds that year, weighted by their headline counts)."""
    c = connect()
    counts = {}                               # (kind, year) -> [n, {theme: hits}]
    for source, url, date, head in c.execute("select source, url, date, headline from news_items"):
        try:
            y = int((date or "")[:4])
        except ValueError:
            continue
        k = kind_of(source, url)
        cell = counts.setdefault((k, y), [0, {}])
        cell[0] += 1
        for th in THEMES:
            if N.THEME_RX[th].search(head or ""):
                cell[1][th] = cell[1].get(th, 0) + 1
    rows = []
    for th in THEMES:
        per_kind = {}
        for (k, y), (n, hits) in counts.items():
            if n >= 30:
                per_kind.setdefault(k, {})[y] = (hits.get(th, 0) / n, n)
        zs = {}
        for k, ys in per_kind.items():
            shares = np.array([v[0] for v in ys.values()])
            if len(shares) < 5 or shares.std() == 0:
                continue
            mu, sd = shares.mean(), shares.std()
            for y, (sh, n) in ys.items():
                zs.setdefault(y, []).append(((sh - mu) / sd, sh, n))
        for y, lst in zs.items():
            w = sum(n for _z, _s, n in lst)
            rows.append((th, y, sum(s * n for _z, s, n in lst) / w, sum(z * n for z, _s, n in lst) / w, w))
    c.execute("delete from news_era")
    c.executemany("insert into news_era values (?,?,?,?,?)", rows)
    c.commit()
    for th in ("war", "disease", "economy", "terrorism"):
        top = c.execute("select year, z from news_era where theme = ? order by z desc limit 5", (th,)).fetchall()
        print(f"{th:10s} heaviest years: " + ", ".join(f"{y} ({z:+.1f})" for y, z in top))
    print(f"news_era: {len(rows):,} theme-years")


def era_z(c, theme, year):
    r = c.execute("select z, share from news_era where theme = ? and year = ?", (theme, year)).fetchone()
    return r if r else (None, None)


# ------------------------------------------------------------------ stance
def film_theme_rows(c):
    """(tmdb_id, title, year, text, keywords) for films with box-office data, and the themes they touch."""
    out = []
    for fid, title, year, g, ov, tag in c.execute(
            "select f.tmdb_id, f.title, f.year, f.genres_json, f.overview, f.tagline from films f "
            "join money m on m.tmdb_id = f.tmdb_id where m.roi > 0"):
        kws = [k.casefold() for (k,) in c.execute("select name from keywords where tmdb_id = ?", (fid,))]
        text = f"{title}. {', '.join(json.loads(g or '[]'))}. {', '.join(kws)}. {tag or ''} {ov or ''}"
        themes = [t for t in THEMES if N.THEME_RX[t].search(text)]
        if themes:
            out.append((fid, title, year, text, set(kws), themes))
    return out


def label(title, text, theme):
    crit, cel = MEANING[theme]
    import ollama
    for n_pred in (120, 300):
        try:
            r = ollama.chat(model=MODEL, format=SCHEMA, keep_alive="30m",
                            options={"temperature": 0, "num_predict": n_pred},
                            messages=[{"role": "system", "content":
                                       f"You classify how a film treats the theme '{theme.replace('_', ' ')}'. "
                                       f"critical = {crit}. celebratory = {cel}. neutral = the film takes no clear "
                                       f"side or the theme is only background. reason: one short sentence. JSON only."},
                                      {"role": "user", "content": text[:1500]}])
            d = json.loads(r["message"]["content"])
            if d.get("stance") in ("critical", "celebratory", "neutral"):
                return d["stance"], str(d.get("reason") or "")[:200]
        except Exception:  # noqa: BLE001
            continue
    return None, ""


def cmd_stance(a):
    c = connect()
    done = {(r[0], r[1]) for r in c.execute("select tmdb_id, theme from news_stance where version = ?", (VERSION,))}
    todo = [(fid, t, y, text, kws, th) for fid, t, y, text, kws, ths in film_theme_rows(c) for th in ths
            if (fid, th) not in done]
    if a.limit:
        todo = todo[:a.limit]
    print(f"stance labels to make: {len(todo):,} (film x theme)")
    n_kw = n_llm = 0
    for i, (fid, title, _y, text, kws, th) in enumerate(todo, 1):
        stance, reason, src = None, "", "keywords"
        if th in KEYWORD_STANCE:
            crit_kw, cel_kw = KEYWORD_STANCE[th]
            if kws & crit_kw and not kws & cel_kw:
                stance, reason = "critical", "TMDB keywords: " + ", ".join(sorted(kws & crit_kw))
            elif kws & cel_kw and not kws & crit_kw:
                stance, reason = "celebratory", "TMDB keywords: " + ", ".join(sorted(kws & cel_kw))
        if stance:
            n_kw += 1
        else:
            stance, reason = label(title, text, th)
            src = MODEL
            n_llm += 1
        if stance:
            c.execute("insert or replace into news_stance values (?,?,?,?,?,?)", (fid, th, stance, reason, src, VERSION))
        if i % 100 == 0 or i == len(todo):
            c.commit()
            print(f"  {i:,}/{len(todo):,}  keywords {n_kw:,}  {MODEL} {n_llm:,}", flush=True)
    c.commit()


# ------------------------------------------------------------------ test
def did(cells, rng, n_boot=1000):
    """(crit_hi - crit_lo) - (cel_hi - cel_lo) on means, with a bootstrap 95% CI (resampling within cells)."""
    def stat(c):
        return (np.mean(c["crit_hi"]) - np.mean(c["crit_lo"])) - (np.mean(c["cel_hi"]) - np.mean(c["cel_lo"]))
    point = stat(cells)
    boots = []
    for _ in range(n_boot):
        boots.append(stat({k: rng.choice(v, len(v)) for k, v in cells.items()}))
    return float(point), [float(np.percentile(boots, 2.5)), float(np.percentile(boots, 97.5))]


def cmd_analyze(_a):
    c = connect()
    res = N.success_residuals(c)
    rng = np.random.default_rng(0)
    lines = [f"Era x stance: does a film's side on a theme matter more when that theme dominates the news? "
             f"({datetime.date.today()}, {VERSION})",
             "success = return relative to films of the same genre and year (news.py); era = z-score of the theme's "
             "share of that year's headlines within each kind of source; heavy / light = top / bottom third of the "
             "era z of that theme's films.",
             "effect = (critical films, heavy - light years) - (celebratory films, heavy - light years), in log return; "
             "+0.69 would mean critical films gained about twice as much relative to their peers.",
             "Rule (written before results): an effect counts only if its 95% CI excludes 0 and every cell has 30+ films.",
             "", f"{'theme':13s} {'n films':>7s}  {'crit hi/lo':>11s} {'cel hi/lo':>11s}  {'effect':>7s}  {'95% CI':>17s}  verdict"]
    out = {"version": VERSION, "at": N.F.now(), "themes": {}}
    for th in THEMES:
        rows = c.execute("select s.tmdb_id, s.stance, f.year from news_stance s join films f on f.tmdb_id = s.tmdb_id "
                         "where s.theme = ? and s.stance in ('critical', 'celebratory')", (th,)).fetchall()
        data = []
        for fid, stance, year in rows:
            z, _sh = era_z(c, th, year)
            if z is not None and fid in res:
                data.append((stance, z, res[fid]))
        if len(data) < 60:
            lines.append(f"{th:13s} {len(data):7d}  too few films with a stance, era and box-office data")
            continue
        zs = np.array([d[1] for d in data])
        lo_cut, hi_cut = np.percentile(zs, [33.3, 66.7])
        cells = {"crit_hi": [], "crit_lo": [], "cel_hi": [], "cel_lo": []}
        for stance, z, r in data:
            side = "crit" if stance == "critical" else "cel"
            if z >= hi_cut:
                cells[f"{side}_hi"].append(r)
            elif z <= lo_cut:
                cells[f"{side}_lo"].append(r)
        ns = {k: len(v) for k, v in cells.items()}
        if min(ns.values()) < 2:
            lines.append(f"{th:13s} {len(data):7d}  a cell is empty: {ns}")
            continue
        eff, ci = did({k: np.array(v) for k, v in cells.items()}, rng)
        ok = min(ns.values()) >= 30
        verdict = ("critical films did relatively better in heavy years" if ci[0] > 0 else
                   "celebratory films did relatively better in heavy years" if ci[1] < 0 else "no evidence")
        if not ok:
            verdict = "too few films per cell (<30): " + verdict
        means = {k: float(np.mean(v)) for k, v in cells.items()}
        out["themes"][th] = {"n": len(data), "cells": ns, "means": means, "effect": eff, "ci": ci,
                             "counts": ok and not (ci[0] <= 0 <= ci[1]), "verdict": verdict}
        lines.append(f"{th:13s} {len(data):7d}  {ns['crit_hi']:>4d}/{ns['crit_lo']:<4d}   {ns['cel_hi']:>4d}/{ns['cel_lo']:<4d}  "
                     f"{eff:+7.3f}  [{ci[0]:+.3f}, {ci[1]:+.3f}]  {verdict}")
    c.execute("insert or replace into news_corpus values ('era', ?)", (json.dumps(out),))
    c.commit()
    path = os.path.join(HERE, "results", "news_era_summary.txt")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, "w").write("\n".join(lines) + "\n")
    print("\n".join(lines))
    print(f"-> {path}")


# ------------------------------------------------------------------ for the answer block (news.news_block)
LABEL = {"war": ("anti-war", "heroic / patriotic"), "civil_rights": ("bleak about injustice", "triumphant")}


def era_lines(c, fid):
    """Lines for news.news_block: the release year's era level per theme the film touches, the film's
    stance, and the corpus result for that theme and stance."""
    try:
        year = (c.execute("select year from films where tmdb_id = ?", (fid,)).fetchone() or [None])[0]
        stances = c.execute("select theme, stance, reason, source from news_stance where tmdb_id = ?", (fid,)).fetchall()
        summ = json.loads((c.execute("select value from news_corpus where key = 'era'").fetchone() or ["{}"])[0])
    except sqlite3.Error:
        return []
    if not year or not stances:
        return []
    lines = []
    for th, stance, reason, src in stances:
        z, share = era_z(c, th, year)
        crit, cel = LABEL.get(th, ("critical", "celebratory"))
        side = {"critical": crit, "celebratory": cel}.get(stance, "neutral")
        how = reason if src == "keywords" else f"{reason} - classified by {src}"
        bits = [f"Theme {th.replace('_', ' ')}: the film reads as {side} ({how})"]
        if z is not None:
            level = "heavy" if z >= 1 else "light" if z <= -1 else "ordinary"
            bits.append(f"{year} was a {level} year for {th.replace('_', ' ')} news ({100 * share:.0f}% of headlines, "
                        f"z {z:+.1f} against other years)")
        t = (summ.get("themes") or {}).get(th)
        if t and stance in ("critical", "celebratory"):
            m = t["means"]
            bits.append(f"across {t['n']:,} {th.replace('_', ' ')} films: {crit} films "
                        f"{m['crit_hi'] - m['crit_lo']:+.2f} and {cel} films {m['cel_hi'] - m['cel_lo']:+.2f} log return "
                        f"(heavy minus light years); difference {t['effect']:+.2f}, 95% CI {t['ci'][0]:+.2f} to "
                        f"{t['ci'][1]:+.2f} - {t['verdict']}")
        lines.append("; ".join(bits) + " [Movie Buff analysis]")
    return lines


def cmd_show(a):
    c = connect()
    r = c.execute("select tmdb_id, title, year from films where title like ? order by vote_count desc limit 1",
                  (a.title,)).fetchone()
    if not r:
        raise SystemExit("no such film")
    print(f"{r[1]} ({r[2]})")
    print("\n".join(era_lines(c, r[0])) or "(no stance labels for this film yet)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["era", "stance", "analyze", "show"])
    ap.add_argument("title", nargs="?")
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()
    {"era": cmd_era, "stance": cmd_stance, "analyze": cmd_analyze, "show": cmd_show}[a.cmd](a)


if __name__ == "__main__":
    main()
