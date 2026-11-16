#!/usr/bin/env python3
"""v8.1 round 46 (16 Nov 2026): use the Guardian key's whole daily allowance (user request, 15 Nov).

The free Guardian key allows 500 calls a day. The nightly job (04:17) spends about 400 of them on pre-release
articles for the box-office research after the review fetch, so 50-100 calls go unused every day. This tool
  probe    makes ONE small call and writes the allowance the Guardian reports in its reply headers
           (X-RateLimit-Remaining-day / -Limit-day) to logs/guardian_quota.jsonl. Run by cron every 2 hours for a
           few days, it shows WHEN the daily count resets. It stops by itself after MAX_PROBES entries.
  report   reads that log: remaining calls over the day, and the window in which the count went back up.
  topup    shortly before the reset: probe, then spend (remaining - RESERVE) calls by running the SAME command the
           nightly job runs, in the live folder: `python pf5_data.py press --max N`. Nothing new touches the data:
           it is the nightly step run a second time. It does nothing unless GUARDIAN_TOPUP=1, and never while the
           nightly job, a press fetch or an evaluation is running.
The key is read from the environment (GUARDIAN_API_KEY, as nightly.sh sets it from keys.env) and is never printed
or logged. The log holds: time (UTC), the rate-limit headers, the HTTP status. Nothing else.
    python tools/v81_guardian_quota.py probe | report | topup [--reserve 40] [--dry]
"""
import argparse
import datetime
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG = os.environ.get("GUARDIAN_QUOTA_LOG", os.path.join(HERE, "logs", "guardian_quota.jsonl"))
LIVE = os.environ.get("CINEMACK_LIVE", os.path.expanduser("~/movie-buff/rag"))
G_API = "https://content.guardianapis.com/search"
UA = "Cinemack/1.0 (university research project; quota probe)"
MAX_PROBES = 120            # 10 days at one probe every 2 hours
RESERVE = 40                # calls always left unspent (the next review fetch, any later live lookup)
MIN_SPEND = 20              # not worth starting a fetch for fewer calls than this
BUSY = ("nightly.sh", "pf5_data.py press", "run_final_v", "eval_v3.py", "stage_scores.py", "fetch_free_data.py guardian")


def now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def rate_headers(headers):
    """The rate-limit headers of a reply as {lower-case name: int or text}."""
    out = {}
    for k, v in headers.items():
        if k.lower().startswith("x-ratelimit"):
            v = str(v).strip()
            out[k.lower()] = int(v) if re.fullmatch(r"-?\d+", v) else v[:40]
    return out


def remaining_day(h):
    for k, v in h.items():
        if "remaining" in k and "day" in k and isinstance(v, int):
            return v
    return None


def limit_day(h):
    for k, v in h.items():
        if "limit" in k and "remaining" not in k and "day" in k and isinstance(v, int):
            return v
    return None


def call(key, opener=urllib.request.urlopen):
    """One small search call -> (status, rate headers). Never raises for an HTTP error."""
    url = G_API + "?" + urllib.parse.urlencode({"section": "film", "page-size": 1, "api-key": key})
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    try:
        with opener(req, timeout=30) as r:
            return r.status, rate_headers(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, rate_headers(e.headers or {})
    except Exception as e:  # noqa: BLE001 - network trouble is a result, not a crash
        return None, {"error": type(e).__name__}


def read_log(path=None):
    rows = []
    p = path or LOG
    if os.path.exists(p):
        for line in open(p, encoding="utf-8"):
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
    return rows


def probe(key, path=None, opener=urllib.request.urlopen, at=None, cap=MAX_PROBES):
    p = path or LOG
    if len(read_log(p)) >= cap:
        return {"skipped": "enough probes recorded (%d); run `report`" % cap}
    status, h = call(key, opener)
    rec = {"at": at or now(), "status": status, "remaining_day": remaining_day(h), "limit_day": limit_day(h), "headers": h}
    os.makedirs(os.path.dirname(os.path.abspath(p)), exist_ok=True)
    with open(p, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, sort_keys=True) + "\n")
    return rec


def resets(rows):
    """[(last probe before, first probe after)] for every time the remaining count went UP."""
    out, prev = [], None
    for r in rows:
        if r.get("remaining_day") is None:
            continue
        if prev is not None and r["remaining_day"] > prev["remaining_day"]:
            out.append((prev["at"], r["at"]))
        prev = r
    return out


def report(rows):
    ok = [r for r in rows if r.get("remaining_day") is not None]
    L = [f"Guardian daily allowance: {len(rows)} probes, {len(ok)} with a remaining count"]
    if not ok:
        L.append("  no reply carried a remaining-calls header yet" + (f" (last status {rows[-1].get('status')}, headers "
                 f"{sorted(rows[-1].get('headers', {}))})" if rows else ""))
        return "\n".join(L)
    L.append(f"  limit per day: {ok[-1].get('limit_day')} | last probe {ok[-1]['at']}: {ok[-1]['remaining_day']} left")
    rs = resets(ok)
    if not rs:
        L.append("  the count has not gone back up between two probes yet: reset time unknown")
    else:
        hours = sorted({(a[11:16], b[11:16]) for a, b in rs})
        L.append(f"  the count went back up {len(rs)} time(s), between (UTC): " + "; ".join(f"{a} and {b}" for a, b in hours))
        L.append("  -> the top-up belongs shortly before the EARLIEST of those windows")
    low = min(ok, key=lambda r: r["remaining_day"])
    L.append(f"  lowest seen: {low['remaining_day']} at {low['at']}")
    by_hour = {}
    for r in ok:
        by_hour.setdefault(r["at"][11:13], []).append(r["remaining_day"])
    L.append("  median left by hour (UTC): " + " ".join(f"{h}h:{sorted(v)[len(v) // 2]}" for h, v in sorted(by_hour.items())))
    return "\n".join(L)


def busy(ps=None):
    """The name of a running job the top-up must not overlap, or None."""
    if ps is None:
        try:
            ps = subprocess.run(["ps", "-eo", "args"], capture_output=True, text=True, timeout=20).stdout
        except Exception:  # noqa: BLE001
            return "cannot list processes"
    for line in ps.splitlines():
        if "v81_guardian_quota" in line:
            continue
        for b in BUSY:
            if b in line:
                return b
    return None


def plan_topup(remaining, reserve=RESERVE, min_spend=MIN_SPEND):
    """How many calls to spend, or 0."""
    if remaining is None:
        return 0
    n = remaining - reserve
    return n if n >= min_spend else 0


def topup(key, reserve=RESERVE, dry=False, enabled=None, opener=urllib.request.urlopen, ps=None, runner=subprocess.run,
          path=None):
    on = (os.environ.get("GUARDIAN_TOPUP", "0") == "1") if enabled is None else enabled
    if not on:
        return "top-up is switched off (GUARDIAN_TOPUP is not 1): nothing done, no call made"
    b = busy(ps)
    if b:
        return f"another job is running ({b}): nothing done"
    rec = probe(key, path, opener, cap=10 ** 9)
    n = plan_topup(rec.get("remaining_day"), reserve)
    if not n:
        return f"{rec.get('remaining_day')} calls left (reserve {reserve}): nothing to spend"
    if dry:
        return f"{rec['remaining_day']} calls left: would run `python pf5_data.py press --max {n}` in {LIVE}"
    r = runner(["python", "pf5_data.py", "press", "--max", str(n)], cwd=LIVE, capture_output=True, text=True, timeout=7200)
    tail = "\n".join((r.stdout or "").strip().splitlines()[-4:])
    return f"{rec['remaining_day']} calls left: ran press --max {n} (exit {r.returncode})\n{tail}"


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["probe", "report", "topup"])
    ap.add_argument("--reserve", type=int, default=RESERVE)
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args(argv)
    if a.cmd == "report":
        print(report(read_log()))
        return 0
    key = os.environ.get("GUARDIAN_API_KEY")
    if not key:
        print(f"{now()} no GUARDIAN_API_KEY in the environment: nothing done")
        return 1
    if a.cmd == "probe":
        rec = probe(key)
        print(f"{now()} probe: " + (rec.get("skipped") or f"status {rec['status']}, {rec['remaining_day']} of {rec['limit_day']} left today"))
        return 0
    print(f"{now()} topup: " + topup(key, a.reserve, a.dry))
    return 0


if __name__ == "__main__":
    sys.exit(main())
