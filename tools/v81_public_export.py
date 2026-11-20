#!/usr/bin/env python3
"""v8.1 round 50 (17 Nov 2026): make a clean PUBLIC copy of this repository for GitHub, next to the private one.

    python tools/v81_public_export.py --out ~/movie-buff/cinemack-public [--email you@users.noreply.github.com]
                                      [--holder "Your Name"] [--keys keys.env] [--replace]

Why a copy and not a cleaned-up original: the private repository (the live site runs from it) is never touched or
rewritten. The copy is a new repository in its own folder; deleting that folder undoes everything.

What the copy holds: the current files of the v8.1 branch, as one commit, with these rules applied -
  REMOVED  sealed test sets (anything with "sealed" in its name), databases, model / data files and archives,
           logs/, inbox/, archive/, raw result files (only results/v81_*.txt and *summary*.txt stay), the machine
           audit (planning/.../audit_raw/), key files (*.env except the *_lock*.env switch files), files over 10 MB.
  CHANGED  every email address (except @example.*, .invalid, GitHub noreply, noreply@anthropic.com, git@github.com)
           -> you@example.edu; home folders "/home/<name>/" -> "/home/you/"; any value found in --keys -> REDACTED
           (the values are read from the key file and never printed).
  ADDED    LICENSE (MIT, --holder), DATA_SOURCES.md (what the data is and whose terms apply), and docs/HISTORY.md:
           every commit message of the private branch with its date, oldest first, cleaned by the same rules - the
           record of how the project was built, without publishing the old commits themselves.
At the end the publish check runs on the copy and its verdict is printed. Nothing is pushed: pushing is the owner's
step, and the commands for it are printed.
HISTORY (default since 19 Nov, owner's request): instead of one commit, every commit of the branch is replayed into
the copy, oldest first (first-parent line), with its original date and message - so the public repository shows how
the project was built, step by step. Each replayed commit gets the same rules as above (removed paths, replaced
addresses / home folders / key values, plus any quoted or KEY=... value that looks like a key is replaced by
REDACTED, and every file the publish check would flag by its name is left out). Commits left empty by the rules are
skipped. Author and committer of every commit become --name / --email. Uses only git itself (no extra tools).
--snapshot gives the old single-commit copy.
BRANCHES (round 56, 20 Nov, owner's request): --branches "main=v8.1,v1=v1.0,v2=v2,..." replays several branches or
tags into the same copy - one public branch per version, `main` being the current best version. Shared history is
shared (each source commit is re-made once, with git's own plumbing: no working-tree checkout per commit), every
branch ends with the same "public copy" commit (LICENSE, DATA_SOURCES.md, ignore rules; docs/HISTORY.md on main).
--replace keeps the copy's `origin` remote, and --push pushes every branch (force-with-lease: the branches are
re-made each time) without ever asking for a password: if the server holds no working GitHub login, it says so and
prints the command for the owner.
"""
import argparse
import datetime
import fnmatch
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MARK = ".cinemack-public"              # a folder with this file inside may be replaced by --replace
MAX_BYTES = 10 * 1024 * 1024
PLACEHOLDER_EMAIL = "you@example.edu"

# ---------------------------------------------------------------- what leaves (glob = fnmatch on the whole path)
DROP = [
    "regex:(^|/)[^/]*sealed[^/]*$",
    "regex:^results/(?![^/]*summary[^/]*\\.txt$|v81_[^/]*\\.txt$)",
    "regex:^(logs|inbox|archive|_archive[^/]*|runs|tmp)/",
    "regex:^planning/cinemack-v8\\.1/audit_raw/",
    "regex:(^|/)(?![^/]*lock[^/]*\\.env$)[^/]*\\.env$",
    "regex:(^|/)\\.env(\\.[^/]*)?$",
    "regex:\\.(db|sqlite|sqlite3)(-wal|-shm|-journal)?$",
    "regex:\\.(gguf|npy|npz|pkl|pickle|joblib|parquet|safetensors|bin|pt|pth|ckpt|onnx|lgb|h5)$",
    "regex:\\.(zip|tgz|gz|bz2|xz|7z|tar)$",
    "regex:(^|/)(adapters?|checkpoints?|models?)/",
    "regex:(^|/)(id_rsa|id_ed25519)[^/]*$",
    "regex:(^|/)[^/]*\\.(pem|p12|key)$",
    "regex:(^|/)[^/]*requests\\.jsonl$",
    "regex:(^|/)crontab_before[^/]*$",
]
KEEP_MAIL = r"(?:example\.(?:com|org|edu|net)|[A-Za-z0-9.-]*\.invalid|users\.noreply\.github\.com|anthropic\.com|github\.com)"
R_MAIL = (r"[A-Za-z0-9._%+-]+@(?!" + KEEP_MAIL + r"\b)[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*"
          r"\.(?!(?:png|jpe?g|gif|svg|webp|ico|js|mjs|css|json|py|md|txt|html)\b)[A-Za-z]{2,}\b")
R_HOME = r"/home/(?!you/)[A-Za-z0-9_.-]+/"
TEXT_EXT = (".py", ".sh", ".md", ".txt", ".json", ".yaml", ".yml", ".csv", ".js", ".mjs", ".css", ".html", ".cmd",
            ".toml", ".cfg", ".ini", ".env", ".sql", ".svg", "")

LICENSE_MIT = """MIT License

Copyright (c) {year} {holder}

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""

DATA_SOURCES = """# Data sources and what this repository does NOT contain

The MIT licence in `LICENSE` covers the code and the documentation written for this project. It does not cover data
from the services below, and none of that data is in this repository: no database, no downloaded reviews, no
ratings, no model weights. To run the system you fetch your own copy with your own keys, under each service's terms
(see `docs/guide/REPRODUCE.md`).

| Source | Used for | Terms to read before you fetch |
| --- | --- | --- |
| TMDB API | Films, shows, people, credits, release dates, posters | This product uses the TMDB API but is not endorsed or certified by TMDB. TMDB API terms of use; attribution required. |
| MovieLens 32M (GroupLens) | Ratings for the recommendation benchmark and "people who liked this" | Research use; the data may not be redistributed. Cite: F. Maxwell Harper and Joseph A. Konstan. 2015. The MovieLens Datasets: History and Context. ACM TiiS 5(4). |
| The Guardian Open Platform | Reviews and articles about films (links and short summaries) | Guardian Open Platform terms; show "From the Guardian" and link to the article. |
| The New York Times API | Review links | NYT API terms. |
| Wikipedia / Wikidata | Reception text, identifiers | Wikipedia text: CC BY-SA 4.0 (attribution, share-alike). Wikidata: CC0. |
| OMDb API | IMDb rating and votes | OMDb terms (patron key). |
| YouTube Data API | Review and essay videos about films | YouTube API Services terms; stored data refreshed or deleted within 30 days. |
| JustWatch (via TMDB) | Where to watch | Attribution to JustWatch where listings are shown. |
| Trakt API | A person's own watch history, when they connect it | Trakt API terms. |

Test sets that were used once for a final, sealed evaluation are not included, so the published numbers can be
read but those exact tests cannot be re-run from this copy. Everything else in `docs/guide/EVALUATION_AND_RESULTS.md`
says how each number was measured.
"""


def compile_rules(rules=DROP):
    out = []
    for r in rules:
        kind, pat = r.split(":", 1)
        out.append(("glob", pat) if kind == "glob" else ("regex", re.compile(pat)))
    return out


def dropped(path, rules=None):
    for kind, pat in rules or compile_rules():
        if (kind == "glob" and fnmatch.fnmatchcase(path, pat)) or (kind == "regex" and pat.search(path)):
            return True
    return False


def secret_values(keys_path):
    """Values from a KEY=value file, 8 characters or longer. Returned for replacing only - never printed."""
    vals = []
    if not keys_path or not os.path.exists(keys_path):
        return vals
    with open(keys_path, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            v = line.split("=", 1)[1].strip().strip("'\"")
            if len(v) >= 8 and "$" not in v:
                vals.append(v)
    return sorted(set(vals), key=len, reverse=True)


R_KEYLIT = re.compile(r"""(?ix)((?:api[_-]?key|apikey|token|secret|passwd|password|client[_-]?secret|bearer)[a-z0-9_]*["']?\s*[=:]\s*["'])((?=[^"']*[0-9])[A-Za-z0-9_\-./+]{16,})(["'])""")
R_KEYENV = re.compile(r"(?im)^(\s*(?:export\s+)?[A-Z0-9_]*(?:KEY|TOKEN|SECRET|PASSWORD)[A-Z0-9_]*=)(?=\S*[0-9])[^\s$\"'<{]{12,}(\s*)$")


def scrub(text, secrets=()):
    for v in secrets:
        text = text.replace(v, "REDACTED")
    text = R_KEYLIT.sub(lambda m: m.group(1) + "REDACTED" + m.group(3), text)
    text = R_KEYENV.sub(lambda m: m.group(1) + "REDACTED" + m.group(2), text)
    text = re.sub(R_MAIL, PLACEHOLDER_EMAIL, text)
    return re.sub(R_HOME, "/home/you/", text)


def run(cmd, cwd=None, check=True, env=None):
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, env=env)
    if check and p.returncode:
        raise RuntimeError("%s failed (%d): %s" % (" ".join(cmd[:3]), p.returncode, (p.stderr or p.stdout)[-400:]))
    return p


def prepare_out(out, replace):
    if os.path.exists(out) and os.listdir(out):
        if not (replace and os.path.exists(os.path.join(out, MARK))):
            raise SystemExit("NOT done: %s exists and is not empty (use --replace only for a folder this tool made)" % out)
        shutil.rmtree(out)


def write_extras(out, holder, year):
    with open(os.path.join(out, "LICENSE"), "w") as f:
        f.write(LICENSE_MIT.format(year=year, holder=holder))
    with open(os.path.join(out, "DATA_SOURCES.md"), "w") as f:
        f.write(DATA_SOURCES)
    gi = os.path.join(out, ".gitignore")
    old = open(gi).read() if os.path.exists(gi) else ""
    add = [x for x in ("*.db", "*.sqlite", "*.env", "!*lock*.env", "logs/", "inbox/", "facts.db", "sets/sealed_*") if x not in old.split("\n")]
    if add:
        with open(gi, "a") as f:
            f.write(("\n" if old and not old.endswith("\n") else "") + "# public copy (v81_public_export.py)\n" + "\n".join(add) + "\n")
    open(os.path.join(out, MARK), "w").close()


def commit_extras(out, name, email):
    env = dict(os.environ, GIT_AUTHOR_NAME=name, GIT_AUTHOR_EMAIL=email, GIT_COMMITTER_NAME=name, GIT_COMMITTER_EMAIL=email)
    run(["git", "add", "-A", "--", ".", ":!" + MARK], cwd=out)
    run(["git", "commit", "-q", "-m", "Public copy: MIT licence, data sources, commit list, ignore rules\n\n"
         "Made by tools/v81_public_export.py from the private repository. Sealed test sets, databases, data files, logs\n"
         "and personal details were removed from every commit; see DATA_SOURCES.md."], cwd=out, env=env)


def history_md(src, branch, secrets):
    """docs/HISTORY.md: date and subject of every commit on the branch, oldest first, cleaned."""
    log = run(["git", "log", "--reverse", "--date=short", "--format=%ad\t%s", branch], cwd=src).stdout.splitlines()
    out, day = ["# How this was built: every commit, oldest first", "",
                "Generated by `tools/v81_public_export.py` from the private repository's `%s` branch (%d commits). "
                "Each line is one commit message; dates are when the change was committed. The detailed reasoning for "
                "each step is in `planning/` and `docs/guide/`." % (branch, len(log)), ""], None
    for line in log:
        d, _, subj = line.partition("\t")
        if d != day:
            out += ["", "## " + d, ""]
            day = d
        out.append("- " + scrub(subj, secrets).replace("|", "/"))
    return "\n".join(out) + "\n"


class _Blobs:
    """One `git cat-file --batch` process: blob contents by id, without a process per file."""
    def __init__(self, repo):
        self.p = subprocess.Popen(["git", "cat-file", "--batch"], cwd=repo, stdin=subprocess.PIPE, stdout=subprocess.PIPE)

    def get(self, sha):
        self.p.stdin.write(sha.encode() + b"\n")
        self.p.stdin.flush()
        head = self.p.stdout.readline().split()
        if len(head) < 3 or head[1] == b"missing":
            return None
        n = int(head[2])
        data = self.p.stdout.read(n)
        self.p.stdout.read(1)
        return data

    def close(self):
        try:
            self.p.stdin.close()
            self.p.wait(timeout=10)
        except Exception:  # noqa: BLE001
            self.p.kill()


def _write(out, path, data, mode, secrets):
    dst = os.path.join(out, path)
    if os.path.lexists(dst):
        os.remove(dst)
    os.makedirs(os.path.dirname(dst) or out, exist_ok=True)
    if mode == "120000":                                   # a symbolic link: its blob is the target
        os.symlink(data.decode("utf-8", "replace"), dst)
        return
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        with open(dst, "wb") as f:
            f.write(data)
    else:
        with open(dst, "w", encoding="utf-8", newline="") as f:
            f.write(scrub(text, secrets))
    if mode == "100755":
        os.chmod(dst, 0o755)


def _remove(out, path):
    dst = os.path.join(out, path)
    if os.path.lexists(dst):
        os.remove(dst)
        d = os.path.dirname(dst)
        while d.startswith(out + os.sep) and os.path.isdir(d) and not os.listdir(d):
            os.rmdir(d)
            d = os.path.dirname(d)


def replay(src, out, branch, secrets, name, email, progress=200):
    """Every first-parent commit of `branch`, oldest first, re-made in `out` with the rules. -> (source commits, made)."""
    sys.path.insert(0, os.path.join(HERE, "tools"))
    import v81_publish_check as PC
    rules = compile_rules()

    def skip(p):
        return dropped(p, rules) or bool(PC.kind_of_name(p))
    commits = run(["git", "rev-list", "--reverse", "--first-parent", branch], cwd=src).stdout.split()
    os.makedirs(out, exist_ok=True)
    run(["git", "init", "-q", "-b", "main"], cwd=out)
    run(["git", "config", "core.autocrlf", "false"], cwd=out)
    blobs = _Blobs(src)
    made, prev = 0, None
    try:
        for i, c in enumerate(commits, 1):
            if prev is None:
                raw = run(["git", "ls-tree", "-r", "-z", c], cwd=src).stdout.split("\0")
                changes = []
                for e in raw:
                    if not e:
                        continue
                    meta, path = e.split("\t", 1)
                    mode, typ, sha = meta.split()
                    if typ == "blob":
                        changes.append(("A", path, mode, sha))
            else:
                parts = run(["git", "diff-tree", "-r", "-z", "--raw", "--no-renames", prev, c], cwd=src).stdout.split("\0")
                changes, k = [], 0
                while k + 1 < len(parts):
                    meta, path = parts[k], parts[k + 1]
                    k += 2
                    if not meta.startswith(":"):
                        continue
                    _om, nm, _os, ns, st = meta[1:].split()
                    changes.append((st[0], path, nm, ns))
            for st, path, mode, sha in changes:
                if skip(path) or mode == "160000":
                    continue
                if st == "D":
                    _remove(out, path)
                    continue
                data = blobs.get(sha)
                if data is None or len(data) > MAX_BYTES:
                    _remove(out, path)
                    continue
                _write(out, path, data, mode, secrets)
            run(["git", "add", "-A"], cwd=out)
            prev = c
            if run(["git", "diff", "--cached", "--quiet"], cwd=out, check=False).returncode == 0 and made:
                continue                                    # nothing left in this commit after the rules
            f = run(["git", "log", "-1", "--format=%aI%x00%cI%x00%B", c], cwd=src).stdout.split("\0", 2)
            env = dict(os.environ, GIT_AUTHOR_NAME=name, GIT_AUTHOR_EMAIL=email, GIT_COMMITTER_NAME=name,
                       GIT_COMMITTER_EMAIL=email, GIT_AUTHOR_DATE=f[0], GIT_COMMITTER_DATE=f[1])
            msg = scrub(f[2].strip(), secrets) or "(no message)"
            subprocess.run(["git", "commit", "-q", "--allow-empty", "-F", "-"], cwd=out, env=env, input=msg,
                           text=True, check=True, capture_output=True)
            made += 1
            if progress and i % progress == 0:
                print("  replayed %d of %d commits" % (i, len(commits)), flush=True)
    finally:
        blobs.close()
    return len(commits), made


class _Plumb:
    """Builds commits in `out` without a working tree: a private index file + hash-object / update-index /
    write-tree / commit-tree. Cleaned blobs are cached by source blob id."""
    def __init__(self, src, out, secrets, name, email):
        self.src, self.out, self.secrets = src, out, secrets
        self.env = dict(os.environ, GIT_INDEX_FILE=os.path.join(out, ".git", "export-index"))
        self.ident = dict(GIT_AUTHOR_NAME=name, GIT_AUTHOR_EMAIL=email, GIT_COMMITTER_NAME=name, GIT_COMMITTER_EMAIL=email)
        self.blob = {}
        self.blobs = _Blobs(src)
        sys.path.insert(0, os.path.join(HERE, "tools"))
        import v81_publish_check as PC
        rules = compile_rules()
        self.skip = lambda p: dropped(p, rules) or bool(PC.kind_of_name(p))

    def git(self, args, input=None, env=None):
        p = subprocess.run(["git"] + args, cwd=self.out, input=input, capture_output=True, text=isinstance(input, str) or input is None,
                           env=env or self.env)
        if p.returncode:
            err = p.stderr if isinstance(p.stderr, str) else p.stderr.decode("utf-8", "replace")
            raise RuntimeError("git %s failed: %s" % (args[0], err[-300:]))
        return p.stdout

    def clean_blob(self, sha, mode):
        """-> cleaned blob id in `out`, or None when the file must be left out."""
        if (sha, mode) in self.blob:
            return self.blob[(sha, mode)]
        data = self.blobs.get(sha)
        new = None
        if data is not None and len(data) <= MAX_BYTES:
            if mode != "120000":
                try:
                    data = scrub(data.decode("utf-8"), self.secrets).encode("utf-8")
                except UnicodeDecodeError:
                    pass
            p = subprocess.run(["git", "hash-object", "-w", "--stdin"], cwd=self.out, input=data, capture_output=True)
            new = p.stdout.decode().strip() if p.returncode == 0 else None
        self.blob[(sha, mode)] = new
        return new

    def tree_of(self, commit):
        return self.git(["rev-parse", commit + "^{tree}"]).strip()

    def commit(self, tree, parents, msg, date_a=None, date_c=None):
        env = dict(self.env, **self.ident)
        if date_a:
            env.update(GIT_AUTHOR_DATE=date_a, GIT_COMMITTER_DATE=date_c or date_a)
        args = ["commit-tree", tree]
        for p in parents:
            args += ["-p", p]
        return self.git(args, input=msg, env=env).strip()


def replay_refs(src, out, refs, secrets, name, email, progress=200):
    """refs: [(public branch, source ref)]. Each ref's first-parent line, oldest first, re-made once per source
    commit (shared history stays shared). -> ({public branch: commit in out}, source commits, made)."""
    os.makedirs(out, exist_ok=True)
    run(["git", "init", "-q", "-b", "main"], cwd=out)
    run(["git", "config", "core.autocrlf", "false"], cwd=out)
    P = _Plumb(src, out, secrets, name, email)
    mapped, heads, seen, made = {}, {}, 0, 0
    empty_tree = P.git(["mktree"], input="").strip()
    try:
        for branch, ref in refs:
            if run(["git", "rev-parse", "--verify", "-q", ref + "^{commit}"], cwd=src, check=False).returncode:
                heads[branch] = None                                  # no such branch or tag: left out, reported
                continue
            commits = run(["git", "rev-list", "--reverse", "--first-parent", ref], cwd=src).stdout.split()
            prev = None
            for c in commits:
                if c in mapped:
                    prev = c
                    continue
                seen += 1
                base = mapped.get(prev)
                if base:
                    P.git(["read-tree", base])
                else:
                    P.git(["read-tree", empty_tree])
                if prev is None:
                    raw = run(["git", "ls-tree", "-r", "-z", c], cwd=src).stdout.split("\0")
                    changes = []
                    for e in raw:
                        if e:
                            meta, path = e.split("\t", 1)
                            mode, typ, sha = meta.split()
                            if typ == "blob":
                                changes.append(("A", path, mode, sha))
                else:
                    parts = run(["git", "diff-tree", "-r", "-z", "--raw", "--no-renames", prev, c], cwd=src).stdout.split("\0")
                    changes, k = [], 0
                    while k + 1 < len(parts):
                        meta, path = parts[k], parts[k + 1]
                        k += 2
                        if meta.startswith(":"):
                            _om, nm, _os, ns, st = meta[1:].split()
                            changes.append((st[0], path, nm, ns))
                lines = []
                for st, path, mode, sha in changes:
                    if P.skip(path) or mode == "160000" or "\n" in path:
                        continue
                    new = None if st == "D" else P.clean_blob(sha, mode)
                    if new is None:
                        lines.append("0 %s\t%s" % ("0" * 40, path))      # remove (a no-op when absent)
                    else:
                        lines.append("%s %s\t%s" % (mode, new, path))
                if lines:
                    P.git(["update-index", "--index-info"], input="\n".join(lines) + "\n")
                tree = P.git(["write-tree"]).strip()
                if base and tree == P.tree_of(base):
                    mapped[c] = base                                  # nothing left after the rules
                else:
                    f = run(["git", "log", "-1", "--format=%aI%x00%cI%x00%B", c], cwd=src).stdout.split("\0", 2)
                    msg = scrub(f[2].strip(), secrets) or "(no message)"
                    mapped[c] = P.commit(tree, [base] if base else [], msg + "\n", f[0], f[1])
                    made += 1
                prev = c
                if progress and seen % progress == 0:
                    print("  replayed %d commits" % seen, flush=True)
            heads[branch] = mapped[commits[-1]] if commits else None
    finally:
        P.blobs.close()
    return heads, seen, made, P


def finish_branch(P, out, branch, head, holder, year, history_text=None):
    """The "public copy" commit on top of one branch: LICENSE, DATA_SOURCES.md, ignore rules (+ docs/HISTORY.md)."""
    P.git(["read-tree", head])
    files = {"LICENSE": LICENSE_MIT.format(year=year, holder=holder), "DATA_SOURCES.md": DATA_SOURCES}
    try:
        old = P.git(["cat-file", "-p", head + ":.gitignore"])
    except RuntimeError:
        old = ""
    add = [x for x in ("*.db", "*.sqlite", "*.env", "!*lock*.env", "logs/", "inbox/", "facts.db", "sets/sealed_*") if x not in old.split("\n")]
    files[".gitignore"] = old + (("\n" if old and not old.endswith("\n") else "") + "# public copy (v81_public_export.py)\n" + "\n".join(add) + "\n" if add else "")
    if history_text:
        files["docs/HISTORY.md"] = history_text
    lines = []
    for path, text in files.items():
        p = subprocess.run(["git", "hash-object", "-w", "--stdin"], cwd=out, input=text.encode("utf-8"), capture_output=True, check=True)
        lines.append("100644 %s\t%s" % (p.stdout.decode().strip(), path))
    P.git(["update-index", "--add", "--index-info"], input="\n".join(lines) + "\n")
    tree = P.git(["write-tree"]).strip()
    # round 58: dated like the branch's last commit, so remaking an unchanged branch gives the same commit (no force push)
    da, dc = P.git(["log", "-1", "--format=%aI%x00%cI", head]).strip().split("\0")
    c = P.commit(tree, [head], "Public copy: MIT licence, data sources, ignore rules%s (branch %s)\n\n"
                 "Made by tools/v81_public_export.py from the private repository. Sealed test sets, databases, data files, logs\n"
                 "and personal details were removed from every commit; see DATA_SOURCES.md.\n"
                 % (", commit list" if history_text else "", branch), da, dc)
    P.git(["update-ref", "refs/heads/" + branch, c])
    return c


def push_all(out, branches):
    """Push every branch to origin without ever prompting. -> (ok, text)."""
    url = run(["git", "remote", "get-url", "origin"], cwd=out, check=False).stdout.strip()
    if not url:
        return False, "no origin remote in the copy"
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GIT_ASKPASS="true", SSH_ASKPASS="true",
               GIT_SSH_COMMAND="ssh -o BatchMode=yes -o ConnectTimeout=20")
    run(["git", "fetch", "-q", "origin"], cwd=out, check=False, env=env)
    p = run(["git", "push", "--force-with-lease", "origin"] + ["refs/heads/%s:refs/heads/%s" % (b, b) for b in branches],
            cwd=out, check=False, env=env)
    return p.returncode == 0, (p.stderr or p.stdout).strip()[-600:]


def snapshot(src, out, branch, secrets, name, email):
    os.makedirs(out, exist_ok=True)
    files = [f for f in run(["git", "ls-files", "-z"], cwd=src).stdout.split("\0") if f]
    rules = compile_rules()
    n = 0
    for f in files:
        p = os.path.join(src, f)
        if dropped(f, rules) or not os.path.isfile(p) or os.path.getsize(p) > MAX_BYTES:
            continue
        dst = os.path.join(out, f)
        os.makedirs(os.path.dirname(dst) or out, exist_ok=True)
        if f.endswith(TEXT_EXT) or os.path.splitext(f)[1] == "":
            try:
                text = open(p, encoding="utf-8").read()
            except UnicodeDecodeError:
                shutil.copy2(p, dst)
            else:
                with open(dst, "w", encoding="utf-8") as fh:
                    fh.write(scrub(text, secrets))
        else:
            shutil.copy2(p, dst)
        n += 1
    os.makedirs(os.path.join(out, "docs"), exist_ok=True)
    with open(os.path.join(out, "docs", "HISTORY.md"), "w", encoding="utf-8") as fh:
        fh.write(history_md(src, branch, secrets))
    env = dict(os.environ, GIT_AUTHOR_NAME=name, GIT_AUTHOR_EMAIL=email, GIT_COMMITTER_NAME=name, GIT_COMMITTER_EMAIL=email)
    run(["git", "init", "-q", "-b", "main"], cwd=out)
    run(["git", "add", "-A"], cwd=out)
    run(["git", "commit", "-q", "-m", "Cinemack: the code, tests and documentation at the end of v8.1\n\n"
         "A public copy of the private repository; docs/HISTORY.md lists every commit message of the work so far."], cwd=out, env=env)
    return n


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--src", default=HERE)
    ap.add_argument("--branch", default="v8.1")
    ap.add_argument("--email", default="author@example.invalid", help="commit email in the copy (your GitHub noreply address)")
    ap.add_argument("--name", default=None, help="commit name in the copy (default: git config user.name)")
    ap.add_argument("--holder", default=None, help="copyright holder for the MIT licence (default: --name)")
    ap.add_argument("--keys", default=None, help="KEY=value file whose values must not appear anywhere")
    ap.add_argument("--replace", action="store_true")
    ap.add_argument("--snapshot", action="store_true", help="one commit of the current files instead of the whole history")
    ap.add_argument("--branches", default=None, help='public branch=source ref, comma separated; the first is checked out, '
                    'e.g. "main=v8.1,v1=v1.0,v2=v2"')
    ap.add_argument("--push", action="store_true", help="push every branch to the copy's origin (never prompts)")
    a = ap.parse_args(argv)
    out = os.path.abspath(os.path.expanduser(a.out))
    src = os.path.abspath(os.path.expanduser(a.src))
    if out == src or out.startswith(src + os.sep) or src.startswith(out + os.sep):
        raise SystemExit("NOT done: the copy must be outside the repository")
    name = a.name or run(["git", "config", "user.name"], cwd=src, check=False).stdout.strip() or "Cinemack author"
    holder = a.holder or name
    secrets = secret_values(os.path.expanduser(a.keys) if a.keys else None)
    origin = run(["git", "remote", "get-url", "origin"], cwd=out, check=False).stdout.strip() \
        if os.path.isdir(os.path.join(out, ".git")) else ""
    prepare_out(out, a.replace)
    year = datetime.date.today().year
    if a.branches:
        refs = [tuple(x.strip().split("=", 1)) for x in a.branches.split(",") if "=" in x]
        heads, total, made, P = replay_refs(src, out, refs, secrets, name, a.email)
        print("branches: %d source commits, %d re-made (%d left empty by the rules)" % (total, made, total - made))
        main_branch, main_ref = refs[0]
        for b, ref in refs:
            if not heads.get(b):
                print("  %s <- %s: no commits, left out" % (b, ref))
                continue
            c = finish_branch(P, out, b, heads[b], holder, year, history_md(src, main_ref, secrets) if b == main_branch else None)
            n = run(["git", "rev-list", "--count", c], cwd=out).stdout.strip()
            print("  %-6s <- %-14s %s commits, head %s" % (b, ref, n, c[:7]))
        run(["git", "symbolic-ref", "HEAD", "refs/heads/" + main_branch], cwd=out)
        run(["git", "reset", "-q", "--hard", main_branch], cwd=out)
        try:
            os.remove(os.path.join(out, ".git", "export-index"))
        except OSError:
            pass
        if origin:
            run(["git", "remote", "add", "origin", origin], cwd=out)
        files = len([x for x in run(["git", "ls-files"], cwd=out).stdout.splitlines() if x])
        size = run(["du", "-sh", os.path.join(out, ".git")], check=False).stdout.split("\t")[0]
        print("public copy: %s | mode branches | %d branches | main %s commits | %d files | .git %s | %d key value(s) "
              "searched for (values not shown)" % (out, len([b for b, _ in refs if heads.get(b)]),
                                                    run(["git", "rev-list", "--count", "HEAD"], cwd=out).stdout.strip(),
                                                    files, size, len(secrets)))
        sys.path.insert(0, os.path.join(HERE, "tools"))
        import v81_publish_check as PC
        text = PC.report(PC.scan(out, history=True, deep=True))
        print(text)
        with open(os.path.join(out, MARK), "w") as f:
            f.write(text)
        ready = "NOT ready" not in text
        if a.push and ready:
            ok, msg = push_all(out, [b for b, _ in refs if heads.get(b)])
            print("push: %s\n%s" % ("OK" if ok else "NOT pushed", msg))
            if not ok:
                print("To push yourself: cd %s && git push --force-with-lease origin %s" % (out, " ".join(b for b, _ in refs if heads.get(b))))
        elif a.push:
            print("push: NOT pushed (the publish check is not READY)")
        return 0 if ready else 1
    if a.snapshot:
        mode = "snapshot"
        n = snapshot(src, out, a.branch, secrets, name, a.email)
        print("snapshot: %d files copied" % n)
    else:
        mode = "history"
        total, made = replay(src, out, a.branch, secrets, name, a.email)
        print("history: %d commits replayed (%d left empty by the rules and skipped)" % (made, total - made))
        os.makedirs(os.path.join(out, "docs"), exist_ok=True)
        with open(os.path.join(out, "docs", "HISTORY.md"), "w", encoding="utf-8") as fh:
            fh.write(history_md(src, a.branch, secrets))
    write_extras(out, holder, year)
    commit_extras(out, name, a.email)
    commits = run(["git", "rev-list", "--count", "HEAD"], cwd=out).stdout.strip()
    files = len([x for x in run(["git", "ls-files"], cwd=out).stdout.splitlines() if x])
    size = run(["du", "-sh", os.path.join(out, ".git")], check=False).stdout.split("\t")[0]
    print("public copy: %s | mode %s | %s commits | %d files | .git %s | %d key value(s) searched for (values not shown)"
          % (out, mode, commits, files, size, len(secrets)))
    sys.path.insert(0, os.path.join(HERE, "tools"))
    import v81_publish_check as PC
    res = PC.scan(out, history=True, deep=True)
    text = PC.report(res)
    print(text)
    with open(os.path.join(out, MARK), "w") as f:
        f.write(text)
    print("To publish (your step; nothing was pushed): make an EMPTY repository on GitHub, then\n"
          "  cd %s\n  git remote add origin git@github.com:<your-user>/cinemack.git\n  git push -u origin main\n"
          "If the commit should show on your GitHub profile, run this tool again with --replace --email <your GitHub "
          "noreply address> first." % out)
    return 0 if "NOT ready" not in text else 1


if __name__ == "__main__":
    sys.exit(main())
