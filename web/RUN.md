# Running the Movie Buff website

Three files: `server.py` (the web server), `pipeline.py` (connects it to your
existing chat.py / retrieve.py) and `index.html` (the page). They go in
`~/movie-buff/rag/web/` on the server.

## 1. Copy the folder to the server (PowerShell, on your laptop)

```
cd "C:\Users\blazi\Documents\School Projects\Research Project"
scp -r web you@example.edu:~/movie-buff/rag/
```

## 2. Install the two web packages (server)

```
source ~/venvs/movie-rag/bin/activate
pip install fastapi uvicorn
```

## 3. Test mode first: no GPU, fake answers (server)

This checks that the department's port actually reaches your server before the
real model is involved.

```
cd ~/movie-buff/rag/web
MOVIE_BUFF_MOCK=1 MOVIE_BUFF_CODE=test python server.py
```

Open **REDACTED** in your browser (use whatever hostname
the department gave you, if it's different), enter `test` as the code, and
click an example. The pill at the top should say "Ready (test mode)".
Ctrl+C to stop.

## 4. The real thing

Pick a code to share with your professor. It isn't a real password: the site
is plain HTTP, so don't reuse one you use anywhere else.

```
cd ~/movie-buff/rag/web
MOVIE_BUFF_CODE=your-code-here python server.py
```

It loads both models into the GPU at startup and prints
`[movie-buff] models warm` when they're ready (about 30 s).

Check that it's the same pipeline as your eval: ask the Parasite example
question. The answer should name Parasite and show it first under "From the
database".

## 5. Keep it running after you log out

```
tmux new -s moviebuff
cd ~/movie-buff/rag/web && source ~/venvs/movie-rag/bin/activate
MOVIE_BUFF_CODE=your-code-here python server.py
```

Detach with **Ctrl+B, then D**. Come back later with `tmux attach -t moviebuff`.
Stop it by attaching and pressing Ctrl+C.

The models stay in GPU memory for 30 minutes after the last question
(`KEEP_ALIVE` in chat.py), then free the GPU for everyone else on the box. The
first question after an idle stretch takes longer while they reload.

## What the page does

- Streams the answer as it's written, with "Finding the right films…" while
  retrieval and the re-ranker run.
- Shows the films retrieved for each answer ("From the database"). When nothing
  matched, it says the answer came from the model's memory.
- Follow-up buttons ("Who directed it?") use the same follow-up resolution as
  chat.py.
- **Reasoning** switch = `/think`. **Details** switch shows how each answer
  was found (the model's title guesses, which film the re-rank picked, how a
  follow-up was resolved, any year/genre filters) plus retrieval time, time to
  first words and total time. Good for showing the pipeline to your professor.
- One question at a time on the GPU. A second person sees "1 question ahead of
  yours". Stop (or closing the tab) ends generation right away.

## If something's wrong

| Symptom | Check |
|---|---|
| Browser can't load the page at all | Is server.py running? Is the port open to where you are (campus network or VPN only?) Test on the server itself: `curl localhost:49151/api/health` |
| `address already in use` | Something else has 49151: `ss -ltnp \| grep 49151`. Often an old copy of server.py still running in tmux. |
| Pill says "Model offline" | `ollama list` should show `movie-buff`. Hover the pill to see the error. |
| "The movie database couldn't be opened" | movies.db must be in `~/movie-buff/rag/` (one level above `web/`). |
| Answers differ from `chat.py` | They shouldn't: both call `chat.build_prompt`. If chat.py changes how it's called, update `prepare()` in pipeline.py. |
