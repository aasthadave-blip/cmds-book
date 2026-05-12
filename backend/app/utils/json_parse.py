"""Robust JSON extractor for Claude responses that may include stray prose,
markdown fences, or trailing commas.
"""

from __future__ import annotations

import json
import re
from typing import Any


def _strip_markdown_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        # Drop the opening fence line (```json or ```)
        text = re.sub(r"^```[a-zA-Z0-9_-]*\n", "", text)
        if text.endswith("```"):
            text = text[: -len("```")]
    return text.strip()


def _remove_trailing_commas(text: str) -> str:
    return re.sub(r",(\s*[}\]])", r"\1", text)


# JSON only recognises these escape characters after a backslash:
#   "  \  /  b  f  n  r  t  u
# Anything else (e.g. \frac, \times, \pi, \v) is invalid and breaks json.loads.
# Gemini Flash often emits raw LaTeX inside string values, producing exactly
# this class of error. We double-escape any unknown backslash so the JSON parser
# treats it as a literal "\f" / "\frac" / etc. inside the string.
_VALID_JSON_ESCAPES = set('"\\/bfnrtu')


def _escape_invalid_backslashes(text: str) -> str:
    out: list[str] = []
    in_str = False
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if not in_str:
            if ch == '"':
                in_str = True
            out.append(ch)
            i += 1
            continue
        # inside a string
        if ch == '"':
            in_str = False
            out.append(ch)
            i += 1
            continue
        if ch == "\\" and i + 1 < n:
            nxt = text[i + 1]
            if nxt in _VALID_JSON_ESCAPES:
                out.append(ch)
                out.append(nxt)
                i += 2
                continue
            # Invalid escape — double the backslash so the parser sees a
            # literal backslash + char (e.g. "\frac" → "\\frac").
            out.append("\\\\")
            i += 1
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def extract_json(text: str) -> str:
    """Return the first balanced JSON object or array substring in ``text``."""
    text = _strip_markdown_fences(text)
    # Find the first { or [ and match braces
    start = None
    for i, ch in enumerate(text):
        if ch in "{[":
            start = i
            break
    if start is None:
        raise ValueError("No JSON object/array found in text")

    open_ch = text[start]
    close_ch = "}" if open_ch == "{" else "]"
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        else:
            if ch == '"':
                in_str = True
            elif ch == open_ch:
                depth += 1
            elif ch == close_ch:
                depth -= 1
                if depth == 0:
                    return text[start : i + 1]
    raise ValueError("Unbalanced JSON in text")


def parse_json(text: str) -> Any:
    """Best-effort parse: strip fences, trim to balanced braces, remove trailing
    commas, and tolerate raw LaTeX (\\frac, \\times, …) by escaping unknown
    backslashes inside string values."""
    candidate = extract_json(text)
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        pass
    try:
        return json.loads(_remove_trailing_commas(candidate))
    except json.JSONDecodeError:
        pass
    # Final attempt — fix LaTeX backslashes that aren't valid JSON escapes
    fixed = _escape_invalid_backslashes(candidate)
    try:
        return json.loads(fixed)
    except json.JSONDecodeError:
        return json.loads(_remove_trailing_commas(fixed))
