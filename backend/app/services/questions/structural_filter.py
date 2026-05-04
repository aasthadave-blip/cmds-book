"""Post-extraction structural filter — reject items that are not actually questions.

The Gemini extractor is permissive by design (we'd rather it tag too much than
miss things), so we apply a mechanical filter on the way out. An item must
satisfy at least one positive question-marker AND none of the hard exclusions
to survive.

Each rejection records a reason so the diagnostic UI can explain "why did this
disappear" without the user having to re-OCR by hand.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

# Minimum length below which "questions" are almost always headings, page
# numbers, or fragments. "1." or "Q.3" alone is not a question.
MIN_RAW_TEXT_LEN = 15

# Items that pass through the LLM but are almost never real questions.
_EXCLUDE_PHRASES = (
    "did you know",
    "fun fact",
    "remember box",
    "learning objective",
    "after this section",
    "vocabulary list",
    "glossary",
    "answer key",
    "answers to exercises",
    # Crossword / puzzle indicators that the prompt should have excluded but
    # might slip through if the LLM ignores its instructions.
    "across:",
    "down:",
    "1 across",
    "1 down",
)

# Numbered-question prefixes the OCR commonly emits.
_NUMBER_PREFIX = re.compile(
    r"""^\s*
        (?:
            q[\s\.]*\d              # Q.3, Q 3, Q3
          | question\s+\d           # Question 5
          | exercise\s+\d           # Exercise 8.2
          | example\s+\d            # Example 4
          | problem\s+\d            # Problem 3
          | \d+\s*[\.\):]           # 1.  1)  1:
          | \(\s*[a-z0-9]+\s*\)     # (a) (1) (iii)
          | [a-z]\s*[\.\):]         # a.  a)
        )
    """,
    re.IGNORECASE | re.VERBOSE,
)

# Imperative verbs that strongly indicate a question/prompt.
_IMPERATIVE_VERB = re.compile(
    r"\b(find|calculate|show|prove|determine|evaluate|solve|state|"
    r"explain|describe|derive|compute|estimate|deduce|verify|"
    r"identify|match|name|define|list|sketch|draw|plot|"
    r"choose|select|which|what|why|how|where|when|who|whom|"
    r"is|are|do|does|did|can|could|would|will)\b",
    re.IGNORECASE,
)


@dataclass
class FilterResult:
    kept: list[dict[str, Any]]
    rejected: list[dict[str, Any]]  # original item + "_reject_reason"


def _looks_like_question(raw_text: str) -> tuple[bool, str]:
    """Return (is_question, reason_if_not).

    A line counts as a question when ANY of these are true:
        - has a numbered/lettered prefix (1., Q.3, (a), Exercise 8.2, …)
        - contains a question mark
        - starts with an imperative verb in the first 10 words
    """
    text = (raw_text or "").strip()
    if len(text) < MIN_RAW_TEXT_LEN:
        return False, f"too short ({len(text)} chars, need ≥{MIN_RAW_TEXT_LEN})"

    lower = text.lower()
    for phrase in _EXCLUDE_PHRASES:
        if phrase in lower:
            return False, f"contains excluded phrase: '{phrase}'"

    if _NUMBER_PREFIX.match(text):
        return True, ""
    if "?" in text:
        return True, ""
    # Check the first ~10 words for an imperative verb. Beyond that, the verb
    # is probably part of the body, not the prompt itself.
    head = " ".join(text.split()[:10])
    if _IMPERATIVE_VERB.search(head):
        return True, ""

    return False, "no question markers (number, '?', or imperative verb)"


def filter_items(items: list[dict[str, Any]]) -> FilterResult:
    """Split LLM-emitted items into kept vs rejected with reasons.

    The contract: kept items keep their original shape; rejected items get an
    extra ``_reject_reason`` key so the UI can explain the drop.
    """
    kept: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for item in items:
        raw = (item or {}).get("raw_text", "")
        ok, reason = _looks_like_question(raw)
        if ok:
            kept.append(item)
        else:
            rejected.append({**item, "_reject_reason": reason})
    return FilterResult(kept=kept, rejected=rejected)
