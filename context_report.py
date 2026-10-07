"""
How much of the answer model's context window Movie Buff actually uses (6 Oct).

movie-buff runs with num_ctx 8192 tokens. A bigger window (16k) costs GPU memory the shared
A10 doesn't have this week, so before asking for one: do real prompts come close to 8k?

Reads what is already logged - no model calls:
  results/v3eval_*.json      prompt size per analysis question (+ tool blocks, to see how often
                             the TOOL_CHARS budget cut something)
  results/stage_scores_*multiturn*.json is not needed: multi-turn prompts are logged by the site
  logs/requests.jsonl        live site: prompt size + history turns per question

Tokens are estimated as characters / 3.6 (English text with this tokenizer runs 3.5-4.2
characters per token; 3.6 errs on the high side). The site also sends up to 12 earlier messages
(trimmed to HISTORY_CHARS) and keeps room for the answer (num_predict).

    python context_report.py            -> results/context_report.txt
"""
import glob
import json
import os
import statistics

CTX = int(os.environ.get("NUM_CTX", "8192"))
CH_PER_TOK = 3.6
ANSWER_ROOM = int(os.environ.get("ANSWER_ROOM", "900"))     # tokens kept free for the answer
HISTORY_CHARS = int(os.environ.get("HISTORY_CHARS", "6000"))
TOOL_CHARS = int(os.environ.get("TOOL_CHARS", "9000"))


def pct(xs, p):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(p / 100 * (len(xs) - 1))))] if xs else 0


def line(name, toks):
    if not toks:
        return f"{name:34s} (none)"
    over = sum(t > CTX - ANSWER_ROOM for t in toks)
    return (f"{name:34s} n={len(toks):4d}  median {statistics.median(toks):6.0f}  p90 {pct(toks, 90):6.0f}  "
            f"max {max(toks):6.0f} tokens  over {CTX - ANSWER_ROOM} (no room to answer): {over}")


def main():
    out = [f"Context window use (num_ctx {CTX}; {ANSWER_ROOM} tokens kept for the answer; "
           f"tokens ~ characters / {CH_PER_TOK})", ""]
    ev, cut, nblk = [], 0, 0
    for f in sorted(glob.glob("results/v3eval_*.json")):
        try:
            rows = json.load(open(f))["rows"]
        except (OSError, ValueError, KeyError):
            continue
        for r in rows:
            if r.get("prompt_chars"):
                ev.append(r["prompt_chars"] / CH_PER_TOK)
            b = r.get("blocks") or []
            if b:
                nblk += 1
                if sum(n for _k, n in b) >= TOOL_CHARS - 300:
                    cut += 1
    out.append(line("analysis questions (eval files)", ev))
    out.append(f"{'':34s} tool data hit the TOOL_CHARS {TOOL_CHARS} budget (something cut): {cut} of {nblk}")
    site, site_hist, hist_turns = [], [], []
    p = os.path.join("logs", "requests.jsonl")
    if os.path.exists(p):
        for ln in open(p, encoding="utf-8"):
            try:
                r = json.loads(ln)
            except ValueError:
                continue
            if r.get("cached") or not r.get("prompt_chars"):
                continue
            t = r["prompt_chars"] / CH_PER_TOK
            site.append(t)
            h = r.get("history_turns") or 0
            hist_turns.append(h)
            # history is trimmed to HISTORY_CHARS; assume the worst case when there was history
            site_hist.append(t + (min(HISTORY_CHARS, h * 2 * 900) / CH_PER_TOK if h else 0))
    out.append(line("site: prompt only", site))
    out.append(line("site: prompt + history (worst case)", site_hist))
    if hist_turns:
        out.append(f"{'':34s} questions with earlier turns: {sum(1 for h in hist_turns if h)} of {len(hist_turns)}; "
                   f"max {max(hist_turns)} earlier turns")
    worst = (TOOL_CHARS + HISTORY_CHARS + 6000) / CH_PER_TOK
    out += ["", f"Upper bound by design: tool data {TOOL_CHARS} + history {HISTORY_CHARS} + film records and "
                f"instructions ~6000 characters = ~{worst:.0f} tokens, + {ANSWER_ROOM} for the answer "
                f"= ~{worst + ANSWER_ROOM:.0f} of {CTX}.",
            "Reading: if 'over' is 0 and p90 sits well below the limit, 8k is enough for these questions and a",
            "16k window would only matter if TOOL_CHARS / HISTORY_CHARS were raised (more context per answer)."]
    os.makedirs("results", exist_ok=True)
    open("results/context_report.txt", "w").write("\n".join(out) + "\n")
    print("\n".join(out))


if __name__ == "__main__":
    main()
