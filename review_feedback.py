"""
Change 4: every thumbs-down from the website, with what the system did.

    cd ~/movie-buff/rag && python review_feedback.py          # thumbs-down only
    python review_feedback.py --all                           # every rated answer

Reads logs/requests.jsonl and logs/feedback.jsonl (written by web/server.py).
Each thumbs-down is a candidate test question: add it to test_set.json with
the right film, then rerun split_sets.py.
"""

import json
import sys
from pathlib import Path

LOGS = Path(__file__).resolve().parent / "logs"


def load(name):
    p = LOGS / f"{name}.jsonl"
    if not p.exists():
        return []
    return [json.loads(line) for line in p.open(encoding="utf-8") if line.strip()]


requests = {r["id"]: r for r in load("requests")}
votes = {}
for f in load("feedback"):
    votes[f["id"]] = f                      # last vote per answer wins
show_all = "--all" in sys.argv
up = sum(1 for v in votes.values() if v["vote"] == "up")
down = sum(1 for v in votes.values() if v["vote"] == "down")
print(f"{len(requests)} questions logged, {len(votes)} rated: {up} up, {down} down\n")
for rid, v in votes.items():
    if v["vote"] != "down" and not show_all:
        continue
    r = requests.get(rid, {})
    print(f"[{v['vote'].upper():4s}] {v['time']}  {r.get('question', '(question not in log)')}")
    if r.get("films"):
        print(f"       films:   {', '.join(r['films'])}")
    if r.get("check") and r["check"].get("flags"):
        print(f"       check:   {'; '.join(r['check']['flags'])}")
    if r.get("answer"):
        print(f"       answer:  {r['answer'][:160].replace(chr(10), ' ')}")
    if v.get("comment"):
        print(f"       comment: {v['comment']}")
    print()
