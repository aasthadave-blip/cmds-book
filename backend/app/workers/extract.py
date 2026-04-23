"""Celery tasks for the extraction pipeline.

Tasks:
  - analyse_book_task       — Gemini schema generation (P2)
  - extract_book_task       — Per-section Gemini OCR extraction in schema order
  - re_extract_section_task — User-triggered re-extract (OCR retry for a section)
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from uuid import UUID

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings
from app.core.storage import download_pdf
from app.models.book import Book
from app.models.job import Job
from app.models.regeneration import Regeneration
from app.models.section import Section
from app.schemas.analyser import AnalyserResult, BookSchema
from app.schemas.regen import RegenParams
from app.services.chunk_builder import flatten_sections
from app.services.regenerator import post_regen_qc, regenerate_section
from app.services.schema_builder import build_schema
from app.services.theory_extractor import (
    ExtractionResult,
    extract_section_with_qc,
    re_extract_with_fix,
)
from app.workers.celery_app import celery_app
from app.workers.runner import register as register_task

logger = logging.getLogger(__name__)

_sync_engine = create_engine(settings.SYNC_DATABASE_URL, pool_pre_ping=True)
SyncSession = sessionmaker(bind=_sync_engine, class_=Session, autoflush=False)


def _update_job(session: Session, job_id: UUID, **fields) -> None:
    job = session.get(Job, job_id)
    if job is None:
        return
    for k, v in fields.items():
        setattr(job, k, v)
    session.commit()



def _local_analyse_pdf(pdf_bytes: bytes) -> "AnalyserResult | None":
    """Compute PDF metadata locally via pymupdf for digital PDFs.

    Returns None if the PDF appears to be scanned/image-based (< 200 chars of
    extractable text across the first 10 pages). In that case, metadata is
    derived from the Gemini schema output instead.
    """
    import re as _re

    try:
        import pymupdf

        doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
        n_pages = len(doc)
        sample_pages = min(n_pages, 10)

        full_text = ""
        for i in range(sample_pages):
            try:
                full_text += doc[i].get_text() or ""
            except Exception:
                pass
        doc.close()

        if len(full_text.strip()) < 200:
            return None  # scanned/image — metadata derived from Gemini schema instead

        word_count = len(full_text.split())
        # Scale word estimate to full document
        estimated_words = int(word_count * n_pages / max(sample_pages, 1))

        has_equations = bool(
            _re.search(
                r"[=∫∑∏√∞≤≥∈∉∀∃]|\\frac|\\sqrt|\d+\s*[+\-\*/]\s*\d+",
                full_text,
            )
        )
        has_tables = bool(
            _re.search(r"\t\S+\t", full_text)
            or (full_text.count("\n") / max(len(full_text), 1)) > 0.15
        )
        has_diagrams = bool(
            _re.search(r"fig(?:ure)?\.?\s*\d|diagram|illustration|graph", full_text, _re.I)
        )

        return AnalyserResult(
            pdf_type="digital",
            estimated_pages=n_pages,
            estimated_words=estimated_words,
            # Title/subject come from the schema (P2) — leave empty here.
            document_title="",
            subject="",
            has_equations=has_equations,
            has_tables=has_tables,
            has_diagrams=has_diagrams,
        )
    except Exception as e:
        logger.warning("Local PDF analysis failed, will use Gemini schema metadata: %s", e)
        return None


# ── analyse_book (Sprint 1) ─────────────────────────────────────────────

@celery_app.task(name="analyse_book", bind=True)
def analyse_book_task(self, book_id: str, job_id: str) -> dict:
    book_uuid = UUID(book_id)
    job_uuid = UUID(job_id)

    with SyncSession() as session:
        _update_job(
            session,
            job_uuid,
            status="running",
            started_at=datetime.utcnow(),
            message="Downloading PDF",
            progress=5,
        )

        book = session.get(Book, book_uuid)
        if book is None or not book.pdf_url:
            _update_job(
                session,
                job_uuid,
                status="failed",
                error="Book or pdf_url missing",
                finished_at=datetime.utcnow(),
            )
            return {"ok": False, "reason": "book_not_found"}

        try:
            pdf_bytes = download_pdf(book.pdf_url)

            # Fast path: try to derive P1 metadata locally (pymupdf, no Claude call).
            # This works for digital PDFs and saves one full agent subprocess round-trip.
            _update_job(session, job_uuid, message="Analysing PDF", progress=15)
            local_result = _local_analyse_pdf(pdf_bytes)

            # Always run Gemini schema (handles digital, scanned, and image PDFs natively).
            # For digital PDFs we have local_result metadata; for scanned/image we derive
            # metadata from the schema output — no Claude P1 call needed for any type.
            pdf_type = "digital" if local_result is not None else "scanned"
            _update_job(session, job_uuid, message=f"Running Gemini schema ({pdf_type} PDF)", progress=30)
            schema = build_schema(pdf_bytes)

            # Derive AnalyserResult: use pymupdf fast-path if available, otherwise
            # build it entirely from the Gemini schema output (no Claude P1 needed).
            if local_result is not None:
                analyser_result = local_result
            else:
                import pymupdf as _pymupdf
                try:
                    _doc = _pymupdf.open(stream=pdf_bytes, filetype="pdf")
                    _n = len(_doc)
                    _doc.close()
                except Exception:
                    _n = schema.total_pages or 0
                analyser_result = AnalyserResult(
                    pdf_type="scanned",
                    estimated_pages=_n or schema.total_pages or 0,
                    estimated_words=(_n or 1) * 250,
                    document_title=schema.document_title,
                    subject=schema.subject or "",
                    has_equations=True,
                    has_tables=False,
                    has_diagrams=True,
                )

            # Always patch title/subject from schema (Gemini reads cover page correctly).
            analyser_result = AnalyserResult(
                **{**analyser_result.model_dump(),
                   "document_title": schema.document_title or analyser_result.document_title,
                   "subject": schema.subject or analyser_result.subject}
            )

            book.analyser = analyser_result.model_dump()
            book.schema = schema.model_dump()
            # Preserve the user-supplied title. Only fall back to the schema's
            # guessed title if the upload had none (or it's a bare filename stub).
            if not (book.title and book.title.strip()):
                book.title = schema.document_title or "Untitled"
            book.subject = schema.subject or book.subject
            book.status = "schema_ready"
            session.commit()

            _update_job(
                session,
                job_uuid,
                status="succeeded",
                progress=100,
                message="Schema ready for approval",
                finished_at=datetime.utcnow(),
            )
            return {"ok": True, "book_id": str(book_uuid)}

        except Exception as e:
            logger.exception("analyse_book_task failed")
            session.rollback()
            _update_job(
                session,
                job_uuid,
                status="failed",
                error=str(e)[:2000],
                finished_at=datetime.utcnow(),
            )
            book = session.get(Book, book_uuid)
            if book is not None:
                book.status = "failed"
                session.commit()
            return {"ok": False, "error": str(e)}


# ── extract_book (Sprint 2) ─────────────────────────────────────────────

@celery_app.task(name="extract_book", bind=True)
def extract_book_task(self, book_id: str, job_id: str) -> dict:
    """Per-section Gemini OCR extraction using page ranges from approved schema."""
    book_uuid = UUID(book_id)
    job_uuid = UUID(job_id)

    with SyncSession() as session:
        _update_job(
            session,
            job_uuid,
            status="running",
            started_at=datetime.utcnow(),
            message="Loading book + schema",
            progress=2,
        )
        book = session.get(Book, book_uuid)
        if book is None or book.schema is None:
            _update_job(
                session,
                job_uuid,
                status="failed",
                error="Book or schema missing",
                finished_at=datetime.utcnow(),
            )
            return {"ok": False, "reason": "book_or_schema_missing"}

        try:
            if not book.pdf_url:
                raise RuntimeError("PDF URL missing — cannot extract")
            pdf_bytes = download_pdf(book.pdf_url)

            schema = BookSchema(**book.schema)
            # all_sections: full flat list — used for both DB upsert and extraction.
            # chunk_builder bounds every section to its own content only (header → next
            # section start), so extracting all sections causes zero duplication — each
            # section only gets its own intro text, not its children's content.
            all_sections = flatten_sections(schema)
            if not all_sections:
                raise RuntimeError("No sections in schema — approve the schema before extracting")

            to_extract = all_sections

            # Top-level section IDs (direct children of schema root).
            # These get extracted with their FULL page range to give a complete
            # chapter overview — not trimmed like intermediate containers.
            top_level_ids = {s.id for s in schema.sections if s.type != "excluded"}

            # Upsert ALL section rows so page ranges and hierarchy are stored
            existing = {
                s.section_id: s
                for s in session.execute(
                    select(Section).where(Section.book_id == book_uuid)
                ).scalars().all()
            }
            for sec_schema in all_sections:
                sec = existing.get(sec_schema.id)
                if sec is None:
                    sec = Section(
                        book_id=book_uuid,
                        section_id=sec_schema.id,
                        title=sec_schema.title,
                        level=sec_schema.level,
                        page_start=sec_schema.page_start,
                        page_end=sec_schema.page_end,
                        blocks=[],
                        status="pending",
                        attempts=0,
                    )
                    session.add(sec)
                else:
                    sec.title = sec_schema.title
                    sec.level = sec_schema.level
                    sec.page_start = sec_schema.page_start
                    sec.page_end = sec_schema.page_end
                    sec.status = "pending"
            session.commit()

            total = len(to_extract)
            failed_section_ids: list[str] = []

            for i, sec_schema in enumerate(to_extract, start=1):
                # next_sec: the immediately following section in pre-order traversal.
                # For containers this is their first child; for leaves it's the next sibling.
                # Used for two things: (1) tell Gemini where to stop, (2) trim page_end.
                next_sec = to_extract[i] if i < total else None
                next_title = next_sec.title if next_sec else None

                # Effective page range:
                # - Top-level sections (direct schema roots): FULL page range.
                #   These are the "overall chapter" view — they contain everything.
                # - Leaf sections (no children): exact schema range, unchanged.
                # - Intermediate containers (has children, depth > 0): trimmed to
                #   next section's page_start — only their own intro text, not children.
                #   If next_sec.page_start unknown: fall back to schema page_end.
                # - Intermediate container with no intro text → empty → status="skipped".
                is_container = len(sec_schema.subsections) > 0
                is_top_level = sec_schema.id in top_level_ids

                if is_container and not is_top_level and next_sec and next_sec.page_start is not None:
                    effective_page_end = min(
                        sec_schema.page_end or next_sec.page_start,
                        next_sec.page_start,
                    )
                else:
                    effective_page_end = sec_schema.page_end

                progress = 10 + int(85 * (i - 1) / max(total, 1))
                _update_job(
                    session,
                    job_uuid,
                    message=f"Extracting {sec_schema.title} ({i}/{total})",
                    progress=progress,
                )

                try:
                    result: ExtractionResult = asyncio.run(
                        extract_section_with_qc(
                            section_id=sec_schema.id,
                            title=sec_schema.title,
                            level=sec_schema.level,
                            pdf_bytes=pdf_bytes,
                            page_start=sec_schema.page_start,
                            page_end=effective_page_end,
                            next_title=next_title,
                        )
                    )
                except Exception as e:
                    logger.exception("extract_section_with_qc crashed on %s", sec_schema.id)
                    sec = session.execute(
                        select(Section).where(
                            Section.book_id == book_uuid,
                            Section.section_id == sec_schema.id,
                        )
                    ).scalar_one_or_none()
                    if sec is not None:
                        sec.status = "failed"
                        sec.qc_local = {"pass": False, "failures": [str(e)[:500]]}
                        sec.attempts = (sec.attempts or 0) + 1
                    session.commit()
                    failed_section_ids.append(sec_schema.id)
                    continue

                sec = session.execute(
                    select(Section).where(
                        Section.book_id == book_uuid,
                        Section.section_id == sec_schema.id,
                    )
                ).scalar_one()
                sec.blocks = result.blocks
                sec.qc_local = result.qc.to_dict()
                sec.attempts = result.attempts

                # Intermediate containers with no intro text (content starts
                # immediately with a child section) produce empty blocks — correct,
                # not an error. Mark skipped so they don't pollute the QC fail list.
                # Top-level sections always have content (full chapter range), so
                # empty result there is a real failure.
                if not result.qc.pass_ and is_container and not is_top_level and not result.blocks:
                    sec.status = "skipped"
                else:
                    sec.status = "passed" if result.qc.pass_ else "failed"
                    if result.local_qc_fail:
                        failed_section_ids.append(sec_schema.id)

                session.commit()

            book.status = "ready"
            session.commit()
            _update_job(
                session,
                job_uuid,
                status="succeeded",
                progress=100,
                message=(
                    f"Extracted {total} sections; {len(failed_section_ids)} need review"
                    if failed_section_ids
                    else f"Extracted {total} sections"
                ),
                finished_at=datetime.utcnow(),
            )
            return {
                "ok": True,
                "book_id": str(book_uuid),
                "total": total,
                "failed": failed_section_ids,
            }

        except Exception as e:
            logger.exception("extract_book_task failed")
            session.rollback()
            _update_job(
                session,
                job_uuid,
                status="failed",
                error=str(e)[:2000],
                finished_at=datetime.utcnow(),
            )
            book = session.get(Book, book_uuid)
            if book is not None:
                book.status = "failed"
                session.commit()
            return {"ok": False, "error": str(e)}


# ── re_extract_section (Sprint 2) ───────────────────────────────────────

@celery_app.task(name="re_extract_section", bind=True)
def re_extract_section_task(self, section_id: str, job_id: str) -> dict:
    """Re-run Gemini OCR on one section using the stored page range."""
    section_uuid = UUID(section_id)
    job_uuid = UUID(job_id)

    with SyncSession() as session:
        _update_job(
            session,
            job_uuid,
            status="running",
            started_at=datetime.utcnow(),
            message="Re-extracting section",
            progress=10,
        )

        sec = session.get(Section, section_uuid)
        if sec is None:
            _update_job(
                session,
                job_uuid,
                status="failed",
                error="Section not found",
                finished_at=datetime.utcnow(),
            )
            return {"ok": False, "reason": "section_missing"}

        try:
            book = session.get(Book, sec.book_id)
            if book is None or not book.pdf_url:
                raise RuntimeError("Book or PDF URL missing")

            pdf_bytes = download_pdf(book.pdf_url)

            # Look up next section title from schema for boundary-aware extraction
            next_title: str | None = None
            if book.schema:
                from app.services.chunk_builder import flatten_sections as _flatten
                flat = _flatten(BookSchema(**book.schema))
                for idx, s in enumerate(flat):
                    if s.id == sec.section_id and idx + 1 < len(flat):
                        next_title = flat[idx + 1].title
                        break

            result: ExtractionResult = asyncio.run(
                re_extract_with_fix(
                    section_id=sec.section_id,
                    title=sec.title,
                    level=sec.level or 1,
                    pdf_bytes=pdf_bytes,
                    page_start=sec.page_start,
                    page_end=sec.page_end,
                    next_title=next_title,
                )
            )

            sec.blocks = result.blocks
            sec.qc_local = result.qc.to_dict()
            sec.attempts = (sec.attempts or 0) + 1
            sec.status = "passed" if result.qc.pass_ else "failed"
            session.commit()

            _update_job(
                session,
                job_uuid,
                status="succeeded",
                progress=100,
                message="Re-extraction complete",
                finished_at=datetime.utcnow(),
            )
            return {"ok": True, "section_id": str(section_uuid), "passed": result.qc.pass_}

        except Exception as e:
            logger.exception("re_extract_section_task failed")
            session.rollback()
            _update_job(
                session,
                job_uuid,
                status="failed",
                error=str(e)[:2000],
                finished_at=datetime.utcnow(),
            )
            return {"ok": False, "error": str(e)}


# ── regenerate_book (Sprint 3) ──────────────────────────────────────────

@celery_app.task(name="regenerate_book", bind=True)
def regenerate_book_task(
    self,
    book_id: str,
    job_id: str,
    regeneration_id: str,
    params: dict,
    section_ids: list[str] | None = None,
) -> dict:
    """Run P5 regeneration across sections with invariant split + post-regen QC.

    If ``section_ids`` is None → regenerate every leaf section in the book.
    If ``section_ids`` is provided → regenerate only those sections.
    Container sections (any schema section with non-excluded subsections) are
    always skipped: their content is fully covered by their children, so
    regenerating them would duplicate every paragraph and waste a slow,
    flaky high-token Gemini call.
    """
    book_uuid = UUID(book_id)
    job_uuid = UUID(job_id)
    regen_uuid = UUID(regeneration_id)

    with SyncSession() as session:
        _update_job(
            session,
            job_uuid,
            status="running",
            started_at=datetime.utcnow(),
            message="Loading sections",
            progress=2,
        )

        regen_row = session.get(Regeneration, regen_uuid)
        if regen_row is None:
            _update_job(
                session,
                job_uuid,
                status="failed",
                error=f"Regeneration row {regen_uuid} not found — transaction visibility issue",
                finished_at=datetime.utcnow(),
            )
            return {"ok": False, "reason": "regen_row_missing"}

        # Build container-section set from schema — these are skipped always.
        container_ids: set[str] = set()
        book_row = session.get(Book, book_uuid)
        if book_row is not None and book_row.schema:
            try:
                from app.schemas.analyser import BookSchema as _BookSchema
                schema_obj = _BookSchema(**book_row.schema)

                def _walk(nodes):
                    for n in nodes:
                        if any(c.type != "excluded" for c in (n.subsections or [])):
                            container_ids.add(n.id)
                        _walk(n.subsections or [])

                _walk(schema_obj.sections)
            except Exception as e:
                logger.warning("Could not parse schema to find containers: %s", e)

        all_sections = session.execute(
            select(Section).where(Section.book_id == book_uuid).order_by(Section.section_id)
        ).scalars().all()

        # Always drop container sections from the regen set
        sections = [s for s in all_sections if s.section_id not in container_ids]

        # If the caller specified section_ids, further filter to those
        if section_ids is not None:
            wanted = set(section_ids)
            unknown = wanted - {s.section_id for s in sections}
            if unknown:
                logger.warning(
                    "regenerate_book_task: ignoring unknown/container section_ids: %s",
                    sorted(unknown),
                )
            sections = [s for s in sections if s.section_id in wanted]

        if not sections:
            _update_job(
                session,
                job_uuid,
                status="failed",
                error="No sections to regenerate (after container + selection filter)",
                finished_at=datetime.utcnow(),
            )
            return {"ok": False, "reason": "no_sections"}

        try:
            rp = RegenParams(**params)
        except Exception as e:
            _update_job(
                session,
                job_uuid,
                status="failed",
                error=f"Invalid params: {e}",
                finished_at=datetime.utcnow(),
            )
            return {"ok": False, "error": str(e)}

        blocks_by_section: dict[str, list[dict]] = {}
        qc_drift: dict[str, dict] = {}
        total = len(sections)

        try:
            for i, sec in enumerate(sections, start=1):
                progress = 5 + int(85 * (i - 1) / max(total, 1))
                _update_job(
                    session,
                    job_uuid,
                    message=f"Regenerating {sec.section_id} ({i}/{total})",
                    progress=progress,
                )
                original = list(sec.blocks or [])
                if not original:
                    blocks_by_section[sec.section_id] = []
                    qc_drift[sec.section_id] = {"pass": True, "drifted": []}
                    continue
                try:
                    regenerated = asyncio.run(
                        regenerate_section(
                            section_id=sec.section_id,
                            section_title=sec.title,
                            blocks=original,
                            params=rp,
                        )
                    )
                except Exception as e:
                    logger.warning(
                        "regenerate_section failed for %s: %s", sec.section_id, e
                    )
                    # Fall back to originals for this section — guarantees no data loss
                    regenerated = [dict(b) for b in original]

                qc = post_regen_qc(original, regenerated)
                blocks_by_section[sec.section_id] = regenerated
                qc_drift[sec.section_id] = {
                    "pass": qc.pass_,
                    "drifted": qc.drifted_values,
                    "original_number_count": qc.original_number_count,
                }

            if regen_row is not None:
                regen_row.blocks_by_section = blocks_by_section
                regen_row.qc_drift = qc_drift
                session.commit()

            fail_count = sum(1 for r in qc_drift.values() if not r.get("pass"))
            _update_job(
                session,
                job_uuid,
                status="succeeded",
                progress=100,
                message=(
                    f"Regenerated {total} sections; {fail_count} flagged for drift"
                    if fail_count
                    else f"Regenerated {total} sections — no drift"
                ),
                finished_at=datetime.utcnow(),
            )
            return {
                "ok": True,
                "regeneration_id": str(regen_uuid),
                "total": total,
                "fail_count": fail_count,
            }

        except Exception as e:
            logger.exception("regenerate_book_task failed")
            session.rollback()
            _update_job(
                session,
                job_uuid,
                status="failed",
                error=str(e)[:2000],
                finished_at=datetime.utcnow(),
            )
            return {"ok": False, "error": str(e)}


# ── Inline-mode wrappers ────────────────────────────────────────────────
# The Celery-bound functions take `self` as first arg. For inline dispatch
# we need no-self wrappers; both modes share the same underlying logic.

def _analyse_book(book_id: str, job_id: str) -> dict:
    return analyse_book_task(None, book_id, job_id)  # type: ignore[arg-type]


def _extract_book(book_id: str, job_id: str) -> dict:
    return extract_book_task(None, book_id, job_id)  # type: ignore[arg-type]


def _re_extract_section(section_id: str, job_id: str) -> dict:
    return re_extract_section_task(None, section_id, job_id)  # type: ignore[arg-type]


def _regenerate_book(
    book_id: str,
    job_id: str,
    regeneration_id: str,
    params: dict,
    section_ids: list[str] | None = None,
) -> dict:
    return regenerate_book_task(None, book_id, job_id, regeneration_id, params, section_ids)  # type: ignore[arg-type]


register_task("analyse_book", _analyse_book)
register_task("extract_book", _extract_book)
register_task("re_extract_section", _re_extract_section)
register_task("regenerate_book", _regenerate_book)


# ── Figure extraction task ──────────────────────────────────────────────

@celery_app.task(bind=True, name="extract_figures")
def extract_figures_task(self, book_id: str, job_id: str) -> dict:
    from app.core.storage import download_pdf, upload_figure
    from app.models.figure import Figure
    from app.services.figure_extractor import extract_figures_for_section

    book_uuid = UUID(book_id)
    job_uuid = UUID(job_id)

    with SyncSession() as session:
        book = session.get(Book, book_uuid)
        if book is None:
            return {"ok": False, "reason": "book_not_found"}

        _update_job(session, job_uuid, status="running", progress=2, message="Loading PDF")

        try:
            pdf_bytes = download_pdf(book.pdf_url)
        except Exception as exc:
            _update_job(session, job_uuid, status="failed", error=str(exc), finished_at=datetime.utcnow())
            return {"ok": False, "error": str(exc)}

        sections = session.execute(
            select(Section)
            .where(Section.book_id == book_uuid)
            .where(Section.status == "ready")
            .order_by(Section.section_id)
        ).scalars().all()

        if not sections:
            _update_job(session, job_uuid, status="failed", error="No ready sections found", finished_at=datetime.utcnow())
            return {"ok": False, "reason": "no_sections"}

        total = len(sections)
        total_figures = 0

        try:
            for i, sec in enumerate(sections, start=1):
                progress = 5 + int(88 * (i - 1) / max(total, 1))
                _update_job(session, job_uuid, message=f"Extracting figures from {sec.section_id} ({i}/{total})", progress=progress)

                page_start = sec.page_start or 1
                page_end = sec.page_end or page_start

                try:
                    figures = asyncio.run(
                        extract_figures_for_section(
                            pdf_bytes=pdf_bytes,
                            section_id=sec.section_id,
                            page_start=page_start,
                            page_end=page_end,
                        )
                    )
                except Exception as exc:
                    logger.warning("Figure extraction failed for section %s: %s", sec.section_id, exc)
                    continue

                for fig_data in figures:
                    img_bytes = fig_data.pop("image_bytes", None)
                    figure = Figure(
                        book_id=book_uuid,
                        section_id=fig_data["section_id"],
                        figure_number=fig_data.get("figure_number"),
                        caption=fig_data.get("caption"),
                        description=fig_data.get("description"),
                        semantic_type=fig_data.get("semantic_type", "other"),
                        tags=fig_data.get("tags") or [],
                        page_number=fig_data.get("page_number"),
                        bounding_box=fig_data.get("bounding_box"),
                        status="extracted" if img_bytes else "no_image",
                    )
                    session.add(figure)
                    session.flush()  # get the UUID

                    if img_bytes:
                        filename = f"orig_{figure.id}.png"
                        key = upload_figure(img_bytes, str(book_uuid), sec.section_id, filename)
                        figure.image_url = key
                        session.commit()
                    else:
                        session.commit()

                    total_figures += 1

            _update_job(
                session, job_uuid,
                status="succeeded", progress=100,
                message=f"Extracted {total_figures} figures from {total} sections",
                finished_at=datetime.utcnow(),
            )
            return {"ok": True, "total_figures": total_figures}

        except Exception as exc:
            logger.exception("extract_figures_task failed")
            session.rollback()
            _update_job(session, job_uuid, status="failed", error=str(exc)[:2000], finished_at=datetime.utcnow())
            return {"ok": False, "error": str(exc)}


# ── Figure regeneration task ────────────────────────────────────────────

@celery_app.task(bind=True, name="regenerate_figures")
def regenerate_figures_task(self, book_id: str, job_id: str) -> dict:
    from app.core.storage import download_figure, upload_figure
    from app.models.figure import Figure
    from app.models.figure_regeneration import FigureRegeneration
    from app.services.figure_regenerator import REGEN_MODEL, redraw_figure

    book_uuid = UUID(book_id)
    job_uuid = UUID(job_id)

    with SyncSession() as session:
        book = session.get(Book, book_uuid)
        if book is None:
            return {"ok": False, "reason": "book_not_found"}

        figures = session.execute(
            select(Figure)
            .where(Figure.book_id == book_uuid)
            .where(Figure.image_url.isnot(None))
            .order_by(Figure.section_id, Figure.page_number)
        ).scalars().all()

        if not figures:
            _update_job(session, job_uuid, status="failed", error="No figures with images found", finished_at=datetime.utcnow())
            return {"ok": False, "reason": "no_figures"}

        total = len(figures)
        succeeded = 0

        _update_job(session, job_uuid, status="running", progress=2, message=f"Redrawing {total} figures")

        try:
            for i, fig in enumerate(figures, start=1):
                progress = 5 + int(88 * (i - 1) / max(total, 1))
                _update_job(session, job_uuid, message=f"Redrawing figure {i}/{total}", progress=progress)

                try:
                    img_bytes = download_figure(fig.image_url)
                    redrawn = asyncio.run(
                        redraw_figure(
                            image_bytes=img_bytes,
                            figure_number=fig.figure_number,
                            caption=fig.caption,
                            description=fig.description,
                            semantic_type=fig.semantic_type,
                        )
                    )
                except Exception as exc:
                    logger.warning("Redraw failed for figure %s: %s", fig.id, exc)
                    regen = FigureRegeneration(
                        book_id=book_uuid,
                        figure_id=fig.id,
                        section_id=fig.section_id,
                        status="failed",
                        model_used=REGEN_MODEL,
                    )
                    session.add(regen)
                    session.commit()
                    continue

                if redrawn:
                    filename = f"regen_{fig.id}.png"
                    key = upload_figure(redrawn, str(book_uuid), fig.section_id, filename)
                    regen = FigureRegeneration(
                        book_id=book_uuid,
                        figure_id=fig.id,
                        section_id=fig.section_id,
                        image_url=key,
                        model_used=REGEN_MODEL,
                        status="completed",
                    )
                    session.add(regen)
                    session.commit()
                    succeeded += 1

            _update_job(
                session, job_uuid,
                status="succeeded", progress=100,
                message=f"Redrawn {succeeded}/{total} figures",
                finished_at=datetime.utcnow(),
            )
            return {"ok": True, "succeeded": succeeded, "total": total}

        except Exception as exc:
            logger.exception("regenerate_figures_task failed")
            session.rollback()
            _update_job(session, job_uuid, status="failed", error=str(exc)[:2000], finished_at=datetime.utcnow())
            return {"ok": False, "error": str(exc)}


def _extract_figures(book_id: str, job_id: str) -> dict:
    return extract_figures_task(None, book_id, job_id)  # type: ignore[arg-type]


def _regenerate_figures(book_id: str, job_id: str) -> dict:
    return regenerate_figures_task(None, book_id, job_id)  # type: ignore[arg-type]


register_task("extract_figures", _extract_figures)
register_task("regenerate_figures", _regenerate_figures)
