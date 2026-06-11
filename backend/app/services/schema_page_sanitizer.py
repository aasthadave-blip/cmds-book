"""Deterministic page-bound sanitizer for schema dicts.

Clamps every section's `page_start` / `page_end` into the valid PDF
range [1, total_pages]. Repairs inverted ranges by setting
`page_end = page_start`. Pure-Python, idempotent — running the
sanitizer twice yields the same result as running it once.

Used by `schema_builder` BEFORE the validator so common Gemini
hallucinations (page_end=999 on a 50-page PDF, page_start=0 on a
1-indexed PDF) auto-fix instead of triggering corrective retries.
"""

from __future__ import annotations

from typing import Any


def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _clamp_pair(
    section: dict,
    total_pages: int,
) -> int:
    """Clamp one section's page_start/page_end. Returns count of fields changed."""
    n_fixes = 0
    ps = section.get("page_start")
    pe = section.get("page_end")

    if _is_int(ps):
        new_ps = max(1, min(ps, total_pages))
        if new_ps != ps:
            section["page_start"] = new_ps
            n_fixes += 1
            ps = new_ps
    if _is_int(pe):
        new_pe = max(1, min(pe, total_pages))
        if new_pe != pe:
            section["page_end"] = new_pe
            n_fixes += 1
            pe = new_pe

    # Repair inverted range (only after individual clamps).
    if _is_int(ps) and _is_int(pe) and pe < ps:
        section["page_end"] = ps
        n_fixes += 1

    return n_fixes


def clamp_pages_to_bounds(
    schema_dict: dict,
    total_pages: int | None,
) -> tuple[dict, int]:
    """Clamp every section/excluded entry's pages into [1, total_pages].

    Walks `sections[]` and `excluded_sections[]` recursively (including
    nested `subsections`). Mutates the dict in place and also returns
    it for caller convenience.

    No-ops if `total_pages` is None or non-positive (we have no bound
    to clamp against). Pages already within range pass through; only
    out-of-bounds and inverted-range values are touched.
    """
    if not isinstance(schema_dict, dict):
        return schema_dict, 0
    if total_pages is None or not isinstance(total_pages, int) or total_pages <= 0:
        return schema_dict, 0

    n_total = 0

    def walk(nodes):
        nonlocal n_total
        for n in nodes or []:
            if not isinstance(n, dict):
                continue
            n_total += _clamp_pair(n, total_pages)
            walk(n.get("subsections") or [])

    walk(schema_dict.get("sections") or [])
    walk(schema_dict.get("excluded_sections") or [])
    return schema_dict, n_total


__all__ = ["clamp_pages_to_bounds"]
