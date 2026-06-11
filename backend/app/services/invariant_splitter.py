"""Convert P4 paragraph dicts into canonical Block dicts.

Theory Worker Unit 2: ``paragraphs_to_blocks`` is now a thin shim over
``block_normalizer.normalize_blocks`` — the single source of truth for
the per-block validation, type aliasing (Body → p, etc.), and LaTeX
table conversion. The shim preserves backward compatibility for callers
that just want the cleaned blocks; new callers should use
``normalize_blocks`` directly to receive telemetry alongside the blocks.

Invariant split (INVARIANT_TYPES never sent to Claude during regeneration)
is provided here too — see split_blocks() / merge_blocks_in_order().
"""

from __future__ import annotations

from app.schemas.block import INVARIANT_TYPES
from app.services.block_normalizer import normalize_blocks


def paragraphs_to_blocks(paragraphs: list[dict]) -> list[dict]:
    """Convert P4-style paragraphs into canonical Block dicts.

    Backward-compat shim. Delegates to ``block_normalizer.normalize_blocks``
    (Unit 2) which handles unknown-type coercion (preserving content
    instead of silent drops), per-type validation, and LaTeX table emission.

    Callers needing drop telemetry should call ``normalize_blocks`` directly.
    """
    return normalize_blocks(paragraphs or []).blocks


def split_blocks(
    blocks: list[dict],
) -> tuple[list[dict], list[dict]]:
    """Return (invariant_blocks, free_blocks) preserving order."""
    invariant = [b for b in blocks if b.get("t") in INVARIANT_TYPES]
    free = [b for b in blocks if b.get("t") not in INVARIANT_TYPES]
    return invariant, free


def merge_blocks_in_order(
    original_blocks: list[dict],
    regenerated_free_blocks: list[dict],
) -> list[dict]:
    """Walk original blocks; at each position copy invariants verbatim and
    pull in order from ``regenerated_free_blocks`` for free slots.

    Defensive fallback: if the LLM under-produced free blocks (e.g. collapsed
    multiple body paragraphs into one), the unfilled free slots now fall
    back to the ORIGINAL block at that position instead of being silently
    dropped. Without this fallback, missing free slots caused invariant
    blocks (equations, figures) to visually cluster at the end of the
    section, which reviewers reported as "equations dumped at end".

    Leftover regen blocks (LLM over-produced) are still appended at the end
    so nothing is lost; usually this combined with the prompt's block-count
    rule means the leftover list is empty in practice.
    """
    merged: list[dict] = []
    free_idx = 0
    for orig in original_blocks:
        if orig.get("t") in INVARIANT_TYPES:
            merged.append(dict(orig))
        else:
            if free_idx < len(regenerated_free_blocks):
                merged.append(regenerated_free_blocks[free_idx])
                free_idx += 1
            else:
                # Defensive: regen under-produced → keep the original block
                # at this position. Reviewer sees the original prose for
                # this slot rather than the slot being silently dropped.
                merged.append(dict(orig))
    while free_idx < len(regenerated_free_blocks):
        merged.append(regenerated_free_blocks[free_idx])
        free_idx += 1
    return merged
