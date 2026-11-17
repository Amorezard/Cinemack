# Putting it on GitHub

**In one sentence:** publish the code and the documentation, never the keys, the data, the sealed test sets or
anything about real people - and check the git history, not just the current files.

## Run the check first

```bash
python tools/v81_publish_check.py --history --deep
```

It lists *where* something sensitive is (file, line, kind), never the value, and changes nothing. "READY" means
nothing below is tracked now or in the history.

## What to do about each kind

| Kind | Why it must not be public | What to do |
| --- | --- | --- |
| secrets file (`keys.env`) | API keys let anyone spend your quotas | Never commit; it is in `.gitignore`. If one was ever committed, **change the key** at the provider - removing the file is not enough |
| key-like value | Same | Read keys from the environment, as the code already does |
| email address | Personal data | Replace with `you@example.com` in docs and comments; `MB_CONTACT` in `nightly.sh` should come from `keys.env` |
| sealed test set (`sets/sealed_*`) | Publishing ends their use as a blind test | Leave out until the final evaluation is done; publish the hashes instead |
| database file (`*.db`) | `facts.db` holds licensed data (TMDB, IMDb, Guardian, NYT, OMDb) whose terms do not allow redistribution; site databases hold accounts and chats | Never publish; `REPRODUCE.md` explains how to rebuild |
| model / data file (`*.gguf`, `*.zip`, large files) | Size, and licences (MovieLens: no redistribution) | Link to the source instead |
| personal data (`logs/`, chats, accounts) | Real people's data | Never publish |

## The history matters

A file deleted in a later commit can still be downloaded from GitHub. If the check lists sensitive files in old
commits, the simplest safe route is a **fresh repository** from the current files:

```bash
mkdir ../cinemack-public && git archive HEAD | tar -x -C ../cinemack-public
cd ../cinemack-public && git init && git add . && python tools/v81_publish_check.py && git commit -m "Cinemack"
```

## Also decide

- **A licence.** Without a `LICENSE` file, people can read the code but have no right to reuse it. MIT or Apache-2.0
  are common for research code; check with your supervisor or university first.
- **Credits.** Keep the data-source credits from the main `README.md` (TMDB, JustWatch, Wikipedia CC BY-SA, MovieLens,
  Guardian, NYT): their terms require them even when the data itself is not published.
- **Course rules.** If this is coursework, check what may be shared publicly and when.
