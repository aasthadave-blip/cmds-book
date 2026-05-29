"""Deterministic post-extraction figure-to-content embedder.

Runs CPU-only (no Gemini, no AI). Takes the figures the extractor
already pulled out of the PDF, matches each to its placeholder
position inside the theory body's blocks (or the question's
raw_text), and writes the placement metadata into
``figure_references``.

Phase 1 of the image-integration plan. Section A of the build.

Why this exists
---------------
The theory extractor produces theory blocks like
   {"t": "fig", "c": "Figure 4.7"}              (anchor mention)
   {"t": "p",   "c": "see Fig. 4.7 for the chart"}
The figure extractor produces ``figures`` rows with
   normalized_label = "4.7", figure_number = "Figure 4.7",
   image_bytes / regen_image_bytes, etc.
Without an explicit join, the frontend doesn't know which image to
drop into which slot. This embedder writes that join into
``figure_references``:
  - ``placement_kind``         "inline" | "appended" | "needs_review"
  - ``placement_block_idx``    for theory: the section.blocks index
                                 the figure should be rendered AFTER
                                 (or AT if the matched block is the
                                 fig placeholder itself)
  - ``placement_char_offset``  for question: the byte offset into
                                 question.raw_text where the inline
                                 figure marker is rendered

Matching is fuzzy but deterministic. Same input always produces the
same output.

The embedder NEVER writes to sections.blocks or questions.raw_text.
Theory and question extractors stay the source of truth for their
own data.
"""

from __future__ import annotations

import logging
import re
from typing import Any
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.figure import Figure
from app.models.figure_reference import FigureReference
from app.models.question import Question
from app.models.section import Section

logger = logging.getLogger(__name__)


# Label normalization — turn any of "Figure 4.7", "Fig. 4.7", "fig 4.7",
# "FIGURE 4.7", "[Figure 4.7]", "(Fig 4.7)" into the canonical "4.7" for
# comparison. Mirrors the regex the linker uses for similar work.
_LABEL_PREFIX_RE = re.compile(
    r"\b(figures?|figs?\.?|fig\s*no\.?|table)\s*",
    re.IGNORECASE,
)


def _normalize_label(text: str | None) -> str:
    """Extract a figure label's NUMBER component.

    Examples:
      "Figure 9.1"                  → "9.1"
      "Figure 9.1 Discharge tube"   → "9.1"   (caption ignored — the
                                                index must key by the
                                                number alone so figure
                                                entities with label
                                                "Figure 9.1" line up
                                                with fig blocks that
                                                include the caption)
      "Fig. 4.7a"                   → "4.7a"
      "(Figure 9.10)"               → "9.10"
      "Table 9.1"                   → "9.1"
      ""                            → ""
    """
    if not text:
        return ""
    # Strip "Figure"/"Fig."/"Table" prefix first, then look for the
    # number — accepts "9", "9.1", "9.1.2", optionally followed by a
    # single trailing letter (e.g. "4.7a").
    s = _LABEL_PREFIX_RE.sub("", str(text).strip())
    m = re.search(r"(\d+(?:\.\d+)*[a-z]?)", s, re.IGNORECASE)
    return m.group(1).lower() if m else ""


def _build_label_pattern(label: str) -> re.Pattern[str] | None:
    """Compile a regex that finds "<prefix> {label}" or just "{label}" in text.

    Uses word boundaries to avoid matching "4.7" inside "14.75". The label
    itself is escaped so "4.10" is matched literally (not as regex).
    """
    if not label:
        return None
    esc = re.escape(label)
    # OCR-tolerant matcher. Accepts (case-insensitive):
    #   "Figure 4.7", "Figures 4.7", "Fig 4.7", "Fig. 4.7", "Fig.4.7",
    #   "Fig_4.7", "Fig:4.7", "Figure-4.7", "Figure4.7" (no separator),
    #   "figure no. 4.7", "(Fig 4.7)", "[Figure 4.7]".
    # Separator class: whitespace / dot / underscore / colon / dash (zero+).
    pat = (
        # Tolerant of: parens / brackets around the number, underscore /
        # colon / dash separators, optional "no." between the keyword and
        # the number. Examples that match:
        #   "Figure 9.2", "Fig 9.2", "Fig. 9.2", "Fig.9.2",
        #   "Fig_9.2", "Fig:9.2", "Figure-9.2", "Figures 9.2",
        #   "figure no. 9.2", "(Fig 9.2)", "[Figure 9.2]",
        #   "Fig. (9.2)", "Fig.(9.2)"  ← the parenthesised forms
        r"(?:\bfigures?\s*(?:no\.?)?|\bfigs?\.?)[\s._:\-]*[(\[]?\s*"
        + esc
        + r"\s*[)\]]?\b"
    )
    try:
        return re.compile(pat, re.IGNORECASE)
    except re.error as e:
        logger.warning("regex compile failed for label %r: %s", label, e)
        return None


def _select_variant(fig: Figure) -> str:
    """Decide which image variant to surface: regen (if approved) else original."""
    if fig.regen_image_bytes and fig.approved_at is not None:
        return "regen"
    return "original"


def _find_inline_block_index(
    blocks: list[Any],
    normalized_label: str,
    label_pattern: re.Pattern[str] | None,
) -> int | None:
    """Scan section.blocks for the best inline anchor for this figure.

    Priority:
      1. A ``fig`` block whose ``c`` text matches the label
         (e.g. ``{"t": "fig", "c": "Figure 4.7"}``)
      2. A paragraph / equation / list block that mentions the label
         in its ``c`` text (e.g. "see Fig. 4.7 for...")

    Returns the index of the matched block, or None if no match.
    """
    if not isinstance(blocks, list) or not blocks:
        return None

    # Priority 1: fig blocks — match against BOTH `label` and `c`
    # because the OCR's fig blocks store the figure number in `label`
    # (e.g. {"t":"fig","label":"Figure 8.5","c":"Pith ball electroscope"}).
    for i, b in enumerate(blocks):
        if not isinstance(b, dict):
            continue
        if b.get("t") != "fig":
            continue
        for field in ("label", "c"):
            v = b.get(field) or ""
            if not isinstance(v, str) or not v:
                continue
            if _normalize_label(v) == normalized_label:
                return i
            if label_pattern and label_pattern.search(v):
                return i

    # Priority 2: any text block that mentions the label
    if label_pattern is None:
        return None
    for i, b in enumerate(blocks):
        if not isinstance(b, dict):
            continue
        c = b.get("c") or b.get("content") or ""
        if not isinstance(c, str):
            continue
        if label_pattern.search(c):
            return i

    return None


def _find_inline_char_offset(
    raw_text: str | None,
    label_pattern: re.Pattern[str] | None,
) -> int | None:
    """Find the character offset of the label mention in question raw_text."""
    if not raw_text or not label_pattern:
        return None
    m = label_pattern.search(raw_text)
    if m:
        return m.start()
    return None


def _build_global_label_index(
    sections: list[Any],
) -> dict[str, list[tuple[str, int, str]]]:
    """Scan EVERY section's blocks once and build a global index from
    normalized_label -> [(section_id, block_idx, source), ...].

    `source` is "fig" when the match came from a ``{"t": "fig"}`` block
    (high-confidence anchor — Gemini's theory extractor saw a labelled
    placeholder there) or "text" when the match came from a paragraph /
    equation / list mention (medium-confidence — could be a "see Fig. 4.7"
    reference rather than the figure's actual location).

    This is the foundation for Pass 1 label-first matching: when a figure
    has normalized_label "4.1", the embedder consults this index to find
    where in the book "Figure 4.1" is actually mentioned in the theory
    body — independent of the figure's section_id (which the figure
    linker assigned by PAGE NUMBER and can be wrong when sections share
    pages).
    """
    out: dict[str, list[tuple[str, int, str]]] = {}
    for sec in sections:
        section_id = sec.section_id
        blocks = sec.blocks or []
        if not isinstance(blocks, list):
            continue
        # Pass A: fig blocks (highest confidence). Check BOTH `label` AND
        # `c` because the OCR sometimes puts "Figure 8.5" in `label` and
        # the caption in `c` — we must index by EITHER to find the figure.
        for i, b in enumerate(blocks):
            if not isinstance(b, dict):
                continue
            if b.get("t") != "fig":
                continue
            seen_for_block: set[str] = set()
            for field in ("label", "c"):
                v = b.get(field) or ""
                if not isinstance(v, str) or not v:
                    continue
                norm = _normalize_label(v)
                if norm and norm not in seen_for_block:
                    seen_for_block.add(norm)
                    out.setdefault(norm, []).append((section_id, i, "fig"))
        # Pass B: text mentions (medium confidence) — captured as
        # "<context>label" e.g. "Fig 4.7" inside a paragraph
        for i, b in enumerate(blocks):
            if not isinstance(b, dict):
                continue
            if b.get("t") == "fig":
                continue  # already handled in Pass A
            c = b.get("c") or b.get("content") or ""
            if not isinstance(c, str) or not c:
                continue
            # find all label-like substrings via a generic pattern.
            # Tolerant: allows optional parens / brackets around the number
            # (e.g. "Fig. (9.2)", "[Figure 9.2]") AND underscore/colon
            # separators (e.g. "Fig_9.2", "Fig:9.2") — same separator set
            # the frontend uses.
            for m in re.finditer(
                r"\bfigs?(?:ure)?\.?[\s._:\-]*[(\[]?\s*(\d+(?:\.\d+)?[a-z]?)\s*[)\]]?\b",
                c,
                flags=re.IGNORECASE,
            ):
                norm = _normalize_label(m.group(0))
                if norm:
                    out.setdefault(norm, []).append((section_id, i, "text"))
    return out


def _pick_label_match(
    candidates: list[tuple[str, int, str]],
    figure_page: int | None,
    sections_by_id: dict[str, Any],
) -> tuple[str, int] | None:
    """Pick the best (section_id, block_idx) match from a list of
    label-index candidates.

    Tie-breaking:
      1. Prefer "fig" source over "text" source (an actual placeholder
         block beats a paragraph reference).
      2. If figure_page is known, prefer the candidate whose section's
         page range contains the figure's page.
      3. Otherwise first match wins (stable).
    """
    if not candidates:
        return None
    # Bucket by source priority
    fig_candidates = [c for c in candidates if c[2] == "fig"]
    text_candidates = [c for c in candidates if c[2] != "fig"]
    pool = fig_candidates or text_candidates
    if not pool:
        return None
    # Page-based tie-break inside the priority bucket
    if figure_page is not None:
        page_matches = []
        for section_id, block_idx, _ in pool:
            sec = sections_by_id.get(section_id)
            ps = getattr(sec, "page_start", None) if sec else None
            pe = getattr(sec, "page_end", None) if sec else None
            if ps is not None and pe is not None and ps <= figure_page <= pe:
                page_matches.append((section_id, block_idx))
        if page_matches:
            return page_matches[0]
    section_id, block_idx, _ = pool[0]
    return section_id, block_idx


async def embed_figures_for_book(
    session: AsyncSession,
    book_id: UUID,
) -> dict[str, int]:
    """Walk every Figure for this book, compute its placement, and write
    the result back to ``figure_references``.

    Idempotent: rebuilds all placement rows from scratch so re-running
    after schema edits or new regen variants produces a consistent
    state.

    Returns a small counters dict for logging.
    """
    counters = {
        "figures_seen": 0,
        "theory_inline": 0,
        "theory_appended": 0,
        "question_inline": 0,
        "question_appended": 0,
        "unattached": 0,           # no valid target — surfaced in Unattached tray
        "skipped_no_section": 0,
    }

    # Pre-load sections and questions for this book
    sections = (
        await session.execute(
            select(Section).where(Section.book_id == book_id)
        )
    ).scalars().all()
    sections_by_id = {s.section_id: s for s in sections}

    # Load questions from ONLY the latest ready bank for this book — older
    # banks have stale question_ids that newer extractions replace. Without
    # this filter the embedder picks the first global match, which is
    # usually the oldest bank's question, leaving the current bank's
    # question without an embedded figure.
    from app.models.question_bank import QuestionBank
    latest_bank = (
        await session.execute(
            select(QuestionBank)
            .where(QuestionBank.book_id == book_id)
            .where(QuestionBank.status == "ready")
            .order_by(QuestionBank.created_at.desc())
            .limit(1)
        )
    ).scalars().first()
    if latest_bank is not None:
        questions = (
            await session.execute(
                select(Question)
                .where(Question.book_id == book_id)
                .where(Question.bank_id == latest_bank.id)
                .where(Question.regen_id.is_(None))
            )
        ).scalars().all()
    else:
        questions = []
    questions_by_section: dict[str, list[Question]] = {}
    for q in questions:
        ref = q.section_ref
        if ref:
            questions_by_section.setdefault(ref, []).append(q)

    figures = (
        await session.execute(
            select(Figure).where(Figure.book_id == book_id)
        )
    ).scalars().all()

    # Strategy: drop existing FigureReference rows for this book and
    # rebuild from scratch. Cleaner than diffing; idempotent.
    await session.execute(
        delete(FigureReference).where(FigureReference.book_id == book_id)
    )

    # Build the global label index — used by Pass 1 label-first matching
    # so a figure can land in the section where its label actually
    # appears in the theory body, regardless of what page-based section
    # the figure linker assigned it to.
    label_index = _build_global_label_index(sections)
    counters["theory_relinked_by_label"] = 0

    new_refs: list[FigureReference] = []

    for fig in figures:
        counters["figures_seen"] += 1
        section_id = fig.section_id or ""
        if not section_id or section_id == "_orphan":
            counters["skipped_no_section"] += 1
            continue

        label_norm = (fig.normalized_label or _normalize_label(fig.figure_number) or "").strip()
        label_pattern = _build_label_pattern(label_norm)

        context = (fig.context_hint or "theory").lower()
        # We embed in theory by default; only questions if explicitly tagged
        target_kind = "question" if context == "question" else "theory"

        # STRICT context routing per user spec:
        #   context="theory"   → only placed in theory body (never in questions).
        #                        If no theory match anywhere → "unattached".
        #   context="question" → only placed beside its question (never in theory).
        #                        If no question target → "unattached".
        # No more cross-fallback. Unattached figures are surfaced in a
        # dedicated UI tray for user review, not dumped into a random
        # section. User can remove appended figures via the ✕ button
        # (is_hidden flag) which suppresses rendering AND export.

        if target_kind == "theory":
            # Pass 1: label-first global match against theory blocks
            label_match = None
            if label_norm:
                cands = label_index.get(label_norm) or []
                label_match = _pick_label_match(
                    cands, fig.page_number, sections_by_id,
                )
            if label_match is not None:
                matched_sec_id, matched_block_idx = label_match
                # NOTE (E4 fix): we used to mutate fig.section_id here to
                # match the label-match section. That made figures hop to
                # whichever section happened to mention "Fig X.Y" in its
                # text, which was often a cross-reference, not the home
                # section. Keep Figure.section_id as the extraction anchor
                # (page-based, more conservative). The FigureReference row
                # below carries the per-mention section_ref independently,
                # so theory-side rendering still works.
                new_refs.append(FigureReference(
                    figure_id=fig.id,
                    book_id=book_id,
                    section_ref=matched_sec_id,
                    context="theory",
                    question_id=None,
                    placeholder_text=fig.figure_number,
                    link_method="auto",
                    placement_kind="inline",
                    placement_block_idx=matched_block_idx,
                    placement_char_offset=None,
                ))
                counters["theory_inline"] += 1
                continue

            # SECTION FALLBACK: when no label match anywhere AND the figure
            # has a section_id from the figure extractor (page-based), append
            # the figure to that section at end-of-blocks. Flagged with
            # placement_kind="page_fallback" so the UI can prompt the user to
            # verify (page detection is less reliable than label matching at
            # section boundaries). Only fires when section_id resolves to a
            # known Section row — never blindly shoves figures under random
            # sections.
            if section_id and section_id in sections_by_id:
                new_refs.append(FigureReference(
                    figure_id=fig.id, book_id=book_id, section_ref=section_id,
                    context="theory", question_id=None,
                    placeholder_text=fig.figure_number, link_method="auto",
                    placement_kind="page_fallback", placement_block_idx=None,
                    placement_char_offset=None,
                ))
                counters["theory_appended"] += 1
                continue

            # No label match + no usable section → Unattached panel.
            new_refs.append(FigureReference(
                figure_id=fig.id, book_id=book_id, section_ref=section_id,
                context="theory", question_id=None,
                placeholder_text=fig.figure_number, link_method="auto",
                placement_kind="unattached", placement_block_idx=None,
                placement_char_offset=None,
            ))
            counters["unattached"] += 1

        else:  # question
            # Strict: only place in questions. No theory fallback.
            #
            # Pass 1 (NEW): label-first GLOBAL match. Scan every question's
            # raw_text for the figure's label pattern. Mirrors the theory
            # branch's global label match — handles the very common case
            # where a question in section A references "Figure X" but the
            # figure was OCR-tagged to section B by page number.
            if label_norm and label_pattern is not None:
                global_hit: tuple[Question, int] | None = None
                for q in questions:
                    # Search BOTH the question body and the solution body —
                    # questions in OCR'd textbooks often reference figures
                    # only in the solution ("see Figure 4.7"), not the prompt.
                    combined = (q.raw_text or "") + "\n" + (getattr(q, "solution_text", "") or "")
                    offset = _find_inline_char_offset(combined, label_pattern)
                    if offset is not None:
                        global_hit = (q, offset)
                        break
                if global_hit is not None:
                    q, offset = global_hit
                    resolved_sec = q.section_ref or section_id
                    # NOTE (E4 fix): no longer mutate fig.section_id —
                    # keep the extraction anchor. Reference row carries
                    # the per-question placement independently.
                    new_refs.append(FigureReference(
                        figure_id=fig.id, book_id=book_id,
                        section_ref=resolved_sec,
                        context="question", question_id=q.id,
                        placeholder_text=fig.figure_number, link_method="auto",
                        placement_kind="inline", placement_block_idx=None,
                        placement_char_offset=offset,
                    ))
                    counters["question_inline"] += 1
                    continue

            # SECTION FALLBACK (question context): when no question's text
            # mentions the label but the figure has a page-detected section,
            # append at the section level as a "page_fallback" theory-context
            # reference. The user can re-link to a specific question in the
            # UI if needed. Conservative — only fires when section_id is
            # known.
            if section_id and section_id in sections_by_id:
                new_refs.append(FigureReference(
                    figure_id=fig.id, book_id=book_id, section_ref=section_id,
                    context="theory", question_id=None,
                    placeholder_text=fig.figure_number, link_method="auto",
                    placement_kind="page_fallback", placement_block_idx=None,
                    placement_char_offset=None,
                ))
                counters["theory_appended"] += 1
                continue

            new_refs.append(FigureReference(
                figure_id=fig.id, book_id=book_id, section_ref=section_id,
                context="question", question_id=None,
                placeholder_text=fig.figure_number, link_method="auto",
                placement_kind="unattached", placement_block_idx=None,
                placement_char_offset=None,
            ))
            counters["unattached"] += 1

    for ref in new_refs:
        session.add(ref)

    await session.flush()
    logger.info("figure_embedder: book=%s %s", book_id, counters)
    return counters


def embed_figures_for_book_sync(session, book_id: UUID) -> dict[str, int]:
    """Sync variant for use inside the v3 worker (which runs in a sync
    SQLAlchemy session context). Behaviour is identical to the async
    version.
    """
    from sqlalchemy import delete as _delete, select as _select

    counters = {
        "figures_seen": 0,
        "theory_inline": 0,
        "theory_appended": 0,
        "question_inline": 0,
        "question_appended": 0,
        "unattached": 0,
        "skipped_no_section": 0,
    }

    sections = session.execute(
        _select(Section).where(Section.book_id == book_id)
    ).scalars().all()
    sections_by_id = {s.section_id: s for s in sections}

    # Restrict to latest ready bank — see async variant for rationale.
    from app.models.question_bank import QuestionBank
    latest_bank = session.execute(
        _select(QuestionBank)
        .where(QuestionBank.book_id == book_id)
        .where(QuestionBank.status == "ready")
        .order_by(QuestionBank.created_at.desc())
        .limit(1)
    ).scalars().first()
    if latest_bank is not None:
        questions = session.execute(
            _select(Question)
            .where(Question.book_id == book_id)
            .where(Question.bank_id == latest_bank.id)
            .where(Question.regen_id.is_(None))
        ).scalars().all()
    else:
        questions = []
    questions_by_section: dict[str, list[Question]] = {}
    for q in questions:
        if q.section_ref:
            questions_by_section.setdefault(q.section_ref, []).append(q)

    figures = session.execute(
        _select(Figure).where(Figure.book_id == book_id)
    ).scalars().all()

    session.execute(
        _delete(FigureReference).where(FigureReference.book_id == book_id)
    )

    # Pass 1 label index — same as async path
    label_index = _build_global_label_index(sections)
    counters["theory_relinked_by_label"] = 0

    for fig in figures:
        counters["figures_seen"] += 1
        section_id = fig.section_id or ""
        if not section_id or section_id == "_orphan":
            counters["skipped_no_section"] += 1
            continue

        label_norm = (fig.normalized_label or _normalize_label(fig.figure_number) or "").strip()
        label_pattern = _build_label_pattern(label_norm)
        context = (fig.context_hint or "theory").lower()
        target_kind = "question" if context == "question" else "theory"

        # STRICT context routing — see async version for full notes.
        if target_kind == "theory":
            label_match = None
            if label_norm:
                cands = label_index.get(label_norm) or []
                label_match = _pick_label_match(cands, fig.page_number, sections_by_id)
            if label_match is not None:
                matched_sec_id, matched_block_idx = label_match
                # NOTE (E4 fix, sync variant): do NOT mutate fig.section_id.
                # Keep the extraction anchor; FigureReference below carries
                # the per-mention section_ref independently.
                session.add(FigureReference(
                    figure_id=fig.id, book_id=book_id, section_ref=matched_sec_id,
                    context="theory", question_id=None,
                    placeholder_text=fig.figure_number, link_method="auto",
                    placement_kind="inline", placement_block_idx=matched_block_idx,
                    placement_char_offset=None,
                ))
                counters["theory_inline"] += 1
                continue

            # SECTION FALLBACK — same logic as async variant: use the figure
            # extractor's page-detected section as a soft placement, flagged
            # so the UI can prompt user verification.
            if section_id and section_id in sections_by_id:
                session.add(FigureReference(
                    figure_id=fig.id, book_id=book_id, section_ref=section_id,
                    context="theory", question_id=None,
                    placeholder_text=fig.figure_number, link_method="auto",
                    placement_kind="page_fallback", placement_block_idx=None,
                    placement_char_offset=None,
                ))
                counters["theory_appended"] += 1
                continue

            session.add(FigureReference(
                figure_id=fig.id, book_id=book_id, section_ref=section_id,
                context="theory", question_id=None,
                placeholder_text=fig.figure_number, link_method="auto",
                placement_kind="unattached", placement_block_idx=None,
                placement_char_offset=None,
            ))
            counters["unattached"] += 1

        else:
            # Pass 1 (NEW): label-first GLOBAL match across ALL questions.
            # Mirrors theory branch. Handles "question in section A references
            # figure tagged to section B" case.
            if label_norm and label_pattern is not None:
                global_hit: tuple[Question, int] | None = None
                for q in questions:
                    combined = (q.raw_text or "") + "\n" + (getattr(q, "solution_text", "") or "")
                    offset = _find_inline_char_offset(combined, label_pattern)
                    if offset is not None:
                        global_hit = (q, offset)
                        break
                if global_hit is not None:
                    q, offset = global_hit
                    resolved_sec = q.section_ref or section_id
                    # NOTE (E4 fix, sync variant): no Figure.section_id mutation.
                    session.add(FigureReference(
                        figure_id=fig.id, book_id=book_id,
                        section_ref=resolved_sec,
                        context="question", question_id=q.id,
                        placeholder_text=fig.figure_number, link_method="auto",
                        placement_kind="inline", placement_block_idx=None,
                        placement_char_offset=offset,
                    ))
                    counters["question_inline"] += 1
                    continue

            # SECTION FALLBACK (question context) — same logic as async.
            if section_id and section_id in sections_by_id:
                session.add(FigureReference(
                    figure_id=fig.id, book_id=book_id, section_ref=section_id,
                    context="theory", question_id=None,
                    placeholder_text=fig.figure_number, link_method="auto",
                    placement_kind="page_fallback", placement_block_idx=None,
                    placement_char_offset=None,
                ))
                counters["theory_appended"] += 1
                continue

            session.add(FigureReference(
                figure_id=fig.id, book_id=book_id, section_ref=section_id,
                context="question", question_id=None,
                placeholder_text=fig.figure_number, link_method="auto",
                placement_kind="unattached", placement_block_idx=None,
                placement_char_offset=None,
            ))
            counters["unattached"] += 1

    session.flush()
    logger.info("figure_embedder (sync): book=%s %s", book_id, counters)
    return counters


__all__ = [
    "embed_figures_for_book",
    "embed_figures_for_book_sync",
    "_select_variant",
]
