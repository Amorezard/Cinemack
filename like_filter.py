#!/usr/bin/env python3
"""
v4 (14 Oct): semantic filter for liked answers (thumbs-up), before they can become training data.

feedback.py never lets a raw vote reach a model: until now a person had to `review ... accept`
every row. The nightly adapter rebuild (rebuild_adapter.sh) cannot wait for that, so this filter
decides automatically which thumbs-up answers are safe to learn from, and sends the doubtful ones
to a person. A thumbs-up says the reader was happy; it does not say the answer was right, grounded
or even about the question - so each liked answer is checked against its own question, its own
context and what the user did next.

  check            how                                                           failing it means
  no_prompt        the stored prompt (context + question) is missing (rows        reject (can't train
                   recorded before 14 Oct, or a cached replay)                     in the RAG format)
  error_text       empty, very short, or an error / "something went wrong" text  reject
  factcheck_flag   the site's rule-based fact check flagged the answer           reject
  off_topic        answer embedding far from the question (and from the planner's
                   rewritten question): below REL_MIN, or its own question not in
                   the top 20% of 50 other questions for this answer              reject
  ungrounded       share of answer sentences with a close context passage
                   (cosine >= SENT_MIN) below GROUND_MIN                           reject
  unsupported_numbers  money figures, percentages and years in the answer that
                   are nowhere in the context: 2+ reject, 1 review
  corrected_next   the user's NEXT message in that chat corrects it ("no, that's
                   not the one", "that's wrong") - embedding close to correction
                   examples, or a correction pattern                               reject
  duplicate        nearly the same question AND answer as one already accepted  reject
  contradicted     someone thumbed-down a nearly identical answer                review
  user_share       one browser would supply > 20% of the accepted examples       review
  llm_doubt        (--llm) llama3.2 says it does not answer the question or is
                   not supported by the context                                   review
Anything else -> auto_accept. A person's own review (feedback.py review) always wins:
adapter_data.py uses status 'accepted' rows, plus 'new' rows the filter auto-accepted.

    python like_filter.py run [--all] [--llm]     # screen thumbs-up rows not yet screened (--all: redo)
    python like_filter.py report                  # counts by verdict and reason
    python like_filter.py show <message_id>
    python like_filter.py calibrate [files...]    # thresholds from judged eval answers (eval_v3 level 2:
                                                  # judge pass vs fail) -> results/like_filter_calibration.json
    python like_filter.py sample 100              # results/like_label_sample.csv to hand-label

Embeddings: mxbai-embed-large (as everywhere), cached in feedback.db (like_vec), so a nightly run
only embeds new rows.
"""
import csv
import hashlib
import json
import os
import random
import re
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
if os.environ.get("MOCK", "off").lower() not in ("", "off", "0"):
    import mock_llm
    mock_llm.install()
import numpy as np  # noqa: E402
import ollama  # noqa: E402

import feedback as FB  # noqa: E402

VERSION = "lf1"
EMBED_MODEL = os.environ.get("EMBED_MODEL", "mxbai-embed-large")
CHECK_MODEL = os.environ.get("LIKE_CHECK_MODEL", "llama3.2")
QUERY_PREFIX = "Represent this sentence for searching relevant passages: "
CHATS_DB = os.environ.get("CHATS_DB", os.path.join(HERE, "logs", "chats.db"))
CALIB = os.path.join(HERE, "results", "like_filter_calibration.json")
# Defaults (14 Oct, before calibration). `calibrate` replaces them from judged answers.
TH = {"REL_MIN": 0.45, "REL_RANK": 0.20, "SENT_MIN": 0.62, "GROUND_MIN": 0.50, "CORR_SIM": 0.80,
      "DUP_Q": 0.95, "DUP_A": 0.95, "CONTRA_A": 0.90, "MAX_USER_SHARE": 0.20}
HARD = {"no_prompt", "error_text", "factcheck_flag", "off_topic", "ungrounded", "corrected_next", "duplicate",
        "unsupported_numbers"}
ERROR_RX = re.compile(r"something went wrong|couldn't (reach|load|open)|model (is )?offline|try again later|"
                      r"internal error|\[mock answer\]|traceback", re.I)
CORRECTION_RX = re.compile(r"^\s*(no+\b|nope\b|wrong\b|incorrect\b)|\bthat'?s (not|wrong|incorrect)\b|"
                           r"\bnot (that|the right|this) (one|movie|film)\b|\byou('re| are) wrong\b|"
                           r"\b(isn'?t|wasn'?t) (right|correct|it)\b|\bactually,? (it|the|that)\b|\bmade (that|it) up\b|"
                           r"\bthat (didn'?t|never) happen", re.I)
CORRECTIONS = ["No, that's not the movie I meant.", "That's wrong.", "No, that is incorrect.",
               "That's not right, try again.", "Wrong film.", "You made that up.",
               "That never happened in the movie.", "No, the one I'm thinking of is different.",
               "Not that one, the other film.", "That's not what I asked."]


def load_thresholds():
    th = dict(TH)
    if os.path.exists(CALIB):
        try:
            th.update(json.load(open(CALIB)).get("thresholds") or {})
        except ValueError:
            pass
    return th


# ------------------------------------------------------------------ embeddings (cached)
def _vconn():
    c = FB.conn()
    c.execute("create table if not exists like_vec(key text primary key, emb blob)")
    return c


def embed(texts, prefix=""):
    """Unit vectors for texts (cached by model + prefix + text)."""
    c = _vconn()
    keys = [hashlib.sha1(f"{EMBED_MODEL}|{prefix}|{t}".encode()).hexdigest() for t in texts]
    have = {}
    for i in range(0, len(keys), 500):
        part = keys[i:i + 500]
        for k, b in c.execute("select key, emb from like_vec where key in (%s)" % ",".join("?" * len(part)), part):
            have[k] = np.frombuffer(b, dtype=np.float32)
    todo = [(k, t) for k, t in zip(keys, texts) if k not in have]
    for i in range(0, len(todo), 32):
        part = todo[i:i + 32]
        vecs = ollama.embed(model=EMBED_MODEL, input=[prefix + t[:6000] for _k, t in part])["embeddings"]
        for (k, _t), v in zip(part, vecs):
            a = np.asarray(v, dtype=np.float32)
            a /= np.linalg.norm(a) + 1e-9
            have[k] = a
            c.execute("insert or replace into like_vec values (?, ?)", (k, a.tobytes()))
    c.commit()
    return np.stack([have[k] for k in keys]) if keys else np.zeros((0, 1024), dtype=np.float32)


# ------------------------------------------------------------------ text helpers
def context_of(prompt):
    """The evidence part of a prompt: everything before the final 'Question:' line."""
    p = prompt or ""
    i = p.rfind("\nQuestion:")
    return p[:i] if i > 0 else p


def passages(context):
    out = []
    for block in re.split(r"\n\s*\n|\n---\n|\n(?=\[)", context):
        for line in block.split("\n"):
            s = " ".join(line.split())
            if len(s.split()) >= 6:
                out.append(s[:700])
    return out[:120]


_SENT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(*])")


def sentences(answer):
    out = []
    for line in (answer or "").split("\n"):
        line = re.sub(r"^\s*([-*#>]+|\d+\.)\s*", "", line).replace("**", "").strip()
        for s in _SENT.split(line):
            if len(s.split()) >= 6:
                out.append(s[:400])
    return out[:40]


_NUM = re.compile(r"\$\s?\d[\d,.]*\s*(million|billion|m|bn|k)?|\b\d[\d,.]*\s?%|\b(1[89]\d\d|20\d\d)\b", re.I)


def _norm_num(s):
    s = s.lower().replace(",", "").replace(" ", "")
    m = re.match(r"\$?([\d.]+)(million|billion|m|bn|k)?%?", s)
    if not m:
        return s
    try:
        v = float(m.group(1))
    except ValueError:
        return s
    mult = {"million": 1e6, "m": 1e6, "billion": 1e9, "bn": 1e9, "k": 1e3}.get(m.group(2) or "", 1)
    return f"{v * mult:.3g}"


def unsupported_numbers(answer, context):
    ctx_nums = {_norm_num(m.group(0)) for m in _NUM.finditer(context)}
    ctx_flat = context.replace(",", "")
    bad = []
    for m in _NUM.finditer(answer or ""):
        raw = m.group(0)
        if _norm_num(raw) in ctx_nums or raw.replace(",", "").strip() in ctx_flat:
            continue
        bad.append(raw.strip())
    return sorted(set(bad))


def next_user_message(message_id):
    if not message_id or not os.path.exists(CHATS_DB):
        return None
    c = sqlite3.connect(f"file:{CHATS_DB}?mode=ro", uri=True)
    try:
        row = c.execute("select conv_id, seq from turns where message_id = ?", (message_id,)).fetchone()
        if not row:
            return None
        nxt = c.execute("select question from turns where conv_id = ? and seq > ? order by seq limit 1",
                        row).fetchone()
        return nxt[0] if nxt else None
    finally:
        c.close()


def llm_check(question, context, answer):
    schema = {"type": "object", "properties": {
        "answers_question": {"type": "boolean"}, "supported_by_context": {"type": "boolean"},
        "reason": {"type": "string"}}, "required": ["answers_question", "supported_by_context", "reason"]}
    try:
        r = ollama.chat(model=CHECK_MODEL, format=schema, keep_alive="10m",
                        options={"temperature": 0, "num_predict": 200, "num_ctx": 8192},
                        messages=[{"role": "system", "content":
                                   "You check a movie chatbot's answer. answers_question: does it answer the user's "
                                   "question? supported_by_context: is every film, name and number in the answer found "
                                   "in the CONTEXT? reason: one short sentence. JSON only."},
                                  {"role": "user", "content": f"CONTEXT:\n{context[:9000]}\n\nQUESTION: {question}"
                                                              f"\n\nANSWER:\n{answer[:3000]}"}])
        return json.loads(r["message"]["content"])
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)[:100]}


# ------------------------------------------------------------------ scoring
def score_rows(rows, th, others_q=None, use_llm=False):
    """{message_id: (status, [reasons], scores)} for liked rows (dicts from feedback.db)."""
    out = {}
    if not rows:
        return out, None, None
    qs = [r.get("contextualized_query") or r["question"] or "" for r in rows]
    q_vec = embed([r["question"] or "" for r in rows], QUERY_PREFIX)
    cq_vec = embed(qs, QUERY_PREFIX)
    a_vec = embed([r["answer"] or "" for r in rows])
    pool = list(others_q or [])
    pool_vec = embed(pool, QUERY_PREFIX) if len(pool) >= 10 else None
    corr_vec = embed(CORRECTIONS)
    for i, r in enumerate(rows):
        reasons, sc = [], {}
        ans, prompt = r.get("answer") or "", r.get("prompt") or ""
        if not prompt:
            reasons.append("no_prompt")
        if len(ans.strip()) < 20 or ERROR_RX.search(ans):
            reasons.append("error_text")
        try:
            chk = json.loads(r.get("check_json") or "null")
        except ValueError:
            chk = None
        if isinstance(chk, dict) and chk.get("ok") is False:
            reasons.append("factcheck_flag")
        rel = float(max(q_vec[i] @ a_vec[i], cq_vec[i] @ a_vec[i]))
        sc["relevance"] = round(rel, 3)
        if pool_vec is not None:
            sims = pool_vec @ a_vec[i]
            sc["rel_rank"] = round(float((sims > rel).mean()), 3)     # share of other questions closer
        if rel < th["REL_MIN"] or sc.get("rel_rank", 0) > th["REL_RANK"]:
            reasons.append("off_topic")
        if prompt:
            ctx = context_of(prompt)
            sents, pas = sentences(ans), passages(ctx)
            if sents and pas:
                sv, pv = embed(sents, QUERY_PREFIX), embed(pas)
                best = (sv @ pv.T).max(axis=1)
                share = float((best >= th["SENT_MIN"]).mean())
                sc["grounded_share"] = round(share, 3)
                if share < th["GROUND_MIN"]:
                    reasons.append("ungrounded")
            bad = unsupported_numbers(ans, ctx)
            sc["unsupported_numbers"] = bad[:8]
            if len(bad) >= 2:
                reasons.append("unsupported_numbers")
            elif len(bad) == 1:
                reasons.append("one_unsupported_number")
        nxt = next_user_message(r.get("message_id"))
        if nxt:
            nv = embed([nxt])[0]
            cs = float((corr_vec @ nv).max())
            sc["next_correction_sim"] = round(cs, 3)
            if CORRECTION_RX.search(nxt) or cs >= th["CORR_SIM"]:
                reasons.append("corrected_next")
                sc["next_message"] = nxt[:200]
        if use_llm and not (set(reasons) & HARD):
            v = llm_check(r["question"] or "", context_of(prompt), ans)
            sc["llm"] = v
            if v.get("answers_question") is False or v.get("supported_by_context") is False:
                reasons.append("llm_doubt")
        out[r["message_id"]] = [reasons, sc, i]
    return out, q_vec, a_vec


def run(redo=False, use_llm=False, quiet=False):
    """Screen thumbs-up rows. Returns {status: count}."""
    th = load_thresholds()
    c = FB.conn()
    where = "vote = 'up'" + ("" if redo else " and (auto_status is null or filter_version != ?)")
    args = () if redo else (VERSION,)
    c.row_factory = sqlite3.Row
    todo = [dict(r) for r in c.execute(f"select * from answers where {where} order by created_at", args)]
    accepted = [dict(r) for r in c.execute("select * from answers where vote = 'up' and "
                                           "(status = 'accepted' or (auto_status = 'auto_accept' and filter_version = ?)) "
                                           "order by created_at", (VERSION,))]
    downs = [dict(r) for r in c.execute("select * from answers where vote = 'down'")]
    others = [r[0] for r in c.execute("select question from answers where question is not null "
                                      "order by random() limit 50")]
    c.row_factory = None
    if not todo:
        if not quiet:
            print("no thumbs-up rows to screen")
        return {}
    res, q_vec, a_vec = score_rows(todo, th, others, use_llm)
    acc_ids = [r["message_id"] for r in accepted if r["message_id"] not in res]
    acc_q = embed([r["question"] or "" for r in accepted if r["message_id"] in acc_ids], QUERY_PREFIX)
    acc_a = embed([r["answer"] or "" for r in accepted if r["message_id"] in acc_ids])
    down_q = embed([d["question"] or "" for d in downs], QUERY_PREFIX) if downs else None
    down_a = embed([d["answer"] or "" for d in downs]) if downs else None
    users = {}
    for r in accepted:
        if r["message_id"] in acc_ids:
            users[r.get("user_id") or "-"] = users.get(r.get("user_id") or "-", 0) + 1
    total_acc = len(acc_ids)
    counts = {}
    kept_q, kept_a = list(acc_q), list(acc_a)
    for r in todo:
        reasons, sc, i = res[r["message_id"]]
        if kept_q:
            dq = np.stack(kept_q) @ q_vec[i]
            da = np.stack(kept_a) @ a_vec[i]
            if bool(((dq >= th["DUP_Q"]) & (da >= th["DUP_A"])).any()):
                reasons.append("duplicate")
        if down_q is not None:
            m = (down_q @ q_vec[i] >= th["DUP_Q"]) & (down_a @ a_vec[i] >= th["CONTRA_A"])
            if bool(m.any()):
                reasons.append("contradicted")
        if set(reasons) & HARD:
            status = "auto_reject"
        elif reasons:
            status = "needs_review"
        else:
            u = r.get("user_id") or "-"
            if total_acc >= 10 and (users.get(u, 0) + 1) / (total_acc + 1) > th["MAX_USER_SHARE"]:
                reasons.append("user_share")
                status = "needs_review"
            else:
                status = "auto_accept"
                users[u] = users.get(u, 0) + 1
                total_acc += 1
                kept_q.append(q_vec[i])
                kept_a.append(a_vec[i])
        counts[status] = counts.get(status, 0) + 1
        c.execute("update answers set auto_status = ?, auto_reasons = ?, auto_scores = ?, filter_version = ?, "
                  "filtered_at = ? where message_id = ?",
                  (status, ",".join(reasons), json.dumps(sc, default=str), VERSION, FB.now(), r["message_id"]))
    c.commit()
    if not quiet:
        print(f"screened {len(todo)} thumbs-up rows: " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())))
    return counts


def report():
    c = FB.conn()
    rows = c.execute("select auto_status, auto_reasons, status from answers where vote = 'up'").fetchall()
    if not rows:
        print("no thumbs-up rows")
        return
    by, reasons = {}, {}
    for st, rs, hs in rows:
        by[(st or "unscreened", hs)] = by.get((st or "unscreened", hs), 0) + 1
        for x in (rs or "").split(","):
            if x:
                reasons[x] = reasons.get(x, 0) + 1
    print(f"{len(rows)} thumbs-up rows")
    for (st, hs), n in sorted(by.items()):
        print(f"  filter {st:13s} person {hs:9s} {n}")
    print("  reasons: " + (", ".join(f"{k} {v}" for k, v in sorted(reasons.items(), key=lambda x: -x[1])) or "-"))


def show(mid):
    c = FB.conn()
    c.row_factory = sqlite3.Row
    r = c.execute("select * from answers where message_id = ?", (mid,)).fetchone()
    c.row_factory = None
    if not r:
        print("no such row")
        return
    r = dict(r)
    print(f"{mid}: vote {r['vote']}, person {r['status']}, filter {r.get('auto_status')} ({r.get('auto_reasons')})")
    print(f"Q: {r['question']}\nA: {(r['answer'] or '')[:600]}")
    print("scores:", r.get("auto_scores"))


# ------------------------------------------------------------------ calibration
def calibrate(files):
    """Pick thresholds on judged answers: eval_v3 level-2 rows carry the context the answer had,
    the answer and the judge's pass/fail. Liked answers should look like judge passes."""
    import glob
    rows = []
    for f in files or sorted(glob.glob(os.path.join(HERE, "results", "v3eval_*.json"))):
        try:
            d = json.load(open(f))
        except (OSError, ValueError):
            continue
        for x in d.get("rows", []):
            j = x.get("judge") or {}
            if x.get("answer") and x.get("context") and j.get("verdict") in ("pass", "fail"):
                rows.append({"message_id": f"{os.path.basename(f)}:{x['id']}", "question": x["question"],
                             "answer": x["answer"], "prompt": x["context"] + f"\n\nQuestion: {x['question']}",
                             "pass": j["verdict"] == "pass", "grounded": j.get("grounded")})
    if len(rows) < 20:
        raise SystemExit(f"only {len(rows)} judged answers found (need 20+); run eval_v3.py --answers first")
    th = dict(TH)
    th.update({"REL_MIN": -1, "REL_RANK": 2, "GROUND_MIN": -1})
    res, _q, _a = score_rows(rows, th, [r["question"] for r in rows[:50]])
    rel = np.array([res[r["message_id"]][1].get("relevance", 0) for r in rows])
    grd = np.array([res[r["message_id"]][1].get("grounded_share", 1) for r in rows])
    nums = np.array([len(res[r["message_id"]][1].get("unsupported_numbers") or []) for r in rows])
    ok = np.array([r["pass"] for r in rows])
    out = {"n": len(rows), "passes": int(ok.sum()), "fails": int((~ok).sum()), "thresholds": {}, "detail": {}}
    # each threshold: the highest value that still keeps >= 90% of judge passes
    for name, vals in (("REL_MIN", rel), ("GROUND_MIN", grd)):
        cut = float(np.quantile(vals[ok], 0.10)) if ok.any() else TH[name]
        out["thresholds"][name] = round(cut, 3)
        out["detail"][name] = {"pass_kept": float((vals[ok] >= cut).mean()) if ok.any() else None,
                               "fail_removed": float((vals[~ok] < cut).mean()) if (~ok).any() else None}
    out["detail"]["unsupported_numbers>=2"] = {
        "pass_flagged": float((nums[ok] >= 2).mean()) if ok.any() else None,
        "fail_flagged": float((nums[~ok] >= 2).mean()) if (~ok).any() else None}
    os.makedirs(os.path.dirname(CALIB), exist_ok=True)
    out["at"] = FB.now()
    json.dump(out, open(CALIB, "w"), indent=1)
    print(json.dumps(out, indent=1))
    print(f"-> {CALIB} (like_filter.py run uses these thresholds from now on)")


def sample(n):
    c = FB.conn()
    rows = c.execute("select message_id, auto_status, auto_reasons, question, answer from answers "
                     "where vote = 'up' and auto_status is not null").fetchall()
    random.Random(3).shuffle(rows)
    path = os.path.join(HERE, "results", "like_label_sample.csv")
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["message_id", "filter", "reasons", "question", "answer", "your_label (good/bad)"])
        for r in rows[:n]:
            w.writerow(list(r[:3]) + [r[3], (r[4] or "")[:1500], ""])
    print(f"{min(n, len(rows))} rows -> {path}")


if __name__ == "__main__":
    a = sys.argv[1:]
    cmd = a[0] if a else ""
    if cmd == "run":
        run(redo="--all" in a, use_llm="--llm" in a or os.environ.get("LIKE_LLM") == "1")
    elif cmd == "report":
        report()
    elif cmd == "show" and len(a) > 1:
        show(a[1])
    elif cmd == "calibrate":
        calibrate(a[1:])
    elif cmd == "sample":
        sample(int(a[1]) if len(a) > 1 else 100)
    else:
        print(__doc__)
