"""
Writes results/site_stats.json, the numbers on the website's How It Works page,
straight from result files - nothing typed by hand. Uses what exists:

  sealed final set   results/stage_scores_*_final_before.json / _final_after.json
  tune set           results/stage_scores_*_night_base.json / _night_all.json
  answers            results/judge-night_style.json (+ factcheck.py over them)

    python make_site_stats.py        # then refresh /how in the browser
"""

import datetime
import glob
import json
import os
import statistics


def load(pattern):
    files = sorted(glob.glob(pattern))
    return json.load(open(files[-1])) if files else None


def passes(d, source=None, honesty=False):
    rows = [r for r in d["rows"]
            if (not r["expect"]) == honesty
            and (source is None or r["group"].startswith(source))]
    return sum(bool(r["result"]["pass"]) for r in rows), len(rows)


def median_seconds(d):
    ms = [r["result"]["trace"].get("total_ms") for r in d["rows"]]
    ms = [m for m in ms if m]
    return statistics.median(ms) / 1000 if ms else None


tiles = []
before, after = load("results/stage_scores_*_final_before.json"), load("results/stage_scores_*_final_after.json")
base, allon = load("results/stage_scores_*_night_base.json"), load("results/stage_scores_*_night_all.json")

if before and after:
    a, n = passes(after, "TREC")
    b, _ = passes(before, "TREC")
    if n:
        tiles.append({"value": f"{a} of {n}", "label": "Real “what's that movie?” posts solved",
                      "note": f"TREC questions it had never seen; {b} of {n} at the start of the project day"})
    a, n = passes(after, "OWN")
    b, _ = passes(before, "OWN")
    if n:
        tiles.append({"value": f"{a} of {n}", "label": "New test questions solved",
                      "note": f"written for the final test and never tuned on; {b} of {n} before"})
    s = median_seconds(after)
    if s:
        tiles.append({"value": f"{s:.1f} s", "label": "Typical time to find the films",
                      "note": "median search time per question on the final set, before the answer is written"})
elif allon and base:
    a, n = passes(allon, "TREC")
    b, _ = passes(base, "TREC")
    if n:
        tiles.append({"value": f"{a} of {n}", "label": "Real “what's that movie?” posts solved",
                      "note": f"TREC tuning questions; {b} of {n} before the latest search changes"})

d = allon or after
if d:
    a, n = passes(d, honesty=True)
    if n:
        tiles.append({"value": f"{a} of {n}", "label": "Made-up films refused",
                      "note": "asked about films that don't exist, it said so instead of inventing one"})

judged = "results/judge-night_style.json"
if os.path.exists(judged):
    rows = json.load(open(judged))
    js = [r["judge"] for r in rows if r.get("judge")]
    if js:
        g = [j["grounded"] for j in js if isinstance(j.get("grounded"), (int, float))]
        ok = sum(j.get("verdict") == "pass" for j in js)
        tiles.append({"value": f"{ok} of {len(js)}", "label": "Answers rated good by a second model",
                      "note": f"graded by gemma3:27b against each question's notes; grounded {statistics.mean(g):.1f}/5"
                              if g else "graded by gemma3:27b against each question's notes"})

out = {"updated": datetime.date.today().strftime("%B %-d, %Y"),
       "lead": "Measured on questions set aside for testing, including real “what's that movie?” "
               "posts from the TREC Tip-of-the-Tongue benchmark.",
       "tiles": tiles}
os.makedirs("results", exist_ok=True)
json.dump(out, open("results/site_stats.json", "w"), indent=1, ensure_ascii=False)
print(f"results/site_stats.json: {len(tiles)} numbers")
for t in tiles:
    print(f"  {t['value']:>10s}  {t['label']}  ({t['note']})")
