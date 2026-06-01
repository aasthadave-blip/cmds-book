"""Recap rules for theory regen v3.

Each rule describes a recap-style insertion (Fun Fact / Food for Thought /
Points to Remember) the regenerator can weave into the rewritten section.

Rules are OPT-IN only — a regen request must pass `recap_rule_ids=[...]`
to activate any of them. With an empty list (the default), behavior is
identical to v1.

This file is consumed by:
  - app.schemas.regen.param_descriptors → renders {recap_rules_block}
    and {recap_active_ids} into the v3 prompt.
  - GET /api/recap-rules → exposes the catalog to the frontend so the
    UI can render checkboxes.

Reversibility: deleting this file + reverting the schema + regenerator
changes returns the system to exact v1 behavior. The v1 prompt does not
reference any recap placeholders.
"""

from __future__ import annotations

from typing import Any

RECAP_RULES: list[dict[str, Any]] = [
    {
        "id": "fun_fact",
        "label": "Fun Fact",
        "source_patterns": ["Konnect", "Did you know", "Fun Fact"],
        "mode": "append_per_section",
        "embed_as": "key_point",
        "description": (
            "Surface a Konnect / Did-You-Know style nugget at the end of "
            "each section as a key-point block. Use only facts present in "
            "the original section — never invent."
        ),
    },
    {
        "id": "food_for_thought",
        "label": "Food for Thought",
        "source_patterns": ["Info Bytes", "Food for Thought", "Think About"],
        "mode": "append_per_section",
        "embed_as": "key_point",
        "description": (
            "Add an 'Info Bytes' / Food-for-Thought provocation at the end "
            "of each section as a key-point block. Derived from the source "
            "section content; do not introduce new facts."
        ),
    },
    {
        "id": "points_to_remember",
        "label": "Points to Remember",
        "source_patterns": ["Points to Remember", "Key Takeaways", "Summary"],
        "mode": "split_distribute",
        "embed_as": "key_point",
        "description": (
            "Distribute the chapter-end 'Points to Remember' across the "
            "relevant sections as key-point blocks (anti-plagiarism: avoid "
            "verbatim copying — paraphrase each point)."
        ),
    },
]


def get_rule(rule_id: str) -> dict[str, Any] | None:
    for r in RECAP_RULES:
        if r["id"] == rule_id:
            return r
    return None


def render_recap_block(active_ids: list[str]) -> str:
    """Render the {recap_rules_block} substitution for the v3 prompt.

    Returns an empty string when no rules are active so the prompt section
    collapses cleanly.
    """
    if not active_ids:
        return ""

    lines: list[str] = ["ACTIVE RECAP RULES:"]
    for rid in active_ids:
        rule = get_rule(rid)
        if rule is None:
            continue
        patterns = ", ".join(f'"{p}"' for p in rule["source_patterns"])
        lines.append(
            f"- [{rule['id']}] {rule['label']} "
            f"(mode={rule['mode']}, embed_as={rule['embed_as']})\n"
            f"  Sources: {patterns}\n"
            f"  Rule: {rule['description']}"
        )
    return "\n".join(lines)


def render_active_ids(active_ids: list[str]) -> str:
    """Render the comma-joined id list for the prompt header."""
    return ", ".join(active_ids) if active_ids else "none"
