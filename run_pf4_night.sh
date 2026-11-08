#!/usr/bin/env bash
# 6 Nov night: Wikipedia headlines back for the prediction model + Letterboxd ratings, then the pf4 refit.
# CPU + network, plus small embedding calls (mxbai) on the GPU next to the live site. No eval runs, no answer changes:
# PERF=0 in the lock, so perf_film is not used by any answer.
#   tmux new -s pf4 ; cd ~/movie-buff/rag && bash run_pf4_night.sh            # ~2-4 h
# Steps (any can be named on the command line):  wiki embed films_src letterboxd fit report
set -u
cd ~/movie-buff/rag || exit 1
source ~/venvs/movie-rag/bin/activate
LOG=results/pf4_night.log
STEPS=${*:-wiki embed films_src letterboxd fit report}
LB_KAGGLE=${LB_KAGGLE:-$HOME/letterboxd-movie-ratings-data.zip}
exec > >(tee -a "$LOG") 2>&1
has() { [[ " $STEPS " == *" $1 "* ]]; }
echo "=== run_pf4_night.sh $(date)   steps: $STEPS"
if has wiki; then
  echo "=== Wikipedia current-events headlines, 1960-now (resumes; months already fetched are skipped) $(date +%H:%M)"
  python news.py fetch --source wiki --from 1960 --to 2026
fi
if has embed; then echo "=== headline vectors $(date +%H:%M)"; python news.py embed; fi
if has films_src; then echo "=== news per source per film $(date +%H:%M)"; python news.py films-src; fi
if has letterboxd; then
  echo "=== Letterboxd $(date +%H:%M)"
  python -c "import huggingface_hub, pyarrow" 2>/dev/null || pip install -q huggingface_hub pyarrow
  python letterboxd_data.py load --hf || echo "(Hugging Face set not loaded - see the error above)"
  if [ ! -f "$LB_KAGGLE" ] && command -v kaggle >/dev/null && [ -f ~/.kaggle/kaggle.json ]; then
    kaggle datasets download -d samlearner/letterboxd-movie-ratings-data -p "$(dirname "$LB_KAGGLE")"
  fi
  if [ -f "$LB_KAGGLE" ]; then python letterboxd_data.py load --kaggle "$LB_KAGGLE"
  else echo "(Kaggle set not found at $LB_KAGGLE: download it, then  bash run_pf4_night.sh letterboxd fit report)"; fi
fi
if has fit; then
  echo "=== pf4 refit $(date +%H:%M)"
  cp -n results/performance_summary.txt results/performance_summary_pf3.txt 2>/dev/null
  python - <<'PY'
import json, sqlite3
c = sqlite3.connect("facts.db")
try:
    rows = c.execute("select * from perf_film").fetchall()
    meta = c.execute("select key, value from perf_meta").fetchall()
    json.dump({"perf_film": rows, "perf_meta": meta}, open("results/perf_pf3_backup.json", "w"))
    print(f"backup: results/perf_pf3_backup.json ({len(rows):,} films)")
except sqlite3.Error as e:
    print("no pf3 tables to back up:", e)
PY
  python performance.py fit | tail -40
fi
if has report; then
  echo "=== summaries $(date +%H:%M)"
  python letterboxd_data.py report || true
  grep -E "pf4|before release|reception|news" results/performance_summary.txt
fi
echo "=== finished $(date)"
