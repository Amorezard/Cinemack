"""v8.1 slice 2.3 (15 Nov 2026): the query plan next to the existing filter paths, in SHADOW.

Today four code paths decide whether a film or show meets a person's limits (audit F4). This adapter lets the one
validated plan (web/query_plan.py -> web/plan_sql.py / web/plan_sql_tv.py) make the same decision NEXT to an existing
path and writes down where the two differ. It never changes what the existing path returned.

    decide(conn, settings, candidates)                 -> which candidate media ids the plan keeps / removes
    shadow(conn, settings, candidates, kept, where)    -> one comparison record, appended to logs/plan_shadow.jsonl

Switch QUERY_PLAN_SHADOW (off by default): while it is off shadow() does nothing and returns None. Nothing calls it
yet; wiring it into a page is a separate, explicitly approved step.
What a record holds: time, the page name, a hash of the limits, counts, and up to 20 media ids on each side of a
difference. No question text, no user id, no titles.
Candidates and results are media ids ("movie:<tmdb>" / "tv:<tmdb>"). When the identity tables of slice 1.1 exist,
ids that have no identity are counted as `unknown_identity` and never kept.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from pathlib import Path
from typing import Dict, Iterable, List, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import media_identity as MI  # noqa: E402
import plan_sql as PS  # noqa: E402
import plan_sql_tv as PT  # noqa: E402
import query_plan as QP  # noqa: E402

SHADOW = os.environ.get("QUERY_PLAN_SHADOW", "0") == "1"
LOG = Path(os.environ.get("PLAN_SHADOW_LOG", Path(__file__).resolve().parent.parent / "logs" / "plan_shadow.jsonl"))
MAX_IDS = 20
LIMIT_KEYS = ("min_year", "max_year", "max_runtime", "max_cert", "never_genres", "only_genres", "genres_all",
              "services_only", "services", "strict")


def limits_of(settings) -> Dict[str, object]:
    """Only the hard limits of a settings dict (nothing personal: no name, no taste, no history)."""
    s = settings or {}
    return {k: s[k] for k in LIMIT_KEYS if s.get(k) not in (None, [], "", False)}


def limits_hash(settings) -> str:
    return hashlib.sha256(json.dumps(limits_of(settings), sort_keys=True).encode()).hexdigest()[:12]


def plan_for(settings, media_types=("movie",), region="US") -> QP.QueryPlan:
    """The schema-1.1 plan for a person's limits and the given media types."""
    d = QP.from_settings(limits_of(settings), region=region).model_dump()
    d["media_types"] = list(media_types)
    return QP.parse(d)


def _split(candidates: Iterable[str]):
    films, shows, bad = [], [], []
    for mid in candidates:
        try:
            kind, n = MI.parse(mid)
        except ValueError:
            bad.append(mid)
            continue
        (films if kind == "movie" else shows).append(n)
    return films, shows, bad


def _known(conn, candidates: List[str]) -> Optional[set]:
    """The candidates that have an identity row, or None when the identity tables are not built."""
    if not MI._has(conn, "media_identity"):
        return None
    out = set()
    for k in range(0, len(candidates), 500):
        chunk = candidates[k:k + 500]
        out |= {r[0] for r in conn.execute(
            "select media_id from media_identity where media_id in (%s)" % ",".join("?" * len(chunk)), chunk)}
    return out


def decide(conn, settings, candidates: Iterable[str], region="US") -> Dict[str, object]:
    """{kept, removed, not_ids, unknown_identity}: the plan's decision for these candidates, order kept."""
    cands = list(dict.fromkeys(candidates))
    films, shows, bad = _split(cands)
    keep = set()
    if films:
        keep |= {"movie:%d" % i for i in PS.matching_ids(conn, plan_for(settings, ("movie",), region), candidate_ids=films)}
    if shows:
        keep |= {"tv:%d" % i for i in PT.matching_show_ids(conn, plan_for(settings, ("tv",), region), candidate_ids=shows)}
    known = _known(conn, [c for c in cands if c not in bad])
    unknown = [] if known is None else [c for c in cands if c not in bad and c not in known]
    keep -= set(unknown)
    return {"kept": [c for c in cands if c in keep],
            "removed": [c for c in cands if c not in keep and c not in bad and c not in unknown],
            "not_ids": bad, "unknown_identity": unknown}


def shadow(conn, settings, candidates: Iterable[str], kept_by_site: Iterable[str], where="site", region="US",
           log_path=None, now=None) -> Optional[Dict[str, object]]:
    """Compare the plan with what an existing path kept. Returns the record, or None when the switch is off or
    anything goes wrong (a shadow check must never break the page it watches)."""
    if not SHADOW:
        return None
    try:
        cands = list(dict.fromkeys(candidates))
        site = set(kept_by_site) & set(cands)
        d = decide(conn, settings, cands, region)
        plan = set(d["kept"])
        only_plan = [c for c in cands if c in plan and c not in site]
        only_site = [c for c in cands if c in site and c not in plan]
        rec = {"at": now or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "where": str(where)[:40],
               "limits": limits_hash(settings), "n": len(cands), "kept_site": len(site), "kept_plan": len(plan),
               "agree": not only_plan and not only_site, "only_plan": only_plan[:MAX_IDS], "only_site": only_site[:MAX_IDS],
               "n_only_plan": len(only_plan), "n_only_site": len(only_site),
               "not_ids": len(d["not_ids"]), "unknown_identity": len(d["unknown_identity"])}
        path = Path(log_path) if log_path else LOG
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, sort_keys=True) + "\n")
        return rec
    except Exception as e:  # noqa: BLE001 - never break the page being watched
        print(f"[cinemack] plan shadow failed: {e!r}", flush=True)
        return None


def summary(log_path=None) -> Dict[str, object]:
    """Counts over the shadow log: comparisons, agreement rate, differences by page."""
    path = Path(log_path) if log_path else LOG
    n = agree = 0
    by: Dict[str, List[int]] = {}
    if path.exists():
        for line in open(path, encoding="utf-8"):
            try:
                r = json.loads(line)
            except ValueError:
                continue
            n += 1
            agree += bool(r.get("agree"))
            b = by.setdefault(r.get("where", "?"), [0, 0])
            b[0] += 1
            b[1] += 0 if r.get("agree") else 1
    return {"comparisons": n, "agree": agree, "by_page": {k: {"n": v[0], "differ": v[1]} for k, v in sorted(by.items())}}
