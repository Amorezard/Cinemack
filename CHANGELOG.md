2026-09-23  fix: re-ranker capped at 30 filtered candidates, closest first. Test set 33/34 -> 34/34; final s@1 0.91 -> 0.98; re-ranker right 26/30 -> 30/30; max candidates 400 -> 30
2026-09-23  TREC ToT 2023 dev baseline (84 questions with film in DB): in top 3 13/84 (15%), s@1 0.11; 40/84 never reach the pool; 16.6 s per question
2026-09-23  fix: year/genre are hints for descriptions. Tune TREC 9 -> 13/60, own 43/45; check TREC 6/24, own 5/5
