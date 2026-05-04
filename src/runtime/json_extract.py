"""Shared tolerant JSON extraction for LLM responses.

Native structured output should be preferred. This helper is the fallback for
providers/components that still return JSON-ish text.
"""

from __future__ import annotations

import json
import re
from typing import Any

_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\}|\[.*?\])\s*```", re.DOTALL)


def extract_json(raw: str) -> Any | None:
    text = (raw or "").strip()
    if not text:
        return None

    fenced = _FENCE_RE.search(text)
    if fenced:
        parsed = _try_json(fenced.group(1))
        if parsed is not None:
            return parsed

    parsed = _try_json(text)
    if parsed is not None:
        return parsed

    return _extract_balanced_json(text)


def _try_json(text: str) -> Any | None:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _extract_balanced_json(text: str) -> Any | None:
    starts = [i for i, ch in enumerate(text) if ch in "{["]
    for start in starts:
        opening = text[start]
        closing = "}" if opening == "{" else "]"
        depth = 0
        in_string = False
        escape = False
        for idx in range(start, len(text)):
            ch = text[idx]
            if in_string:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_string = False
                continue
            if ch == '"':
                in_string = True
            elif ch == opening:
                depth += 1
            elif ch == closing:
                depth -= 1
                if depth == 0:
                    parsed = _try_json(text[start:idx + 1])
                    if parsed is not None:
                        return parsed
                    break
    return None
