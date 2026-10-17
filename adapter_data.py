#!/usr/bin/env python3
"""
v4 (14 Oct): data, bookkeeping and the promotion test for the nightly adapter rebuild
(rebuild_adapter.sh runs these in order; each also works by hand).

    python adapter_data.py build --out builds/a20261015 [--force]
          training set for one rebuild -> <out>/train.jsonl + <out>/manifest.json.
          Exit code 3 = "not enough new data, skip tonight" (fewer than ADAPTER_MIN_NEW new
          examples since the last build that trained), unless --force.
    python adapter_data.py distill --n 300
          results/distill_questions.json: questions in the RAG format about films that are in NO
          evaluation set (templated by category). rebuild_adapter.sh answers them with the live
          model and keeps only answers the judge passes with high scores (rejection-sampling
          self-training), so the first rebuild has RAG-format examples beyond the few likes.
    python adapter_data.py modelfile --lora <lora.gguf> --out <Modelfile>
          the live model's Modelfile with the new ADAPTER line (FROM / TEMPLATE / PARAMETER / SYSTEM kept).
    python adapter_data.py gate --cand <tag> --live <tag>
          promote or reject by the rule below; exit 0 = promote, 1 = reject.
    python adapter_data.py register|status|list|prune|promote|rollback ...

Where examples come from (each tagged with its source; all in Ollama chat-message form):
    persona   ADAPTER_PERSONA (the original persona examples; kept in every build so the voice stays)
    written   train/rag_examples.jsonl (hand-written ideal RAG answers, if any)
    liked     thumbs-up answers that a person accepted OR like_filter.py auto-accepted, with the full
              prompt they were answered from (rows without a stored prompt are never used)
    corrected accepted thumbs-down rows with a reviewer's "better: ..." answer
    distill   live-model answers to distill questions the judge passed (score >= DISTILL_MIN_SCORE)
Old entries: liked / corrected / distill examples older than ADAPTER_MAX_AGE_DAYS (90) are left
out of every new build ("expired"); persona and written examples never expire.
Leakage guard: an example is dropped if its question matches any question in sets/*.json
(analytic, multiturn, sealed, tune, check, final, train ...) or names a film from the gate set.

Promotion rule (written 14 Oct, before any build): the candidate replaces the live model only if
on sets/analytic.json (answers + judge) judge passes >= live, mean grounding >= live - 0.05,
and on sets/check.json (retrieval: the model is also the planner and re-ranker) passes >= live - 1.
"""
import argparse
import datetime
import glob
import hashlib
import json
import os
import random
import re
import sqlite3
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
REG = os.environ.get("ADAPTER_DB", os.path.join(HERE, "logs", "adapters.db"))
MAX_AGE = int(os.environ.get("ADAPTER_MAX_AGE_DAYS", "90"))
MIN_NEW = int(os.environ.get("ADAPTER_MIN_NEW", "50"))
KEEP = int(os.environ.get("ADAPTER_KEEP", "3"))
PERSONA = os.environ.get("ADAPTER_PERSONA", os.path.expanduser("~/movie-buff/movie_training_data.jsonl"))
WRITTEN = os.path.join(HERE, "train", "rag_examples.jsonl")
DISTILL_MIN_SCORE = int(os.environ.get("DISTILL_MIN_SCORE", "29"))    # of 35 (7 judge scores x 5)
GATE_SET = os.path.join(HERE, "sets", "analytic.json")
ORIGINAL = os.environ.get("ADAPTER_ORIGINAL", "movie-buff")          # never removed


def today():
    return datetime.date.today().isoformat()


def now():
    return datetime.datetime.now().isoformat(timespec="seconds")


# ------------------------------------------------------------------ registry
def reg():
    os.makedirs(os.path.dirname(REG), exist_ok=True)
    c = sqlite3.connect(REG, timeout=60)
    c.executescript("""
    create table if not exists builds(
      name text primary key, created_at text, status text, dir text, n_examples integer,
      n_new integer, sources_json text, gate_json text, note text, updated_at text);
    create table if not exists used(example_id text primary key, first_build text, source text);""")
    return c


def live_model():
    """The answer model in effect (shell > live_model.yaml > config.yaml > movie-buff)."""
    import mbconfig
    mbconfig.apply()
    return os.environ.get("ANSWER_MODEL", "movie-buff")


# ------------------------------------------------------------------ normalising examples
def _norm_q(s):
    return re.sub(r"[^a-z0-9 ]+", "", str(s or "").casefold()).strip()


def to_messages(x):
    """Ollama/OpenAI messages from the formats training files commonly use, or None."""
    if isinstance(x.get("messages"), list):
        msgs = [{"role": m.get("role"), "content": str(m.get("content") or "")} for m in x["messages"]
                if m.get("role") in ("system", "user", "assistant")]
    elif isinstance(x.get("conversations"), list):            # ShareGPT
        role = {"human": "user", "user": "user", "gpt": "assistant", "assistant": "assistant", "system": "system"}
        msgs = [{"role": role.get(m.get("from")), "content": str(m.get("value") or "")} for m in x["conversations"]
                if role.get(m.get("from"))]
    elif x.get("instruction") is not None and x.get("output") is not None:      # Alpaca
        u = x["instruction"] + (("\n\n" + x["input"]) if x.get("input") else "")
        msgs = [{"role": "user", "content": u}, {"role": "assistant", "content": x["output"]}]
    elif x.get("prompt") is not None and (x.get("response") or x.get("completion")) is not None:
        msgs = [{"role": "user", "content": x["prompt"]},
                {"role": "assistant", "content": x.get("response") or x.get("completion")}]
    else:
        return None
    if len(msgs) < 2 or msgs[-1]["role"] != "assistant" or not msgs[-1]["content"].strip():
        return None
    return msgs


def _question_of(msgs):
    u = next((m["content"] for m in reversed(msgs) if m["role"] == "user"), "")
    m = re.search(r"\nQuestion: (.+?)\n", u + "\n")
    return m.group(1) if m else u[-300:]


def _id(source, msgs):
    return source + ":" + hashlib.sha1(json.dumps(msgs, sort_keys=True).encode()).hexdigest()[:16]


def read_jsonl(path, source):
    out = []
    if not path or not os.path.exists(path):
        return out
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                msgs = to_messages(json.loads(line))
            except ValueError:
                continue
            if msgs:
                out.append({"id": _id(source, msgs), "source": source, "messages": msgs, "created_at": None})
    return out


# ------------------------------------------------------------------ leakage guard
def eval_questions():
    """Normalised questions of every evaluation set, and the films the gate set names."""
    qs, films = set(), set()
    for p in glob.glob(os.path.join(HERE, "sets", "*.json")):
        try:
            d = json.load(open(p))
        except (OSError, ValueError):
            continue
        items = d if isinstance(d, list) else d.get("questions") or d.get("conversations") or []
        for q in items:
            if not isinstance(q, dict):
                continue
            for t in [q.get("question")] + list(q.get("turns") or []):
                if isinstance(t, dict):
                    t = t.get("user") or t.get("content") or t.get("text")
                if t:
                    qs.add(_norm_q(t))
            if os.path.abspath(p) == os.path.abspath(GATE_SET):
                for f in (q.get("expect_films") or []):
                    films.add(_norm_q(f))
    return qs, films


def leaks(ex, qs, films):
    q = _norm_q(_question_of(ex["messages"]))
    if q in qs:
        return True
    return any(len(f) > 3 and re.search(rf"\b{re.escape(f)}\b", q) for f in films)


# ------------------------------------------------------------------ sources
def liked_examples():
    import feedback as FB
    c = FB.conn()
    c.row_factory = sqlite3.Row
    rows = [dict(r) for r in c.execute(
        "select * from answers where prompt is not null and prompt != '' and ("
        " (vote = 'up' and (status = 'accepted' or (status = 'new' and auto_status = 'auto_accept')))"
        " or (vote = 'down' and status = 'accepted' and lower(coalesce(reviewer_note, '')) like 'better:%'))")]
    c.row_factory = None
    out = []
    for r in rows:
        msgs = FB.messages_of(r)
        if r["vote"] == "up":
            ans, src = r["answer"], "liked"
        else:
            ans, src = r["reviewer_note"].strip()[7:].strip(), "corrected"
        if not (ans or "").strip():
            continue
        msgs = msgs + [{"role": "assistant", "content": ans}]
        out.append({"id": f"{src}:{r['message_id']}", "source": src, "messages": msgs,
                    "created_at": r.get("created_at")})
    return out


def distill_examples():
    out = []
    for f in sorted(glob.glob(os.path.join(HERE, "results", "v3eval_distill_*.json"))):
        try:
            d = json.load(open(f))
        except (OSError, ValueError):
            continue
        made = datetime.datetime.fromtimestamp(os.path.getmtime(f)).isoformat(timespec="seconds")
        for r in d.get("rows", []):
            j = r.get("judge") or {}
            if j.get("verdict") != "pass" or not r.get("answer"):
                continue
            score = sum(j.get(k) or 0 for k in ("grounded", "direct", "specific", "honest", "concise",
                                                  "sources", "inference") if isinstance(j.get(k), (int, float)))
            if score < DISTILL_MIN_SCORE or (j.get("grounded") or 0) < 5 or j.get("unsupported_claims"):
                continue
            prompt = r.get("prompt") or (r["context"] + f"\n\nQuestion: {r['question']}")
            msgs = [{"role": "user", "content": prompt}, {"role": "assistant", "content": r["answer"]}]
            out.append({"id": f"distill:{os.path.basename(f)}:{r['id']}", "source": "distill",
                        "messages": msgs, "created_at": made, "score": score})
    return out


def cmd_build(a):
    os.makedirs(a.out, exist_ok=True)
    qs, films = eval_questions()
    cutoff = (datetime.datetime.now() - datetime.timedelta(days=MAX_AGE)).isoformat()
    persona = read_jsonl(PERSONA, "persona")
    written = read_jsonl(WRITTEN, "written")
    dated = liked_examples() + distill_examples()
    stats = {"persona": len(persona), "written": len(written)}
    kept, seen, dropped = [], set(), {"expired": 0, "leak": 0, "duplicate": 0}
    for ex in persona + written + dated:
        if ex.get("created_at") and ex["created_at"][:19] < cutoff[:19]:
            dropped["expired"] += 1
            continue
        if ex["source"] not in ("persona",) and leaks(ex, qs, films):
            dropped["leak"] += 1
            continue
        key = (_norm_q(_question_of(ex["messages"])), _norm_q(ex["messages"][-1]["content"])[:300])
        if key in seen:
            dropped["duplicate"] += 1
            continue
        seen.add(key)
        kept.append(ex)
    for ex in kept:
        stats[ex["source"]] = stats.get(ex["source"], 0) + (0 if ex["source"] in ("persona", "written") else 1)
    c = reg()
    used = {r[0] for r in c.execute("select example_id from used")}
    new = [ex for ex in kept if ex["source"] not in ("persona", "written") and ex["id"] not in used]
    if not persona:
        print(f"!! no persona examples at {PERSONA}: the rebuilt adapter would lose the Movie Buff voice. "
              f"Set ADAPTER_PERSONA in adapter.env (default ~/movie-buff/movie_training_data.jsonl).")
    # Persona examples repeated so they stay >= 15% of the set (the voice must not wash out).
    n_other = len(kept) - len(persona)
    reps = max(1, -(-int(0.15 * n_other) // max(len(persona), 1))) if persona else 0
    train = [ex for ex in kept if ex["source"] != "persona"] + [ex for ex in persona for _ in range(reps)]
    random.Random(len(train)).shuffle(train)
    # Ollama adds the Modelfile's SYSTEM prompt to every real answer, so examples without one get it too
    # (the persona examples carry their own).
    system = modelfile_system()
    if system:
        for ex in train:
            if ex["messages"][0]["role"] != "system":
                ex["messages"] = [{"role": "system", "content": system}] + ex["messages"]
    with open(os.path.join(a.out, "train.jsonl"), "w", encoding="utf-8") as f:
        for ex in train:
            f.write(json.dumps({"messages": ex["messages"], "source": ex["source"], "id": ex["id"]},
                               ensure_ascii=False) + "\n")
    man = {"created_at": now(), "n_train_rows": len(train), "n_unique": len(kept), "n_new": len(new),
           "by_source": stats, "persona_repeats": reps, "dropped": dropped, "max_age_days": MAX_AGE,
           "min_new": MIN_NEW, "new_ids": [ex["id"] for ex in new]}
    json.dump(man, open(os.path.join(a.out, "manifest.json"), "w"), indent=1)
    print(f"examples: {len(kept)} unique ({', '.join(f'{k} {v}' for k, v in stats.items())}); "
          f"{len(new)} new since the last trained build; dropped {dropped}; persona x{reps} -> {len(train)} rows")
    if len(new) < MIN_NEW and not a.force:
        print(f"SKIP: {len(new)} new examples < ADAPTER_MIN_NEW={MIN_NEW} (use --force to train anyway)")
        sys.exit(3)
    if not persona and not a.force:
        print("SKIP: no persona examples (use --force to train without them)")
        sys.exit(3)


# ------------------------------------------------------------------ distill questions
TEMPLATES = {
    "factual": ["Who directed {t}?", "When did {t} come out, and how long is it?", "Who starred in {t}?"],
    "money": ["How much did {t} make at the box office compared with its budget?", "Was {t} profitable?"],
    "why": ["Why did {t} do well?", "What made {t} succeed or struggle at the box office?"],
    "reviews": ["What did critics think of {t}?", "How was {t} received by audiences?"],
    "detail": ["How was {t} made?", "What is {t} about?"],
    "compare": ["Compare {t} and {u}: budgets, grosses and reviews."],
    "awards": ["What awards did {t} win?"],
}
NOTES = {"factual": "Answers the fact in the first sentence from the context.",
         "money": "Gives budget and gross with their source and says whether it beat ~2.5x.",
         "why": "Keeps evidence and interpretation apart; numbers name their source; says what is uncertain.",
         "reviews": "Keeps critics and audiences apart with counts and a short quote from the context.",
         "detail": "Uses concrete details from the context; says so if the context lacks them.",
         "compare": "Puts both films side by side using only the context.",
         "awards": "Lists awards from the context; says so if there are none."}


def cmd_distill(a):
    import facts_db as F
    qs, gate_films = eval_questions()
    c = F.connect(readonly=True)
    if c is None:
        raise SystemExit("facts.db not found")
    films = [(t, y) for t, y in c.execute(
        "select f.title, f.year from films f join money m on m.tmdb_id = f.tmdb_id "
        "where f.vote_count >= 200 and m.gross > 0 order by random() limit ?", (a.n * 3,))]
    # films named anywhere in the evaluation sets are left out
    all_text = " ".join(qs)
    films = [(t, y) for t, y in films if _norm_q(t) not in gate_films and
             not re.search(rf"\b{re.escape(_norm_q(t))}\b", all_text)]
    rnd = random.Random(a.seed)
    out, cats = [], list(TEMPLATES)
    for i in range(min(a.n, len(films))):
        cat = cats[i % len(cats)]
        t, y = films[i]
        u = films[(i + 7) % len(films)][0]
        q = rnd.choice(TEMPLATES[cat]).format(t=t, u=u)
        out.append({"id": f"d{i:04d}", "category": cat, "question": q, "notes": NOTES[cat]})
    os.makedirs(os.path.join(HERE, "results"), exist_ok=True)
    path = os.path.join(HERE, "results", "distill_questions.json")
    json.dump({"about": f"adapter_data.py distill {now()}: training questions, NOT an evaluation set",
               "questions": out}, open(path, "w"), indent=1)
    print(f"{len(out)} questions -> {path}")


# ------------------------------------------------------------------ Modelfile
MODELFILE = os.environ.get("ADAPTER_MODELFILE", os.path.expanduser("~/movie-buff/Modelfile"))


def _template_modelfile(source=None):
    """The Modelfile text new adapters copy: ADAPTER_MODELFILE (the hand-written ~/movie-buff/Modelfile:
    FROM qwen3.8:27b-mtp-q4_K_M by name, PARAMETERs, SYSTEM) when it exists, else `ollama show --modelfile`
    of the original model (which spells the base out as blob paths, sometimes as several FROM lines)."""
    if not source and MODELFILE and os.path.exists(MODELFILE):
        return open(MODELFILE, encoding="utf-8").read(), MODELFILE
    src = source or ORIGINAL
    try:
        return subprocess.run(["ollama", "show", src, "--modelfile"], capture_output=True, text=True,
                              timeout=60, check=True).stdout, f"ollama show {src}"
    except (OSError, subprocess.SubprocessError) as e:
        raise SystemExit(f"ollama show {src} --modelfile failed: {e}")


def modelfile_system():
    """The SYSTEM prompt Ollama adds to every real answer (so training examples carry it too)."""
    try:
        text, _src = _template_modelfile()
    except SystemExit:
        return ""
    m = re.search(r'^SYSTEM\s+"""(.*?)"""', text, re.S | re.M) or re.search(r"^SYSTEM\s+(.+)$", text, re.M)
    return m.group(1).strip() if m else ""


def cmd_modelfile(a):
    """The original Modelfile (FROM, TEMPLATE, PARAMETER, SYSTEM) with the new adapter in place of the old
    one: the rebuilt LoRA is trained from the base model, persona examples included, so it replaces the
    old adapter rather than stacking on it. ONE ADAPTER line, after the first FROM."""
    text, src = _template_modelfile(a.source)
    lines = text.splitlines()
    has_adapter = any(l.startswith("ADAPTER ") for l in lines)
    out, placed = [], False
    for l in lines:
        if l.startswith("ADAPTER "):
            continue
        if l.startswith("FROM ") and not has_adapter and not placed:
            base = os.environ.get("ADAPTER_BASE_GGUF", "")
            if not base:
                raise SystemExit(f"{src} has no ADAPTER line (the LoRA was merged into the weights), so the new "
                                 f"adapter needs the plain base model: set ADAPTER_BASE_GGUF.")
            l = f"FROM {base}"
        out.append(l)
        if l.startswith("FROM ") and not placed:
            out.append(f"ADAPTER {os.path.abspath(a.lora)}")
            placed = True
    open(a.out, "w").write("\n".join(out) + "\n")
    print(f"Modelfile from {src} ({'adapter replaced' if has_adapter else 'base + new adapter'}) -> {a.out}")


# ------------------------------------------------------------------ gate
def _an(tag):
    p = os.path.join(HERE, "results", f"v3eval_{tag}.json")
    d = json.load(open(p))
    js = [r.get("judge") for r in d["rows"] if r.get("judge")]
    g = [j.get("grounded") for j in js if isinstance(j.get("grounded"), (int, float)) and j.get("grounded") > 0]
    return {"n": len(d["rows"]), "judged": len(js), "pass": sum(1 for j in js if j.get("verdict") == "pass"),
            "grounded": round(sum(g) / len(g), 3) if g else 0.0}


def _check(tag):
    ps = sorted(glob.glob(os.path.join(HERE, "results", f"stage_scores_*_check_{tag}.json")), key=os.path.getmtime)
    if not ps:
        raise FileNotFoundError(f"no stage_scores ..._check_{tag}.json")
    d = json.load(open(ps[-1]))
    return {"n": len(d["rows"]), "pass": sum(1 for r in d["rows"] if r["result"]["pass"])}


def cmd_gate(a):
    cand, live = {"analytic": _an(a.cand)}, {"analytic": _an(a.live)}
    try:
        cand["check"], live["check"] = _check(a.cand), _check(a.live)
    except FileNotFoundError as e:
        cand["check"] = live["check"] = None
        print(f"(no retrieval check: {e})")
    ca, la = cand["analytic"], live["analytic"]
    rules = {"judge passes >= live": ca["pass"] >= la["pass"],
             "grounding >= live - 0.05": ca["grounded"] >= la["grounded"] - 0.05,
             "every answer judged": ca["judged"] >= la["judged"]}
    if cand["check"]:
        rules["retrieval check >= live - 1"] = cand["check"]["pass"] >= live["check"]["pass"] - 1
    ok = all(rules.values())
    res = {"candidate": cand, "live": live, "rules": rules, "promote": ok, "at": now()}
    print(json.dumps(res, indent=1))
    if a.name:
        c = reg()
        c.execute("update builds set gate_json = ?, status = ?, updated_at = ? where name = ?",
                  (json.dumps(res), "passed" if ok else "rejected", now(), a.name))
        c.commit()
    print("PROMOTE" if ok else "REJECT")
    sys.exit(0 if ok else 1)


# ------------------------------------------------------------------ registry commands
def cmd_register(a):
    c = reg()
    man = json.load(open(os.path.join(a.dir, "manifest.json"))) if os.path.exists(os.path.join(a.dir, "manifest.json")) else {}
    c.execute("insert or replace into builds values (?,?,?,?,?,?,?,?,?,?)",
              (a.name, now(), a.status, os.path.abspath(a.dir), man.get("n_unique"), man.get("n_new"),
               json.dumps(man.get("by_source")), None, a.note or "", now()))
    if a.status == "trained":
        c.executemany("insert or ignore into used values (?,?,?)",
                      [(i, a.name, i.split(":")[0]) for i in man.get("new_ids") or []])
    c.commit()
    print(f"registered {a.name}: {a.status}")


def cmd_setstatus(a):
    c = reg()
    c.execute("update builds set status = ?, note = coalesce(?, note), updated_at = ? where name = ?",
              (a.status, a.note, now(), a.name))
    c.commit()


def cmd_list(_a):
    c = reg()
    live = live_model()
    for name, created, status, n, nn, src, gate in c.execute(
            "select name, created_at, status, n_examples, n_new, sources_json, gate_json from builds order by created_at"):
        g = json.loads(gate) if gate else {}
        ga = (g.get("candidate") or {}).get("analytic") or {}
        print(f"{name:24s} {created[:16]} {status:10s} examples {n or 0:5d} new {nn or 0:4d}"
              f"{'  judge ' + str(ga.get('pass')) + '/' + str(ga.get('judged')) if ga else ''}"
              f"{'   <- LIVE' if name == live else ''}")
    print(f"live answer model: {live}")


def cmd_status(_a):
    c = reg()
    last = c.execute("select name, status, created_at from builds order by created_at desc limit 1").fetchone()
    print(f"live: {live_model()}; last build: {last or '-'}")


def cmd_prune(a):
    """Keep the newest KEEP promoted/passed adapters (+ the live one + the original); remove the
    rest from Ollama and delete their build folders. Prints what it removed."""
    import shutil
    c = reg()
    live = live_model()
    rows = c.execute("select name, status, dir from builds where status != 'removed' "
                     "order by created_at desc, name desc").fetchall()
    keep, removed = set(), []
    good = [r for r in rows if r[1] in ("live", "passed", "retired")]
    for r in good[:a.keep]:
        keep.add(r[0])
    keep |= {live, ORIGINAL}
    for name, status, d in rows:
        if name in keep:
            continue
        if not a.dry:
            subprocess.run(["ollama", "rm", name], capture_output=True, timeout=120)
            if d and os.path.isdir(d) and os.path.abspath(d).startswith(os.path.join(HERE, "builds")):
                shutil.rmtree(d, ignore_errors=True)
            c.execute("update builds set status = 'removed', updated_at = ? where name = ?", (now(), name))
        removed.append(f"{name} ({status})")
    # old distill result files and the like-filter's embedding cache for rows that no longer exist
    cutoff = datetime.datetime.now().timestamp() - MAX_AGE * 86400
    for f in glob.glob(os.path.join(HERE, "results", "v3eval_distill_*.json")) + \
            glob.glob(os.path.join(HERE, "results", "v3eval_gate_*.json")) + \
            glob.glob(os.path.join(HERE, "results", "stage_scores_*_check_gate_*.json")):
        if os.path.getmtime(f) < cutoff and not a.dry:
            os.remove(f)
            removed.append(os.path.basename(f))
    c.commit()
    print(f"kept: {sorted(keep)}; removed: {removed or 'nothing'}")


def cmd_promote(a):
    """Make <name> the live answer model (live_model.yaml) and mark the old live one retired."""
    c = reg()
    old = live_model()
    path = os.path.join(HERE, "live_model.yaml")
    with open(path, "w") as f:
        f.write(f"# written by adapter_data.py promote {now()} (rebuild_adapter.sh); previous: {old}\n"
                f"ANSWER_MODEL: {a.name}\n")
    c.execute("update builds set status = 'retired', updated_at = ? where status = 'live'", (now(),))
    c.execute("update builds set status = 'live', updated_at = ? where name = ?", (now(), a.name))
    c.commit()
    print(f"live answer model: {old} -> {a.name} ({path}). Restart the website to use it.")


def cmd_rollback(_a):
    c = reg()
    path = os.path.join(HERE, "live_model.yaml")
    prev = c.execute("select name from builds where status = 'retired' order by updated_at desc limit 1").fetchone()
    cur = c.execute("select name from builds where status = 'live'").fetchone()
    if cur:
        c.execute("update builds set status = 'rolled_back', updated_at = ? where name = ?", (now(), cur[0]))
    if prev:
        with open(path, "w") as f:
            f.write(f"# rollback {now()}\nANSWER_MODEL: {prev[0]}\n")
        c.execute("update builds set status = 'live', updated_at = ? where name = ?", (now(), prev[0]))
        print(f"live answer model -> {prev[0]}")
    else:
        if os.path.exists(path):
            os.remove(path)
        print("live_model.yaml removed: config.yaml's ANSWER_MODEL is live again")
    c.commit()


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("build"); p.add_argument("--out", required=True); p.add_argument("--force", action="store_true")
    p = sub.add_parser("distill"); p.add_argument("--n", type=int, default=300); p.add_argument("--seed", type=int, default=0)
    p = sub.add_parser("modelfile"); p.add_argument("--lora", required=True); p.add_argument("--out", required=True)
    p.add_argument("--source")
    p = sub.add_parser("gate"); p.add_argument("--cand", required=True); p.add_argument("--live", required=True)
    p.add_argument("--name")
    p = sub.add_parser("register"); p.add_argument("name"); p.add_argument("--dir", required=True)
    p.add_argument("--status", default="trained"); p.add_argument("--note")
    p = sub.add_parser("setstatus"); p.add_argument("name"); p.add_argument("status"); p.add_argument("--note")
    sub.add_parser("list"); sub.add_parser("status")
    p = sub.add_parser("prune"); p.add_argument("--keep", type=int, default=KEEP); p.add_argument("--dry", action="store_true")
    p = sub.add_parser("promote"); p.add_argument("name")
    sub.add_parser("rollback")
    a = ap.parse_args()
    {"build": cmd_build, "distill": cmd_distill, "modelfile": cmd_modelfile, "gate": cmd_gate,
     "register": cmd_register, "setstatus": cmd_setstatus, "list": cmd_list, "status": cmd_status,
     "prune": cmd_prune, "promote": cmd_promote, "rollback": cmd_rollback}[a.cmd](a)


if __name__ == "__main__":
    main()
