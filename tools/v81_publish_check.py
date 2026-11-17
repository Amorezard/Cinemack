#!/usr/bin/env python3
"""Before the repository goes to GitHub: is there anything in it that must not be public?

It looks at every file git tracks (and, with --history, every file name git has EVER tracked, and with --deep the
text of every old commit) and lists WHERE something sensitive is - file, line number and kind - never the value
itself. Nothing is changed or deleted; the decisions are yours.

What it looks for
  secrets file        keys.env, .env, *.pem, id_rsa and the like
  key-like value      api_key / token / secret / password = "a long quoted literal with a digit in it", or a
                      NAME_KEY=value line as in an .env file (reading a key from the environment is fine)
  email address       any address except example.com and the commit-trailer address
  sealed test set     sets/sealed_* - publishing them ends their use as a blind test
  database file       *.db, *.sqlite: facts.db holds licensed data (TMDB, IMDb, Guardian, NYT ...) that their terms do
                      not let us redistribute; the site's own databases hold accounts and chats
  model / data file   *.gguf, *.npy, *.zip, *.tgz and anything over 5 MB
  personal data       logs/, chats, accounts, requests.jsonl, anything under results/ with per-person rows
"Ready" means none of the above is tracked now. With --history it also needs none of them in the past: a file
deleted in a later commit is still downloadable from GitHub unless the history is rewritten or the repository is
started fresh from the current files.
    python tools/v81_publish_check.py [--history] [--deep] [--repo PATH]
"""
import argparse
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BIG = 5 * 1024 * 1024
ALLOWED_MAIL = ("@example.com", "@example.org", ".invalid", "@x.edu", "noreply@anthropic.com", "@users.noreply.github.com")
R_SECRET_FILE = re.compile(r"(^|/)(keys\.env|\.env(\..*)?|.*secret.*|.*\.pem|id_rsa.*|id_ed25519.*|credentials.*|.*\.p12)$", re.I)
R_KEY = re.compile(r"""(?ix)(api[_-]?key|apikey|token|secret|passwd|password|client[_-]?secret|bearer)[a-z0-9_]*["']?\s*[=:]\s*
                       ["']((?=[^"']*[0-9])[A-Za-z0-9_\-./+]{16,})["']""")
R_ENV = re.compile(r"(?i)^\s*(export\s+)?[A-Z0-9_]*(KEY|TOKEN|SECRET|PASSWORD)[A-Z0-9_]*=(?=\S*[0-9])[^\s$\"'<{]{12,}\s*$")
R_MAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
R_SEALED = re.compile(r"(^|/)sets/sealed_", re.I)
R_DB = re.compile(r"\.(db|sqlite|sqlite3)(-wal|-shm)?$", re.I)
R_DATA = re.compile(r"\.(gguf|npy|npz|zip|tgz|tar\.gz|safetensors|bin|parquet)$", re.I)
R_PERSONAL = re.compile(r"(^|/)(logs/|chats?/|accounts?[./]|.*requests\.jsonl$|.*users?\.(json|csv)$|.*crontab_before)", re.I)
TEXT = (".py", ".sh", ".md", ".txt", ".json", ".yaml", ".yml", ".js", ".mjs", ".html", ".css", ".csv", ".env", ".cfg", ".ini", ".toml", ".cmd")


def git(repo, *args, timeout=300):
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, timeout=timeout, errors="replace").stdout


def kind_of_name(path):
    if R_SECRET_FILE.search(path):
        return "secrets file"
    if R_SEALED.search(path):
        return "sealed test set"
    if R_DB.search(path):
        return "database file"
    if R_PERSONAL.search(path) and not path.endswith((".py", ".js", ".md", ".html", ".sh")):
        return "personal data"
    if R_DATA.search(path):
        return "model / data file"
    return None


def scan_text(text):
    """[(line number, kind)] - what was found, not the text."""
    out = []
    for n, line in enumerate(text.splitlines(), 1):
        if len(line) > 2000:
            continue
        if R_KEY.search(line) or R_ENV.match(line):
            out.append((n, "key-like value"))
        for m in R_MAIL.finditer(line):
            if not m.group(0).lower().endswith(ALLOWED_MAIL) and not re.search(r"\.(png|jpg|svg|js|css|json)$", m.group(0), re.I):
                out.append((n, "email address"))
                break
    return out


def scan(repo, history=False, deep=False):
    files = [f for f in git(repo, "ls-files").splitlines() if f]
    findings = []
    for f in files:
        k = kind_of_name(f)
        p = os.path.join(repo, f)
        if k:
            findings.append({"file": f, "line": None, "kind": k})
        try:
            size = os.path.getsize(p)
        except OSError:
            continue
        if size > BIG and not k:
            findings.append({"file": f, "line": None, "kind": "model / data file"})
        if f.endswith(TEXT) and size <= BIG:
            try:
                text = open(p, encoding="utf-8", errors="replace").read()
            except OSError:
                continue
            for n, kind in scan_text(text):
                findings.append({"file": f, "line": n, "kind": kind})
    res = {"tracked_files": len(files), "findings": findings, "history": None,
           "has_license": any(f.upper().startswith(("LICENSE", "LICENCE", "COPYING")) for f in files),
           "has_readme": any(f.upper().startswith("README") for f in files)}
    if history:
        ever = {f for f in git(repo, "log", "--all", "--format=", "--name-only", "--diff-filter=A").splitlines() if f}
        gone = sorted(f for f in ever if f not in set(files) and kind_of_name(f))
        res["history"] = {"files_ever": len(ever), "sensitive_names_no_longer_tracked": [{"file": f, "kind": kind_of_name(f)} for f in gone],
                          "old_text_hits": None}
        if deep:
            n = 0
            try:
                for line in git(repo, "log", "--all", "-p", "--no-color", timeout=1800).splitlines():
                    if line.startswith("+") and not line.startswith("+++") and (R_KEY.search(line) or R_ENV.match(line[1:])):
                        n += 1
            except subprocess.TimeoutExpired:
                n = None
            res["history"]["old_text_hits"] = n
    res["ready"] = not findings and not (res["history"] and (res["history"]["sensitive_names_no_longer_tracked"] or res["history"]["old_text_hits"]))
    return res


def report(res):
    by = {}
    for f in res["findings"]:
        by.setdefault(f["kind"], []).append(f)
    L = [f"Publish check: {res['tracked_files']:,} tracked files", ""]
    if not res["findings"]:
        L.append("tracked now: nothing sensitive found")
    for kind in sorted(by):
        rows = by[kind]
        L.append(f"{kind}: {len(rows)}")
        seen = {}
        for r in rows:
            seen.setdefault(r["file"], []).append(r["line"])
        for f, lines in list(sorted(seen.items()))[:25]:
            where = "" if lines == [None] else " lines " + ", ".join(str(x) for x in lines[:8]) + (" ..." if len(lines) > 8 else "")
            L.append(f"    {f}{where}")
        if len(seen) > 25:
            L.append(f"    ... and {len(seen) - 25} more files")
    h = res["history"]
    if h:
        L += ["", f"history: {h['files_ever']:,} file names ever tracked"]
        gone = h["sensitive_names_no_longer_tracked"]
        L.append(f"  sensitive files that were tracked once and still sit in old commits: {len(gone)}")
        L += [f"    {g['file']} ({g['kind']})" for g in gone[:25]]
        if h["old_text_hits"] is not None:
            L.append(f"  key-like values in the text of old commits: {h['old_text_hits']}")
    else:
        L += ["", "history: not checked (run with --history, and --deep for the text of old commits)"]
    L += ["", f"README: {'yes' if res['has_readme'] else 'MISSING'} | LICENSE: {'yes' if res['has_license'] else 'none - without one, others may read the code but have no right to reuse it'}",
          "", "READY to publish" if res["ready"] else "NOT ready: see docs/guide/PUBLISHING.md for what to do about each kind"]
    return "\n".join(L) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=HERE)
    ap.add_argument("--history", action="store_true")
    ap.add_argument("--deep", action="store_true")
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    res = scan(a.repo, a.history or a.deep, a.deep)
    text = report(res)
    if a.out:
        os.makedirs(os.path.dirname(a.out), exist_ok=True)
        with open(a.out + ".txt", "w") as fh:
            fh.write(text)
        with open(a.out + ".json", "w") as fh:
            json.dump(res, fh, indent=1)
    print(text, end="")
    return 0 if res["ready"] else 1


if __name__ == "__main__":
    sys.exit(main())
