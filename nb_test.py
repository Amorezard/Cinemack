import json, random, time, urllib.request
URL = "http://localhost:11434/api/generate"
BASE = ("A retired detective returns to a small coastal town where a string of "
        "disappearances matches a case he never solved. ") * 180
def run(nb):
    body = {"model": "movie-buff", "prompt": f"[{random.random()}] " + BASE,
            "stream": False, "options": {"num_predict": 1}}
    if nb: body["options"]["num_batch"] = nb
    t = time.time()
    req = urllib.request.Request(URL, json.dumps(body).encode(), {"Content-Type": "application/json"})
    r = json.load(urllib.request.urlopen(req))
    tok, ms = r["prompt_eval_count"], r["prompt_eval_duration"] / 1e6
    print(f"num_batch={nb or 'default':>7}  tokens={tok}  read={ms:6.0f} ms  ({tok/ms*1000:4.0f} tok/s)  wall={time.time()-t:5.1f} s")
for nb in [None, None, 1024, 1024, 2048, 2048]:
    run(nb)
