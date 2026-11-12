"""Persona rename (v8 Should tier; built 11 Nov 2026, round 27). Switch: PERSONA=cinemack (default: unset = Movie Buff).

The site was renamed Cinemack on 4 Nov, but the prompts and the model's own system text still say "Movie Buff"
(left alone so the v7 sealed run measured the same prompts). With PERSONA=cinemack:
  * every "Movie Buff" in the messages sent to the answer model becomes "Cinemack" (the prompt lines chat.py,
    web/memory.py and tools.py write), at the one place messages leave for the model (with_system);
  * the answer model gets its Modelfile SYSTEM text with "Movie Buff" replaced by "Cinemack" as a system message
    (an Ollama system message replaces the Modelfile's SYSTEM for that call), so the model's own self-description
    changes too. If the SYSTEM text can't be read, only the prompt lines change.
Without the switch NAME is "Movie Buff" and with_system() returns the messages unchanged: byte-for-byte the old prompts.
Rule (validation matrix, written 11 Nov before any run): analytic, reception and TV sets (eval_v3 + judge) each lose
at most 1 pass, and the mean answer length stays within 15%.
"""
import os
import re

PERSONA = os.environ.get("PERSONA", "").strip().lower()
ON = PERSONA == "cinemack"
NAME = "Cinemack" if ON else "Movie Buff"
# round 33 (11 Nov): the full switch failed its rule (reception 37 -> 34 passes). PERSONA_SYSTEM=0 renames only the
# prompt lines the code writes and leaves the model's own SYSTEM text alone (no system message added).
SYSTEM = os.environ.get("PERSONA_SYSTEM", "1") == "1"
_SYS = {"text": None, "tried": False}


def rename(text):
    """'Movie Buff' (and 'MovieBuff', any case) -> 'Cinemack' when the switch is on."""
    if not ON or not text:
        return text
    return re.sub(r"(?i)\bmovie\s?buff\b", "Cinemack", text)


def system_text(model):
    """The model's Modelfile SYSTEM text, renamed; '' when unknown. Read once."""
    if not ON:
        return ""
    if not _SYS["tried"]:
        _SYS["tried"] = True
        try:
            import ollama
            info = ollama.show(model)
            s = (info.get("system") if isinstance(info, dict) else getattr(info, "system", None)) or ""
            if not s:                                      # older clients: SYSTEM only inside the modelfile text
                mf = (info.get("modelfile") if isinstance(info, dict) else getattr(info, "modelfile", None)) or ""
                m = re.search(r'^SYSTEM\s+"""(.*?)"""', mf, re.S | re.M) or re.search(r'^SYSTEM\s+"?(.*?)"?$', mf, re.M)
                s = m.group(1) if m else ""
            _SYS["text"] = rename(s.strip())
        except Exception as e:  # noqa: BLE001 - only the prompt lines change then
            print(f"[persona] could not read the system text of {model}: {e!r}", flush=True)
            _SYS["text"] = ""
    return _SYS["text"] or ""


def with_system(messages, model):
    """Messages for the answer model with the switch on: every message's "Movie Buff" renamed (the prompt lines
    chat.py, tools.py and web/memory.py write all pass through here), and the renamed SYSTEM text first."""
    if not ON:
        return messages
    out = [dict(m, content=rename(m.get("content") or "")) for m in messages]
    if not SYSTEM or any(m.get("role") == "system" for m in out):
        return out
    s = system_text(model)
    return ([{"role": "system", "content": s}] + out) if s else out
