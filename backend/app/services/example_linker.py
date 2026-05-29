"""Example/Question touchpoint linker.

After both theory + question extraction complete, walk the book's sections
and inject `question_ref` block placeholders into each parent section's
`blocks` JSON at the position where its child example/exercise appears in
the prose.

This is a POST-PROCESSING step. It does NOT touch any extraction prompt.
The linker is idempotent — re-running it strips previously-injected
`question_ref` blocks before re-inserting fresh ones, so the linker output
stays in sync with the latest theory + question extractions.

How parent matching works
-------------------------
Schema convention: a worked-example section_id looks like
`<parent>-example-9.1` (or `<parent>.example-9.1`). The parent prefix is
the substring before "-example-" / ".example-".

How position matching works
---------------------------
For each child example, we scan the parent's blocks for the FIRST text
content matching the example's printed label (e.g. `EXAMPLE\\s+9\\.1`). If
a block matches, the touchpoint is inserted RIGHT AFTER it. If no block
matches, the touchpoint is appended at the end of the parent's blocks.

The injected block:
    {
      "t": "question_ref",
      "label": "EXAMPLE 9.1",
      "section_id": "<child section_id>",       # navigation target
      "question_id": "<question UUID, optional>" # if a Question row exists
    }

Frontend renders this as a clickable purple chip.
"""

from __future__ import annotations

import logging
import re
from typing import Iterable
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.question import Question
from app.models.section import Section

logger = logging.getLogger(__name__)


# Regex over a section_id — captures parent prefix, kind, and printed number
# when the id matches the question-child convention. Recognized kinds:
# example, exercise, problem, practice-problem, worked-example, solved-example,
# in-text-question, intext-question. Theory-aid kinds (illustration,
# progress-check, activity, etc.) are NOT linked because they're transcribed
# as theory body, not chips.
_QUESTION_KINDS = (
    "worked-example",  # longest first so the regex engine prefers them
    "solved-example",
    "practice-problem",
    "in-text-question",
    "intext-question",
    "example",
    "exercise",
    "problem",
)
# Compile one regex per kind in priority order (longest first). We try each
# in turn and return the first match. This handles two tricky cases at once:
#   1. Parent slug contains a kind keyword: "9-solved-examples-example-9.7"
#      → parent="9-solved-examples", kind="example", num="9.7"
#   2. Multi-word kind embedded in shorter kind: "chap-2.3-practice-problem-4"
#      → kind="practice-problem", NOT kind="problem".
# Greedy parent (`.+`) ensures the shortest valid suffix wins for each kind.
# Num must start with a digit (or be empty for unnumbered like "in-text-question").
_KIND_PATTERNS = [
    (kind, re.compile(rf"^(?P<parent>.+)[\.\-]{re.escape(kind)}(?:[\.\-](?P<num>\d[\w\.\-]*))?$"))
    for kind in _QUESTION_KINDS
]


def _split_question_id(section_id: str) -> tuple[str, str, str] | None:
    """Return (parent_id, kind, num) if this is a question-kind child, else None.

    Iterates kinds in longest-first priority order to disambiguate multi-word
    kinds (e.g., "practice-problem" wins over "problem").
    """
    for kind, pat in _KIND_PATTERNS:
        m = pat.match(section_id)
        if m:
            return m.group("parent"), kind, (m.group("num") or "")
    return None


def _build_parent_map_from_schema(schema: dict | None) -> dict[str, str]:
    """Walk the live book.schema_ tree and return {child_section_id:
    parent_section_id} for every child.

    This is the source of truth for chip placement AFTER schema edits in
    the editor. If a user drag-drops Example 3.8 under "(A) ..." in the
    schema editor, this map reflects that immediately — overriding the
    older ID-based parent inference (which would still resolve to the
    original parent baked into the id string).

    Returns empty dict if `schema` is None or has no sections.
    """
    out: dict[str, str] = {}
    if not schema or not isinstance(schema, dict):
        return out

    def walk(parent_id: str | None, nodes: list) -> None:
        if not isinstance(nodes, list):
            return
        for n in nodes:
            if not isinstance(n, dict):
                continue
            child_id = n.get("id")
            if isinstance(child_id, str) and parent_id is not None:
                out[child_id] = parent_id
            walk(child_id if isinstance(child_id, str) else parent_id, n.get("subsections") or [])

    walk(None, schema.get("sections") or [])
    return out


def _split_example_id(section_id: str) -> tuple[str, str] | None:
    """Backward-compat: return (parent_id, num) if this is a question-kind
    child, else None. Drops the kind (callers wanting kind use _split_question_id)."""
    parsed = _split_question_id(section_id)
    if parsed is None:
        return None
    return parsed[0], parsed[2]


# Backward-compat alias — old call sites used `_EXAMPLE_ID_RE`. Now unused
# (replaced by per-kind regexes), kept as None to surface any stale references.
_EXAMPLE_ID_RE = None  # type: ignore[assignment]


def _num_sort_key(num: str) -> tuple:
    """Sort key for example numbers like "9.1", "9.10", "9.2" → numeric order.

    Splits on dots/dashes, treats segments as ints when possible, falls back
    to lower-cased string for non-numeric segments. So "9.10" sorts AFTER
    "9.2" (correct), and "A.1" sorts deterministically against "1.1".
    """
    parts: list[tuple[int, int | str]] = []
    for seg in re.split(r"[.\-]", str(num)):
        try:
            parts.append((0, int(seg)))
        except ValueError:
            parts.append((1, seg.lower()))
    return tuple(parts)


def _label_for(section_title: str, num: str, kind: str = "example") -> str:
    """Build the label shown on the touchpoint chip.

    Prefers the section's own title verbatim when it matches a canonical
    question label (e.g. "EXAMPLE 9.1", "Exercise 1.1", "Problem 5"). Falls
    back to "<Kind Title> <num>" if the title doesn't look canonical.
    """
    t = (section_title or "").strip()
    if re.match(
        r"(?i)^(example|worked\s+example|solved\s+example|exercise|problem|practice\s+problem|in[-\s]?text\s+question)\b",
        t,
    ):
        return t
    # Fall back to the kind keyword title-cased + num.
    pretty = kind.replace("-", " ").title()
    return f"{pretty} {num}".strip()


def _label_pattern(label: str) -> re.Pattern[str]:
    """Build a regex that matches a printed question label inside prose.

    "EXAMPLE 9.1" / "Exercise 1.1" / "Problem 5" / etc. Word-boundary anchored
    so "Example 9.10" doesn't match for "Example 9.1".
    """
    m = re.search(r"([\w\.\-]+)$", label.strip())
    num = re.escape(m.group(1)) if m else ""
    return re.compile(
        rf"(?i)\b(?:example|worked\s+example|solved\s+example|exercise|problem|practice\s+problem|in[-\s]?text\s+question)\s+{num}\b"
    )


def _block_text(block: dict) -> str:
    """Concatenate all human-readable text fields from a block for regex match."""
    parts: list[str] = []
    for key in ("c", "term", "label", "prob", "caption"):
        v = block.get(key)
        if isinstance(v, str):
            parts.append(v)
    items = block.get("items")
    if isinstance(items, list):
        parts.extend(str(x) for x in items if isinstance(x, str))
    eqs = block.get("eqs")
    if isinstance(eqs, list):
        parts.extend(str(x) for x in eqs if isinstance(x, str))
    return " ".join(parts)


def _strip_existing_refs(blocks: list[dict], child_section_id: str) -> list[dict]:
    """Drop any prior `question_ref` blocks that already point at this child.

    Lets the linker run multiple times without piling up duplicate chips.
    """
    out: list[dict] = []
    for b in blocks:
        if b.get("t") == "question_ref" and b.get("section_id") == child_section_id:
            continue
        out.append(b)
    return out


def _inject_ref(
    parent_blocks: list[dict],
    label: str,
    child_section_id: str,
    question_id: str | None,
) -> tuple[list[dict], str]:
    """Return new block list with the `question_ref` injected, plus the
    placement strategy used ("inline" or "appended").
    """
    cleaned = _strip_existing_refs(parent_blocks, child_section_id)
    ref_block = {
        "t": "question_ref",
        "label": label,
        "section_id": child_section_id,
    }
    if question_id is not None:
        ref_block["question_id"] = question_id

    pat = _label_pattern(label)
    for idx, b in enumerate(cleaned):
        if pat.search(_block_text(b)):
            return cleaned[: idx + 1] + [ref_block] + cleaned[idx + 1 :], "inline"
    return cleaned + [ref_block], "appended"


async def link_examples_to_theory(
    session: AsyncSession,
    book_id: UUID,
) -> dict:
    """Walk all sections of a book and inject `question_ref` chips into the
    parent of each example/exercise child section. Returns a small summary
    dict for logging/return-to-API.

    Idempotent: safe to call repeatedly.

    Parent-resolution priority:
      1. Live `book.schema_` tree (the user's edited hierarchy, including
         any drag-drop in the schema editor) — wins if the child's
         section_id appears in the schema map.
      2. Fallback to the section_id naming convention (e.g.
         "<parent>-example-9.1" → parent "<parent>"). Used only when the
         schema doesn't have the child mapped (orphans, legacy data).
    """
    from app.models.book import Book

    # Load the book so we can read its current schema tree
    book = await session.get(Book, book_id)
    schema_parent_map: dict[str, str] = {}
    if book is not None and book.schema is not None:
        schema_parent_map = _build_parent_map_from_schema(book.schema)

    sections = (
        await session.execute(
            select(Section).where(Section.book_id == book_id)
        )
    ).scalars().all()

    by_id: dict[str, Section] = {s.section_id: s for s in sections}

    # Map child section_id → first matching question.id (if extracted)
    questions_by_section: dict[str, str] = {}
    qrows = (
        await session.execute(
            select(Question).where(Question.book_id == book_id)
        )
    ).scalars().all()
    for q in qrows:
        if q.section_ref and q.section_ref in by_id and q.section_ref not in questions_by_section:
            questions_by_section[q.section_ref] = str(q.id)

    n_inline = 0
    n_appended = 0
    n_skipped_no_parent = 0
    touched_parents: set[str] = set()

    # Process children in (parent_id, numeric question number) order so
    # appended chips on a given parent end up in 1.1 → 1.2 → 1.10 order
    # instead of whatever order SQL returned the rows in.
    children_with_parsed: list[tuple[Section, str, str, str]] = []
    for c in sections:
        parsed = _split_question_id(c.section_id)
        if parsed is None:
            continue
        # parsed = (id_parent_id, kind, num). Live-schema tree wins over
        # the ID-derived parent so manual drag-drop in the schema editor
        # moves the chip to the new parent.
        effective_parent = schema_parent_map.get(c.section_id) or parsed[0]
        children_with_parsed.append((c, effective_parent, parsed[1], parsed[2]))
    children_with_parsed.sort(key=lambda t: (t[1], _num_sort_key(t[3])))

    # Strip any old chips for these children from EVERY parent before
    # re-injecting. This makes chip-position updates visible after a
    # drag-drop in the schema editor.
    affected_child_ids: set[str] = {c.section_id for c, *_ in children_with_parsed}
    for sec in sections:
        if not sec.blocks:
            continue
        original = list(sec.blocks)
        cleaned = [
            b for b in original
            if not (isinstance(b, dict)
                    and b.get("t") == "question_ref"
                    and b.get("section_id") in affected_child_ids)
        ]
        if cleaned != original:
            sec.blocks = cleaned

    for child, parent_id, kind, num in children_with_parsed:
        parent = by_id.get(parent_id)
        if parent is None:
            n_skipped_no_parent += 1
            continue

        # Skip injecting chips into a parent that is ITSELF a question-kind
        # section (Exercise/Example/Problem etc). The user's rule: theory
        # sections get a single chip pointing to the parent exercise, NOT
        # 20 individual question-level chips inside that exercise. Those
        # questions get rendered by the question extraction pipeline.
        if _split_question_id(parent.section_id) is not None:
            continue

        label = _label_for(child.title or "", num, kind=kind)
        question_id = questions_by_section.get(child.section_id)

        new_blocks, mode = _inject_ref(
            list(parent.blocks or []),
            label=label,
            child_section_id=child.section_id,
            question_id=question_id,
        )
        if new_blocks != list(parent.blocks or []):
            parent.blocks = new_blocks
            touched_parents.add(parent.section_id)
            if mode == "inline":
                n_inline += 1
            else:
                n_appended += 1

    await session.commit()
    summary = {
        "book_id": str(book_id),
        "parents_touched": len(touched_parents),
        "inline_inserts": n_inline,
        "appended_inserts": n_appended,
        "skipped_no_parent": n_skipped_no_parent,
    }
    logger.info("example_linker summary: %s", summary)
    return summary


def example_section_ids(section_iter: Iterable[Section]) -> set[str]:
    """Helper for tests: which section_ids are example children."""
    return {s.section_id for s in section_iter if _split_example_id(s.section_id)}


def link_examples_to_theory_sync(session, book_id: UUID) -> dict:
    """Sync variant of `link_examples_to_theory` — same logic, sync session.

    Used by the v3 worker which runs in a sync SQLAlchemy context. Behaviour
    is identical to the async version.
    """
    from sqlalchemy import select as _select  # local import to keep module light
    from app.models.book import Book as _Book

    book = session.get(_Book, book_id)
    schema_parent_map: dict[str, str] = {}
    if book is not None and book.schema is not None:
        schema_parent_map = _build_parent_map_from_schema(book.schema)

    sections = session.execute(
        _select(Section).where(Section.book_id == book_id)
    ).scalars().all()
    by_id: dict[str, Section] = {s.section_id: s for s in sections}

    questions_by_section: dict[str, str] = {}
    qrows = session.execute(
        _select(Question).where(Question.book_id == book_id)
    ).scalars().all()
    for q in qrows:
        if (
            q.section_ref
            and q.section_ref in by_id
            and q.section_ref not in questions_by_section
        ):
            questions_by_section[q.section_ref] = str(q.id)

    n_inline = 0
    n_appended = 0
    n_skipped_no_parent = 0
    touched_parents: set[str] = set()

    # Process children in (parent_id, numeric question number) order so
    # appended chips on a given parent end up in 1.1 → 1.2 → 1.10 order
    # instead of whatever order SQL returned the rows in.
    children_with_parsed: list[tuple[Section, str, str, str]] = []
    for c in sections:
        parsed = _split_question_id(c.section_id)
        if parsed is None:
            continue
        # parsed = (id_parent_id, kind, num). Live-schema tree wins over
        # the ID-derived parent so manual drag-drop in the schema editor
        # moves the chip to the new parent.
        effective_parent = schema_parent_map.get(c.section_id) or parsed[0]
        children_with_parsed.append((c, effective_parent, parsed[1], parsed[2]))
    children_with_parsed.sort(key=lambda t: (t[1], _num_sort_key(t[3])))

    # Strip any old chips for these children from EVERY parent before
    # re-injecting. This makes chip-position updates visible after a
    # drag-drop in the schema editor.
    affected_child_ids: set[str] = {c.section_id for c, *_ in children_with_parsed}
    for sec in sections:
        if not sec.blocks:
            continue
        original = list(sec.blocks)
        cleaned = [
            b for b in original
            if not (isinstance(b, dict)
                    and b.get("t") == "question_ref"
                    and b.get("section_id") in affected_child_ids)
        ]
        if cleaned != original:
            sec.blocks = cleaned

    for child, parent_id, kind, num in children_with_parsed:
        parent = by_id.get(parent_id)
        if parent is None:
            n_skipped_no_parent += 1
            continue

        # Skip injecting chips into a parent that is ITSELF a question-kind
        # section (Exercise/Example/Problem etc). The user's rule: theory
        # sections get a single chip pointing to the parent exercise, NOT
        # 20 individual question-level chips inside that exercise. Those
        # questions get rendered by the question extraction pipeline.
        if _split_question_id(parent.section_id) is not None:
            continue

        label = _label_for(child.title or "", num, kind=kind)
        question_id = questions_by_section.get(child.section_id)

        new_blocks, mode = _inject_ref(
            list(parent.blocks or []),
            label=label,
            child_section_id=child.section_id,
            question_id=question_id,
        )
        if new_blocks != list(parent.blocks or []):
            parent.blocks = new_blocks
            touched_parents.add(parent.section_id)
            if mode == "inline":
                n_inline += 1
            else:
                n_appended += 1

    session.commit()
    summary = {
        "book_id": str(book_id),
        "parents_touched": len(touched_parents),
        "inline_inserts": n_inline,
        "appended_inserts": n_appended,
        "skipped_no_parent": n_skipped_no_parent,
    }
    logger.info("example_linker (sync) summary: %s", summary)
    return summary
