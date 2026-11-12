# Hand checks from the v8 plan (graded 11 Nov 2026, 23:50)

**Rater: Claude (an AI model), not a person.** The plan asked for these checks to be graded by a person. The user
asked Claude to do them on 11 Nov, so they are recorded here as an AI second rater. They catch matcher and parser
errors well; they are not the human check the plan's judge rule assumed. The user can still overwrite any row (the
`right (y/n)` column) and re-run the counts.

## 1. Judge check (results/judge_check.html, 30 answers from the 3-4 Nov tuning runs)
- Claude graded the 30 answers blind to the judge on 10 Nov (results/judge_check_claude.csv): 25 pass / 5 fail
  (rows 3, 6, 18, 20, 22). Agreement with the gemma3:27b judge: **16/30 (53%)**. Kappa and the full disagreement list:
  results/judge_check_report.txt (`python tools/judge_compare.py`, round 35 adds kappa).
- Plan rule: >= 80% agreement AND kappa >= 0.6 -> **FAIL** (16/30 is below 80% whatever kappa is).
- What it means: the judge's pass counts cannot stand on their own. The v8 sealed run therefore reports the
  deterministic checks next to every judge number (final_score.py: right title named first / right year; same-name
  routing; invented-film "presents a film" check; judge_audit.py flags), as declared on 9 Nov. Judge-only results
  (analysis / reception) are reported as "judge, agreement with a second rater 53%".
- A person's grades would still strengthen this: open results/judge_check.html, export, then
  `python tools/judge_compare.py my_grades.csv` (prints judge vs you, Claude vs you, kappa, the rule).

## 2. YouTube video matches (results/video_match_check.csv -> results/video_match_check_claude.csv)
**35/50 right (70%); wrong 15/50 = 30% (95% CI 19-44%).** Every error is one pattern:
| Pattern | Rows | Example |
|---|---|---|
| franchise / series name matched to the first film | 1, 5, 7, 19, 25, 26, 28, 43 | "Star Wars: Rogue One - Trailer 3 Thoughts" -> Star Wars (1977) |
| video game with the film's name | 8, 20 | "Uncharted 4: A Thief's End - Game Review" -> Uncharted (2022) |
| remake / later version matched to the original | 18, 24, 45 | Kermode's Rebecca review (2020 Netflix) -> Rebecca (1940) |
| TV series | 21 | The Rings of Power -> The Lord of the Rings (1978) |
| different title sharing a word | 44 | "Best of the Worst: Wicked World" -> Wicked (2024) |
Effect today: limited - the YouTube line in answers is behind YT_COMMENTS=0 (off), and the prediction model's trailer
features come from the separate trailer search (fetch_free_data.py youtube), not from these channel matches. Anything
else that reads the channel matches (youtube_analysis.py match) inherits the 30% error. Fix proposed (not built tonight, would be checked on a NEW
sample): reject "Game Review", sequel numbers / subtitles that differ from the film's, "Episode(s)" / series words, and
when the upload date is far from the film's release prefer the film released nearest the upload.

## 3. Critic outlet names (results/critic_outlet_check.csv -> results/critic_outlet_check_claude.csv)
129 statements; 120 with an outlet found.
- **Outlet right 106, wrong 10, cannot be checked from the line 4** (book citations) -> wrong 10/116 checkable = **8.6%
  (95% CI 4.7-15%)**. One more line had no outlet but a wrong critic field (row 31: the director, not a critic).
- Of the 9 lines with no outlet found, 4 name one in the text (Harrison's Reports, Variety, Filmcritic.com, Rediff).
- Error patterns: the cited URL's site used when the line is someone else's (Rotten Tomatoes consensus -> IGN;
  Cinapse -> Comic Book Resources); publisher or host instead of the paper (Singapore Press Holdings for My Paper,
  Yahoo! Japan for Nikkei); critic field from the reference's author that does not match the line ("she" vs Francis
  Rizzo III; "Kho" vs "Stephanie Synder"); wiki-template text left in the outlet (row 74); a non-review line given an
  outlet (row 36).
- Effect: reception answers cite outlets from this table. About 1 in 12 outlet names is wrong, mostly on lines that
  are not reviews. A fix (prefer an outlet named in the line over the URL's site; drop template text; leave the critic
  empty when the line's pronoun or surname disagrees) can be checked on a new sample.

## 4. Decision-log corrections (proposed 9 Nov, recorded 11 Nov in docs/decision_log.md)
v7 sealed TV counts corrected for two judge false passes in every arm; CLARIFY -1 (not "no effect"); SHORTS +1 = noise;
RECEPTION / PROMPT_ORDER untested on the analysis set (not "no effect"). No switch changes: these correct the record.
