"""Celery tasks for the extraction pipeline.

Tasks:
  - analyse_book_task       — Gemini schema generation (P2)
  - extract_book_task       — Per-section Gemini OCR extraction in schema order
  - re_extract_section_task — User-triggered re-extract (OCR retry for a section)
"""

from __future__ import annotations

import asyncio
import logging
import os
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
from app.core.heartbeat import Heartbeat
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
    # Bump heartbeat on every progress/message update so the watchdog measures
    # time since real progress, not since job start. Without this, any run
    # longer than the watchdog's stale window (5 min) gets killed regardless
    # of how much work is actually happening.
    from datetime import datetime, timezone
    job.last_heartbeat_at = datetime.now(timezone.utc)
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

            # Read the user-set multi-column flag from book.analyser (set
            # at upload time via POST /api/books form param is_multi_column).
            # When True, build_schema routes to the multi-column-aware prompt
            # so dense MCQ-bank pages (MHT-CET / JEE) don't get mis-tagged
            # as "all explanations" and silently dropped. Defaults to False
            # so single-column books are processed exactly as before.
            existing_analyser = book.analyser or {}
            is_multi_column = bool(existing_analyser.get("is_multi_column", False))

            # Always run Gemini schema (handles digital, scanned, and image PDFs natively).
            # For digital PDFs we have local_result metadata; for scanned/image we derive
            # metadata from the schema output — no Claude P1 call needed for any type.
            pdf_type = "digital" if local_result is not None else "scanned"
            layout_tag = "multi-column" if is_multi_column else pdf_type
            _update_job(session, job_uuid, message=f"Running Gemini schema ({layout_tag} PDF)", progress=30)
            # Phase 5d (CONTRACT.md §2): mark schema stage as running. Lets
            # /quality endpoint distinguish "schema in flight" from "schema
            # not yet attempted". Watchdog (Phase 7) will look for stale
            # "running" stages.
            book.schema_status = "running"
            session.commit()
            # Heartbeat keeps the watchdog from killing long Gemini schema
            # calls for scanned PDFs (5–10 min is normal for image-based pages).
            with Heartbeat(
                job_uuid,
                base_msg=f"Running Gemini schema ({layout_tag} PDF)",
                progress=30,
            ):
                schema = build_schema(
                    pdf_bytes,
                    is_multi_column=is_multi_column,
                    pdf_title=(book.title or None),
                )

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

            # Preserve the upload-time multi-column flag on analyser
            # overwrite so re-analyse calls keep routing to the right prompt.
            new_analyser = analyser_result.model_dump()
            if is_multi_column:
                new_analyser["is_multi_column"] = True
            book.analyser = new_analyser

            # Lock previously-extracted section_ids when re-analysing an
            # existing book. The freshly generated schema can carry new IDs
            # (e.g. Gemini moves from "5-introduction" to "5.3"); without
            # alignment, all DB sections become orphans of the new schema
            # and the sidebar / merge / export silently lose them. Match
            # each new node to an existing DB Section row by (title + page
            # range) and force the new node's id back to the extraction-time
            # id. No-op when there are no existing sections (first analyse).
            try:
                from app.services.schema_alignment import (
                    align_schema_ids_to_existing_sections,
                )
                existing_secs = (
                    session.execute(
                        select(Section).where(Section.book_id == book.id)
                    )
                ).scalars().all()
                if existing_secs:
                    schema, _remap = align_schema_ids_to_existing_sections(
                        schema, existing_secs
                    )
            except Exception as e:
                logger.warning(
                    "schema_alignment failed (continuing with fresh IDs): %s", e
                )

            book.schema = schema.model_dump()
            # Preserve the user-supplied title. Only fall back to the schema's
            # guessed title if the upload had none (or it's a bare filename stub).
            if not (book.title and book.title.strip()):
                book.title = schema.document_title or "Untitled"
            book.subject = schema.subject or book.subject
            # Phase 5d: schema completed successfully. Downstream stages stay
            # "pending" until extract_book picks them up.
            book.schema_status = "done"
            # Phase 5e: derive book.status from per-stage fields. Legacy
            # "schema_ready" is preserved when downstream stages haven't
            # been triggered yet (derive_book_status returns "queued" here,
            # but to maintain backward compatibility with the schema-review
            # gate, we keep the old "schema_ready" literal in this slot).
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

            # Phase 6 (ORCH Day 3) — auto-fire post-schema orchestrator.
            # coordinate_extraction is idempotent and decides whether to
            # actually kick off theory based on current state. Removes
            # the dependency on a polling frontend to call /approve —
            # schema completion now triggers downstream extraction
            # without any UI interaction.
            try:
                from app.workers.runner import dispatch
                dispatch("coordinate_extraction", str(book_uuid))
                logger.info(
                    "analyse_book: dispatched coordinator for book=%s",
                    book_uuid,
                )
            except Exception as e:
                # Coordinator dispatch failure should not fail the
                # schema task — user can manually re-fire via /approve
                # as a fallback.
                logger.warning(
                    "analyse_book: coordinator dispatch failed (continuing): %s",
                    e,
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
                # Phase 5d: schema-stage failure (analyse_book is the schema
                # task — extract_book covers theory/questions/figures).
                book.schema_status = "failed"
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

            # Phase 5d (CONTRACT.md §2): mark theory stage as running.
            book.theory_status = "running"
            session.commit()

            schema = BookSchema(**book.schema)
            # all_sections: full flat list — used for both DB upsert and extraction.
            # chunk_builder bounds every section to its own content only (header → next
            # section start), so extracting all sections causes zero duplication — each
            # section only gets its own intro text, not its children's content.
            all_sections = flatten_sections(schema)
            if not all_sections:
                raise RuntimeError("No sections in schema — approve the schema before extracting")

            # Skip pure Cat A (questions-only) sections from theory extraction.
            # They're handled by the question pipeline as placeholders. Calling
            # Gemini on them wastes attempts (Gemini correctly returns nothing
            # per the placeholder rule, QC fails, retries burn out).
            # Sections with "theory" in content_types (including Mixed
            # "theory + questions") DO get extracted as theory.
            to_extract = [
                s for s in all_sections
                if "theory" in (s.content_types or ["theory"])
            ]

            skipped_cat_a = len(all_sections) - len(to_extract)
            if skipped_cat_a > 0:
                logger.info(
                    "Skipping %d Cat A (questions-only) sections from theory extraction "
                    "(handled by question pipeline as placeholders)",
                    skipped_cat_a,
                )

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

            # ── PARALLEL SECTION EXTRACTION ────────────────────────────────
            # Each section's Gemini call is an independent HTTP request — no
            # shared context, no cross-section bleeding (impossible by design,
            # since Gemini doesn't keep state between requests). Concurrency
            # controlled by THEORY_SECTION_CONCURRENCY env var (default 8).
            # Set to 1 to revert to sequential behaviour byte-for-byte.
            #
            # SAFETY GUARDS (all preserved from sequential version):
            #   1. Same theory_extractor.extract_section_with_qc call per
            #      section — same prompt, same Pro model, same retry policy.
            #   2. Each task uses its OWN SyncSession for DB writes (no
            #      shared session across tasks → no race / lock corruption).
            #   3. Failures are isolated per-section (return_exceptions=True)
            #      — one section's crash does not abort the batch.
            #   4. Per-section retry inside extract_section_with_qc is
            #      untouched (MAX_ATTEMPTS=3 + backoff).
            #   5. Post-write section_id verification (paranoid guard) catches
            #      any cross-section write corruption with a loud assert.
            #   6. Schema postpass + example_linker still run AFTER all
            #      sections complete — same invariant as the sequential loop.
            CONCURRENCY = max(1, int(os.environ.get("THEORY_SECTION_CONCURRENCY", "8")))

            # Pre-compute per-section payloads sequentially (cheap — just
            # arithmetic + schema lookups, no Gemini calls). Captures the
            # current iteration-order-dependent effective_page_end values
            # so the parallel phase has all the data it needs.
            section_payloads: list[dict] = []
            for i, sec_schema in enumerate(to_extract, start=1):
                next_sec = to_extract[i] if i < total else None
                next_title = next_sec.title if next_sec else None
                is_container = len(sec_schema.subsections) > 0
                if is_container and next_sec and next_sec.page_start is not None:
                    effective_page_end = min(
                        sec_schema.page_end or next_sec.page_start,
                        next_sec.page_start,
                    )
                elif (not is_container) and next_sec and next_sec.page_start is not None:
                    effective_page_end = max(
                        sec_schema.page_end or 0,
                        next_sec.page_start,
                    )
                else:
                    effective_page_end = sec_schema.page_end
                section_payloads.append({
                    "sec_schema": sec_schema,
                    "next_title": next_title,
                    "effective_page_end": effective_page_end,
                    "is_container": is_container,
                    "idx": i,
                })

            # Shared mutable counter for monotonic progress reporting. Each
            # task increments under the lock and writes the new progress
            # via its own SyncSession.
            done_counter = {"n": 0}
            counter_lock = asyncio.Lock()

            async def _extract_and_persist(payload: dict) -> tuple[str, str]:
                """Run one section through extract_section_with_qc and
                persist the result. Returns (section_id, outcome) where
                outcome is one of: ok / failed / skipped / crashed."""
                sec_schema = payload["sec_schema"]
                is_container = payload["is_container"]
                try:
                    result: ExtractionResult = await extract_section_with_qc(
                        section_id=sec_schema.id,
                        title=sec_schema.title,
                        level=sec_schema.level,
                        pdf_bytes=pdf_bytes,
                        page_start=sec_schema.page_start,
                        page_end=payload["effective_page_end"],
                        next_title=payload["next_title"],
                        is_container=payload["is_container"],
                    )
                except Exception as e:
                    logger.exception(
                        "extract_section_with_qc crashed on %s", sec_schema.id
                    )
                    # Persist failure in this task's own session
                    with SyncSession() as own_session:
                        sec = own_session.execute(
                            select(Section).where(
                                Section.book_id == book_uuid,
                                Section.section_id == sec_schema.id,
                            )
                        ).scalar_one_or_none()
                        if sec is not None:
                            sec.status = "failed"
                            sec.qc_local = {
                                "pass": False,
                                "failures": [str(e)[:500]],
                            }
                            sec.attempts = (sec.attempts or 0) + 1
                        own_session.commit()
                    return (sec_schema.id, "crashed")

                # Persist success in this task's own session
                outcome: str
                with SyncSession() as own_session:
                    sec = own_session.execute(
                        select(Section).where(
                            Section.book_id == book_uuid,
                            Section.section_id == sec_schema.id,
                        )
                    ).scalar_one()
                    sec.blocks = result.blocks
                    sec.qc_local = result.qc.to_dict()
                    sec.attempts = result.attempts
                    # Container parents (sections with non-excluded children)
                    # legitimately return empty blocks when the parent-vs-leaf
                    # rule (f0a574a) finds no content between the parent
                    # heading and the first child — children carry it all.
                    # Mark these as "passed" with empty blocks so the heading
                    # stays visible in the sidebar + Preview / Composer /
                    # DOCX with a blank body, preserving the schema
                    # hierarchy. Previously these were "skipped" and hidden,
                    # making the chapter look like sections were missing.
                    if is_container and not result.blocks:
                        sec.status = "passed"
                        outcome = "passed"
                    else:
                        sec.status = "passed" if result.qc.pass_ else "failed"
                        outcome = "passed" if result.qc.pass_ else "failed"
                    own_session.commit()
                    # PARANOID: verify section_id matches after commit. If
                    # something somehow corrupted the write, fail LOUDLY.
                    check = own_session.execute(
                        select(Section).where(
                            Section.book_id == book_uuid,
                            Section.section_id == sec_schema.id,
                        )
                    ).scalar_one()
                    if check.section_id != sec_schema.id:
                        raise RuntimeError(
                            f"Post-write section_id mismatch: "
                            f"expected={sec_schema.id} got={check.section_id}"
                        )

                # Atomic progress update — each task contributes one tick.
                # Progress goes 10 → 95 as sections complete.
                async with counter_lock:
                    done_counter["n"] += 1
                    n_done = done_counter["n"]
                with SyncSession() as own_session:
                    prog = 10 + int(85 * n_done / max(total, 1))
                    _update_job(
                        own_session,
                        job_uuid,
                        message=f"Extracted {n_done}/{total} sections",
                        progress=prog,
                    )
                    own_session.commit()
                return (sec_schema.id, outcome)

            async def _run_parallel() -> list[tuple[str, str]]:
                sem = asyncio.Semaphore(CONCURRENCY)

                async def gated(p):
                    async with sem:
                        return await _extract_and_persist(p)

                # return_exceptions=True so one section's unexpected error
                # in the OUTER plumbing (not the Gemini call — that's caught
                # inside) doesn't bring down the whole batch.
                return await asyncio.gather(
                    *[gated(p) for p in section_payloads],
                    return_exceptions=True,
                )

            # One outer Heartbeat covers the whole parallel phase so the
            # watchdog doesn't kill the job during long Gemini calls. With
            # concurrency=4, expected wall-clock is total_sections/4 × per-
            # section time. Heartbeat thread is independent of the worker.
            with Heartbeat(
                job_uuid,
                base_msg=(
                    f"Extracting {total} sections "
                    f"(parallel × {CONCURRENCY})"
                ),
                progress=10,
            ):
                outcomes = asyncio.run(_run_parallel())

            # Tally failures and crashes for the final job summary
            for outcome in outcomes:
                if isinstance(outcome, BaseException):
                    # An unexpected error inside _extract_and_persist itself
                    # (not the Gemini call — that's caught inside). Log and
                    # treat the whole batch as having had a failure; we
                    # cannot determine which section_id it came from at this
                    # layer, so the message will reflect it at job-end.
                    logger.exception("section task plumbing crashed: %s", outcome)
                    continue
                section_id, status = outcome
                if status in ("failed", "crashed"):
                    failed_section_ids.append(section_id)

            # Phase 5d / ORCH Day 4 — derive theory_status from outcomes
            # but DEFER persisting it until after example_linker +
            # figure_embedder finish their tail work. Today's race:
            # theory_status="done" committed here → frontend polling sees
            # "done" → fires kickRestParallel → questions+figures workers
            # start while figure_embedder (in tail below) is still mid-
            # run, causing two embedder passes to race on Section blocks.
            # Strict ORCH design: theory_status remains "running" in DB
            # until AFTER the tail completes, then committed together
            # with theory_finalized_at — the coordinator gate field.
            if total == 0:
                _derived_theory_status = "done"
            elif len(failed_section_ids) == 0:
                _derived_theory_status = "done"
            elif len(failed_section_ids) == total:
                _derived_theory_status = "failed"
            else:
                _derived_theory_status = "partial"
            # NB: book.theory_status NOT set here; book.status NOT updated
            # here. Both deferred to the post-tail finalization block
            # below (after example_linker + figure_embedder).

            # Inject example/exercise placeholder chips into parent theory
            # sections. Idempotent post-processing — does not modify
            # transcribed theory blocks beyond inserting `question_ref`
            # references for child `<parent>-example-N` sections.
            try:
                from app.services.example_linker import link_examples_to_theory_sync
                link_examples_to_theory_sync(session, book_uuid)
            except Exception as e:
                logger.warning("example_linker failed (book=%s): %s", book_uuid, e)

            # Auto-embed figures into the freshly-extracted theory blocks.
            # If figures were extracted before theory, this is when the
            # figure_references finally land in the right sections. No-op if
            # the book has no figures yet — embedder is idempotent.
            try:
                from app.services.figure_embedder import embed_figures_for_book_sync
                embed_counters = embed_figures_for_book_sync(session, book_uuid)
                logger.info(
                    "[embed] post-theory book=%s %s", book_uuid, embed_counters
                )
            except Exception as e:
                logger.warning(
                    "figure_embedder failed post-theory (book=%s): %s", book_uuid, e
                )

            # Phase 6 (ORCH Day 4) — FINALIZE theory after tail completes.
            # example_linker + figure_embedder have flushed; it's now
            # safe to mark theory truly done. theory_finalized_at is the
            # coordinator's gate field for dispatching questions+figures.
            book.theory_status = _derived_theory_status
            book.theory_finalized_at = datetime.utcnow()
            from app.services.book_status import derive_book_status
            derived = derive_book_status(book)
            book.status = "extracting" if derived == "queued" else derived
            session.commit()

            # Step the state machine forward — coordinator typically
            # dispatches extract_questions_v3 + extract_figures_v2 in
            # parallel from here. Idempotent; safe even if frontend
            # also polled and tried to fire kickRestParallel.
            try:
                from app.workers.runner import dispatch
                dispatch("coordinate_extraction", str(book_uuid))
                logger.info(
                    "extract_book: theory finalized for book=%s status=%s "
                    "— dispatched coordinator",
                    book_uuid, _derived_theory_status,
                )
            except Exception as e:
                logger.warning(
                    "extract_book: coordinator dispatch failed (continuing): %s",
                    e,
                )

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
                # Phase 5d: extract_book failure = theory stage failure
                # (questions/figures have their own tasks + status writes).
                book.theory_status = "failed"
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

            # Look up next section title AND page_start from schema for
            # boundary-aware extraction. For leaf sections, extend page_end
            # to next sibling's page_start so prose continuing onto the page
            # where the next section starts is captured (next_title acts as
            # the STOP anchor in the prompt). Mirrors the logic in
            # extract_book_task.
            next_title: str | None = None
            next_page_start: int | None = None
            sec_is_container = False
            if book.schema:
                from app.services.chunk_builder import flatten_sections as _flatten
                book_schema_obj = BookSchema(**book.schema)
                flat = _flatten(book_schema_obj)
                for idx, s in enumerate(flat):
                    if s.id == sec.section_id:
                        sec_is_container = len(s.subsections) > 0
                        if idx + 1 < len(flat):
                            next_title = flat[idx + 1].title
                            next_page_start = flat[idx + 1].page_start
                        break

            # Same effective_page_end logic as extract_book_task.
            if sec_is_container and next_page_start is not None:
                effective_page_end = min(
                    sec.page_end or next_page_start,
                    next_page_start,
                )
            elif (not sec_is_container) and next_page_start is not None:
                effective_page_end = max(
                    sec.page_end or 0,
                    next_page_start,
                )
            else:
                effective_page_end = sec.page_end

            result: ExtractionResult = asyncio.run(
                re_extract_with_fix(
                    section_id=sec.section_id,
                    title=sec.title,
                    level=sec.level or 1,
                    pdf_bytes=pdf_bytes,
                    page_start=sec.page_start,
                    page_end=effective_page_end,
                    next_title=next_title,
                    is_container=sec_is_container,
                )
            )

            sec.blocks = result.blocks
            sec.qc_local = result.qc.to_dict()
            sec.attempts = (sec.attempts or 0) + 1
            sec.status = "passed" if result.qc.pass_ else "failed"
            session.commit()

            # Re-link example placeholder chips into parent theory after a
            # single-section re-extract — keeps inline chips in sync.
            try:
                from app.services.example_linker import link_examples_to_theory_sync
                link_examples_to_theory_sync(session, sec.book_id)
            except Exception as e:
                logger.warning("example_linker failed after re_extract (section=%s): %s", section_uuid, e)

            # Re-run figure embedder so anchor matches against the freshly
            # re-extracted theory blocks land in this section. Without
            # this, the figure_references for this section's figures stay
            # stale pointing at the OLD blocks. Best-effort.
            try:
                from app.services.figure_embedder import embed_figures_for_book_sync
                embed_figures_for_book_sync(session, sec.book_id)
            except Exception as e:
                logger.warning(
                    "figure_embedder failed after theory re_extract "
                    "(section=%s): %s", section_uuid, e,
                )

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

        # Container + example sections are dropped from "regen all" so we
        # don't waste a Gemini call on parent sections whose content is fully
        # covered by their children, or on worked-example sections that
        # belong to the questions pipeline. When the user EXPLICITLY picks
        # section_ids, honor their choice regardless — they know what they
        # want and silently dropping the selection is hostile.
        _EX_PREFIXES = (
            "example ", "worked example", "exercise ", "problem ",
            "question ", "illustration ", "solved example",
        )
        def _is_example_section(s) -> bool:
            t = (getattr(s, "title", None) or "").strip().lower()
            return any(t.startswith(p) for p in _EX_PREFIXES)

        if section_ids is None:
            # "Regen all" — drop containers + example sections
            sections = [s for s in all_sections if s.section_id not in container_ids]
            before_n = len(sections)
            sections = [s for s in sections if not _is_example_section(s)]
            skipped_n = before_n - len(sections)
            if skipped_n:
                logger.info(
                    "regenerate_book_task: skipping %d example/exercise sections "
                    "from theory regen 'all' scope",
                    skipped_n,
                )
        else:
            # Explicit selection — honor whatever the user picked, even
            # containers / examples. They asked for it.
            sections = list(all_sections)

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

        # ─── RECAP pre-loop (v3 only, opt-in) ─────────────────────────
        # Detect chapter-end Points-to-Remember / Summary / Key Takeaways
        # sections, extract their bullets, assign each bullet to its best
        # matching topic via deterministic Jaccard match (no extra LLM
        # call, no double-assignment), then SKIP those source sections
        # from the per-section regen output. Orphan bullets get appended
        # at chapter end as a fallback "Key Takeaways" subsection.
        per_section_keypoints: dict[str, list[str]] = {}
        orphan_keypoints: list[str] = []
        ptr_source_section_ids: set[str] = set()
        try:
            from app.services.recap_config import (
                active_redistribute_rules,
                assign_bullets_to_sections,
                detect_redistribute_source_sections,
            )
            # Worker recap pre-loop fires whenever the request opts in via
            # recap_rule_ids. We no longer gate on the prompt-version env
            # var: the live regenerator.txt is the source of truth (the
            # operator can swap it for v3 content to enable recap-aware
            # LLM behavior). If the live prompt happens to be v1, recap
            # rule ids will still be processed by the worker but the LLM
            # will not honor them — harmless, just no recap blocks emitted.
            if rp.recap_rule_ids:
                section_tuples = [
                    (s.section_id, s.title or "", list(s.blocks or []))
                    for s in sections
                ]
                src_ids, bullets = detect_redistribute_source_sections(
                    section_tuples, rp.recap_rule_ids
                )
                ptr_source_section_ids = set(src_ids)
                if bullets:
                    # Only match against sections that are NOT the source.
                    target_pool = [
                        (sid, title, blocks)
                        for sid, title, blocks in section_tuples
                        if sid not in ptr_source_section_ids
                    ]
                    per_section_keypoints, orphan_keypoints = (
                        assign_bullets_to_sections(bullets, target_pool)
                    )
                    logger.info(
                        "recap redistribute: %d bullets from %d source sections; "
                        "assigned to %d sections, %d orphans",
                        len(bullets),
                        len(src_ids),
                        sum(1 for v in per_section_keypoints.values() if v),
                        len(orphan_keypoints),
                    )
        except Exception as e:
            # Recap is best-effort — never block the regen if config fails.
            logger.warning("recap pre-loop skipped: %s", e)

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
                # Suppress PTR source sections — their content has been
                # redistributed into other sections via per_section_keypoints.
                # Write [] as the SENTINEL so the final-merge layer skips
                # this section entirely instead of falling back to the
                # original Section.blocks (without sentinel the merger
                # would see "no regen for this sid" and serve original).
                if sec.section_id in ptr_source_section_ids:
                    logger.info(
                        "recap: suppressing PTR source section %s (bullets redistributed)",
                        sec.section_id,
                    )
                    blocks_by_section[sec.section_id] = []
                    qc_drift[sec.section_id] = {
                        "pass": True,
                        "drifted": [],
                        "note": "section bullets redistributed via recap",
                    }
                    continue
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
                            assigned_keypoints=per_section_keypoints.get(
                                sec.section_id, []
                            ),
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

            # ─── RECAP post-loop: standalone-section RENAME promotion ──
            # Some textbooks extract Konnect / Note / Info Edge / Info Bytes
            # as SIBLING SECTIONS instead of inline callouts. For these,
            # take the regenerated section's content, fold it as a renamed
            # subsection (Fun Fact / Remember / Food for Thought / …) at
            # the END of the preceding topic, and drop the source section.
            # Pure post-processing — no extra LLM call, no prompt directive.
            promote_skip_ids: list[str] = []
            try:
                from app.services.recap_config import active_rename_rules

                active_renames = active_rename_rules(rp.recap_rule_ids or [])
                if active_renames:
                    # Build a lowercase source-label → target-label map.
                    label_to_target: dict[str, str] = {}
                    for r in active_renames:
                        for src in r["source_labels"]:
                            label_to_target[src.lower()] = r["label"]

                    # Document-order sid list + title lookup
                    section_order = [s.section_id for s in sections]
                    sid_to_title = {
                        s.section_id: (s.title or "").strip() for s in sections
                    }

                    for idx, sid in enumerate(section_order):
                        title = sid_to_title.get(sid, "")
                        target_label = label_to_target.get(title.lower())
                        if not target_label:
                            continue
                        # Find preceding "real" topic — skip other promote
                        # candidates and PTR sources.
                        prev_idx = idx - 1
                        while prev_idx >= 0:
                            prev_sid = section_order[prev_idx]
                            prev_title = sid_to_title.get(prev_sid, "")
                            is_rename_source = (
                                prev_title.lower() in label_to_target
                            )
                            is_ptr_source = prev_sid in ptr_source_section_ids
                            if not is_rename_source and not is_ptr_source:
                                break
                            prev_idx -= 1
                        if prev_idx < 0:
                            # No preceding topic — leave as-is (rare; first
                            # section being a Konnect would be unusual).
                            continue
                        target_sid = section_order[prev_idx]

                        # Extract bullets from THIS section's regenerated
                        # blocks. Lists → items; p/kp → bullets verbatim.
                        src_blocks = blocks_by_section.get(sid, []) or []
                        bullets: list[str] = []
                        for b in src_blocks:
                            bt = b.get("t")
                            if bt == "list":
                                for item in (b.get("items") or []):
                                    if item and str(item).strip():
                                        bullets.append(str(item).strip())
                            elif bt in ("p", "kp"):
                                c = (b.get("c") or "").strip()
                                if c:
                                    bullets.append(c)
                        if not bullets:
                            continue

                        # Append renamed subsection to target topic's blocks
                        target_blocks = list(blocks_by_section.get(target_sid, []) or [])
                        target_blocks.append({"t": "h3", "c": target_label})
                        target_blocks.append({"t": "list", "items": bullets})
                        blocks_by_section[target_sid] = target_blocks

                        # Mark source for removal
                        promote_skip_ids.append(sid)
                        logger.info(
                            "recap: promoted standalone %s → %s subsection in %s",
                            sid,
                            target_label,
                            target_sid,
                        )

                # SUPPRESS (not delete) promoted source sections.
                # Writing [] as sentinel — the final-merge layer treats
                # "key present with empty list" as intentionally suppressed
                # and skips the section entirely (no fall-back to
                # Section.blocks original). Without this sentinel,
                # Composer/Preview/DOCX export would still show the source
                # section by falling back to the original extraction.
                for sid in promote_skip_ids:
                    blocks_by_section[sid] = []
                    qc_drift[sid] = {
                        "pass": True,
                        "drifted": [],
                        "note": "section promoted into preceding topic",
                    }
            except Exception as e:
                logger.warning("recap promote post-loop skipped: %s", e)

            # ─── RECAP post-loop: orphan bullet fallback ──────────
            # If any chapter-end bullets did not match any section above
            # the threshold, append them as a synthetic chapter-end
            # "Key Takeaways" section so nothing is silently dropped.
            if orphan_keypoints:
                fallback_blocks = [
                    {"t": "h3", "c": "Key Takeaways"},
                    {"t": "list", "items": list(orphan_keypoints)},
                ]
                # Use a stable synthetic id that sorts to the very end.
                blocks_by_section["zzz-key-takeaways-orphan-fallback"] = fallback_blocks
                qc_drift["zzz-key-takeaways-orphan-fallback"] = {
                    "pass": True,
                    "drifted": [],
                    "note": "synthetic orphan-fallback from recap redistribute",
                }
                logger.info(
                    "recap: appended %d orphan bullets under fallback Key Takeaways section",
                    len(orphan_keypoints),
                )

            if regen_row is not None:
                # MERGE (don't replace) — the regen row was seeded by the
                # API with the prior regen's blocks_by_section, so sections
                # the user previously regenerated keep their saved output.
                # Only the sections in THIS run's scope get overwritten.
                # Without flag_modified, SQLAlchemy doesn't notice the JSON
                # dict mutated in place and skips the UPDATE.
                from sqlalchemy.orm.attributes import flag_modified
                existing_blocks = dict(regen_row.blocks_by_section or {})
                # PTR REDISTRIBUTE + RENAME PROMOTE FIX: when a source
                # section is suppressed in THIS run, also suppress its
                # carried-forward copy from a prior regen. Write [] as
                # the sentinel (same shape as the worker's in-run write)
                # so the final-merge layer can detect explicit
                # suppression and not fall back to Section.blocks
                # originals.
                suppress_ids = set(ptr_source_section_ids) | set(promote_skip_ids)
                # blocks_by_section already contains [] sentinels for these
                # ids from the worker's pre/post-loop. The .update() below
                # will overwrite any prior carried-forward content with
                # those sentinels.
                existing_blocks.update(blocks_by_section)
                regen_row.blocks_by_section = existing_blocks
                flag_modified(regen_row, "blocks_by_section")

                existing_qc = dict(regen_row.qc_drift or {})
                existing_qc.update(qc_drift)
                regen_row.qc_drift = existing_qc
                flag_modified(regen_row, "qc_drift")

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
