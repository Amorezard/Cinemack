#!/usr/bin/env bash
# Round 33 (11 Nov night): re-tests of the two variants written after their rules failed (not blind: same sets):
#   personalite  PERSONA=cinemack PERSONA_SYSTEM=0 (prompt lines renamed; the model's own SYSTEM text untouched)
#   ctx3tv       CTX_BUILDER=1 CTX_BUILDER_V=3 CTX_V3_SKIP_TV=1 (TV answers keep the plain context)
# Waits for r27night (one GPU job at a time), then tools/r27_rules.py -> results/r27_night.txt.
set -u
cd ~/movie-buff/rag || exit 1
source ~/venvs/movie-rag/bin/activate 2>/dev/null
mkdir -p runs; LOG=runs/r33night-$(date +%Y%m%d-%H%M%S).txt
exec > >(tee -a "$LOG") 2>&1
echo "=== r33night $(date)"
while tmux has-session -t r27night 2>/dev/null; do echo "waiting for r27night $(date +%H:%M)"; sleep 300; done
L7=$(grep -v '^#' v7_lock.env | tr '\n' ' '); L8=$(grep -v '^#' v8_lock.env | tr '\n' ' ')
BASE="TOOLS=1 EXTRA_DATA=1 DATE_WORDS=1 $L7 $L8"
for arm in "r33personalite:PERSONA=cinemack PERSONA_SYSTEM=0" "r33ctx3:CTX_BUILDER=1 CTX_BUILDER_V=3 CTX_V3_SKIP_TV=1"; do
  T=${arm%%:*}; E=${arm#*:}
  for s in analytic:an reception:rc tv:tv; do
    f=${s%%:*}; k=${s##*:}
    echo "=== $T $f $(date +%H:%M)"; env $BASE $E python eval_v3.py --questions sets/$f.json --tag ${T}_$k --answers | tail -4
  done
done
python tools/r27_rules.py | tee results/r27_night.txt
echo "=== r33night done $(date)"
