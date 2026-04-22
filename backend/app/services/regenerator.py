"""P5 Regenerator — invariant split + post-regen value drift check.

Invariant blocks (eq, def, fig, example) are split out and copied verbatim.
Only free blocks (p, h3, kp, list) are sent to Claude. Results are merged
back in original positional order. Post-regen QC scans for numeric values
that drifted.
"""

from __future__ import annotations

import logging

from app.core.gemini_client import extract_text, messages_create
from app.schemas.regen import PostRegenQCResult, RegenParams, param_descriptors
from app.services.invariant_splitter import (
    merge_blocks_in_order,
    paragraphs_to_blocks,
    split_blocks,
)
from app.services.prompt_loader import render
from app.services.qc.helpers import blocks_to_plain_text, extract_numbers
from app.utils.json_parse import parse_json

logger = logging.getLogger(__name__)

MAX_TOKENS = 16000


def free_blocks_to_text(free_blocks: list[dict]) -> str:
    """Flatten free blocks into the plain-text body used in the P5 user message.

    Lists are wrapped in [LIST]...[/LIST] tags so the model knows to output
    them back as list_item blocks rather than folding them into body paragraphs.
    """
    parts: list[str] = []
    for b in free_blocks:
        t = b.get("t")
        if t == "p":
            parts.append(b.get("c", ""))
        elif t == "h3":
            parts.append(f"\n### {b.get('c', '')}\n")
        elif t == "kp":
            parts.append(f"[KEY POINT: {b.get('c', '')}]")
        elif t == "list":
            items = b.get("items", []) or []
            numbered = "\n".join(f"{i + 1}. {item}" for i, item in enumerate(items))
            parts.append(f"[LIST]\n{numbered}\n[/LIST]")
    return "\n\n".join(p for p in parts if p.strip())


def build_regen_system_prompt(params: RegenParams) -> str:
    return render("regenerator", **param_descriptors(params))


def build_user_message(section_id: str, section_title: str, free_text: str) -> str:
    return (
        "Regenerate the theory content for this section according to your system parameters.\n\n"
        f"SECTION ID: {section_id}\n"
        f"SECTION TITLE: {section_title}\n\n"
        "IMPORTANT RULES:\n"
        "- You are only receiving the REWRITABLE blocks (prose, key points, lists).\n"
        "- Equations, definitions, figures, and examples are handled separately — do NOT add or modify them.\n"
        "- Content wrapped in [LIST]...[/LIST] is a numbered list. "
        "You MUST output each item as a separate \"list_item\" block — NEVER merge list items into a body paragraph.\n"
        "- [KEY POINT: ...] blocks must be output as \"key_point\" type.\n"
        "- Preserve the number of list items exactly — do not add or remove items.\n\n"
        "REWRITABLE CONTENT TO REGENERATE:\n"
        f"{free_text}\n\n"
        "Return the regenerated JSON now. Output list items as list_item blocks, key points as key_point blocks, "
        "paragraphs as body blocks, and subheadings as heading blocks."
    )


async def regenerate_section(
    *,
    section_id: str,
    section_title: str,
    blocks: list[dict],
    params: RegenParams,
) -> list[dict]:
    """Regenerate one section with invariant split. Returns merged block list."""
    invariant_blocks, free_blocks = split_blocks(blocks)

    if not free_blocks:
        # Nothing to rewrite — return originals untouched
        return [dict(b) for b in blocks]

    system = build_regen_system_prompt(params)
    free_text = free_blocks_to_text(free_blocks)
    user_msg = build_user_message(section_id, section_title, free_text)

    response = await messages_create(
        max_tokens=MAX_TOKENS,
        system=system,
        messages=[{"role": "user", "content": user_msg}],
    )
    text = extract_text(response)
    data = parse_json(text)
    regen_paragraphs = list(data.get("paragraphs") or [])

    # Convert paragraphs → blocks, then KEEP ONLY free types. Any invariant type
    # Claude smuggled in is silently dropped (per the spec, case 4 edge case).
    from app.schemas.block import INVARIANT_TYPES

    regen_blocks_all = paragraphs_to_blocks(regen_paragraphs)
    regen_free = [b for b in regen_blocks_all if b.get("t") not in INVARIANT_TYPES]

    return merge_blocks_in_order(blocks, regen_free)


def post_regen_qc(original_blocks: list[dict], regenerated_blocks: list[dict]) -> PostRegenQCResult:
    """Value drift check — every >1-char numerical value in original must appear in regenerated."""
    orig_text = blocks_to_plain_text(original_blocks)
    regen_text = blocks_to_plain_text(regenerated_blocks).lower()

    orig_numbers = extract_numbers(orig_text)
    drifted = [n for n in orig_numbers if len(n) > 1 and n.lower() not in regen_text]

    return PostRegenQCResult(
        pass_=(len(drifted) == 0),
        drifted_values=drifted,
        original_number_count=len(orig_numbers),
    )
