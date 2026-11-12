#!/usr/bin/env bash
# Round 34 (11 Nov): after tonight's runs (tmux r27night, r33night), re-score the four guard v2 attack runs with judge
# v2 (attack_eval.py --rescore: "complied" only with a quote that is really in the answer). Same stored answers; the
# v1 numbers and the sealed gate stay as they were. Then results/r27_night.txt is written again with both.
cd "$(dirname "$0")/.." || exit 1
source ~/venvs/movie-rag/bin/activate 2>/dev/null
while tmux has-session -t r27night 2>/dev/null || tmux has-session -t r33night 2>/dev/null; do sleep 120; done
echo "=== judge v2 re-score $(date +%H:%M)"
python attack_eval.py --rescore results/attack_r27dev_g0.json results/attack_r27dev_g1.json \
  results/attack_r27sealed_g0.json results/attack_r27sealed_g1.json 2>&1 | tee results/attack_r34_rescore.txt
python tools/r27_rules.py > results/r27_night.txt 2>&1
echo "=== done $(date +%H:%M)"
