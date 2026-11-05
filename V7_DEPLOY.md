# v7 files, 28 Oct: how to put them on the server

Everything in `v7-oct28b.tar.gz` goes into `~/movie-buff/rag/` with its folders (`web/`, `sets/`, `tests/`,
`connectors/`). Every new answer-changing feature is a switch that is **off**; nothing changes what the site answers
until a switch is turned on in `config.yaml` after its rule passes.

Always-on changes (small, tested): the NYT review line in answers now says only who reviewed it, when, and Critic's
Pick (their terms bar giving NYT text to a model); per-request state so two people asking at once can't mix answers;
the page only turns allow-listed links into links; the film details panel shows a Reception section once the
reception cards exist; About and How pages updated (licensing register).

## 1. Upload (laptop Command Prompt)

Download v7-oct28b.tar.gz from the chat first (it lands in Downloads), then:

```
cd %USERPROFILE%\Downloads
scp v7-oct28b.tar.gz you@example.edu:~/
```

## 2. Install (server), after Tuesday night's run has finished

Do not unpack while `run_v7_night.sh` is running (bash reads a running script from disk as it goes):

```
pgrep -af run_v7_night.sh
```

When that prints nothing:

```
cd ~/movie-buff/rag
tar czf ~/before-v7-oct28b.tar.gz $(tar tzf ~/v7-oct28b.tar.gz | grep -v '/$') 2>/dev/null
tar xzf ~/v7-oct28b.tar.gz
source ~/venvs/movie-rag/bin/activate
python -m pytest -q tests
python check_sealed_v7.py
python money_extra.py critics      # 28 Oct: re-read Rotten Tomatoes / Metacritic from Wikipedia text (fixed parser)
python reception_cards.py mccheck "Lawrence of Arabia|Frida|The White Ribbon|Three Colors: Red"
```

Quick revert: `cd ~/movie-buff/rag && tar xzf ~/before-v7-oct28b.tar.gz` (new files stay but are not used), then
restart the site.

Then restart the site the usual way. To keep the laptop copy the same, unpack there too:
`tar -xzf v7-oct28b.tar.gz -C server-latest`

## 3. config.yaml: add this block once, at the end (a key written twice stops config.yaml from loading)

```
v7:
  # 27 Oct. Each switch stays 0 until its rule passes (README "v7"; python v7_rules.py ...). Set by hand.
  RECEPTION: 0                      # reception answers: card + cited evidence (rule: reception set +6, claims, grounding)
  TV: 0                             # TV shows (rule: TV set >= 20/30; tune/check/analytic lose <= 1)
  PEOPLE: 0                         # director / actor filters
  REC_CLEAN: 0                      # no TV movies / concerts / shorts / unreleased in recommendations
  IMDB: 0                           # IMDb rating line in film facts
  ALIAS_IMDB: 0                     # IMDb alternate titles as aliases
  GUARD: 0                          # injection defences (rule: >= 36/40 attacks defended)
  MODERATION: 0                     # Llama Guard + Detoxify (rule: <= 1 false block)
  MEM_EMBED: 0                      # recall earlier chats by meaning
  CTX_BUILDER: 0                    # context builder
  PROMPT_ORDER: 0                   # instructions before the data
  FAST: 0                           # fact fast path on the site
  SEMCACHE: 0                       # semantic answer cache on the site
  RECENT: 1                         # recent films in search (Friday's A/B tests 0)
```

## 4. Nights

```
tmux new -s night
source ~/venvs/movie-rag/bin/activate && cd ~/movie-buff/rag
export TMDB_API_KEY=...  MB_CONTACT='you@example.edu'  YOUTUBE_API_KEY=...  OMDB_API_KEY=...
bash run_v7_night.sh wed       # Wed 28
bash run_v7_night.sh thu       # Thu 29: D switches together vs off, TV, attacks, moderation, checks
bash run_v7_night.sh fri       # Fri 30: speed, prompt order, recent films, fits
bash run_v7_night.sh sat       # Sat 31: split a failed combined arm, samples, judge-check page
bash run_v7_night.sh allon     # Sun 1 / Mon 2, after setting the passed switches in config.yaml
```

Each night ends with `results/v7_night_summary.txt`: send it to Claude the next morning.

## 5. Your hand checks (pages open in any browser, offline)

- `results/label_reviews.html` (after Tuesday night): label rows 1-100, Export CSV, send it back; Claude drafts
  101-200 for you to approve. Then `python sentiment.py compare results/review_labels_done.csv`.
- `results/critic_outlet_check.csv`: is the outlet right? (`python critics.py score ...`, rule >= 90%).
- `results/video_match_check.csv` (Saturday): right film? (rule >= 90%).
- `results/judge_check.html` (Saturday): grade 30 answers blind, Export CSV, `python judge_check.py agree ...`.

## 6. Sealed run (Tue 3 Nov)

Fill in `sets/sealed_v7_ret.json`, `sealed_v7_an.json`, `sealed_v7_mt.json` (and `sealed_v7_tv.json` if TV is on)
by Sun 1 Nov; `python check_sealed_v7.py` checks them. Code freeze Tue 3 Nov 18:00: `git add -A && git commit -m
"v7 lock" && git tag v7.0-lock`, then `bash run_final_v7.sh` in tmux.
