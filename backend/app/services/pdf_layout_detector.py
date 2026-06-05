"""PDF column layout detector — autodetects single vs multi-column.

Today users have to manually flag PDFs as multi-column at upload time;
forgetting the flag routes a dense MCQ/JEE prep book through the
single-column prompt → schema mis-classification → downstream
extraction failures.

This module detects column count per page via PyMuPDF block geometry
and returns a majority-vote layout. Caller (schema_builder or upload
handler) uses the result as a SUGGESTION; user's explicit upload-time
flag still wins.

Pure CPU work via PyMuPDF. Sub-second on typical PDFs.

Detection algorithm:
1. For each sampled page (up to 10), get text blocks via page.get_text("blocks")
2. Compute the horizontal-midpoint distribution of block x-centres
3. If two distinct clusters → 2 columns; three distinct → 3 columns
4. Otherwise → single column
5. Majority across sampled pages wins

Used by `services/schema_builder.py` (SCHEMA Week 2 wiring).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


LayoutType = Literal["single", "double", "triple", "unknown"]


@dataclass(frozen=True)
class LayoutResult:
    """Result of column-layout detection on a PDF."""

    layout: LayoutType
    """Majority layout across sampled pages."""

    confidence: float
    """0.0-1.0; fraction of sampled pages that match `layout`."""

    pages_sampled: int
    """How many pages were inspected (capped at 10)."""

    per_page_layouts: list[LayoutType]
    """Per-page detection results, length == pages_sampled."""

    notes: str
    """Human-readable note about why this layout was chosen."""


def detect_layout(pdf_bytes: bytes, sample_count: int = 10) -> LayoutResult:
    """Detect predominant column layout of the PDF.

    Returns LayoutResult with majority layout + confidence. Never raises;
    returns layout='unknown' if detection fails.
    """
    try:
        import pymupdf
    except ImportError:
        return LayoutResult(
            layout="unknown",
            confidence=0.0,
            pages_sampled=0,
            per_page_layouts=[],
            notes="PyMuPDF not installed; cannot detect layout.",
        )

    if not pdf_bytes:
        return LayoutResult(
            layout="unknown",
            confidence=0.0,
            pages_sampled=0,
            per_page_layouts=[],
            notes="Empty PDF bytes.",
        )

    try:
        doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    except Exception as e:
        return LayoutResult(
            layout="unknown",
            confidence=0.0,
            pages_sampled=0,
            per_page_layouts=[],
            notes=f"Cannot open PDF: {e}",
        )

    try:
        total = doc.page_count
        if total == 0:
            return LayoutResult(
                layout="unknown",
                confidence=0.0,
                pages_sampled=0,
                per_page_layouts=[],
                notes="PDF has 0 pages.",
            )

        # Sample evenly across the document so we don't bias toward
        # cover/index pages which are often single-column even in
        # a multi-column book.
        sample_count = min(sample_count, total)
        if sample_count <= 0:
            return LayoutResult(
                layout="unknown",
                confidence=0.0,
                pages_sampled=0,
                per_page_layouts=[],
                notes="No pages to sample.",
            )

        if total <= sample_count:
            sample_indices = list(range(total))
        else:
            step = max(1, total // sample_count)
            sample_indices = list(range(0, total, step))[:sample_count]

        per_page_layouts: list[LayoutType] = []
        for page_idx in sample_indices:
            page = doc.load_page(page_idx)
            layout = _detect_page_layout(page)
            per_page_layouts.append(layout)

        # Majority vote across sampled pages (excluding "unknown" votes
        # from the denominator so a few blank pages don't dilute the
        # count).
        decisive_votes = [v for v in per_page_layouts if v != "unknown"]
        if not decisive_votes:
            return LayoutResult(
                layout="unknown",
                confidence=0.0,
                pages_sampled=len(per_page_layouts),
                per_page_layouts=per_page_layouts,
                notes="No page had detectable column structure.",
            )

        counts: dict[LayoutType, int] = {}
        for v in decisive_votes:
            counts[v] = counts.get(v, 0) + 1
        winner = max(counts, key=counts.get)  # type: ignore[arg-type]
        confidence = counts[winner] / len(decisive_votes)

        return LayoutResult(
            layout=winner,
            confidence=round(confidence, 2),
            pages_sampled=len(per_page_layouts),
            per_page_layouts=per_page_layouts,
            notes=(
                f"Majority of {len(decisive_votes)} decisive pages "
                f"are {winner} ({int(confidence * 100)}%)."
            ),
        )
    finally:
        doc.close()


def _detect_page_layout(page) -> LayoutType:
    """Detect column layout of a single page.

    Uses text-block geometry: blocks within the same vertical column
    have similar x-centres. We cluster x-centres and report the cluster
    count as the column count.
    """
    try:
        blocks = page.get_text("blocks") or []
    except Exception:
        return "unknown"

    # Filter to text blocks (block[6] is type; 0 = text). Each block:
    # (x0, y0, x1, y1, "text", block_no, block_type)
    text_blocks = [
        b for b in blocks
        if len(b) >= 7 and b[6] == 0 and b[4].strip()
    ]
    if len(text_blocks) < 3:
        # Not enough blocks to infer structure (cover page, title page,
        # mostly-image page).
        return "unknown"

    # Page width
    page_width = float(page.rect.width)
    if page_width <= 0:
        return "unknown"

    # Compute x-centre as a fraction of page width (0.0–1.0)
    x_centres = [
        ((b[0] + b[2]) / 2.0) / page_width
        for b in text_blocks
    ]

    # Cluster by simple bucketing into thirds.
    #   left third  (< 0.4)
    #   middle      (0.4-0.6)
    #   right third (> 0.6)
    # If blocks cluster around both LEFT and RIGHT thirds with similar
    # density → 2 columns. If left/middle/right → 3 columns. Otherwise
    # single column.
    left = sum(1 for x in x_centres if x < 0.4)
    middle = sum(1 for x in x_centres if 0.4 <= x <= 0.6)
    right = sum(1 for x in x_centres if x > 0.6)
    total = len(x_centres)
    if total == 0:
        return "unknown"

    left_frac = left / total
    middle_frac = middle / total
    right_frac = right / total

    # Triple: all three buckets have ≥20% AND none dominate (>60%)
    if (
        left_frac >= 0.2
        and middle_frac >= 0.2
        and right_frac >= 0.2
        and max(left_frac, middle_frac, right_frac) < 0.6
    ):
        return "triple"

    # Double: left ≥ 25% AND right ≥ 25% AND middle < 35%
    if left_frac >= 0.25 and right_frac >= 0.25 and middle_frac < 0.35:
        return "double"

    # Default: single column
    return "single"


__all__ = ["LayoutResult", "LayoutType", "detect_layout"]
