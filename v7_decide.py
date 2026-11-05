#!/usr/bin/env python3
"""
1 Nov: apply the v7 rules (v7_rules.py, written before the results) to this week's result files and write the
switch set for the "all together" run (Mon 2 Nov) and the sealed run (Tue 3 Nov):

    python v7_decide.py            # prints every rule, writes v7_lock.env and results/v7_decisions.txt
    cat v7_lock.env                # one line of KEY=value; run_final_v7.sh and run_v7_night.sh monday read it

Each switch is compared with an OFF arm run on the same day (the 1 Nov finding: arms and baselines from different
days differed by 1-2 analytic questions with no switch changed). A switch is on only if EVERY part of its rule
passes; a missing result file counts as not passed. Settled earlier and written in as they are:
    RECENT=1 (30 Oct A/B), MODERATION=1 (29 Oct false blocks 1/396), FAST=1 and SEMCACHE=1 (29 Oct checks),
    RECEPTION=0 (rule needs 42/40), GUARD=0 (attacks 32/40, analytic lost 5), IMDB=0 (not tested this week).
You can still edit v7_lock.env by hand before Tuesday's freeze; the sealed run's stamp records what it used.
"""
import contextlib
import glob
import io
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(HERE)
import v7_rules as V  # noqa: E402

ALL = ["ALIAS_IMDB", "RECEPTION", "TV", "PEOPLE", "REC_CLEAN", "IMDB", "GUARD", "MODERATION", "MEM_EMBED",
       "CTX_BUILDER", "PROMPT_ORDER", "FAST", "SEMCACHE", "SHORTS", "RECENT", "CLARIFY",
       "ISSUES", "YT_COMMENTS", "TV_REVIEWS", "PERF"]
FIXED = {"RECENT": 1, "MODERATION": 1, "FAST": 1, "SEMCACHE": 1, "RECEPTION": 0, "GUARD": 0, "IMDB": 0}
OUT = []


def say(s=""):
    print(s)
    OUT.append(s)


def latest(split, tag):
    fs = sorted(glob.glob(f"results/stage_scores_*_{split}_{tag}.json"), key=os.path.getmtime)
    return fs[-1] if fs else None


def run(fn, *paths, **kw):
    """Call a v7_rules check; (passed, its printed lines). Missing files -> not passed."""
    if any(p is None or not os.path.exists(p) for p in paths):
        return False, [f"(not run: {', '.join(str(p) for p in paths)})"]
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        try:
            ok = fn(*paths, **kw)
        except Exception as e:  # noqa: BLE001
            print(f"(error: {e!r})")
            ok = False
    return bool(ok), buf.getvalue().strip().splitlines()


def part(name, ok_lines):
    ok, lines = ok_lines
    say(f"    {name}: {'PASS' if ok else 'FAIL'}")
    for l in lines:
        say(f"      {l}")
    return ok


def stage(off, on, split):
    return run(V.cmd_stage, latest(split, off), latest(split, on))


def ev(tag):
    return f"results/v3eval_{tag}.json"


def multiturn(tag):
    fs = sorted(glob.glob(f"results/stage_scores_*_multiturn_{tag}.json"), key=os.path.getmtime)
    return fs[-1] if fs else None


def decide():
    on = dict(FIXED)
    say("v7 decisions (rules from v7_rules.py; each arm against an off arm of the same day)")
    say(f"  settled earlier: {' '.join(f'{k}={v}' for k, v in FIXED.items())}")

    say("  ALIAS_IMDB (alone, after the 30 Oct guards):")
    ok = all([part("tune", stage("v7off", "v7alias2", "tune")), part("check", stage("v7off", "v7alias2", "check")),
              part("analytic (an_off3 vs an_alias3, same day)", run(V.cmd_regression, ev("an_off3"), ev("an_alias3")))])
    on["ALIAS_IMDB"] = int(ok)

    say("  PEOPLE + REC_CLEAN (together, as tested):")
    ok = all([part("tune", stage("v7off2", "v7pr", "tune")), part("check", stage("v7off2", "v7pr", "check")),
              part("analytic (an_off3 vs an_pr3, same day)", run(V.cmd_regression, ev("an_off3"), ev("an_pr3")))])
    on["PEOPLE"] = on["REC_CLEAN"] = int(ok)

    say("  TV (after the 1 Nov film-title guard):")
    ok = all([part("tune", stage("v7off3", "v7tv3", "tune")), part("check", stage("v7off3", "v7tv3", "check")),
              part("TV set (>= 20/30)", run(V.cmd_tv, ev("tv_on3"), ev("tv_off3")) if os.path.exists(ev("tv_off3"))
                   else run(V.cmd_tv, ev("tv_on3"))),
              part("analytic (an_off4 vs an_tv3)", run(V.cmd_regression, ev("an_off4"), ev("an_tv3")))])
    on["TV"] = int(ok)

    say("  SHORTS (new 1 Nov):")
    ok = all([part("tune", stage("v7off3", "v7sh", "tune")), part("check", stage("v7off3", "v7sh", "check")),
              part("shorts set (on >= off + 4)", run(V.cmd_shorts, ev("sh_on"), ev("sh_off"))),
              part("analytic (an_off4 vs an_sh)", run(V.cmd_regression, ev("an_off4"), ev("an_sh")))])
    on["SHORTS"] = int(ok)

    for sw, tag in (("CTX_BUILDER", "cb"), ("PROMPT_ORDER", "po")):
        say(f"  {sw} (alone):")
        ok = all([part(f"analytic (an_off4 vs an_{tag})", run(V.cmd_regression, ev("an_off4"), ev(f"an_{tag}"))),
                  part("multi-turn", run(V.cmd_stage, multiturn("mt_off3"), multiturn(f"mt_{tag}")))])
        on[sw] = int(ok)

    say("  MEM_EMBED (cut-off as configured; the sweep is reported only):")
    on["MEM_EMBED"] = int(part("recall", run(V.cmd_recall, "results/recall_off.json", "results/recall_on.json")))

    if on["CTX_BUILDER"] and on["PROMPT_ORDER"]:
        say("  note: CTX_BUILDER and PROMPT_ORDER each passed alone but failed together on 30 Oct; Monday's "
            "all-together run decides.")
    line = " ".join(f"{k}={on.get(k, 0)}" for k in ALL)
    say("")
    say(f"LOCK: {line}")
    with open("v7_lock.env", "w") as f:
        f.write("# written by v7_decide.py; read by run_v7_night.sh monday and run_final_v7.sh\n" + line + "\n")
    os.makedirs("results", exist_ok=True)
    with open("results/v7_decisions.txt", "w") as f:
        f.write("\n".join(OUT) + "\n")
    print("wrote v7_lock.env and results/v7_decisions.txt")


def read_env(p):
    d = {}
    for line in open(p):
        for kv in line.split() if not line.startswith("#") else []:
            if "=" in kv:
                k, v = kv.split("=", 1)
                d[k] = int(v) if v.isdigit() else v
    return d


def decide_fix():
    """3 Nov: the fixed switches, each added to the 1 Nov locked set (v7_lock_base.env) and compared with that set run
    the same day (run_v7_night.sh fix). Rules from v7_rules.py, the 3 Nov replacements where the old rule could not be
    met. Writes v7_lock.env (base + every switch that passed) and results/v7_decisions_fix.txt."""
    base = read_env("v7_lock_base.env" if os.path.exists("v7_lock_base.env") else "v7_lock.env")
    on = {k: base.get(k, 0) for k in ALL}
    say("v7 decisions, 3 Nov fixes (each arm = 1 Nov locked set + one switch, against the locked set, same day)")
    say(f"  base: {' '.join(f'{k}={v}' for k, v in base.items())}")

    say("  TV (3 Nov: show answers get their own paragraph; 'like <show>' recommends):")
    tv_ok = all([part("tune", stage("lkbase", "lktv", "tune")), part("check", stage("lkbase", "lktv", "check")),
                 part("TV set (>= 20/30)", run(V.cmd_tv, ev("tv_lk_tv"), ev("tv_lk"))
                      if os.path.exists(ev("tv_lk")) else run(V.cmd_tv, ev("tv_lk_tv"))),
                 part("analytic (an_lk vs an_lk_tv)", run(V.cmd_regression, ev("an_lk"), ev("an_lk_tv")))])
    on["TV"] = int(tv_ok)

    say("  CLARIFY (3 Nov, needs TV): film-or-show choice, against the TV arm:")
    if tv_ok:
        ok = all([part("TV set (tv_lk_tv vs tv_lk_cl, lose <= 1)", run(V.cmd_regression, ev("tv_lk_tv"), ev("tv_lk_cl"))),
                  part("analytic (an_lk_tv vs an_lk_cl)", run(V.cmd_regression, ev("an_lk_tv"), ev("an_lk_cl")))])
        on["CLARIFY"] = int(ok)
    else:
        say("    TV did not pass: CLARIFY stays 0")
        on["CLARIFY"] = 0

    say("  SHORTS (3 Nov: only recommendations use the shorts-only search):")
    on["SHORTS"] = int(all([part("tune", stage("lkbase", "lksh", "tune")), part("check", stage("lkbase", "lksh", "check")),
                            part("shorts set (3 Nov rule)", run(V.cmd_shorts2, ev("sh_lk_sh"), ev("sh_lk"))),
                            part("analytic (an_lk vs an_lk_sh)", run(V.cmd_regression, ev("an_lk"), ev("an_lk_sh")))]))

    for sw, tag in (("CTX_BUILDER", "cb"), ("PROMPT_ORDER", "po")):
        say(f"  {sw} (3 Nov fix):")
        on[sw] = int(all([part(f"analytic (an_lk vs an_lk_{tag})", run(V.cmd_regression, ev("an_lk"), ev(f"an_lk_{tag}"))),
                          part("multi-turn", run(V.cmd_stage, multiturn("mt_lk"), multiturn(f"mt_lk_{tag}")))]))

    say("  RECEPTION (3 Nov: answers only the group asked about, shorter; head-to-head rule):")
    on["RECEPTION"] = int(all([
        part("reception set (3 Nov rule)", run(V.cmd_reception2, ev("rec_lk"), ev("rec_lk_rec"), "results/pairwise_rec_lk.json")),
        part("analytic (an_lk vs an_lk_rec)", run(V.cmd_regression, ev("an_lk"), ev("an_lk_rec")))]))

    say("  MEM_EMBED (3 Nov: refer-back questions; cut-off picked on c1-c8, checked on c9-c15):")
    on["MEM_EMBED"] = int(part("recall (3 Nov rule)", run(V.cmd_recall2, "results/recall_off.json", "results/recall_on.json")))

    if on["CTX_BUILDER"] and on["PROMPT_ORDER"]:
        say("  note: CTX_BUILDER and PROMPT_ORDER are both on; the `fixall` run checks them together.")
    line = " ".join(f"{k}={on.get(k, 0)}" for k in ALL)
    say("")
    say(f"LOCK: {line}")
    with open("v7_lock.env", "w") as f:
        f.write("# written by v7_decide.py --fix (3 Nov); base = v7_lock_base.env\n" + line + "\n")
    os.makedirs("results", exist_ok=True)
    with open("results/v7_decisions_fix.txt", "w") as f:
        f.write("\n".join(OUT) + "\n")
    print("wrote v7_lock.env and results/v7_decisions_fix.txt")


def decide_feat():
    """4 Nov: the new switches, each added to the current lock (v7_lock.env as the fixall step left it) and compared
    with that lock run the same day (run_v7_night.sh feat). Writes v7_lock.env (+ each switch that passed; the lock
    before is kept as v7_lock_pre_feat.env) and results/v7_decisions_feat.txt."""
    import shutil
    if not os.path.exists("v7_lock_pre_feat.env"):
        shutil.copy("v7_lock.env", "v7_lock_pre_feat.env")
    base = read_env("v7_lock_pre_feat.env")
    on = {k: base.get(k, 0) for k in ALL}
    say("v7 decisions, 4 Nov switches (each arm = the lock + one switch, against the lock, same day)")
    say(f"  base: {' '.join(f'{k}={v}' for k, v in base.items())}")
    say("  ISSUES (issue questions -> reception with themes / controversy evidence):")
    on["ISSUES"] = int(bool(base.get("RECEPTION")) and all([
        part("issues set (head to head, margin 3)", run(V.cmd_h2h, ev("is_fb"), ev("is_on"), "results/pairwise_is.json", margin=3)),
        part("reception set (lose <= 1)", run(V.cmd_regression, ev("rec_fb"), ev("rec_is"))),
        part("analytic (lose <= 1)", run(V.cmd_regression, ev("an_fb"), ev("an_is")))]))
    say("  YT_COMMENTS (YouTube stats line + viewer comments as evidence):")
    on["YT_COMMENTS"] = int(all([
        part("reception set (head to head, margin 0)", run(V.cmd_h2h, ev("rec_fb"), ev("rec_yt"), "results/pairwise_yt.json", margin=0)),
        part("analytic (lose <= 1)", run(V.cmd_regression, ev("an_fb"), ev("an_yt")))]))
    say("  TV_REVIEWS (critics from the show's Wikipedia reception section):")
    on["TV_REVIEWS"] = int(bool(base.get("TV")) and part("TV set (not worse)", run(V.cmd_notworse, ev("tv_fb"), ev("tv_tr"))))
    say("  PERF (box-office expectation model in 'why did it do well / badly' answers):")
    on["PERF"] = int(all([
        part("success set (head to head, margin 2)", run(V.cmd_h2h, ev("su_fb"), ev("su_pf"), "results/pairwise_pf.json", margin=2)),
        part("analytic (lose <= 1)", run(V.cmd_regression, ev("an_fb"), ev("an_pf")))]))
    line = " ".join(f"{k}={on.get(k, 0)}" for k in ALL)
    say("")
    say(f"LOCK: {line}")
    with open("v7_lock.env", "w") as f:
        f.write("# written by v7_decide.py --feat (4 Nov); before: v7_lock_pre_feat.env\n" + line + "\n")
    os.makedirs("results", exist_ok=True)
    with open("results/v7_decisions_feat.txt", "w") as f:
        f.write("\n".join(OUT) + "\n")
    print("wrote v7_lock.env and results/v7_decisions_feat.txt")


if __name__ == "__main__":
    if sys.argv[1:] == ["--feat"]:
        decide_feat()
    elif sys.argv[1:] == ["--fix"]:
        decide_fix()
    elif len(sys.argv) > 1:
        sys.exit(__doc__)
    else:
        decide()
