"""Schema-DB ID alignment.

When the schema is regenerated (POST /analyse on an existing book) or
edited (PATCH /api/books/{id}), the new schema may carry fresh
section_id strings while the DB already has Section rows with the old
extraction-time IDs. Letting the new IDs win would orphan extracted
content from its schema slot.

This helper walks the new schema and, for every node that matches an
existing DB section by (title + page range), force-keeps the existing
DB section_id. Unmatched nodes keep their fresh IDs.

After alignment:
  - DB section_ids never change post-extraction.
  - The schema's hierarchy can evolve freely (titles, order, parents,
    excluded vs included).
  - Frontend joins by section_id are stable.
  - figure_references.section_ref + questions.section_ref also remain
    valid because they point at the locked DB IDs.

Matching strategy (in priority order):
  1. Exact (case-folded, whitespace-normalized) title + same page_start.
  2. Same title + overlapping page range.
  3. Same page_start + page_end (handles renames where pages didn't move).
No match → leave the schema node's id unchanged.

Conflicts (two schema nodes match the same DB id) are resolved by
first match wins; later matches fall through to their original IDs.
"""

from __future__ import annotations

import logging
import re
from typing import Iterable

from app.schemas.analyser import BookSchema, ExcludedSection, SchemaSection

logger = logging.getLogger(__name__)


def _norm_title(t: str | None) -> str:
    """Normalize a title for matching: lowercase, collapse whitespace,
    strip trailing punctuation/dots/dashes."""
    if not t:
        return ""
    s = re.sub(r"\s+", " ", t).strip().lower()
    s = s.rstrip(" .:-—")
    return s


def _pages_overlap(
    a_start: int | None,
    a_end: int | None,
    b_start: int | None,
    b_end: int | None,
) -> bool:
    """True if two page ranges share at least one page."""
    if None in (a_start, a_end, b_start, b_end):
        return False
    return a_start <= b_end and b_start <= a_end  # type: ignore[operator]


def _build_existing_index(
    existing_sections: Iterable,
) -> tuple[dict, dict, dict]:
    """Build three lookups from existing DB Section rows:

      title_pagestart : {(norm_title, page_start) : section_id}
      title_only      : {norm_title : [(section_id, page_start, page_end), ...]}
      page_range      : {(page_start, page_end) : section_id}

    Only sections the worker successfully processed (status passed/failed)
    participate — pending/skipped rows shouldn't pin schema IDs since
    they may not represent real extracted content.
    """
    title_pagestart: dict[tuple[str, int], str] = {}
    title_only: dict[str, list[tuple[str, int | None, int | None]]] = {}
    page_range: dict[tuple[int, int], str] = {}

    for sec in existing_sections:
        status = getattr(sec, "status", "")
        if status not in ("passed", "failed"):
            continue
        sid = getattr(sec, "section_id", None)
        if not sid:
            continue
        title = _norm_title(getattr(sec, "title", ""))
        ps = getattr(sec, "page_start", None)
        pe = getattr(sec, "page_end", None)

        if title and ps is not None:
            title_pagestart.setdefault((title, ps), sid)
        if title:
            title_only.setdefault(title, []).append((sid, ps, pe))
        if ps is not None and pe is not None:
            page_range.setdefault((ps, pe), sid)

    return title_pagestart, title_only, page_range


def _match_node(
    node_title: str,
    node_ps: int | None,
    node_pe: int | None,
    title_pagestart: dict,
    title_only: dict,
    page_range: dict,
    consumed_ids: set[str],
) -> str | None:
    """Return an existing section_id that best matches this node, or None.

    Priority: title+page_start exact > title+page-overlap > page-range exact.
    Each existing section_id can only be claimed by one schema node
    (consumed_ids tracks first-match-wins).
    """
    title = _norm_title(node_title)

    # 1. Exact title + page_start
    if title and node_ps is not None:
        sid = title_pagestart.get((title, node_ps))
        if sid and sid not in consumed_ids:
            return sid

    # 2. Same title + overlapping page range
    if title:
        for sid, ps, pe in title_only.get(title, []):
            if sid in consumed_ids:
                continue
            if _pages_overlap(node_ps, node_pe, ps, pe):
                return sid

    # 3. Page range exact (handles renames where page span is the anchor)
    if node_ps is not None and node_pe is not None:
        sid = page_range.get((node_ps, node_pe))
        if sid and sid not in consumed_ids:
            return sid

    return None


def align_schema_ids_to_existing_sections(
    new_schema: BookSchema,
    existing_sections: Iterable,
) -> tuple[BookSchema, dict[str, str]]:
    """Walk new_schema and rewrite each node's id to an existing DB
    section_id when a match is found. Returns the (possibly mutated)
    schema plus a {old_id: new_id} remap dict for caller logging.

    The schema instance is mutated in place AND returned (callers can
    use either; we return for clarity at call sites).
    """
    title_pagestart, title_only, page_range = _build_existing_index(
        existing_sections
    )
    if not title_only and not page_range:
        return new_schema, {}

    consumed: set[str] = set()
    remap: dict[str, str] = {}

    def walk_section(node: SchemaSection) -> None:
        match = _match_node(
            node.title, node.page_start, node.page_end,
            title_pagestart, title_only, page_range, consumed,
        )
        if match is not None and match != node.id:
            remap[node.id] = match
            node.id = match
            consumed.add(match)
        elif match is not None:
            # ID was already correct, still mark consumed so a sibling
            # with the same title doesn't steal it.
            consumed.add(match)
        for sub in node.subsections:
            walk_section(sub)

    def walk_excluded(node: ExcludedSection) -> None:
        # ExcludedSection has no `id` field — only the `sections` table
        # uses ids; excluded sections are referenced by title verbatim
        # in questions.section_ref. So nothing to align here; we walk
        # into subsections anyway in case future schema versions add ids.
        for sub in node.subsections:
            walk_excluded(sub)

    for s in new_schema.sections:
        walk_section(s)
    for ex in new_schema.excluded_sections:
        walk_excluded(ex)

    if remap:
        logger.info(
            "schema_alignment: preserved %d section_id(s) from DB: %s",
            len(remap),
            ", ".join(f"{k}→{v}" for k, v in list(remap.items())[:5]),
        )

    return new_schema, remap
