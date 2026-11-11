#!/usr/bin/env python3
"""
D3 (v7, 27 Oct): the recall set - does memory find the right earlier exchange when someone refers back to it in
other words, and stay quiet when they don't? 15 earlier chats, 30 refer-back questions (2 per chat, paraphrased),
15 unrelated questions. Written 27 Oct, before any result.

    python memory_eval.py                 # both arms -> results/recall_off.json, results/recall_on.json + a cut-off sweep
    python v7_rules.py recall results/recall_off.json results/recall_on.json
        Rule: MEM_EMBED=1 kept if hit@1 is higher and false recall is not higher.
hit@1: the first recalled exchange is the chat the question refers to. False recall: an unrelated question recalls
anything. The cut-off sweep is reported only; the switch uses MEM_MIN_COS as configured (0.72, chosen beforehand).
"""
import importlib
import json
import os
import sqlite3
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
HISTORY = [
    ("c1", "What's a good movie about grief to watch on a rainy day?",
     "Try Manchester by the Sea (2016): Casey Affleck plays a man who returns home after his brother dies and has to face an old loss.", ["Manchester by the Sea"]),
    ("c2", "Recommend a heist movie with a clever twist",
     "Inside Man (2006) is a bank robbery thriller with a smart reveal; Ocean's Eleven (2001) is the lighter, slicker option.", ["Inside Man", "Ocean's Eleven"]),
    ("c3", "Who composed the score for Interstellar?", "Hans Zimmer composed Interstellar's score, built around a church organ.", ["Interstellar"]),
    ("c4", "I'm planning a family movie night with my 8 year old, any animated films?",
     "Paddington 2 (2017), Coco (2017) and My Neighbor Totoro (1988) are all gentle and funny for an 8-year-old.", ["Paddington 2", "Coco", "My Neighbor Totoro"]),
    ("c5", "Why did Blade Runner flop at the box office?",
     "Blade Runner (1982) opened against E.T. and The Thing, and its slow, bleak tone puzzled audiences; it later became a cult classic.", ["Blade Runner"]),
    ("c6", "What's that movie where a man relives the same day over and over?",
     "That's Groundhog Day (1993): Bill Murray's weatherman wakes up on February 2 again and again.", ["Groundhog Day"]),
    ("c7", "Suggest a slow-burn horror film that isn't full of jump scares",
     "The Witch (2015) builds dread slowly in 1630s New England; It Follows (2014) is tense without cheap scares.", ["The Witch", "It Follows"]),
    ("c8", "Compare the budgets of Avatar and Titanic",
     "Avatar cost about $237 million and Titanic about $200 million, per TMDB; both were directed by James Cameron.", ["Avatar", "Titanic"]),
    ("c9", "What are some good Korean thrillers?",
     "Oldboy (2003), Memories of Murder (2003) and The Handmaiden (2016) are three of the best.", ["Oldboy", "Memories of Murder", "The Handmaiden"]),
    ("c10", "Is The Godfather Part III worth watching?",
     "It is the weakest of the trilogy, but Al Pacino's performance and the ending make it worth seeing once.", ["The Godfather Part III"]),
    ("c11", "Tell me about the making of Apocalypse Now",
     "Apocalypse Now had a notoriously troubled shoot in the Philippines: a typhoon destroyed sets and Martin Sheen had a heart attack.", ["Apocalypse Now"]),
    ("c12", "I want a feel-good sports movie",
     "Remember the Titans (2000), about a newly integrated high-school football team, and Rocky (1976) are classic picks.", ["Remember the Titans", "Rocky"]),
    ("c13", "What should I watch after finishing The Lord of the Rings?",
     "Willow (1988) and Stardust (2007) have the same sense of adventure.", ["Willow", "Stardust"]),
    ("c14", "Who directed Parasite and what else did he make?",
     "Bong Joon Ho directed Parasite; he also made Memories of Murder, The Host, Snowpiercer and Okja.", ["Parasite"]),
    ("c15", "Any space movies that are scientifically accurate?",
     "The Martian (2015) and Apollo 13 (1995) are praised for getting the science right.", ["The Martian", "Apollo 13"]),
]
BACK = [
    ("c1", "What was that sad film you recommended when I was feeling down about losing someone?"),
    ("c1", "remind me of the movie about mourning you suggested last time"),
    ("c2", "what were the bank robbery films you told me about before?"),
    ("c2", "you suggested a clever robbery movie earlier, which one was it?"),
    ("c3", "who did you say wrote the music for that Nolan space film?"),
    ("c3", "earlier you told me the Interstellar composer, remind me"),
    ("c4", "the cartoons you picked for my kid's movie night - what were they again?"),
    ("c4", "which animated movies did you suggest for my daughter last week?"),
    ("c5", "you explained why that Ridley Scott sci-fi did badly in theaters, what was the reason?"),
    ("c5", "remind me why Blade Runner bombed"),
    ("c6", "what was the time loop comedy we talked about?"),
    ("c6", "the Bill Murray movie where he repeats a day, what's it called again?"),
    ("c7", "those creepy horror films without cheap scares you recommended - names?"),
    ("c7", "earlier you gave me some atmospheric horror picks, what were they?"),
    ("c8", "what did you say Avatar cost compared to Titanic?"),
    ("c8", "remind me of the James Cameron budget comparison"),
    ("c9", "the films from South Korea you listed, which ones were they?"),
    ("c9", "you recommended some Korean crime movies before, can you repeat them?"),
    ("c10", "was the third Godfather worth it, like you said earlier?"),
    ("c10", "what did you tell me about the last Godfather movie?"),
    ("c11", "what happened during the chaotic filming of that Vietnam war movie you described?"),
    ("c11", "you told me about a typhoon wrecking a film set, which movie?"),
    ("c12", "the uplifting football movie you suggested, what was it?"),
    ("c12", "remind me of the inspiring sports films from last time"),
    ("c13", "you gave me fantasy picks for after Tolkien, what were they?"),
    ("c13", "what was I supposed to watch after LOTR again?"),
    ("c14", "what other films by the Parasite director did you mention?"),
    ("c14", "remind me what else Bong Joon Ho made"),
    ("c15", "the realistic astronaut movies you recommended, which ones?"),
    ("c15", "you suggested some accurate space films earlier, names please"),
]
UNRELATED = ["Who directed Jaws?", "What's a good romantic comedy from the 90s?", "How long is The Irishman?",
             "Recommend a western", "What is Casablanca about?", "best musicals of all time", "Is Dune Part Two good?",
             "who stars in Heat (1995)", "a documentary about food", "what won best picture in 2020",
             "funny movies for a party", "a movie about a talking pig", "zombie films with humor",
             "what's a good courtroom drama", "spy movies set in the cold war"]


DAYS = {}          # round 27: history id -> days ago (the harder set spreads chats over months)


def build_history(path, user="eval"):
    c = sqlite3.connect(path)
    c.executescript("""create table chats(conv_id text primary key, user_id text, title text, created_at real, updated_at real);
        create table turns(conv_id text, seq integer, message_id text, question text, answer text, films_json text,
          recent_json text, conv_json text, created_at real, cached integer);""")
    t0 = time.time() - 86400 * 20
    for i, (cid, q, a, films) in enumerate(HISTORY):
        t = time.time() - 86400 * DAYS[cid] if cid in DAYS else t0 + i * 3600
        c.execute("insert into chats values (?,?,?,?,?)", (cid, user, q[:40], t, t))
        c.execute("insert into turns values (?,?,?,?,?,?,?,?,?,?)", (cid, 0, f"m{i}", q, a,
                  json.dumps([{"title": f} for f in films]), "[]", "{}", t, 0))
    c.commit()
    c.close()


def run(embed, min_cos=None):
    os.environ["MEM_EMBED"] = "1" if embed else "0"
    if min_cos is not None:
        os.environ["MEM_MIN_COS"] = str(min_cos)
    sys.path.insert(0, os.path.join(HERE, "web"))
    import memory
    importlib.reload(memory)
    rows, hit, false = [], 0, 0
    for exp, q in BACK:
        r = memory.recall("eval", q, "new")
        got = r[0]["conv"] if r else None
        hit += got == exp
        rows.append({"q": q, "expect": exp, "got": got, "n": len(r)})
    for q in UNRELATED:
        r = memory.recall("eval", q, "new")
        false += bool(r)
        rows.append({"q": q, "expect": None, "got": r[0]["conv"] if r else None, "n": len(r)})
    return {"embed": embed, "min_cos": memory.MEM_MIN_COS, "hit1": hit / len(BACK), "false_recall": false / len(UNRELATED),
            "hits": hit, "false": false, "rows": rows}


def main():
    tmp = tempfile.mkdtemp()
    os.environ["CHATS_DB"] = os.path.join(tmp, "chats.db")
    os.environ["MEMORY_DB"] = os.path.join(tmp, "memory.db")
    build_history(os.environ["CHATS_DB"])
    os.makedirs(os.path.join(HERE, "results"), exist_ok=True)
    off = run(False)
    on = run(True)
    for name, d in (("off", off), ("on", on)):
        json.dump(d, open(os.path.join(HERE, "results", f"recall_{name}.json"), "w"), indent=1)
        print(f"MEM_EMBED={int(d['embed'])}: hit@1 {d['hits']}/{len(BACK)} = {d['hit1']:.2f}, false recall "
              f"{d['false']}/{len(UNRELATED)} = {d['false_recall']:.2f}")
    print("cut-off sweep (reported only):")
    for mc in (0.60, 0.65, 0.68, 0.70, 0.72, 0.75, 0.78, 0.80):
        d = run(True, mc)
        print(f"  MEM_MIN_COS {mc:.2f}: hit@1 {d['hit1']:.2f}, false recall {d['false_recall']:.2f}")
    os.environ.pop("MEM_MIN_COS", None)
    misses = [r for r in on["rows"] if r["expect"] and r["got"] != r["expect"]]
    for r in misses[:10]:
        print(f"  miss (on): {r['q']}  expected {r['expect']}, got {r['got']}")


HALF_A = {f"c{i}" for i in range(1, 9)}           # 3 Nov: cut-off chosen on c1-c8, checked on c9-c15


def split(d):
    """hit@1 / false recall on half A (chats c1-c8, first 8 unrelated) and half B (c9-c15, the other 7)."""
    back = [r for r in d["rows"] if r["expect"]]
    unrel = [r for r in d["rows"] if not r["expect"]]
    out = {}
    for name, bk, un in (("A", [r for r in back if r["expect"] in HALF_A], unrel[:8]),
                         ("B", [r for r in back if r["expect"] not in HALF_A], unrel[8:])):
        out[name] = {"hit": sum(r["got"] == r["expect"] for r in bk), "n": len(bk),
                     "false": sum(r["got"] is not None for r in un), "n_un": len(un)}
    return out


def pick():
    """Choose MEM_BACK_COS on half A only (most refer-backs right; ties -> the higher cut-off); write it to
    results/mem_back_cos.txt; then report both halves for off and on."""
    tmp = tempfile.mkdtemp()
    os.environ["CHATS_DB"] = os.path.join(tmp, "chats.db")
    os.environ["MEMORY_DB"] = os.path.join(tmp, "memory.db")
    build_history(os.environ["CHATS_DB"])
    os.environ["MEM_EMBED_V"] = "2"
    best = None
    print("MEM_BACK_COS sweep on half A (c1-c8):")
    for bc in (0.45, 0.50, 0.55, 0.60, 0.65, 0.70, 0.75):
        os.environ["MEM_BACK_COS"] = str(bc)
        s = split(run(True))["A"]
        print(f"  {bc:.2f}: refer-backs right {s['hit']}/{s['n']}, unrelated recalled {s['false']}/{s['n_un']}")
        key = (s["hit"], -s["false"], bc)
        if best is None or key > best[0]:
            best = (key, bc)
    bc = best[1]
    os.makedirs(os.path.join(HERE, "results"), exist_ok=True)
    open(os.path.join(HERE, "results", "mem_back_cos.txt"), "w").write(f"{bc}\n")
    print(f"chosen MEM_BACK_COS {bc} (written to results/mem_back_cos.txt)")
    off = run(False)
    on = run(True)
    for name, d in (("off", off), ("on", on)):
        json.dump(d, open(os.path.join(HERE, "results", f"recall_{name}.json"), "w"), indent=1)
        sp = split(d)
        print(f"MEM_EMBED={int(d['embed'])}: all hit@1 {d['hits']}/30, false {d['false']}/15 | half A {sp['A']['hit']}/"
              f"{sp['A']['n']} false {sp['A']['false']}/{sp['A']['n_un']} | half B (check) {sp['B']['hit']}/{sp['B']['n']} "
              f"false {sp['B']['false']}/{sp['B']['n_un']}")
    os.environ.pop("MEM_BACK_COS", None)


def sealed(path, tag):
    """4 Nov: the blind recall check on a set written after the 3 Nov fixes (sets/sealed_v7_rc.json), with the
    cut-off already chosen (results/mem_back_cos.txt) - nothing is picked here. Same rule as recall2's first part:
    hit@1 higher and false recall not higher. Items: {"kind": "history", "id", "question", "answer", "films"},
    {"kind": "back", "chat": <history id>, "question"}, {"kind": "unrelated", "question"}."""
    global HISTORY, BACK, UNRELATED
    items = json.load(open(path))["questions"]
    if any('"<' in json.dumps(q) for q in items):
        sys.exit(f"{path} still has <...> placeholders")
    HISTORY = [(q["id"], q["question"], q["answer"], q.get("films") or []) for q in items if q.get("kind") == "history"]
    BACK = [(q["chat"], q["question"]) for q in items if q.get("kind") == "back"]
    UNRELATED = [q["question"] for q in items if q.get("kind") == "unrelated"]
    tmp = tempfile.mkdtemp()
    os.environ["CHATS_DB"] = os.path.join(tmp, "chats.db")
    os.environ["MEMORY_DB"] = os.path.join(tmp, "memory.db")
    build_history(os.environ["CHATS_DB"])
    os.environ["MEM_EMBED_V"] = "2"
    os.makedirs(os.path.join(HERE, "results"), exist_ok=True)
    res = {}
    for name, emb in (("off", False), ("on", True)):
        d = run(emb)
        res[name] = d
        json.dump(d, open(os.path.join(HERE, "results", f"recall_{tag}_{name}.json"), "w"), indent=1)
        print(f"sealed recall MEM_EMBED={int(emb)}: refer-backs right {d['hits']}/{len(BACK)} = {d['hit1']:.2f}, "
              f"unrelated recalled {d['false']}/{len(UNRELATED)} = {d['false_recall']:.2f}")
    ok = res["on"]["hit1"] > res["off"]["hit1"] and res["on"]["false_recall"] <= res["off"]["false_recall"]
    print(f"sealed recall rule (hit@1 higher, false recall not higher): {'PASS' if ok else 'FAIL'}")


def load_set(path):
    """A set in the sealed format (history / back / unrelated items; history may carry days_ago)."""
    global HISTORY, BACK, UNRELATED
    items = json.load(open(path))["questions"]
    HISTORY = [(q["id"], q["question"], q["answer"], q.get("films") or []) for q in items if q.get("kind") == "history"]
    BACK = [(q["chat"], q["question"]) for q in items if q.get("kind") == "back"]
    UNRELATED = [q["question"] for q in items if q.get("kind") == "unrelated"]
    DAYS.clear()
    DAYS.update({q["id"]: q["days_ago"] for q in items if q.get("kind") == "history" and q.get("days_ago")})


def cards(path=None, tag="tune", mock=False):
    """Round 27 (3a): memory cards. Both arms have MEM_EMBED=1 (the shipped v7 memory); the card arm adds MEM_CARDS=1
    with a card built for every history chat (topic by llama3.2, or the first question with --mock-cards).
    Rule (plan, 6 Nov; on the recall tuning set = the default set): refer-backs right not lower and false recalls not
    higher than without cards. Any other set (--set) is reported only."""
    if path:
        load_set(path)
    tmp = tempfile.mkdtemp()
    os.environ["CHATS_DB"] = os.path.join(tmp, "chats.db")
    os.environ["MEMORY_DB"] = os.path.join(tmp, "memory.db")
    build_history(os.environ["CHATS_DB"])
    os.environ["MEM_EMBED_V"] = "2"
    os.makedirs(os.path.join(HERE, "results"), exist_ok=True)
    os.environ["MEM_CARDS"] = "0"
    off = run(True)
    os.environ["MEM_CARDS"] = "1"
    sys.path.insert(0, os.path.join(HERE, "web"))
    import memory
    importlib.reload(memory)
    t0 = time.time()
    built = [memory.update_card("eval", cid, mock=mock) for cid, *_ in HISTORY]
    print(f"cards built: {sum(1 for b in built if b)} in {time.time() - t0:.0f} s; e.g. "
          + " | ".join(f"{b['conv']}: {b['topic']} [{', '.join(b['films'][:2])}]" for b in built[:3] if b))
    on = run(True)
    os.environ["MEM_CARDS"] = "0"
    for name, d in (("nocards", off), ("cards", on)):
        d["cards"] = name == "cards"
        json.dump(d, open(os.path.join(HERE, "results", f"recall_{tag}_{name}.json"), "w"), indent=1)
        print(f"{tag} MEM_CARDS={int(name == 'cards')}: refer-backs right {d['hits']}/{len(BACK)}, unrelated recalled "
              f"{d['false']}/{len(UNRELATED)}")
    ok = on["hits"] >= off["hits"] and on["false"] <= off["false"]
    print(f"{tag} cards rule (right not lower, false not higher): {'PASS' if ok else 'FAIL'}"
          + ("" if not path else " (reported only: not the tuning set)"))
    for r in on["rows"]:
        if r["expect"] and r["got"] != r["expect"]:
            print(f"  miss (cards): {r['q'][:80]}  expected {r['expect']}, got {r['got']}")


if __name__ == "__main__":
    if "--cards" in sys.argv:
        cards(sys.argv[sys.argv.index("--set") + 1] if "--set" in sys.argv else None,
              sys.argv[sys.argv.index("--tag") + 1] if "--tag" in sys.argv else "tune", "--mock-cards" in sys.argv)
        sys.exit(0)
    if "--set" in sys.argv:
        sealed(sys.argv[sys.argv.index("--set") + 1],
               sys.argv[sys.argv.index("--tag") + 1] if "--tag" in sys.argv else "sealed7")
    else:
        pick() if "--pick" in sys.argv else main()
