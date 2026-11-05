#!/usr/bin/env bash
# 4 Nov: start the Cinemack site WITH the locked v7 switches. config.yaml has no v7 switch lines, so a plain
# `python server.py` runs every v7 feature off (no TV, reception, shorts, memory-by-meaning, film-or-show choice).
#   bash start_site.sh                 # the live site, port 49151
#   PORT=49152 bash start_site.sh      # a test copy next to it (for smoke_site.py), same data, same GPU
set -u
cd ~/movie-buff/rag || exit 1
source ~/venvs/movie-rag/bin/activate
[ -f v7_lock.env ] || { echo "no v7_lock.env"; exit 1; }
L=$(grep -v '^#' v7_lock.env | tr '\n' ' ')
ENV="TOOLS=1 EXTRA_DATA=1 DATE_WORDS=1 $L"
echo "Cinemack on port ${PORT:-49151} with: $ENV"
[ -f results/mem_back_cos.txt ] && echo "memory cut-off (MEM_BACK_COS): $(cat results/mem_back_cos.txt)"
cd web && exec env $ENV MOVIE_BUFF_CODE="${MOVIE_BUFF_CODE:-movies}" PORT="${PORT:-49151}" python server.py
