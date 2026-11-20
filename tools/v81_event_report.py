#!/usr/bin/env python
"""v8.1 round 55 (20 Nov 2026): the event-log ranking test - two weeks of the CURRENT ranking, written down first.

Why this test and not another ranking candidate: three offline tests failed (14-17 Nov), and the round-48 rule says no
further candidate is picked on the same MovieLens data. The honest next step is to measure the live ranking on what
real visitors do (logs/rec_events.db, anonymous, switch REC_EVENTS on since 18 Nov), so that any later candidate is
compared with a baseline that was fixed before it existed.

PRE-REGISTERED (20 Nov 2026, before any of these numbers were looked at):
  window       Fri 20 Nov 2026 00:00 to Fri 4 Dec 2026 00:00 (server time); events before or after are not counted.
  unit         an exposure (one row of titles shown together); position = place in that row (0 = first).
  measures     click rate      = exposures with >= 1 click on a shown title / exposures
               save rate       = shown titles later added to the watchlist in the same visit / shown titles
               position curve  = click rate of position 0, 1, 2, ... 9 (is the order doing anything?)
               per placement   = the same for home / detail / pick_tonight / calendar / watchlist
  enough data  at least 200 exposures AND at least 30 visits in the window; otherwise the report says
               "NOT ENOUGH DATA" and gives no rates as findings (a site with one or two users cannot test a ranking).
  what it is   a baseline, not a pass/fail: it decides nothing on its own. A later candidate ranking is shown to a
               random half of visits and must beat these rates by a rule written before it runs.
Nothing here writes to the event log. Output: results/v81_event_report.txt (+ .json).

    python tools/v81_event_report.py [--db logs/rec_events.db] [--start 2026-11-20] [--end 2026-12-04]
"""
import argparse
import json
import os
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
START, END = "2026-11-20", "2026-12-04"
MIN_EXPOSURES, MIN_VISITS = 200, 30


def report(conn, start=START, end=END):
    rows = conn.execute("""select session_key, media_id, placement, event_type, exposure_id, position, occurred_at
                           from rec_event where occurred_at >= ? and occurred_at < ?""", (start, end)).fetchall()
    visits = {r[0] for r in rows}
    shown = defaultdict(dict)            # exposure -> {media: (position, placement, session)}
    kinds = defaultdict(int)
    for s, m, pl, t, ex, pos, _at in rows:
        kinds[t] += 1
        if t == "impression" and ex:
            shown[ex][m] = (pos, pl, s)
    clicked_ex, saved = set(), set()
    pos_shown, pos_click = defaultdict(int), defaultdict(int)
    pl_ex, pl_click = defaultdict(int), defaultdict(int)
    by_session_saves = defaultdict(set)
    for s, m, pl, t, ex, pos, _at in rows:
        if t == "watchlist_add":
            by_session_saves[s].add(m)
    for ex, items in shown.items():
        place = next(iter(items.values()))[1]
        pl_ex[place] += 1
        for m, (pos, _pl, s) in items.items():
            if pos is not None and pos < 10:
                pos_shown[pos] += 1
            if m in by_session_saves[s]:
                saved.add((ex, m))
    for s, m, pl, t, ex, pos, _at in rows:
        if t == "click" and ex in shown and m in shown[ex]:
            if ex not in clicked_ex:
                pl_click[shown[ex][m][1]] += 1
            clicked_ex.add(ex)
            p = shown[ex][m][0]
            if p is not None and p < 10:
                pos_click[p] += 1
    n_ex = len(shown)
    n_titles = sum(len(v) for v in shown.values())
    enough = n_ex >= MIN_EXPOSURES and len(visits) >= MIN_VISITS
    rate = lambda a, b: round(a / b, 4) if b else None  # noqa: E731
    return {
        "window": [start, end], "events": len(rows), "visits": len(visits), "exposures": n_ex, "titles_shown": n_titles,
        "by_kind": dict(kinds), "enough_data": enough,
        "click_rate": rate(len(clicked_ex), n_ex), "save_rate": rate(len(saved), n_titles),
        "position_click_rate": {p: rate(pos_click[p], pos_shown[p]) for p in sorted(pos_shown)},
        "placement": {p: {"exposures": pl_ex[p], "click_rate": rate(pl_click[p], pl_ex[p])} for p in sorted(pl_ex)},
        "linked_to_a_person": conn.execute("select count(*) from rec_event where subject_key is not null").fetchone()[0],
    }


def text(r):
    L = [f"event-log ranking baseline, window {r['window'][0]} to {r['window'][1]} (pre-registered 20 Nov 2026)",
         f"  events {r['events']:,} | visits {r['visits']:,} | rows shown (exposures) {r['exposures']:,} | titles shown {r['titles_shown']:,}",
         "  by kind: " + (", ".join(f"{k} {v:,}" for k, v in sorted(r["by_kind"].items(), key=lambda x: -x[1])) or "none"),
         f"  linked to a person: {r['linked_to_a_person']} (must be 0)"]
    if not r["enough_data"]:
        L.append(f"  NOT ENOUGH DATA (needs {MIN_EXPOSURES} exposures and {MIN_VISITS} visits): the rates below are not findings")
    L.append(f"  click rate {r['click_rate']} | save rate {r['save_rate']}")
    L.append("  click rate by position: " + (", ".join(f"{p}: {v}" for p, v in r["position_click_rate"].items()) or "none"))
    for p, v in r["placement"].items():
        L.append(f"  {p}: {v['exposures']:,} rows shown, click rate {v['click_rate']}")
    return "\n".join(L)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=os.environ.get("REC_EVENTS_DB", str(ROOT / "logs" / "rec_events.db")))
    ap.add_argument("--start", default=START)
    ap.add_argument("--end", default=END)
    ap.add_argument("--out", default=str(ROOT / "results" / "v81_event_report"))
    a = ap.parse_args(argv)
    if not Path(a.db).exists():
        print(f"no event log at {a.db} yet")
        return 0
    c = sqlite3.connect(f"file:{a.db}?mode=ro", uri=True, timeout=10)
    r = report(c, a.start, a.end)
    t = text(r)
    print(t)
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out + ".txt").write_text(t + "\n")
    Path(a.out + ".json").write_text(json.dumps(r, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
