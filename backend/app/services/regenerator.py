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

    Each block is wrapped in explicit [BLOCK N type=X]…[/BLOCK N] markers so
    the LLM knows EXACTLY how many blocks to produce and what type each one
    must be. Without these markers the model frequently collapsed multiple
    body paragraphs into one, which caused the downstream merge to leave
    equation invariants visually clustered at the end of the section.

    Lists are still wrapped in [LIST]...[/LIST] inside the block so the model
    knows to output them back as list_item entries rather than folding into
    a body paragraph.
    """
    parts: list[str] = []
    for idx, b in enumerate(free_blocks, start=1):
        t = b.get("t")
        if t == "p":
            parts.append(
                f"[BLOCK {idx} type=body]\n{b.get('c', '')}\n[/BLOCK {idx}]"
            )
        elif t == "h3":
            parts.append(
                f"[BLOCK {idx} type=heading]\n{b.get('c', '')}\n[/BLOCK {idx}]"
            )
        elif t == "kp":
            parts.append(
                f"[BLOCK {idx} type=key_point]\n{b.get('c', '')}\n[/BLOCK {idx}]"
            )
        elif t == "list":
            items = b.get("items", []) or []
            numbered = "\n".join(f"{i + 1}. {item}" for i, item in enumerate(items))
            parts.append(
                f"[BLOCK {idx} type=list]\n[LIST]\n{numbered}\n[/LIST]\n[/BLOCK {idx}]"
            )
    return "\n\n".join(p for p in parts if p.strip())


def build_regen_system_prompt(params: RegenParams) -> str:
    return render("regenerator", **param_descriptors(params))


def build_user_message(section_id: str, section_title: str, free_text: str) -> str:
    # Count the input [BLOCK N ...] markers so we can tell the LLM exactly
    # how many blocks it MUST produce. This stops the silent-omission bug
    # where the model collapsed 5 paragraphs into 2 and equations downstream
    # bunched at the end.
    block_count = free_text.count("[BLOCK ")
    return (
        "Regenerate the theory content for this section according to your system parameters.\n\n"
        f"SECTION ID: {section_id}\n"
        f"SECTION TITLE: {section_title}\n\n"
        "IMPORTANT RULES:\n"
        "- You are only receiving the REWRITABLE blocks (prose, key points, lists).\n"
        "- Equations, definitions, figures, and examples are handled separately — do NOT add or modify them.\n"
        f"- The input below contains EXACTLY {block_count} blocks tagged "
        "[BLOCK 1 type=…] through [BLOCK N type=…]. Your output JSON's \"paragraphs\" array MUST contain "
        f"EXACTLY {block_count} entries (or {block_count} entries with each list expanded into N list_item entries).\n"
        "- Map [BLOCK i type=body]   → output[i].type = \"body\"\n"
        "- Map [BLOCK i type=heading] → output[i].type = \"heading\"\n"
        "- Map [BLOCK i type=key_point] → output[i].type = \"key_point\"\n"
        "- Map [BLOCK i type=list]   → emit one list_item block per numbered item (preserve count exactly).\n"
        "- DO NOT merge two consecutive body blocks into one. DO NOT skip any block.\n"
        "- Content wrapped in [LIST]...[/LIST] is a numbered list. Output each item as a separate "
        "\"list_item\" block — NEVER merge list items into a body paragraph.\n"
        "- [KEY POINT: ...] blocks must be output as \"key_point\" type.\n\n"
        "REWRITABLE CONTENT TO REGENERATE:\n"
        f"{free_text}\n\n"
        "Return the regenerated JSON now. Preserve block count and order exactly."
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
