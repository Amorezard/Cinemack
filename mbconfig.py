"""
Settings from config.yaml, applied as environment defaults.

Every setting in this project is read from an environment variable (WIKI, RERANK_N, VEC_MEM,
...). config.yaml lists the same names with the values to use when the shell does not set them:

    shell variable set?  -> the shell wins (so VEC_MEM=0 python stage_scores.py ... still works)
    otherwise            -> the value in config.yaml
    not in config.yaml   -> the default written in the code

retrieve.py calls apply() when it is imported, so chat.py, stage_scores.py, the website and
every script that imports retrieve see the same settings.

    MB_CONFIG=/path/other.yaml   use another file
    MB_CONFIG=none               ignore config files entirely (code defaults + shell only)

v4 (14 Oct): live_model.yaml (next to config.yaml, not in git), written by rebuild_adapter.sh when a
new adapter passes its test, overrides config.yaml for the keys it holds (ANSWER_MODEL); the shell
still wins over both. Delete the file (or `rebuild_adapter.sh rollback`) to go back to config.yaml.

The file format is a small subset of YAML: `KEY: value` lines, optionally grouped under
section lines (`search:`) that are only for reading. Comments start with #. Keys are the
upper-case variable names; values are single words or numbers (quotes optional).
"""
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_PATH = os.path.join(HERE, "config.yaml")
LIVE_PATH = os.environ.get("MB_LIVE", os.path.join(HERE, "live_model.yaml"))

_LINE = re.compile(r"^\s*([A-Z][A-Z0-9_]*)\s*:\s*(.*?)\s*$")
_SECTION = re.compile(r"^[a-z][a-z0-9_]*\s*:\s*$")

# What apply() did, for `python mb.py settings`: name -> (value, "shell" | "config")
applied = {}
path_used = None


def path():
    p = os.environ.get("MB_CONFIG", "")
    if p.lower() == "none":
        return None
    return p or DEFAULT_PATH


def _strip_comment(v):
    out, quote = [], None
    for ch in v:
        if quote:
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
        elif ch == "#":
            break
        out.append(ch)
    return "".join(out).strip()


def load(p=None):
    """{KEY: "value"} from a config file (empty if there is none). Raises ValueError on a bad line."""
    p = p or path()
    if not p or not os.path.exists(p):
        return {}
    vals = {}
    with open(p, encoding="utf-8") as f:
        for n, raw in enumerate(f, 1):
            line = raw.rstrip("\n")
            if not line.strip() or line.lstrip().startswith("#") or _SECTION.match(line):
                continue
            m = _LINE.match(line)
            if not m:
                raise ValueError(f"{p}, line {n}: expected 'KEY: value', got {line.strip()!r}")
            v = _strip_comment(m.group(2))
            if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
                v = v[1:-1]
            if v.lower() in ("true", "yes", "on"):
                v = "1"
            elif v.lower() in ("false", "no", "off"):
                v = "0"
            if m.group(1) in vals:
                raise ValueError(f"{p}, line {n}: {m.group(1)} is set twice")
            vals[m.group(1)] = v
    return vals


def apply():
    """Set every config value the shell has not already set. Safe to call more than once."""
    global path_used
    if path_used is not None:
        return applied
    p = path()
    path_used = p or ""
    vals = {k: (v, "config") for k, v in load(p).items()}
    if p and LIVE_PATH and os.path.exists(LIVE_PATH):          # MB_CONFIG=none skips it too
        vals.update({k: (v, "live_model.yaml") for k, v in load(LIVE_PATH).items()})
    for k, (v, src) in vals.items():
        if k in os.environ:
            applied[k] = (os.environ[k], "shell")
        else:
            os.environ[k] = v
            applied[k] = (v, src)
    return applied
