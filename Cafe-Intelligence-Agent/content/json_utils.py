"""
json_utils.py — LLM responses (Gemini and Claude both) sometimes wrap JSON in
markdown fences, add a leading "Here's the JSON:" line, or add trailing
whitespace/newlines the model itself introduced. A plain `json.loads()` on the
raw text is brittle against all of that. `extract_json()` tries progressively
more forgiving strategies before giving up, so a real API key's response
isn't discarded over a formatting quirk.
"""

from __future__ import annotations

import json
import re


def extract_json(text: str):
    """Returns the parsed JSON value (dict or list). Raises json.JSONDecodeError
    if nothing in the text parses, same as a plain json.loads() would, so
    callers' existing try/except json.JSONDecodeError fallback still works."""
    text = text.strip()

    # Strategy 1: as-is.
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Strategy 2: strip a ```json ... ``` or ``` ... ``` fence, wherever it is
    # (not just anchored at the very start/end — a model sometimes adds a
    # one-line preamble before the fence).
    fenced = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
    if fenced:
        try:
            return json.loads(fenced.group(1).strip())
        except json.JSONDecodeError:
            pass

    # Strategy 3: grab the outermost {...} or [...] span and parse that,
    # ignoring any stray prose before/after it.
    for open_ch, close_ch in (("{", "}"), ("[", "]")):
        start = text.find(open_ch)
        end = text.rfind(close_ch)
        if start != -1 and end != -1 and end > start:
            candidate = text[start:end + 1]
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                continue

    # Nothing worked — raise the original error so callers' existing
    # except json.JSONDecodeError handling still catches it.
    return json.loads(text)
