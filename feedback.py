"""
v3 answer feedback: stored with everything needed to learn from it later, and never fed
straight into training.

    thumbs up / down (+ optional reasons, comment)  ─►  logs/feedback.db  answers table
                                                         (question, rewritten query, films,
                                                          per-stage scores and timings, tool route,
                                                          final context size, answer, versions)
    python feedback.py list [--down] [--new]        what came in
    python feedback.py review <id> accept|reject [note]   a person decides what it is worth
    python feedback.py list --auto                  with the semantic filter's verdict (like_filter.py)
    python feedback.py stats                        dislike rate by reason, route, version, intent
    python feedback.py export                       ONLY accepted rows -> results/feedback_curated.jsonl
    python feedback.py pairs [v3eval files ...]     training data (6 Oct), from reviewed votes + judged answers:
        results/preference_pairs.jsonl   {"prompt","chosen","rejected", ...}  (DPO / TRL format)
        results/sft_examples.jsonl       {"messages": [user, assistant], ...}  (LoRA retrain format)
    A reviewer can attach a corrected answer: review <id> accept better: <the answer it should have given>

The loop the brief asks for (section 15), in order:
  1. evaluation    which question types and routes get thumbs-down (stats)
  2. retrieval     a reviewed dislike whose right film was not retrieved becomes a test question
  3. re-ranker     reviewed cases where the right film was in rerank_in but not picked
  4. prompts       compare dislike rates across prompt_version
  5. training      only after review: preference pairs (DPO), reranker or embedder data
Raw votes never reach a model directly: one person (or one bot) clicking thumbs-up 200 times
must not change behaviour. Rows are capped per user per day and deduplicated per message.
Film likes ("I liked this film") are a different signal and live in personal.py.
"""
import datetime
import json
import os
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PATH = os.environ.get("FEEDBACK_DB", os.path.join(HERE, "logs", "feedback.db"))
REASONS = ["inaccurate", "missed the question", "poor sources", "too verbose", "too short",
           "recommendation wasn't useful", "outdated information", "hallucinated", "slow response",
           "wrong film"]
PER_USER_PER_DAY = 200

_C = {"conn": None}


def conn():
    if _C["conn"] is None:
        os.makedirs(os.path.dirname(PATH), exist_ok=True)
        c = sqlite3.connect(PATH, check_same_thread=False, timeout=30)
        c.execute("pragma journal_mode=WAL")      # v7 D4: readers never block the writer
        c.executescript("""
        create table if not exists answers(
          message_id text primary key, conversation_id text, user_id text, vote text, reasons_json text,
          comment text, question text, contextualized_query text, intent text, films_json text,
          stages_json text, route_json text, prompt_chars integer, answer text, versions_json text,
          created_at text, updated_at text, status text default 'new', reviewer_note text);
        create index if not exists answers_status on answers(status, vote);""")
        # v4 (14 Oct): the prompt the answer was built from (context + question), the chat history
        # sent with it and the fact-check result - so a liked answer can become a training example
        # in the real RAG format - and the semantic filter's verdict (like_filter.py).
        have = {r[1] for r in c.execute("pragma table_info(answers)")}
        for col in ("prompt", "history_json", "check_json", "auto_status", "auto_reasons", "auto_scores",
                    "filter_version", "filtered_at"):
            if col not in have:
                c.execute(f"alter table answers add column {col} text")
        c.commit()
        _C["conn"] = c
    return _C["conn"]


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def record(message_id, vote, reasons=(), comment="", user_id="", request=None):
    """One vote per message (a changed vote replaces the old one). request: what the server
    kept about that answer (question, films, stages, route, answer, versions)."""
    if vote not in ("up", "down"):
        return False
    c = conn()
    day = now()[:10]
    if user_id:
        n = c.execute("select count(*) from answers where user_id = ? and substr(created_at, 1, 10) = ?",
                      (user_id, day)).fetchone()[0]
        if n >= PER_USER_PER_DAY:
            return False
    r = request or {}
    stages = r.get("stages") or {}
    plan = (stages.get("plan") or {})
    reasons = [x for x in (reasons or []) if x in REASONS][:5]
    old = c.execute("select created_at from answers where message_id = ?", (message_id,)).fetchone()
    cols = ("message_id", "conversation_id", "user_id", "vote", "reasons_json", "comment", "question",
            "contextualized_query", "intent", "films_json", "stages_json", "route_json", "prompt_chars", "answer",
            "versions_json", "created_at", "updated_at", "status", "reviewer_note", "prompt", "history_json",
            "check_json")
    # A changed vote replaces the row, so any earlier filter verdict (auto_*) is cleared with it.
    c.execute(f"insert or replace into answers ({','.join(cols)}) values ({','.join('?' * len(cols))})",
              (message_id, r.get("conversation_id"), user_id, vote, json.dumps(reasons), (comment or "")[:1000],
               r.get("question"), plan.get("search_query") or stages.get("merged_description"),
               plan.get("intent"), json.dumps(r.get("films") or []),
               json.dumps({k: v for k, v in stages.items() if k != "plan"}, default=str),
               json.dumps((stages.get("tools") or {}).get("route"), default=str), r.get("prompt_chars"),
               r.get("answer"), json.dumps(r.get("versions") or {}), old[0] if old else now(), now(), "new", None,
               r.get("prompt"), json.dumps(r.get("history") or []), json.dumps(r.get("check"), default=str)))
    c.commit()
    return True


def _rows(where="1=1", args=()):
    c = conn()
    c.row_factory = sqlite3.Row
    out = [dict(r) for r in c.execute(f"select * from answers where {where} order by created_at desc", args)]
    c.row_factory = None
    return out


def cmd_list(args):
    where, a = [], []
    if "--down" in args:
        where.append("vote = 'down'")
    if "--new" in args:
        where.append("status = 'new'")
    if "--auto" in args:
        where.append("auto_status is not null")
    for r in _rows(" and ".join(where) or "1=1", a)[:50]:
        print(f"{r['message_id']}  {r['vote']:4s} {r['status']:8s} {(r.get('auto_status') or '-'):12s} "
              f"{r['intent'] or '-':10s} "
              f"{','.join(json.loads(r['reasons_json'] or '[]')) or '-':30s} {(r['question'] or '')[:60]}")
        if r["comment"]:
            print(f"      comment: {r['comment'][:120]}")
        if r.get("auto_reasons"):
            print(f"      filter: {r['auto_reasons'][:120]}")


def cmd_review(args):
    mid, verdict = args[0], args[1]
    if verdict not in ("accept", "reject"):
        sys.exit("review <message_id> accept|reject [note]")
    c = conn()
    c.execute("update answers set status = ?, reviewer_note = ?, updated_at = ? where message_id = ?",
              ("accepted" if verdict == "accept" else "rejected", " ".join(args[2:]), now(), mid))
    c.commit()
    print("ok")


def cmd_stats(_args):
    rows = _rows()
    if not rows:
        print("no feedback yet")
        return
    def rate(key):
        g = {}
        for r in rows:
            k = key(r)
            s = g.setdefault(k, [0, 0])
            s[0] += r["vote"] == "down"
            s[1] += 1
        for k, (d, n) in sorted(g.items(), key=lambda x: -x[1][1]):
            print(f"    {str(k)[:40]:40s} {d}/{n} down")
    print(f"{len(rows)} votes, {sum(r['vote'] == 'down' for r in rows)} down")
    print("  by intent:"); rate(lambda r: r["intent"])
    print("  by tool route:"); rate(lambda r: ",".join((json.loads(r["route_json"] or "null") or {}).get("tasks") or []) or "-")
    print("  by prompt version:"); rate(lambda r: json.loads(r["versions_json"] or "{}").get("prompt_version"))
    print("  by git:"); rate(lambda r: json.loads(r["versions_json"] or "{}").get("git"))
    reasons = {}
    for r in rows:
        for x in json.loads(r["reasons_json"] or "[]"):
            reasons[x] = reasons.get(x, 0) + 1
    print("  dislike reasons:", ", ".join(f"{k} {v}" for k, v in sorted(reasons.items(), key=lambda x: -x[1])) or "-")


def _judged(files):
    """Answers the judge graded, per question id, from eval_v3.py result files."""
    import glob
    out = {}
    for f in files or sorted(glob.glob("results/v3eval_l2_*.json")):
        try:
            d = json.load(open(f))
        except (OSError, ValueError):
            continue
        for r in d.get("rows", []):
            j = r.get("judge") or {}
            if not r.get("answer") or j.get("verdict") not in ("pass", "fail"):
                continue
            score = sum(j.get(k) or 0 for k in ("grounded", "direct", "specific", "honest", "concise"))
            out.setdefault(r["id"], []).append({
                "file": os.path.basename(f), "question": r["question"], "category": r.get("category"),
                "prompt": (r.get("context") or "") + f"\n\nQuestion: {r['question']}",
                "answer": r["answer"], "verdict": j["verdict"], "score": score, "reason": j.get("reason", "")})
    return out


def messages_of(r):
    """The messages the answer model saw: earlier turns, then the full prompt (or the question)."""
    hist = []
    try:
        hist = [m for m in json.loads(r.get("history_json") or "[]")
                if isinstance(m, dict) and m.get("role") in ("user", "assistant") and m.get("content")]
    except ValueError:
        pass
    return hist + [{"role": "user", "content": r.get("prompt") or r["question"]}]


def cmd_pairs(args):
    """Training data from what people and the judge said - never from raw votes.
      1. reviewed feedback: an accepted thumbs-down with a corrected answer ("better: ...") gives
         a pair (correction > given answer); accepted thumbs-up answers become SFT examples.
      2. the judge (eval_v3.py level 2 files): for a question answered by several versions, the
         best passing answer is "chosen" and the worst failing one "rejected". The two answers
         came from different prompts (different versions' context), so the pair uses the chosen
         answer's prompt and is marked off_policy - fine for a first DPO run, stated in the file.
    Training itself (DPO or a LoRA refresh on sft_examples) needs the GPU and is the next step;
    any trained model goes through the same sealed set and judge before it replaces movie-buff."""
    os.makedirs("results", exist_ok=True)
    pairs, sft = [], []
    for r in _rows("status = 'accepted'"):
        note = (r.get("reviewer_note") or "").strip()
        if r["vote"] == "down" and note.lower().startswith("better:") and r.get("answer"):
            pairs.append({"prompt": r.get("prompt") or r["question"], "chosen": note[7:].strip(), "rejected": r["answer"],
                          "source": "feedback", "message_id": r["message_id"],
                          "reasons": json.loads(r["reasons_json"] or "[]"), "off_policy": False})
        elif r["vote"] == "up" and r.get("answer"):
            # v4: the real prompt (context + question) and history when stored; the bare question
            # only for rows recorded before 14 Oct (marked, so training can leave them out).
            sft.append({"messages": messages_of(r) + [{"role": "assistant", "content": r["answer"]}],
                        "source": "feedback", "message_id": r["message_id"],
                        "rag_prompt": bool(r.get("prompt"))})
    judged = _judged(args)
    for qid, rows in sorted(judged.items()):
        good = sorted((x for x in rows if x["verdict"] == "pass"), key=lambda x: -x["score"])
        bad = sorted((x for x in rows if x["verdict"] == "fail"), key=lambda x: x["score"])
        if good:
            g = good[0]
            sft.append({"messages": [{"role": "user", "content": g["prompt"]},
                                     {"role": "assistant", "content": g["answer"]}],
                        "source": f"judge:{g['file']}", "question_id": qid})
        if good and bad and good[0]["answer"].strip() != bad[0]["answer"].strip():
            g, b = good[0], bad[0]
            pairs.append({"prompt": g["prompt"], "chosen": g["answer"], "rejected": b["answer"],
                          "source": f"judge:{g['file']} > {b['file']}", "question_id": qid,
                          "category": g["category"], "rejected_reason": b["reason"],
                          "off_policy": g["file"] != b["file"]})
    with open("results/preference_pairs.jsonl", "w") as f:
        for p in pairs:
            f.write(json.dumps(p, ensure_ascii=False) + "\n")
    with open("results/sft_examples.jsonl", "w") as f:
        for x in sft:
            f.write(json.dumps(x, ensure_ascii=False) + "\n")
    nf = sum(p["source"] == "feedback" for p in pairs)
    print(f"preference pairs: {len(pairs)} ({nf} from reviewed feedback, {len(pairs) - nf} from the judge, "
          f"{sum(p['off_policy'] for p in pairs)} off-policy) -> results/preference_pairs.jsonl")
    print(f"SFT examples: {len(sft)} -> results/sft_examples.jsonl")
    print(f"judged questions: {len(judged)}; answers: {sum(len(v) for v in judged.values())}")


def cmd_export(_args):
    rows = _rows("status = 'accepted'")
    os.makedirs("results", exist_ok=True)
    with open("results/feedback_curated.jsonl", "w") as f:
        for r in rows:
            f.write(json.dumps({k: r[k] for k in ("message_id", "vote", "reasons_json", "comment", "question",
                                                   "contextualized_query", "intent", "films_json", "answer",
                                                   "versions_json", "reviewer_note")}) + "\n")
    print(f"{len(rows)} reviewed rows -> results/feedback_curated.jsonl (unreviewed rows are never exported)")


if __name__ == "__main__":
    cmds = {"list": cmd_list, "review": cmd_review, "stats": cmd_stats, "export": cmd_export, "pairs": cmd_pairs}
    if len(sys.argv) < 2 or sys.argv[1] not in cmds:
        print(__doc__)
    else:
        cmds[sys.argv[1]](sys.argv[2:])
