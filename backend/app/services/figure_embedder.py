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


# ─── PURE PLACEMENT LOGIC ─────────────────────────────────────────
# Single source of truth for Pass 1 (labelled) + Pass 2 (unlabelled)
# figure placement decisions. Called by both the async and sync
# wrappers below. Pure function — no DB I/O. Eliminates the
# duplication that previously caused async/sync drift bugs (e.g.
# bf73339 + 3950df8 had to be fixed in both variants separately).

def _compute_figure_placements(
    figures,
    sections_by_id,
    questions,
    questions_by_section,
    label_index,
    book_id: UUID,
):
    """Walk every figure and decide where each FigureReference row
    should land. Returns (refs_to_insert, counters_dict). No DB I/O.

    Two passes per figure:

    Pass 2 (UNLABELLED) — figures whose regen_meta carries
    is_labelled=False were extracted without a "Figure X.Y" caption.
    Placement strategy:
      1. Resolve target section via fig.section_id or page_number → section
      2. context=question + question_no → attach to the matching
         question (searched globally; question_number is unique per
         book) at the END of question.raw_text
      3. context=theory + anchor_text → fuzzy-match the anchor against
         section.blocks[].c (60-char snippet, 30-char fallback);
         anchor_position="above" inserts BEFORE the matched block;
         "below"/"beside" inserts AFTER
      4. Ultimate fallback: section end (placement_kind="page_fallback")
      5. No section resolvable: unattached tray

    Pass 1 (LABELLED) — figures with a "Figure X.Y" caption. Default
    path. Routing follows context_hint strictly:
      context="theory" → only placed in theory body
      context="question" → only placed beside a question
    Within each, the priority is:
      1. Global label match against text bodies (theory blocks or
         question raw_text + solution_text)
      2. Section fallback (figure's page-detected section_id)
      3. (Theory only) orphan page-range fallback for figures whose
         extraction anchor was lost
      4. Unattached tray
    """
    counters = {
        "figures_seen": 0,
        "theory_inline": 0,
        "theory_appended": 0,
        "question_inline": 0,
        "question_appended": 0,
        "unattached": 0,
        "skipped_no_section": 0,
        "theory_relinked_by_label": 0,
    }
    new_refs: list[FigureReference] = []

    for fig in figures:
        counters["figures_seen"] += 1
        section_id = fig.section_id or ""

        # ─── Pass 2: positional placement for UNLABELLED figures ──
        pos_meta = fig.regen_meta if isinstance(fig.regen_meta, dict) else None
        if pos_meta and pos_meta.get("is_labelled") is False:
            anchor_text = (pos_meta.get("anchor_text") or "").strip()
            anchor_position = (pos_meta.get("anchor_position") or "below").lower()
            question_no = (pos_meta.get("question_no") or "").strip()
            ctx = (fig.context_hint or "theory").lower()

            target_sid = section_id if section_id and section_id != "_orphan" else ""
            if (not target_sid or target_sid not in sections_by_id) and fig.page_number is not None:
                for sid_iter, sec_iter in sections_by_id.items():
                    ps = getattr(sec_iter, "page_start", None)
                    pe = getattr(sec_iter, "page_end", None)
                    if ps is not None and pe is not None and ps <= fig.page_number <= pe:
                        target_sid = sid_iter
                        break

            placed = False

            # Question path — global question_number lookup. The
            # figure's page-based section_id often does NOT match the
            # question's section_ref (e.g. EXAMPLE 6.13 is filed under
            # its own section "6-example-6.13" while the figure lands
            # under the surrounding theory section). question_number
            # is globally unique per book, so a global search is safe.
            #
            # Normalise both sides: strip surrounding parens / dots /
            # whitespace and lowercase. Handles Gemini emitting "Q.39"
            # / "(39)" / "39." / "Q39" while the question_number field
            # in DB carries just "39", and vice versa. Without this
            # the match fails on any cosmetic difference.
            import re as _qre

            def _norm_qno(s: str | None) -> str:
                if not s:
                    return ""
                t = s.strip().lower()
                # Drop a leading "Q" prefix ("q.39", "q39", "q 39")
                t = _qre.sub(r"^q\.?\s*", "", t)
                # Drop wrapping parens / brackets / dots
                t = t.strip("().[]{} \t.")
                return t

            if ctx == "question" and question_no:
                want = _norm_qno(question_no)
                if want:
                    for q in questions:
                        if _norm_qno(q.question_number) == want:
                            char_end = len((q.raw_text or ""))
                            new_refs.append(FigureReference(
                                figure_id=fig.id, book_id=book_id,
                                section_ref=(q.section_ref or target_sid),
                                context="question", question_id=q.id,
                                placeholder_text=None, link_method="auto",
                                placement_kind="inline", placement_block_idx=None,
                                placement_char_offset=char_end,
                            ))
                            counters["question_inline"] += 1
                            placed = True
                            break
            if placed:
                continue

            # Theory path — STRICT full-anchor substring match.
            #
            # The prompt instructs Gemini to emit anchor_text as the
            # complete verbatim OCR wording of the printed sentence
            # adjacent to the image (no paraphrase, no truncation, no
            # length cap). Block.c contains the same OCR of the same
            # printed text. So a 100% substring of the normalised
            # anchor_text in the normalised block.c is the strong
            # signal that THIS block is the right anchor.
            #
            # We deliberately do NOT fall back to a shorter fuzzy
            # window. A wrong-position match (figure pinned to the
            # wrong block because a 30-char prefix happened to appear
            # somewhere else) is worse than the page_fallback below
            # — the user can spot a section-end figure and reposition,
            # but a silently-misplaced inline figure looks intentional.
            #
            # Normalisation kept minimal:
            #   - lowercase
            #   - collapse runs of whitespace to a single space
            # We deliberately keep punctuation and math symbols
            # (∠, ≤, π, etc.) — those are part of the anchor identity
            # and a real OCR-to-OCR match preserves them.
            import re as _re
            # Anchor-text fallback. Runs for theory figures AND for
            # question figures whose question_no didn't resolve to a DB
            # question above (typo, OCR drift, duplicate-question dedup).
            # Better to surface the image at the right block in the
            # section than to silently drop it.
            if anchor_text:
                # Subscript / superscript digits (and a few math letters)
                # commonly drift between Gemini passes — the figure
                # extractor may transcribe "l1, l2" while the theory
                # extractor uses Unicode "l₁, l₂". Both refer to the
                # same printed glyph. Normalising before substring match
                # preserves accuracy without weakening to fuzzy logic.
                _SUB_SUP_TR = str.maketrans({
                    "₀": "0", "₁": "1", "₂": "2", "₃": "3", "₄": "4",
                    "₅": "5", "₆": "6", "₇": "7", "₈": "8", "₉": "9",
                    "⁰": "0", "¹": "1", "²": "2", "³": "3", "⁴": "4",
                    "⁵": "5", "⁶": "6", "⁷": "7", "⁸": "8", "⁹": "9",
                    "₊": "+", "₋": "-", "⁺": "+", "⁻": "-",
                    "ₓ": "x", "ⁿ": "n",
                    # Math-glyph OCR confusion. Theory extractor sometimes
                    # transcribes the angle glyph ∠ as a capital Z (slab-
                    # serif visual similarity). Figure extractor reads ∠
                    # correctly. Map ∠ → z so anchors containing "∠AOC"
                    # match blocks containing "ZAOC" once both sides are
                    # lowercased. One-direction only (Z stays Z) — never
                    # broaden Z-as-letter to match ∠-as-glyph, that would
                    # false-match prose containing words like "Zone".
                    "∠": "z",
                })

                def _norm_match(text: str) -> str:
                    """Lowercase + collapse whitespace + flatten
                    subscript/superscript digits to ASCII. Keeps math
                    symbols (∠, ≤, π, etc.) and punctuation intact so the
                    substring check remains identity-preserving — we only
                    smooth over OCR-pass differences."""
                    if not text:
                        return ""
                    t = text.translate(_SUB_SUP_TR).lower()
                    return _re.sub(r"\s+", " ", t).strip()

                anchor_norm = _norm_match(anchor_text)

                # Anchor text is the PRIMARY signal for placement — page
                # assignment is just an initial hint. The linker sometimes
                # picks the wrong section when multiple sections share a
                # page (e.g. "5-introduction" page 3-3 and "5-basic-
                # concepts" page 3-6 both cover page 3; linker takes the
                # first → fig assigned to 5-introduction but the anchor
                # actually lives in 5-basic-concepts).
                #
                # Strategy: try the originally-assigned target_sid first
                # (cheap, usually correct), then expand to ANY section
                # whose page range contains the figure's page. First
                # 100%-substring match wins and the figure migrates to
                # that section. Still strict — wrong placement is worse
                # than section-end fallback.
                def _block_candidates(b: dict) -> list[str]:
                    """Return the normalised text(s) to try matching the
                    anchor against. For `def` blocks the candidate is
                    `term + ": " + c` (the printed form often combines
                    them) AND `c` alone (some def blocks are body-only).
                    For `list` blocks each `items[i]` string is its own
                    candidate (the text lives in items[], not c). Other
                    block types fall through to just `c`."""
                    out: list[str] = []
                    c = b.get("c") or ""
                    term = b.get("term") or ""
                    if b.get("t") == "def" and term:
                        out.append(_norm_match(f"{term}: {c}"))
                    if c:
                        out.append(_norm_match(c))
                    if b.get("t") == "list":
                        for it in (b.get("items") or []):
                            if isinstance(it, str) and it:
                                out.append(_norm_match(it))
                    if b.get("t") == "example":
                        # `prob` carries the problem statement — exactly
                        # what Gemini would quote as anchor_text when a
                        # figure sits next to a worked example.
                        prob = b.get("prob") or ""
                        if prob:
                            out.append(_norm_match(prob))
                    return [s for s in out if s]

                def _match_in_section(sid: str) -> int | None:
                    """Strict full-anchor substring match against blocks
                    of section ``sid``. Returns matched block index or None."""
                    sec_row = sections_by_id.get(sid)
                    if not sec_row:
                        return None
                    blocks = (sec_row.blocks if sec_row else None) or []
                    if not anchor_norm:
                        return None
                    for idx, b in enumerate(blocks):
                        if not isinstance(b, dict):
                            continue
                        for cand in _block_candidates(b):
                            if anchor_norm in cand:
                                return idx
                    return None

                matched_sid: str | None = None
                matched_idx: int | None = None

                # 1. Try the initially-assigned target_sid first.
                if target_sid:
                    idx = _match_in_section(target_sid)
                    if idx is not None:
                        matched_sid = target_sid
                        matched_idx = idx

                # 2. If no match in target_sid, scan other sections whose
                # page range includes the figure's page. Anchor text is
                # the source of truth — page assignment was just a hint.
                if matched_idx is None and fig.page_number is not None:
                    page = fig.page_number
                    for sid_iter, sec_iter in sections_by_id.items():
                        if sid_iter == target_sid:
                            continue  # already tried
                        ps = getattr(sec_iter, "page_start", None)
                        pe = getattr(sec_iter, "page_end", None)
                        if ps is None or pe is None:
                            continue
                        if not (ps <= page <= pe):
                            continue
                        idx = _match_in_section(sid_iter)
                        if idx is not None:
                            matched_sid = sid_iter
                            matched_idx = idx
                            break

                # 3. Last-resort full anchor scan ignoring page hint.
                # Catches schema bugs where a leaf section's page_end
                # didn't grow with its extracted block content (e.g.
                # blocks span pages 3-5 but page_end=3), leaving figures
                # on the un-covered pages with no page-overlapping leaf
                # to migrate to. Anchor_text is a full-sentence strict
                # substring match — extremely unlikely to false-match
                # across sections, so safe to widen the scan.
                if matched_idx is None and anchor_norm:
                    tried = {target_sid} if target_sid else set()
                    for sid_iter in sections_by_id:
                        if sid_iter in tried:
                            continue
                        idx = _match_in_section(sid_iter)
                        if idx is not None:
                            matched_sid = sid_iter
                            matched_idx = idx
                            break

                if matched_sid is not None and matched_idx is not None:
                    # anchor_position semantics — relative to where the
                    # printed anchor sentence sits with respect to the
                    # image on the original page:
                    #   "above"  → anchor sits ABOVE the image (image is
                    #              below the anchor) → figure renders
                    #              right AFTER the anchor block
                    #   "below"  → anchor sits BELOW the image (image is
                    #              above the anchor) → figure renders
                    #              right BEFORE the anchor block, i.e.
                    #              after the previous block
                    #   "beside" → anchor is at the same vertical level
                    #              as the image; conventionally we put
                    #              the image just BEFORE the anchor so
                    #              the reader sees the figure first,
                    #              then the descriptive sentence
                    # seed_draft_items_from_merge emits figures with
                    # placement_block_idx=i AFTER block i.
                    if anchor_position == "above":
                        placement_idx = matched_idx
                    elif anchor_position == "below":
                        placement_idx = max(0, matched_idx - 1)
                    else:  # "beside" or unknown
                        placement_idx = max(0, matched_idx - 1)
                    new_refs.append(FigureReference(
                        figure_id=fig.id, book_id=book_id,
                        section_ref=matched_sid,   # MIGRATE to the section where anchor was found
                        context="theory", question_id=None,
                        placeholder_text=None, link_method="auto",
                        placement_kind="inline",
                        placement_block_idx=placement_idx,
                        placement_char_offset=None,
                    ))
                    counters["theory_inline"] += 1
                    placed = True
            if placed:
                continue

            # Ultimate fallback — section end. Always emit as
            # context="theory" page_fallback so the renderer surfaces it
            # at the section tail. Previously a ctx="question" figure
            # that missed its question_no match landed here with
            # context="question" + question_id=None, which final_merge
            # silently dropped (it requires question_id to attach a
            # question figure). Routing through theory keeps the image
            # visible while preserving section context.
            if target_sid:
                new_refs.append(FigureReference(
                    figure_id=fig.id, book_id=book_id,
                    section_ref=target_sid,
                    context="theory",
                    question_id=None,
                    placeholder_text=None, link_method="auto",
                    placement_kind="page_fallback",
                    placement_block_idx=None,
                    placement_char_offset=None,
                ))
                counters["theory_appended"] += 1
                continue

            # No section at all → unattached tray
            new_refs.append(FigureReference(
                figure_id=fig.id, book_id=book_id, section_ref=section_id,
                context="theory", question_id=None,
                placeholder_text=None, link_method="auto",
                placement_kind="unattached", placement_block_idx=None,
                placement_char_offset=None,
            ))
            counters["unattached"] += 1
            continue

        # ─── Pass 1: label-first placement for LABELLED figures ──
        orphan = (not section_id or section_id == "_orphan")
        label_norm = (fig.normalized_label or _normalize_label(fig.figure_number) or "").strip()
        label_pattern = _build_label_pattern(label_norm)
        context = (fig.context_hint or "theory").lower()
        target_kind = "question" if context == "question" else "theory"

        if target_kind == "theory":
            # Label-first global match against theory blocks
            label_match = None
            if label_norm:
                cands = label_index.get(label_norm) or []
                label_match = _pick_label_match(cands, fig.page_number, sections_by_id)
            if label_match is not None:
                matched_sec_id, matched_block_idx = label_match
                new_refs.append(FigureReference(
                    figure_id=fig.id, book_id=book_id,
                    section_ref=matched_sec_id,
                    context="theory", question_id=None,
                    placeholder_text=fig.figure_number, link_method="auto",
                    placement_kind="inline",
                    placement_block_idx=matched_block_idx,
                    placement_char_offset=None,
                ))
                counters["theory_inline"] += 1
                continue

            # Section fallback — figure's page-detected section_id
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

            # Orphan page-range fallback
            if orphan and fig.page_number is not None:
                page = fig.page_number
                matched_section_id = None
                for sid_iter, sec_iter in sections_by_id.items():
                    ps = getattr(sec_iter, "page_start", None)
                    pe = getattr(sec_iter, "page_end", None)
                    if ps is not None and pe is not None and ps <= page <= pe:
                        matched_section_id = sid_iter
                        break
                if matched_section_id:
                    new_refs.append(FigureReference(
                        figure_id=fig.id, book_id=book_id,
                        section_ref=matched_section_id,
                        context="theory", question_id=None,
                        placeholder_text=fig.figure_number, link_method="auto",
                        placement_kind="page_fallback", placement_block_idx=None,
                        placement_char_offset=None,
                    ))
                    counters["theory_appended"] += 1
                    continue

            # No match anywhere → unattached
            new_refs.append(FigureReference(
                figure_id=fig.id, book_id=book_id, section_ref=section_id,
                context="theory", question_id=None,
                placeholder_text=fig.figure_number, link_method="auto",
                placement_kind="unattached", placement_block_idx=None,
                placement_char_offset=None,
            ))
            counters["unattached"] += 1
            continue

        # target_kind == "question"
        # Label-first global match in question text (raw_text + solution_text)
        if label_norm and label_pattern is not None:
            global_hit = None
            for q in questions:
                combined = (q.raw_text or "") + "\n" + (getattr(q, "solution_text", "") or "")
                offset = _find_inline_char_offset(combined, label_pattern)
                if offset is not None:
                    global_hit = (q, offset)
                    break
            if global_hit is not None:
                q, offset = global_hit
                resolved_sec = q.section_ref or section_id
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

        # Section fallback (question context) — appended as theory
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

        # Unattached
        new_refs.append(FigureReference(
            figure_id=fig.id, book_id=book_id, section_ref=section_id,
            context="question", question_id=None,
            placeholder_text=fig.figure_number, link_method="auto",
            placement_kind="unattached", placement_block_idx=None,
            placement_char_offset=None,
        ))
        counters["unattached"] += 1

    return new_refs, counters


# ─── ASYNC WRAPPER ────────────────────────────────────────────────
async def embed_figures_for_book(
    session: AsyncSession,
    book_id: UUID,
) -> dict[str, int]:
    """Walk every Figure for this book, compute its placement, and write
    the result back to ``figure_references``. Idempotent: rebuilds all
    placement rows from scratch so re-running after schema edits or new
    regen variants produces a consistent state. All placement decisions
    live in _compute_figure_placements() — this function is just the
    async DB I/O wrapper.
    """
    sections = (
        await session.execute(
            select(Section).where(Section.book_id == book_id)
        )
    ).scalars().all()
    sections_by_id = {s.section_id: s for s in sections}

    # Restrict to latest ready bank — older banks have stale question_ids
    # that newer extractions replace.
    from app.models.question_bank import QuestionBank
    # Accept both "ready" and "partial" — a partial bank still has
    # questions in the DB, and final_merge surfaces them. Restricting to
    # "ready" only would silently drop every question-attached unlabelled
    # figure when even one section's question worker failed.
    latest_bank = (
        await session.execute(
            select(QuestionBank)
            .where(QuestionBank.book_id == book_id)
            .where(QuestionBank.status.in_(["ready", "partial"]))
            .order_by(QuestionBank.created_at.desc())
            .limit(1)
        )
    ).scalars().first()
    questions: list[Question] = []
    if latest_bank is not None:
        questions = (
            await session.execute(
                select(Question)
                .where(Question.book_id == book_id)
                .where(Question.bank_id == latest_bank.id)
                .where(Question.regen_id.is_(None))
            )
        ).scalars().all()
    questions_by_section: dict[str, list[Question]] = {}
    for q in questions:
        if q.section_ref:
            questions_by_section.setdefault(q.section_ref, []).append(q)

    figures = (
        await session.execute(
            select(Figure).where(Figure.book_id == book_id)
        )
    ).scalars().all()

    # Rebuild references from scratch — cleaner than diffing; idempotent.
    await session.execute(
        delete(FigureReference).where(FigureReference.book_id == book_id)
    )

    label_index = _build_global_label_index(sections)

    refs, counters = _compute_figure_placements(
        figures, sections_by_id, questions,
        questions_by_section, label_index, book_id,
    )

    for ref in refs:
        session.add(ref)
    await session.flush()

    logger.info("figure_embedder: book=%s %s", book_id, counters)
    return counters


# ─── SYNC WRAPPER ─────────────────────────────────────────────────
def embed_figures_for_book_sync(session, book_id: UUID) -> dict[str, int]:
    """Sync variant for the v3 worker (which runs in a sync SQLAlchemy
    session context). Identical behaviour to the async wrapper —
    same placement logic via _compute_figure_placements(), just sync
    DB I/O.
    """
    from sqlalchemy import delete as _delete, select as _select

    sections = session.execute(
        _select(Section).where(Section.book_id == book_id)
    ).scalars().all()
    sections_by_id = {s.section_id: s for s in sections}

    from app.models.question_bank import QuestionBank
    # Accept both "ready" and "partial" — mirrors final_merge (a partial
    # bank still has live questions; restricting to "ready" silently drops
    # every question-attached unlabelled figure on books where one
    # section's question worker failed).
    latest_bank = session.execute(
        _select(QuestionBank)
        .where(QuestionBank.book_id == book_id)
        .where(QuestionBank.status.in_(["ready", "partial"]))
        .order_by(QuestionBank.created_at.desc())
        .limit(1)
    ).scalars().first()
    questions: list[Question] = []
    if latest_bank is not None:
        questions = session.execute(
            _select(Question)
            .where(Question.book_id == book_id)
            .where(Question.bank_id == latest_bank.id)
            .where(Question.regen_id.is_(None))
        ).scalars().all()
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

    label_index = _build_global_label_index(sections)

    refs, counters = _compute_figure_placements(
        figures, sections_by_id, questions,
        questions_by_section, label_index, book_id,
    )

    for ref in refs:
        session.add(ref)
    session.flush()

    logger.info("figure_embedder (sync): book=%s %s", book_id, counters)
    return counters


__all__ = [
    "embed_figures_for_book",
    "embed_figures_for_book_sync",
    "_select_variant",
]
