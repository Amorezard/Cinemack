# Engineering: how changes ship, and why it is built this way

**In one sentence:** all new work happens in a separate copy of the code behind off switches, arrives as one guarded
"round" per command, and never touches the live site without an explicit yes.

## Two copies of the code

| Folder | Branch | Purpose |
| --- | --- | --- |
| `~/movie-buff/rag` | `v8` at tag `v8.0-lock` (929bbbf) | The live site. Frozen. |
| `~/movie-buff/rag-v81` | `v8.1` (git worktree) | All v8.1 work, with its own copy of `facts.db` |

Every round prints the live commit and the site's status at the end to prove they did not change.

## A round

1. Code is written and tested in a separate workspace against a snapshot of the source, with made-up databases.
2. The round's script runs on a **fake server** first: install, run again (must only collect), and failure cases
   (must leave nothing behind).
3. `todo.sh` and `outbox/rNN.tar.gz` go into the laptop folder; `sync.cmd` copies them over SSH, runs `todo.sh`,
   and brings back `todo_out.txt` and a results archive.
4. **Each slice is guarded:** package checksum, expected parent commit, clean working tree, new files absent, edited
   files byte-identical to what was expected. Then its tests run and it commits. A failure removes its files and puts
   edited files back; other slices still install.
5. Long jobs run in `tmux` and commit their own results. Running the sync again only collects.

## Switches

Every v8.1 feature has a new switch, off by default: `MEDIA_IDENTITY`, `QUERY_PLAN`, `QUERY_PLAN_SHADOW`,
`RECS_V2`, `REC_EVENTS`, `FORECAST_PANEL`, `GUARDIAN_LIVE`, `TOOL_ROUTER`, `I18N`, `DEALS`. While a switch is off its
routes answer 404 exactly as if they did not exist, and its page code is never loaded.

Always needs an explicit yes from the owner: installing on or restarting the live site, sealed runs, long GPU jobs,
real email, paid data, deleting or migrating a live database, opening ports 80/443.

## The Guardian allowance (500 calls a day)

- Decided 16 Nov: about 20 calls a day for lookups when someone opens a film; every result is stored, so a film is
  looked up once. Bulk work (new reviews, then historical press) spends the rest.
- Once the daily reset time is known, bulk work moves to just before the reset and spends every call left.
- Finding the reset time is harder than expected: single probes read 376, 499, 498, 499, 497, 498, 499 in that order,
  which only makes sense if the Guardian's servers each keep their own count. The live lookup therefore trusts the
  **lowest** recent reading, and the probe takes bursts of 5 calls around midnight UTC to test the usual reset time.

## Design choices and why

| Choice | Why | Given up |
| --- | --- | --- |
| Answers grounded in retrieved records | Facts change and models misremember; records can be cited and checked | Answers depend on retrieval |
| Local open models | No per-question cost, data stays on the server, repeatable | One shared GPU |
| Four searches fused with RRF | Each search fails on different questions; rank fusion needs no score tuning | More compute |
| Hard limits as a validated plan compiled to SQL | A rule in a prompt can be talked around; a filtered-out film cannot reach the model | Each limit needs code and a reference test |
| `movie:` / `tv:` ids | 1,072 TMDB numbers are shared by a film and a show | Migration of older tables |
| Judge from another model family + deterministic checks | Self-grading is lenient; the judge missed wrong-title answers | Two large models share the GPU, so evaluation runs at night |
| Rules before runs, sealed sets | Stops choosing the metric after seeing the result | Some good ideas stay off |
| Every feature behind an off switch | Attributable effects, instant rollback | More configuration |
| Log-ratio target, conformal ranges | Hits and flops are multiplicative; "80%" should mean 80% | Wide ranges, shown honestly |
| Never "profit" | Gross over budget leaves out marketing and cinema shares | A less exciting headline |
| Typed tool router, no free web access | Fetched text can carry instructions; free URLs can reach internal services | Each new source needs its own tool |
| Worktree + guarded rounds | The live site never changes by accident | Slower, hand-run deployment |

## Known limits and open work

No HTTPS; shared GPU; judge agreement unproven against a human; YouTube trailer matching was about 30% wrong before
the round 46 rules (fix awaiting grading); critic-outlet attribution 8.6% wrong; a long-chat regression (`tl05`);
Spanish text unreviewed; deals blocked on a named official source; retrieval refresh and load tests need the GPU.
