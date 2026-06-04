"""Final Draft seeder + operations — Phase 3.2.

Two responsibilities:

1) ``seed_draft_items_from_merge`` — convert the build_final_merge output
   into a flat ordered list of authoring items. Each item carries a stable
   id used by the composer for drag-drop reorder.

2) ``apply_operation`` — pure function that mutates an items list per a
   typed operation dict. Used by the PATCH endpoint. Operations are
   declarative so the frontend can replay them without server round-trips
   for instant feedback.
"""

from __future__ import annotations

import uuid
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.final_merge import build_final_merge


def _new_id() -> str:
    """Stable per-item id used by the composer. Short prefix helps with
    debugging draft JSON in the DB."""
    return "it_" + uuid.uuid4().hex[:12]


def _is_chip(block: dict[str, Any]) -> bool:
    return block.get("t") in ("example_ref", "exercise_ref", "question_ref")


async def seed_draft_items_from_merge(
    session: AsyncSession,
    book_id: UUID,
    *,
    prefer_regen: bool = True,
) -> list[dict[str, Any]]:
    """Build the ordered list of items for a fresh draft.

    Mirrors what the Final view shows:
      - Chips whose target section is in the doc are dropped.
      - Section heading echo (first h3 matching section title) is dropped.
      - Embedded figures are interleaved with theory blocks by
        placement_block_idx.
      - Questions come after the theory body of their section.
    """
    doc = await build_final_merge(session, book_id, prefer_regen=prefer_regen)
    section_ids_in_doc: set[str] = {s["section_id"] for s in doc["sections"]}
    items: list[dict[str, Any]] = []

    for sec in doc["sections"]:
        section_id = sec["section_id"]
        title = sec.get("section_title") or section_id

        items.append({
            "id": _new_id(),
            "type": "section_heading",
            "parent_section_id": section_id,
            "section_id": section_id,
            "title": title,
            "level": sec.get("level", 0),
            "regen": sec.get("block_source") == "regen",
        })

        # Theory blocks interleaved with inline figures and inlined
        # questions (the latter come from the chip↔question merge done
        # server-side in build_final_merge). Dedupe leading h3 echo.
        blocks: list[dict[str, Any]] = list(sec.get("blocks") or [])
        figures = list(sec.get("embedded_figures") or [])
        inlined_by_idx = dict(sec.get("inlined_questions_by_block_idx") or {})

        if blocks and blocks[0].get("t") == "h3":
            h3_text = (blocks[0].get("c") or "").strip().lower()
            if h3_text == title.strip().lower():
                blocks = blocks[1:]
                shifted_figs = []
                for f in figures:
                    idx = f.get("placement_block_idx")
                    if idx is None:
                        shifted_figs.append(f)
                    elif idx == 0:
                        shifted_figs.append({**f, "placement_block_idx": None})
                    else:
                        shifted_figs.append({**f, "placement_block_idx": idx - 1})
                figures = shifted_figs
                # Shift inlined question anchors too
                shifted_inlined: dict[str, list[dict[str, Any]]] = {}
                for k, qs in inlined_by_idx.items():
                    try:
                        ki = int(k)
                    except (TypeError, ValueError):
                        continue
                    if ki == 0:
                        # was after the dropped h3 → demote to start
                        shifted_inlined.setdefault("-1", []).extend(qs)
                    elif ki < 0:
                        shifted_inlined.setdefault(str(ki), []).extend(qs)
                    else:
                        shifted_inlined.setdefault(str(ki - 1), []).extend(qs)
                inlined_by_idx = shifted_inlined

        figures_by_idx: dict[int, list[dict[str, Any]]] = {}
        trailing_figs: list[dict[str, Any]] = []
        for f in figures:
            idx = f.get("placement_block_idx")
            if idx is None:
                trailing_figs.append(f)
            else:
                figures_by_idx.setdefault(int(idx), []).append(f)

        # Inlined questions BEFORE any block (anchor "-1")
        for q in inlined_by_idx.get("-1", []):
            items.append({
                "id": _new_id(),
                "type": "question",
                "parent_section_id": section_id,
                "question": q,
            })

        for i, b in enumerate(blocks):
            # Drop chips whose target is in the doc (the standalone section
            # renders separately; the chip is just placeholder noise).
            if _is_chip(b):
                target = b.get("section_id")
                if target and target in section_ids_in_doc:
                    continue
            # Conditional fig-block suppression: drop the theory
            # extractor's `fig` placeholder block when a figure item is
            # already rendering ADJACENT to it (at this index OR the
            # previous index). The figure_embedder places figures at
            # placement_block_idx=N meaning "render after block N", so a
            # fig block sitting at index N+1 immediately follows that
            # render and would visually duplicate the image (image +
            # muted "📷 caption" callout for the same figure).
            #
            # When no adjacent figure exists, KEEP the fig block — its
            # muted callout signals to the reader that a figure was
            # extracted by the theory worker but the embedder couldn't
            # link an actual image to this spot. Previously fig blocks
            # were unconditionally suppressed in renderers, which caused
            # silent gaps for unlinked labelled figures.
            if isinstance(b, dict) and b.get("t") == "fig":
                has_adjacent_figure = (
                    bool(figures_by_idx.get(i))
                    or bool(figures_by_idx.get(i - 1))
                )
                if has_adjacent_figure:
                    # Emit any figure item(s) anchored AT this exact
                    # index. Figures anchored at i-1 already rendered
                    # immediately before this block (via the previous
                    # iteration's `figures_by_idx.get(i)` emit), so no
                    # additional emit needed here.
                    for f in figures_by_idx.get(i, []):
                        items.append({
                            "id": _new_id(),
                            "type": "figure",
                            "parent_section_id": section_id,
                            "figure": f,
                        })
                    for q in inlined_by_idx.get(str(i), []):
                        items.append({
                            "id": _new_id(),
                            "type": "question",
                            "parent_section_id": section_id,
                            "question": q,
                        })
                    continue
                # No adjacent figure → keep the fig block as a visible
                # placeholder. Fall through to the normal emit-block path.
            items.append({
                "id": _new_id(),
                "type": "block",
                "parent_section_id": section_id,
                "block": b,
            })
            for f in figures_by_idx.get(i, []):
                items.append({
                    "id": _new_id(),
                    "type": "figure",
                    "parent_section_id": section_id,
                    "figure": f,
                })
            for q in inlined_by_idx.get(str(i), []):
                items.append({
                    "id": _new_id(),
                    "type": "question",
                    "parent_section_id": section_id,
                    "question": q,
                })

        for f in trailing_figs:
            items.append({
                "id": _new_id(),
                "type": "figure",
                "parent_section_id": section_id,
                "figure": f,
            })

        for q in sec.get("questions") or []:
            items.append({
                "id": _new_id(),
                "type": "question",
                "parent_section_id": section_id,
                "question": q,
            })

    # Emit unattached figures at the END of the items list so they
    # remain visible in Preview / Composer / DOCX / Markdown. These are
    # figures the embedder couldn't place in any section (no label
    # match, no anchor match, no question_no match, no page→section
    # resolution). Without surfacing them here, the user has no way to
    # see them in the document view — they only appear in the Figures
    # tab. Rendered with a synthetic parent_section_id so the front-end
    # can group them under an "Unattached figures" heading.
    unattached = doc.get("unattached_figures") or []
    if unattached:
        # Synthetic section heading so the tray sits visually distinct.
        items.append({
            "id": _new_id(),
            "type": "section_heading",
            "parent_section_id": "__unattached__",
            "section_id": "__unattached__",
            "title": "Unattached Figures",
            "level": 2,
            "regen": False,
        })
        for f in unattached:
            items.append({
                "id": _new_id(),
                "type": "figure",
                "parent_section_id": "__unattached__",
                "figure": f,
            })

    return items


# ---------------------------------------------------------------------------
# Operations — applied by PATCH endpoint
# ---------------------------------------------------------------------------

class OperationError(ValueError):
    """Raised when an operation refers to an unknown item id or has a bad
    payload. The API translates these into 400 responses."""


def apply_operation(
    items: list[dict[str, Any]],
    op: dict[str, Any],
) -> list[dict[str, Any]]:
    """Apply a single typed operation to the items list. Returns a NEW
    list (does not mutate the input).

    Supported operations:
      {op: "reorder",   id, after_id | "start"}   move item next to another
      {op: "remove",    id}                       drop one item
      {op: "edit_item", id, patch: {...}}         shallow-merge patch
      {op: "insert_custom_text", after_id | "start", content}
      {op: "insert_existing", after_id | "start", item: {...complete item dict...}}
    """
    name = op.get("op")
    new = list(items)
    if name == "reorder":
        target_id = op.get("id")
        after_id = op.get("after_id", "start")
        if not target_id:
            raise OperationError("reorder: missing id")
        moved = _pop_by_id(new, target_id)
        if moved is None:
            raise OperationError(f"reorder: unknown id {target_id}")
        _insert_after(new, after_id, moved)
        return new
    if name == "remove":
        target_id = op.get("id")
        if not target_id:
            raise OperationError("remove: missing id")
        if _pop_by_id(new, target_id) is None:
            raise OperationError(f"remove: unknown id {target_id}")
        return new
    if name == "edit_item":
        target_id = op.get("id")
        patch = op.get("patch") or {}
        if not target_id or not isinstance(patch, dict):
            raise OperationError("edit_item: id and patch required")
        for it in new:
            if it.get("id") == target_id:
                # shallow-merge — caller can target nested fields via the
                # appropriate key (e.g. patch={"block": {...new block...}}).
                it.update(patch)
                return new
        raise OperationError(f"edit_item: unknown id {target_id}")
    if name == "insert_custom_text":
        content = op.get("content") or ""
        item = {
            "id": _new_id(),
            "type": "custom_text",
            "parent_section_id": None,
            "content": content,
        }
        _insert_after(new, op.get("after_id", "start"), item)
        return new
    if name == "insert_existing":
        provided = op.get("item")
        if not isinstance(provided, dict) or "type" not in provided:
            raise OperationError("insert_existing: item dict with `type` required")
        item = {**provided, "id": _new_id()}
        _insert_after(new, op.get("after_id", "start"), item)
        return new
    raise OperationError(f"unknown operation: {name}")


def _pop_by_id(arr: list[dict[str, Any]], item_id: str) -> dict[str, Any] | None:
    for i, it in enumerate(arr):
        if it.get("id") == item_id:
            return arr.pop(i)
    return None


def _insert_after(
    arr: list[dict[str, Any]],
    after_id: str,
    item: dict[str, Any],
) -> None:
    if after_id == "start":
        arr.insert(0, item)
        return
    for i, it in enumerate(arr):
        if it.get("id") == after_id:
            arr.insert(i + 1, item)
            return
    # Unknown anchor → append at end (safest no-op fallback)
    arr.append(item)
