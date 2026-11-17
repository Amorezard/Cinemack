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

## The history matters, so publish a clean copy

A file deleted in a later commit can still be downloaded from GitHub, and the private history holds sealed sets, an
old cache database and personal email addresses. So the public repository is a **separate clean copy**, made by one
command; the private repository (the live site runs from it) is never rewritten:

```bash
python tools/v81_public_export.py --out ~/movie-buff/cinemack-public --keys ~/movie-buff/rag/keys.env \
    --email <your GitHub noreply address> --holder "Your Name"
```

It copies the current files of the `v8.1` branch, applies the table above to every file (removes what must not be
published, replaces email addresses, home folders and any value from `keys.env`), adds `LICENSE` (MIT),
`DATA_SOURCES.md` and `docs/HISTORY.md` (every commit message of the private branch with its date, cleaned the
same way - the record of how it was built), makes one commit, and runs the publish check on the copy. It pushes
nothing. When the check says READY:

```bash
cd ~/movie-buff/cinemack-public
git remote add origin git@github.com:<your-user>/cinemack.git    # an EMPTY repository made on github.com
git push -u origin main
```

Run it again with `--replace` to refresh the copy later (it only replaces a folder it made itself).

## Also decide

- **The licence.** The copy uses MIT (chosen 17 Nov: others may copy and reuse the code with credit). It covers the
  code and documentation only; `DATA_SOURCES.md` says why the data is not included and whose terms apply.
- **Credits.** Keep the data-source credits (TMDB, JustWatch, Wikipedia CC BY-SA, MovieLens, Guardian, NYT): their
  terms require them even when the data itself is not published.
- **Course rules.** If this is coursework, check what may be shared publicly and when.
