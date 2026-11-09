#!/usr/bin/env python3
"""9 Nov: tidy the server folders WITHOUT deleting anything.

    python tidy.py            # dry run: prints the plan (what moves where, what gets a symlink), changes nothing
    python tidy.py --apply    # does it, writes docs/tidy_manifest_<date>.md, refuses while an eval or fetch runs

Rules (every move is reversible; the manifest lists old -> new for each file):
  rag/ code backups (*.bak*, *.before-*, *.broken, *.dup)        -> archive/code-backups/
  rag/ old top-level logs (*.log)                                 -> archive/old-logs/
  rag/ finished experiment / version runners (run_e*, run_v3-v7, run_final v1-v7, speed_ab, run_pf4_night)
                                                                  -> archive/old-runs/   (git mv: history kept)
  rag/ one-off diagnostics no code imports                        -> archive/one-off/    (git mv)
  rag/ database backup copies (facts/movies/wiki .db.*)           -> backups/db/
  rag/ raw downloads (TMDB csv, TREC json)                        -> data/raw/  + a symlink at the old path when any
                                                                     active script still names the file
  rag/ web.bak-* folders                                          -> archive/web-backups/
  ~/   installer packages *.tar.gz, their unpack folders          -> ~/packages/ , ~/packages/unpacked/
  ~/   before-*.tar.gz (installer backups), retrieve.py.pre_e12   -> ~/backups/installer-backups/
  tmux sessions idle at a shell prompt (not site*, not press*, not final*): scrollback saved to logs/tmux/<name>.txt,
  then the session is closed. A session running anything is left alone.
Never touched: the live databases, results/, runs/, sets/, web/, tests/, docs/, models/, data/ contents, venvs,
llama.cpp-patched, dietitian-lora, ~/movie-buff outside rag/.
"""
import datetime
import os
import re
import shutil
import subprocess
import sys

RAG = os.path.expanduser("~/movie-buff/rag")
HOME = os.path.expanduser("~")
APPLY = "--apply" in sys.argv
STAMP = datetime.date.today().isoformat()

OLD_RUNS = re.compile(r"^(run_e\d+\.sh|run_v3[a-f]\.sh|run_v4\.sh|run_v5\.sh|run_v6_\w+\.sh|run_v7_night\.sh|"
                      r"run_overnight\.sh|run_extra\.sh|run_final\.sh|run_final_v3\.sh|run_final_v7\.sh|speed_ab\.sh|"
                      r"run_pf4_night\.sh)$")
ONE_OFF = {"diag_models.py", "diag_q07.py", "nb_test.py", "patch_followup.py", "patch_understand.py", "mc_pct_check.py",
           "new_questions_fixed.txt", "check_sealed_v7.py", "clarify_check.py"}
BACKUP = re.compile(r"(\.bak\w*|\.bak-[\w-]+|\.before-[\w.-]+|\.broken|\.dup|\.pre_e12)$")
DB_BACKUP = re.compile(r"^(facts|movies|wiki)\.db\.(bak[\w-]*|credits2?|precredits|prefetch|prewiki)$")
RAW = {"TMDB_movie_dataset_v11.csv", "tmdb_5000_credits.csv", "tmdb_5000_movies.csv", "trec_tot_2023.json",
       "trec_tot_2023_missing.json"}
KEEP_TMUX = re.compile(r"^(site|press|final)")


def tracked(path):
    r = subprocess.run(["git", "ls-files", "--error-unmatch", path], cwd=RAG, capture_output=True)
    return r.returncode == 0


def active_refs(name):
    """Active (not archived) scripts that mention this file name."""
    hits = []
    for f in os.listdir(RAG):
        if f.endswith((".py", ".sh", ".yaml")) and not BACKUP.search(f) and not OLD_RUNS.match(f) and f not in ONE_OFF \
                and f != "tidy.py":
            try:
                if name in open(os.path.join(RAG, f), errors="ignore").read():
                    hits.append(f)
            except OSError:
                pass
    return hits


def plan():
    moves = []                                   # (src, dst, how, note)
    for f in sorted(os.listdir(RAG)):
        p = os.path.join(RAG, f)
        if DB_BACKUP.match(f):
            moves.append((p, f"{RAG}/backups/db/{f}", "mv", "database backup copy"))
        elif os.path.isfile(p) and BACKUP.search(f) and not f.endswith((".db", ".sqlite")):
            moves.append((p, f"{RAG}/archive/code-backups/{f}", "mv", "code backup"))
        elif os.path.isfile(p) and f.endswith(".log"):
            moves.append((p, f"{RAG}/archive/old-logs/{f}", "mv", "old log"))
        elif OLD_RUNS.match(f):
            moves.append((p, f"{RAG}/archive/old-runs/{f}", "git" if tracked(f) else "mv", "finished runner"))
        elif f in ONE_OFF:
            refs = active_refs(f[:-3] if f.endswith(".py") else f)
            if refs:
                moves.append((p, None, "keep", f"still named by {refs}"))
            else:
                moves.append((p, f"{RAG}/archive/one-off/{f}", "git" if tracked(f) else "mv", "one-off script"))
        elif f in RAW:
            refs = active_refs(f)
            moves.append((p, f"{RAG}/data/raw/{f}", "mv+link", f"raw download (link left at the old path); named by {refs}"))
        elif f.startswith("web.bak-") and os.path.isdir(p):
            moves.append((p, f"{RAG}/archive/web-backups/{f}", "mv", "old web folder"))
    for f in sorted(os.listdir(HOME)):
        p = os.path.join(HOME, f)
        if f.startswith("before-") and f.endswith(".tar.gz") or f == "retrieve.py.pre_e12":
            moves.append((p, f"{HOME}/backups/installer-backups/{f}", "mv", "installer backup"))
        elif f.endswith(".tar.gz"):
            moves.append((p, f"{HOME}/packages/{f}", "mv", "installer package"))
        elif os.path.isdir(p) and re.match(r"^(v8_4\w*|v8d1\w*|v7fix-\w+|feat-nov4\w*|filters|pf4b?|presealed|sealed|s8|site-nov4)$", f):
            moves.append((p, f"{HOME}/packages/unpacked/{f}", "mv", "unpacked installer"))
    return moves


def tmux_plan():
    r = subprocess.run(["tmux", "list-panes", "-a", "-F", "#{session_name}\t#{pane_current_command}"],
                       capture_output=True, text=True)
    out = {}
    for line in r.stdout.splitlines():
        s, cmd = line.split("\t", 1)
        out.setdefault(s, set()).add(cmd)
    close = [s for s, cmds in out.items() if not KEEP_TMUX.match(s) and cmds <= {"bash", "zsh", "sh"}]
    keep = {s: sorted(c) for s, c in out.items() if s not in close}
    return sorted(close), keep


def busy():
    r = subprocess.run(["pgrep", "-af", "eval_v3.py|stage_scores.py|memory_eval.py|performance.py fit|pf5_model.py"],
                       capture_output=True, text=True)
    return [l for l in r.stdout.splitlines() if "pgrep" not in l]


def main():
    moves = plan()
    close, keep = tmux_plan()
    print(f"{'APPLY' if APPLY else 'DRY RUN'} {datetime.datetime.now():%Y-%m-%d %H:%M}: {len(moves)} items, "
          f"{len(close)} idle tmux sessions to close")
    for src, dst, how, note in moves:
        print(f"  {how:8s} {src.replace(HOME, '~')} -> {dst.replace(HOME, '~') if dst else '(stays)'}   [{note}]")
    print("  tmux close (scrollback saved first):", " ".join(close) or "-")
    print("  tmux keep:", "; ".join(f"{s} ({'/'.join(c)})" for s, c in sorted(keep.items())))
    if not APPLY:
        return
    if busy():
        sys.exit(f"refusing: a job is running: {busy()}")
    log = [f"# Tidy {STAMP}", "", "Nothing was deleted. Old path -> new path.", "", "| how | old | new | why |", "|---|---|---|---|"]
    for src, dst, how, note in moves:
        if how == "keep" or not os.path.exists(src):
            continue
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        if os.path.exists(dst):
            print(f"  skip (exists): {dst}"); continue
        if how == "git":
            subprocess.run(["git", "mv", os.path.relpath(src, RAG), os.path.relpath(dst, RAG)], cwd=RAG, check=True)
        else:
            shutil.move(src, dst)
            if how == "mv+link":
                os.symlink(os.path.relpath(dst, os.path.dirname(src)), src)
        log.append(f"| {how} | {src.replace(HOME, '~')} | {dst.replace(HOME, '~')} | {note} |")
    os.makedirs(f"{RAG}/logs/tmux", exist_ok=True)
    for s in close:
        with open(f"{RAG}/logs/tmux/{s}.txt", "w") as fh:
            fh.write(subprocess.run(["tmux", "capture-pane", "-p", "-S", "-", "-t", s], capture_output=True, text=True).stdout)
        subprocess.run(["tmux", "kill-session", "-t", s])
        log.append(f"| tmux | session {s} | logs/tmux/{s}.txt | idle shell, scrollback saved |")
    open(f"{RAG}/docs/tidy_manifest_{STAMP}.md", "w").write("\n".join(log) + "\n")
    print(f"done: docs/tidy_manifest_{STAMP}.md")


if __name__ == "__main__":
    main()
