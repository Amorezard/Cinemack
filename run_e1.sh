#!/usr/bin/env bash
# E0 + E1 (v2 plan): does searching Wikipedia passages find more films, and what does it cost?
#   tmux new -s e1      (then, inside tmux)
#   source ~/venvs/movie-rag/bin/activate && cd ~/movie-buff/rag && bash run_e1.sh
# Safe to start while wiki_embed.py is still running: it waits for it to finish.
# Everything goes to results/e1.log; the short version to results/e1_summary.txt.
set -u
cd "$(dirname "$0")"
LOG=results/e1.log
SUM=results/e1_summary.txt
latest() { ls -t results/stage_scores_*_"$1".json 2>/dev/null | head -1; }   # newest run with this tag
mkdir -p results
exec > >(tee -a "$LOG") 2>&1
echo "=== run_e1.sh started $(date)"

while pgrep -f "wiki_embed.py" >/dev/null; do
  echo "waiting for wiki_embed.py to finish ... $(date +%H:%M)"; sleep 120
done
python wiki_embed.py --wiki-db wiki.db          # finishes any chunks left over; no-op if complete
python wiki_fts.py

echo "=== sanity searches"
python wiki_try.py "a weatherman keeps reliving the same day in a small town"
python wiki_try.py "two lighthouse keepers slowly go mad on an island"

run() {  # $1 = WIKI value, $2 = set, $3 = tag, rest = extra args
  local w=$1 set=$2 tag=$3; shift 3
  echo "=== WIKI=$w $set $tag $(date +%H:%M)"
  WIKI=$w python stage_scores.py --questions sets/$set.json --tag $tag "$@" | tail -45
}

run 0 tune v1base
run 1 tune wiki
run 0 check v1base
run 1 check wiki
# speed, no cache (every model call is real): 20 tune questions each way
run 0 tune time_v1 --no-cache --limit 20
run 1 tune time_wiki --no-cache --limit 20

{
  echo "E1 summary  $(date)"
  echo; echo "##### TUNE: A = v1 (WIKI=0), B = Wikipedia passages (WIKI=1)"
  python compare_stage.py "$(latest tune_v1base)" "$(latest tune_wiki)"
  echo; echo "##### CHECK"
  python compare_stage.py "$(latest check_v1base)" "$(latest check_wiki)"
  echo; echo "##### SPEED (no cache, 20 tune questions)"
  python compare_stage.py "$(latest tune_only_time_v1)" "$(latest tune_only_time_wiki)"
} > "$SUM" 2>&1
cat "$SUM"
echo "=== run_e1.sh finished $(date)"
