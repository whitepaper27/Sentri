"""Robust JSON extraction from LLM responses.

LLMs (especially Gemini) often return preamble text, markdown fences, or
mixed text+JSON even when asked for "ONLY JSON".  This module provides a
single utility that both the Researcher and Judge parsers use.
"""

from __future__ import annotations

import json
import re
from typing import Any

_FENCE_RE = re.compile(r"```(?:json)?\s*\n(.*?)```", re.DOTALL)


def extract_json_from_text(text: str) -> Any | None:
    """Extract a JSON array or object from text that may contain preamble.

    Tries in order:
      1. Strip markdown code fences and parse
      2. Direct ``json.loads``
      3. Bracket-match ``[ ... ]`` (array)
      4. Bracket-match ``{ ... }`` (single object)

    Returns the parsed Python object, or ``None`` if no valid JSON found.
    """
    if not text or not text.strip():
        return None

    text = text.strip()

    # 1. Strip markdown fences
    fence_match = _FENCE_RE.search(text)
    if fence_match:
        fenced = fence_match.group(1).strip()
        try:
            return json.loads(fenced)
        except json.JSONDecodeError:
            pass

    # 2. Direct parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # 3. Bracket-match [ ... ] then { ... }
    for open_ch, close_ch in [("[", "]"), ("{", "}")]:
        start = text.find(open_ch)
        if start < 0:
            continue
        depth = 0
        for i in range(start, len(text)):
            if text[i] == open_ch:
                depth += 1
            elif text[i] == close_ch:
                depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start : i + 1])
                except json.JSONDecodeError:
                    pass
                break

    return None
