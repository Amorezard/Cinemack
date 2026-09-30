#!/usr/bin/env python3
"""
mb.py - one entry point for setting up and running Movie Buff.

    python mb.py check              is everything in place? (packages, Ollama, models, databases,
                                    embedder match, vector cache, GPU). Exit code 1 if not.
    python mb.py settings           every setting in effect and where it came from
    python mb.py build              the data build steps, which are done, and the command for each
    python mb.py build-cache        build + check the in-memory vector cache (vec_cache/)
    python mb.py ask "question"     one question through the full pipeline (same as chat.py)
    python mb.py chat               the terminal chat (chat.py)
    python mb.py serve              the website (web/server.py; set MOVIE_BUFF_CODE first)
    python mb.py eval ARGS...       stage_scores.py with the same arguments

Run it from the folder that holds movies.db (the rag folder). Settings live in config.yaml
(see mbconfig.py); a variable set in the shell overrides the file.
"""
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import mbconfig  # noqa: E402

MOVIES_DB = "movies.db"
WIKI_DB = "wiki.db"
SOURCES = ("retrieve.py", "chat.py")        # files whose environ.get(...) calls define the settings


# ------------------------------------------------------------------ small helpers

class Report:
    def __init__(self):
        self.fails = 0
        self.warns = 0

    def line(self, status, what, detail=""):
        mark = {"ok": "  ok  ", "warn": " WARN ", "fail": " FAIL ", "info": "  --  "}[status]
        if status == "fail":
            self.fails += 1
        elif status == "warn":
            self.warns += 1
        print(f"[{mark}] {what}" + (f": {detail}" if detail else ""))

    def section(self, name):
        print(f"\n{name}")


def env(name, default=""):
    return os.environ.get(name, default)


def on(name, default="1"):
    return env(name, default) not in ("0", "", "false", "False")


def connect(path, vec=True):
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    if vec:
        import sqlite_vec
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
    return conn


def table_sql(conn, name):
    row = conn.execute("SELECT sql FROM sqlite_master WHERE name = ?", (name,)).fetchone()
    return row[0] if row else None


def count(conn, sql, args=()):
    try:
        return conn.execute(sql, args).fetchone()[0]
    except sqlite3.Error:
        return None


def vec_dim(sql):
    m = re.search(r"float\[(\d+)\]", sql or "", re.I)
    return int(m.group(1)) if m else None


def model_names():
    import ollama
    res = ollama.list()
    models = res.get("models") if isinstance(res, dict) else getattr(res, "models", [])
    names = set()
    for m in models or []:
        n = m.get("model") or m.get("name") if isinstance(m, dict) else (getattr(m, "model", None)
                                                                            or getattr(m, "name", None))
        if n:
            names.add(n)
            if n.endswith(":latest"):
                names.add(n[:-len(":latest")])
    return names


# ------------------------------------------------------------------ settings

def known_settings():
    """{NAME: code default or None} for every environ.get("NAME", ...) in the pipeline code."""
    out = {}
    for f in SOURCES:
        p = os.path.join(HERE, f)
        if not os.path.exists(p):
            continue
        src = open(p, encoding="utf-8").read()
        for m in re.finditer(r'environ\.get\(\s*"([A-Z0-9_]+)"\s*(?:,\s*"([^"]*)")?', src):
            out.setdefault(m.group(1), m.group(2))
    return out


def cmd_settings(_args):
    shell_before = dict(os.environ)          # before apply(): what the shell itself set
    applied = mbconfig.apply()
    cfg_path = mbconfig.path_used or "(none: MB_CONFIG=none)"
    print(f"Config file: {cfg_path}" + ("" if not cfg_path or os.path.exists(cfg_path) else "  (missing)"))
    print(f"{'setting':22} {'value':34} from")
    names = known_settings()
    for k in sorted(set(names) | set(applied)):
        if k in applied:
            v, src = applied[k]
        elif k in shell_before:
            v, src = shell_before[k], "shell"
        else:
            v, src = names.get(k), "code default"
            v = {"QUERY_MODEL": env("ANSWER_MODEL", "movie-buff") + " (= ANSWER_MODEL)"}.get(
                k, "(computed in code)") if v is None else v
        flag = "  <- differs from config.yaml" if src == "shell" and k in applied and \
            mbconfig.load().get(k) not in (None, v) else ""
        print(f"{k:22} {str(v)[:34]:34} {src}{flag}")
    return 0


# ------------------------------------------------------------------ check

def cmd_check(args):
    quick = "--quick" in args          # skip the calls that load a model
    r = Report()
    os.chdir(HERE)

    r.section("Python")
    r.line("ok" if sys.version_info >= (3, 10) else "warn", f"Python {sys.version.split()[0]}",
           "" if sys.version_info >= (3, 10) else "tested on 3.12")
    if not os.environ.get("VIRTUAL_ENV"):
        r.line("warn", "no virtual environment active", "source ~/venvs/movie-rag/bin/activate")
    for mod, need in (("ollama", True), ("sqlite_vec", True), ("numpy", True),
                      ("fastapi", False), ("uvicorn", False)):
        try:
            __import__(mod)
            r.line("ok", f"import {mod}")
        except ImportError:
            r.line("fail" if need else "warn", f"import {mod}",
                   "pip install " + mod.replace("_", "-") + ("" if need else "   (only for the website)"))

    r.section("Settings")
    try:
        applied = mbconfig.apply()
        cfg = mbconfig.path_used
        if cfg and os.path.exists(cfg):
            shell = [k for k, (_, s) in applied.items() if s == "shell"]
            r.line("ok", f"config file {os.path.basename(cfg)}", f"{len(applied)} settings"
                   + (f"; overridden by the shell: {', '.join(shell)}" if shell else ""))
        else:
            r.line("warn", "no config file", "using code defaults (" + (cfg or "MB_CONFIG=none") + ")")
    except ValueError as e:
        r.line("fail", "config file", str(e))
    answer_model = env("ANSWER_MODEL", "movie-buff")
    query_model = env("QUERY_MODEL", answer_model)
    embed_model = env("EMBED_MODEL", "mxbai-embed-large")

    r.section("Ollama")
    have = None
    try:
        have = model_names()
        r.line("ok", "Ollama is running", f"{len(have)} models")
    except Exception as e:  # noqa: BLE001
        r.line("fail", "Ollama is not reachable", f"{e}  (start it: ollama serve)")
    if have is not None:
        want = {answer_model: "answers + re-ranker", query_model: "planner", embed_model: "embedder"}
        if on("PLAN_ROUTE", "0"):
            want.setdefault(env("ROUTE_MODEL", "llama3.2"), "route planner (PLAN_ROUTE=1)")
        for m, role in want.items():
            r.line("ok" if m in have else "fail", f"model {m}", role if m in have else
                   f"{role}; not pulled (ollama pull {m}, or ollama create for the persona model)")

    r.section("movies.db")
    mconn, mdim = None, None
    if not os.path.exists(MOVIES_DB):
        r.line("fail", "movies.db", f"not found in {HERE}; see `python mb.py build`")
    else:
        try:
            mconn = connect(MOVIES_DB)
            n = count(mconn, "SELECT count(*) FROM movies")
            r.line("ok" if n else "fail", "movies", f"{n:,} films" if n else "table missing or empty")
            for t, need in (("movie_vectors", True), ("movie_plot_vectors", True),
                            ("movies_fts", on("HYBRID"))):
                sql = table_sql(mconn, t)
                if not sql:
                    r.line("fail" if need else "info", t, "missing" if need else "missing (not used)")
                    continue
                c = count(mconn, f"SELECT count(*) FROM {t}")
                d = vec_dim(sql)
                if d:
                    mdim = mdim or d
                r.line("ok", t, f"{c:,} rows" + (f", {d} dims" if d else ""))
        except Exception as e:  # noqa: BLE001
            r.line("fail", "movies.db", str(e))

    r.section("wiki.db")
    wdim = None
    if not on("WIKI"):
        r.line("info", "WIKI=0", "Wikipedia search is off; wiki.db not needed")
    elif not os.path.exists(WIKI_DB):
        r.line("fail", "wiki.db", f"not found next to movies.db (WIKI=1); see `python mb.py build`")
    else:
        try:
            w = connect(WIKI_DB)
            for t in ("links", "pages", "chunks"):
                c = count(w, f"SELECT count(*) FROM {t}")
                r.line("ok" if c else "fail", t, f"{c:,} rows" if c else "missing or empty")
            kinds = dict(w.execute("SELECT kind, count(*) FROM vec_chunks GROUP BY kind").fetchall()) \
                if table_sql(w, "vec_chunks") else {}
            if not kinds:
                r.line("fail", "vec_chunks", "no passage vectors (wiki_embed.py)")
            for k in [x for x in env("WIKI_KINDS", "plot,lead").split(",") if x]:
                r.line("ok" if kinds.get(k) else "fail", f"vec_chunks[{k}]", f"{kinds.get(k, 0):,} vectors")
            if env("WIKI_Q", "rrf") not in ("", "0", "off"):
                q = kinds.get("question", 0)
                r.line("ok" if q else "warn", "vec_chunks[question]",
                       f"{q:,} generated descriptions" if q else "none (wiki_enrich.py, then wiki_embed.py --kinds question)")
            if on("WIKI_KW"):
                c = count(w, "SELECT count(*) FROM wiki_fts")
                r.line("ok" if c else "fail", "wiki_fts", f"{c:,} films" if c else "missing (python wiki_fts.py)")
            man = dict(w.execute("SELECT key, value FROM manifest").fetchall()) if table_sql(w, "manifest") else {}
            wdim = int(man["dim"]) if man.get("dim") else vec_dim(table_sql(w, "vec_chunks"))
            built = man.get("embedder")
            if built:
                r.line("ok" if built == embed_model else "fail", "passages embedded with",
                       built + ("" if built == embed_model else f", but EMBED_MODEL={embed_model}: "
                                "searches would compare different vector spaces"))
        except Exception as e:  # noqa: BLE001
            r.line("fail", "wiki.db", str(e))

    r.section("Embedder")
    if quick or have is None or embed_model not in (have or ()):
        r.line("info", "dimension check skipped", "--quick" if quick else "Ollama/embedder unavailable")
    else:
        try:
            import ollama
            t = time.time()
            d = len(ollama.embed(model=embed_model, input=["dimension probe"])["embeddings"][0])
            for name, want in (("movie vectors", mdim), ("passage vectors", wdim)):
                if want:
                    r.line("ok" if d == want else "fail", f"{embed_model} gives {d} dims",
                           f"{name} have {want}" + ("" if d == want else ": rebuild them or change EMBED_MODEL"))
            r.line("info", "embed call", f"{time.time() - t:.1f} s (includes loading the model)")
        except Exception as e:  # noqa: BLE001
            r.line("fail", "embedder call", str(e))

    r.section("Vector cache (VEC_MEM)")
    if not on("VEC_MEM"):
        r.line("info", "VEC_MEM=0", "searching with sqlite-vec")
    elif os.path.exists(MOVIES_DB):
        try:
            import vecmem
            t = time.time()
            s = vecmem.Store(MOVIES_DB)
            parts = [f"{tb} {len(ids):,}" for tb, (ids, *_rest) in s.tables.items()]
            if s.chunks is not None:
                parts.append(f"passages {len(s.chunks[0]):,}")
            ok = True
            if mconn is not None:
                for tb, (ids, *_rest) in s.tables.items():
                    c = count(mconn, f"SELECT count(*) FROM {tb}")
                    if c is not None and c != len(ids):
                        ok = False
                        r.line("warn", f"vec_cache {tb}", f"{len(ids):,} rows but movies.db has {c:,}")
            r.line("ok" if ok else "warn", "vec_cache is current",
                   ", ".join(parts) + f" (opened in {time.time() - t:.1f} s)")
            if on("WIKI") and s.chunks is None:
                r.line("warn", "vec_cache has no passages", "rebuild: python mb.py build-cache")
        except FileNotFoundError:
            r.line("warn", "vec_cache not built", "search still works through sqlite-vec, ~7x slower; "
                   "run: python mb.py build-cache")
        except Exception as e:  # noqa: BLE001
            r.line("warn", "vec_cache not usable", f"{e}; search falls back to sqlite-vec. "
                   "Run: python mb.py build-cache")

    r.section("GPU")
    if shutil.which("nvidia-smi"):
        try:
            out = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.used,memory.total",
                                  "--format=csv,noheader,nounits"], capture_output=True, text=True,
                                 timeout=20).stdout.strip()
            for ln in out.splitlines():
                name, used, total = [x.strip() for x in ln.split(",")]
                r.line("info", name, f"{int(used) / 1024:.1f} of {int(total) / 1024:.1f} GB in use "
                       "(the 27B persona model needs ~17 GB)")
        except Exception as e:  # noqa: BLE001
            r.line("info", "nvidia-smi", str(e))
    else:
        r.line("info", "no nvidia-smi", "CPU only: expect answers to take minutes, not seconds")

    r.section("Folders")
    os.makedirs("results", exist_ok=True)
    r.line("ok" if os.access("results", os.W_OK) else "fail", "results/ is writable")

    print(f"\n{r.fails} problem(s), {r.warns} warning(s).",
          "Ready." if not r.fails else "Fix the FAIL lines above first.")
    return 1 if r.fails else 0


# ------------------------------------------------------------------ build guide

STEPS = [
    # (what, done-test, command)
    ("TMDB films -> movies.db", ("movies.db", "SELECT count(*) FROM movies"),
     "python build_db.py                 # needs TMDB_movie_dataset_v*.csv (Kaggle) in this folder"),
    ("cast + director from the TMDB API", ("movies.db", "SELECT count(*) FROM movies WHERE credits_source IS NOT NULL"),
     "TMDB_API_KEY=... python fetch_credits.py"),
    ("film record vectors", ("movies.db", "SELECT count(*) FROM movie_vectors"),
     "python rebuild_embeddings.py       # (embed_movies.py on a database without credits)"),
    ("plot-only vectors", ("movies.db", "SELECT count(*) FROM movie_plot_vectors"),
     "python build_plot_vectors.py"),
    ("keyword index (TMDB)", ("movies.db", "SELECT count(*) FROM movies_fts"),
     "python build_fts.py"),
    ("link films to Wikipedia", ("wiki.db", "SELECT count(*) FROM links"),
     "python wiki_link.py --movies movies.db --corpus $CORPUS/corpus.jsonl.zip "
     "--imdb-map $CORPUS/wikipage_id_to_imdb.json.zip --out wiki.db"),
    ("Wikipedia passages", ("wiki.db", "SELECT count(*) FROM chunks WHERE kind IN ('lead','plot')"),
     "python wiki_chunks.py --movies movies.db --corpus $CORPUS/corpus.jsonl.zip --wiki-db wiki.db"),
    ("passage vectors", ("wiki.db", "SELECT count(*) FROM vec_chunks WHERE kind IN ('lead','plot')"),
     "python wiki_embed.py --wiki-db wiki.db              # ~40 min on the A10; resumable"),
    ("keyword index (Wikipedia)", ("wiki.db", "SELECT count(*) FROM wiki_fts"),
     "python wiki_fts.py"),
    ("generated descriptions", ("wiki.db", "SELECT count(*) FROM gen_questions"),
     "python wiki_enrich.py                              # ~6 h with llama3.2; resumable"),
    ("description vectors", ("wiki.db", "SELECT count(*) FROM vec_chunks WHERE kind = 'question'"),
     "python wiki_embed.py --wiki-db wiki.db --kinds question"),
    ("in-memory vector cache", None, "python mb.py build-cache                           # 1-3 min"),
]


def cmd_build(_args):
    os.chdir(HERE)
    print("Data build, in order. $CORPUS = the TREC ToT 2023 corpus folder "
          "(ours: ~/movie-buff/data/trec_tot).\nEvery step is safe to rerun; later steps need the earlier ones.\n")
    conns = {}
    for i, (what, test, cmd) in enumerate(STEPS, 1):
        status = "?"
        if test is None:
            try:
                import vecmem
                vecmem.Store(MOVIES_DB)
                status = "done"
            except Exception:  # noqa: BLE001
                status = "TO DO"
        else:
            db, sql = test
            if os.path.exists(db):
                if db not in conns:
                    try:
                        conns[db] = connect(db)
                    except Exception:  # noqa: BLE001
                        conns[db] = connect(db, vec=False)
                n = count(conns[db], sql)
                status = f"done ({n:,})" if n else "TO DO"
            else:
                status = "TO DO"
        print(f"{i:2}. {what:34} {status}\n      {cmd}")
    print("\nThen: python mb.py check")
    return 0


def cmd_build_cache(_args):
    os.chdir(HERE)
    import vecmem
    vecmem.build(MOVIES_DB)
    print()
    vecmem.check(MOVIES_DB)
    return 0


# ------------------------------------------------------------------ run

def cmd_ask(args):
    q = " ".join(a for a in args if not a.startswith("--")).strip()
    if not q:
        sys.exit('usage: python mb.py ask "that movie where ..."')
    os.chdir(HERE)
    import chat
    import retrieve as R
    t = time.time()
    prompt, hits = chat.build_prompt(q)
    print(f"[search: {time.time() - t:.1f} s, vectors via {R.vec_status()}]")
    for h in hits:
        d = h.get("distance")
        print(f"  {h.get('title')} ({str(h.get('release_year') or '')[:4]})"
              + (f"  distance {d:.3f}" if isinstance(d, (int, float)) else ""))
    chat.ask([{"role": "user", "content": prompt}], "--think" in args)
    return 0


def run_script(path, args):
    os.chdir(HERE)
    return subprocess.call([sys.executable, path, *args])


COMMANDS = {
    "check": cmd_check, "doctor": cmd_check,
    "settings": cmd_settings,
    "build": cmd_build,
    "build-cache": cmd_build_cache,
    "ask": cmd_ask,
    "chat": lambda a: run_script("chat.py", a),
    "serve": lambda a: run_script(os.path.join("web", "server.py"), a),
    "eval": lambda a: run_script("stage_scores.py", a),
}


def main(argv):
    if not argv or argv[0] not in COMMANDS:
        print(__doc__)
        return 0 if not argv or argv[0] in ("-h", "--help", "help") else 2
    return COMMANDS[argv[0]](argv[1:])


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
