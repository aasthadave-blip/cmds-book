"""Figures pipeline v2 worker tasks.

Distinct from the OLD `extract_figures` task in extract.py (which targets the
never-completed `app.services.figure_extractor` scaffolding). v2 uses the
new `app/services/figures/` package wrapping the user's standalone scripts.

Task types registered:
  - extract_figures_v2(book_id, job_id)  — single Gemini call across the
    whole PDF → figures + figure_references persisted to DB
  - regenerate_figures_v2(book_id, section_ref, params_json, job_id)
    — section-scoped regen across every figure in that section (Q5 model)

Pure additive — touches no frozen file.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
from datetime import datetime
from typing import Any
from uuid import UUID

# Hard ceiling per figure regen call. Default 90s — Gemini image generation
# usually finishes in 15-40s; past 90s the call is hung and we should abandon
# it and move on to the next figure rather than let one stuck figure block
# an entire section reseed. Override via env if needed.
PER_FIGURE_REGEN_TIMEOUT_S = int(os.environ.get("FIGURE_REGEN_TIMEOUT_S", "90"))


class _FigureTimeout(Exception):
    """Per-figure regen call exceeded the timeout. Treated as transient
    failure (figure marked failed, loop continues)."""


# Process-wide semaphore — at most ONE figure EXTRACT runs at a time
# in this worker process. The Gemini call returns ALL figure PNGs in a
# single response (can be 100s of MB on figure-heavy chapters); running
# two of these in parallel reliably OOMs the Railway container. Other
# Celery tasks (theory, questions, regen) are unaffected — they keep
# the per-process concurrency=8 budget. Override via env if needed.
_FIGURE_EXTRACT_INFLIGHT = threading.BoundedSemaphore(
    int(os.environ.get("FIGURE_EXTRACT_MAX_INFLIGHT", "1"))
)


def _run_with_timeout(fn, timeout_s: int):
    """Run fn() in a worker thread; raise _FigureTimeout if it doesn't
    finish within timeout_s. The thread is abandoned on timeout — the
    underlying HTTP socket may still be open until its own deadline, but
    the Celery worker can move on instead of hanging forever.
    """
    result: dict[str, Any] = {}
    err: dict[str, BaseException] = {}

    def _target():
        try:
            result["v"] = fn()
        except BaseException as e:  # capture all so the caller can re-raise
            err["e"] = e

    t = threading.Thread(target=_target, daemon=True)
    t.start()
    t.join(timeout_s)
    if t.is_alive():
        raise _FigureTimeout(f"figure regen exceeded {timeout_s}s")
    if "e" in err:
        raise err["e"]
    return result.get("v")

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings
from app.models import Book, Figure, FigureReference, FigureRegeneration, Job, Question
from app.workers.celery_app import celery_app


def _orch_dispatch(book_uuid: UUID) -> None:
    """Fire the post-schema extraction coordinator (ORCH Day 6).

    Called from every terminal status write in extract_figures_v2 so
    the orchestrator's state machine can advance (finalize the book if
    questions also done, retry if Day 7 logic applies, etc.). Idempotent
    on the coordinator side. Failure here is non-fatal — frontend
    polling still works as a fallback during the transition (until
    Day 12 strips it).
    """
    try:
        from app.workers.runner import dispatch
        dispatch("coordinate_extraction", str(book_uuid))
    except Exception as e:
        logger.warning(
            "extract_figures_v2: coordinator dispatch failed (continuing): %s",
            e,
        )
from app.workers.runner import register as register_task

logger = logging.getLogger(__name__)


def _sync_session() -> sessionmaker:
    """Build a sync sessionmaker against the same DB the async sessions use."""
    sync_url = settings.SYNC_DATABASE_URL
    engine = create_engine(sync_url, pool_pre_ping=True, future=True)
    return sessionmaker(bind=engine, future=True, expire_on_commit=False)


def _update_job(session: Session, job_id: UUID, **fields: Any) -> None:
    job = session.get(Job, job_id)
    if job is None:
        return
    for k, v in fields.items():
        setattr(job, k, v)
    session.commit()


def _load_pdf_bytes(book: Book) -> bytes:
    """Read the PDF associated with this book from local storage.
    Mirrors how the existing question/theory workers pull PDF bytes
    (questions_v3.py, extract.py all use download_pdf)."""
    from app.core.storage import download_pdf

    return download_pdf(book.pdf_url or "")


# ---------------------------------------------------------------------------
# extract_figures_v2
# ---------------------------------------------------------------------------

def _extract_figures_v2(book_id: str, job_id: str) -> dict[str, Any]:
    """Whole-PDF figure extraction + section/question linking.

    Single Gemini call (per Figure Handling.docx cost analysis: ~$0.30/book).
    All section-mapping and question-linking happens CPU-side via the linker
    service. No additional Gemini calls.

    Wrapped in _FIGURE_EXTRACT_INFLIGHT — blocks until a free slot. Other
    Celery tasks aren't held up; only concurrent figure-extracts wait.
    """
    with _FIGURE_EXTRACT_INFLIGHT:
        return _extract_figures_v2_impl(book_id, job_id)


def _extract_figures_v2_impl(book_id: str, job_id: str) -> dict[str, Any]:
    """Inner implementation — semaphore-protected. See _extract_figures_v2."""
    from app.services.figures import extractor as fig_extractor
    from app.services.figures import linker as fig_linker
    from app.services.figures import cache as fig_cache

    book_uuid = UUID(book_id)
    job_uuid = UUID(job_id)
    Session = _sync_session()

    # 1. Resolve book + PDF bytes
    with Session() as session:
        book = session.get(Book, book_uuid)
        if book is None:
            logger.error("extract_figures_v2: book %s not found", book_id)
            _update_job(
                session, job_uuid,
                status="failed", error="book not found",
                finished_at=datetime.utcnow(),
            )
            return {"status": "failed", "error": "book not found"}
        if not book.pdf_url:
            _update_job(
                session, job_uuid,
                status="failed", error="book has no pdf_url",
                finished_at=datetime.utcnow(),
            )
            book.figures_status = "failed"
            session.commit()
            _orch_dispatch(book_uuid)  # ORCH Day 6
            return {"status": "failed", "error": "book has no pdf_url"}
        # Phase 5d (CONTRACT.md §2): mark figures stage as running. Watchdog
        # (Phase 7) will detect runs that stall past stale_after.
        book.figures_status = "running"
        session.commit()

        _update_job(
            session, job_uuid,
            status="running",
            progress=5,
            message="Loading PDF",
        )
        try:
            pdf_bytes = _load_pdf_bytes(book)
        except Exception as e:
            logger.exception("extract_figures_v2: failed to load PDF")
            _update_job(
                session, job_uuid,
                status="failed", error=f"PDF load failed: {e}",
                finished_at=datetime.utcnow(),
            )
            return {"status": "failed", "error": str(e)}

        schema = book.schema or None

    # 2. Single Gemini call — coordinate oracle
    Session2 = _sync_session()
    with Session2() as session:
        _update_job(
            session, job_uuid,
            status="running", progress=15,
            message="Calling Gemini for figure detection",
        )
    try:
        metadata, images = fig_extractor.extract(pdf_bytes)
    except Exception as e:
        logger.exception("extract_figures_v2: Gemini extract failed")
        with _sync_session()() as session:
            _update_job(
                session, job_uuid,
                status="failed", error=f"Gemini extract failed: {e}",
                finished_at=datetime.utcnow(),
            )
            # Phase 5d: Gemini extract failed.
            b = session.get(Book, book_uuid)
            if b is not None:
                b.figures_status = "failed"
                session.commit()
        _orch_dispatch(book_uuid)  # ORCH Day 6
        return {"status": "failed", "error": str(e)}

    figures_raw = metadata.get("figures") or []
    total = len(figures_raw)
    logger.info(
        "extract_figures_v2: detected %d figures, %d images cropped",
        total, len(images),
    )

    # 3. Pull bank's questions (for question_ref linking)
    with _sync_session()() as session:
        questions_rows = list(session.execute(
            select(Question).where(Question.book_id == book_uuid)
        ).scalars().all())
        questions_dicts = [
            {
                "id": str(q.id),
                "section_ref": q.section_ref,
                "question_number": q.question_number,
                "exercise_ref": q.exercise_ref,
                "section_title": q.section_title,
            }
            for q in questions_rows
        ]

    # 4. CPU-only linker pass — map (page, context, question_ref) -> our schema
    candidates = fig_linker.build_link_candidates(
        figures_raw, schema, questions_dicts,
    )
    grouped = fig_linker.merge_dual_context(candidates)

    # 5. Persist figures + references
    with _sync_session()() as session:
        _update_job(
            session, job_uuid,
            status="running", progress=70,
            message=f"Persisting {total} figures",
        )
        inserted_figures = 0
        inserted_refs = 0
        # Wipe prior figures for this book so re-runs are idempotent
        session.query(FigureReference).filter_by(book_id=book_uuid).delete()
        session.query(Figure).filter_by(book_id=book_uuid).delete()
        session.commit()

        # Memory guard — pop image bytes from the dict as we consume them
        # (don't keep BOTH the full ``images`` dict AND the row-attached
        # bytes in memory at once). Also commit every N inserts so the
        # session doesn't accumulate all rows for the whole book — that
        # was the OOM/SIGKILL trigger on big figure-heavy chapters.
        _COMMIT_EVERY = 8
        _since_commit = 0
        for fig_id_text, cands in grouped.items():
            head = cands[0]
            img_bytes = images.pop(fig_id_text, None)
            source_hash = fig_cache.source_hash(img_bytes) if img_bytes else None
            # Determine the figure's primary section anchor — use the first
            # candidate's section_ref (already chosen most-specific by linker).
            primary_section_ref = head.get("section_ref") or ""
            # Positional-linking metadata is stored in regen_meta JSON so
            # the embedder (Pass 2) can place unlabelled figures using
            # anchor_text / anchor_position / question_no. Labelled figures
            # ignore these fields; their embedder path uses figure_label
            # against in-block references exactly as before.
            positional_meta = None
            if head.get("is_labelled") is False:
                positional_meta = {
                    "is_labelled": False,
                    "anchor_text": head.get("anchor_text"),
                    "anchor_position": head.get("anchor_position"),
                    "question_no": head.get("question_no"),
                }
            # Phase 2 of canonical identity migration (CONTRACT.md §1):
            # resolve primary_section_ref (slug) → Section UUID once per
            # figure. Used to stamp Figure.section_uuid AND every child
            # FigureReference. None if slug doesn't match (e.g. "_orphan"
            # placeholder); Phase 4 reader handles NULL gracefully.
            from app.services.section_identity import resolve_section_uuid as _resolve_sid
            primary_section_uuid = _resolve_sid(
                session, book_uuid, primary_section_ref
            ) if primary_section_ref else None

            fig_row = Figure(
                book_id=book_uuid,
                section_id=primary_section_ref or "_orphan",
                section_uuid=primary_section_uuid,  # Phase 2: canonical FK
                figure_number=head.get("placeholder_text"),
                caption=head.get("caption"),
                description=None,
                page_number=head.get("page"),
                bounding_box=head.get("bounding_box"),
                semantic_type=head.get("type") or "other",
                tags=[],
                status="extracted",
                image_bytes=img_bytes,
                mime_type="image/png" if img_bytes else None,
                regen_version=0,
                regen_status="none",
                figure_id_text=fig_id_text,
                normalized_label=head.get("normalized_label"),
                source_hash=source_hash,
                context_hint=", ".join(
                    sorted({c.get("context") for c in cands if c.get("context")})
                ) or None,
                regen_meta=positional_meta,
            )
            session.add(fig_row)
            session.flush()  # need fig_row.id for refs
            inserted_figures += 1
            for c in cands:
                # Each candidate may target a different section than the
                # figure's primary one — resolve per-cand to be correct.
                cand_slug = c.get("section_ref") or ""
                cand_uuid = (
                    _resolve_sid(session, book_uuid, cand_slug)
                    if cand_slug else None
                )
                ref = FigureReference(
                    figure_id=fig_row.id,
                    book_id=book_uuid,
                    section_ref=cand_slug,
                    section_uuid=cand_uuid,  # Phase 2: canonical FK
                    context=c.get("context") or "theory",
                    question_id=(
                        UUID(c["question_id"]) if c.get("question_id") else None
                    ),
                    placeholder_text=c.get("placeholder_text"),
                    link_method="auto",
                )
                session.add(ref)
                inserted_refs += 1
            _since_commit += 1
            if _since_commit >= _COMMIT_EVERY:
                session.commit()
                # Detach so the next iteration doesn't keep these row
                # objects (with their PNG bytes) alive in identity map.
                session.expire_all()
                _since_commit = 0
        session.commit()

        # 5b. Deterministic figure embedder — writes placement metadata
        # so theory / questions know WHERE to render each figure inline.
        # Non-fatal: extraction is already saved if this fails.
        try:
            from app.services.figure_embedder import embed_figures_for_book_sync
            embed_counters = embed_figures_for_book_sync(session, book_uuid)
            session.commit()
            logger.info("figure_embedder after extract: %s", embed_counters)
        except Exception as e:
            logger.warning("figure_embedder after extract failed: %s", e)

        # 6. Finish job
        result = {
            "status": "succeeded",
            "figures_extracted": inserted_figures,
            "references_created": inserted_refs,
            "missed_anchors": metadata.get("missed_anchors") or [],
        }
        # Phase 5d: figures stage done. "partial" if some anchors missed
        # but at least one figure was inserted; "done" if everything clean.
        # Empty PDFs (no figures detected at all) count as "done", not failed.
        book_row = session.get(Book, book_uuid)
        if book_row is not None:
            missed = len(result["missed_anchors"])
            if inserted_figures == 0 and missed == 0:
                book_row.figures_status = "done"  # no figures in PDF
            elif missed > 0 and inserted_figures > 0:
                book_row.figures_status = "partial"
            elif inserted_figures > 0:
                book_row.figures_status = "done"
            else:
                book_row.figures_status = "failed"
            # Phase 5e: re-derive book.status. Figures is often the last
            # stage to complete; this is where the book finally flips to
            # "ready" (or "partial" if anything failed/empty).
            from app.services.book_status import derive_book_status
            derived = derive_book_status(book_row)
            book_row.status = "extracting" if derived == "queued" else derived
            session.commit()
        _update_job(
            session, job_uuid,
            status="succeeded", progress=100,
            message=(
                f"Extracted {inserted_figures} figures · "
                f"{inserted_refs} references · "
                f"{len(result['missed_anchors'])} anchors missed"
            ),
            finished_at=datetime.utcnow(),
        )
        _orch_dispatch(book_uuid)  # ORCH Day 6
        return result


# Celery-mode wrapper. Inline path uses _extract_figures_v2 directly.
@celery_app.task(name="extract_figures_v2", bind=True)
def extract_figures_v2_task(self, book_id: str, job_id: str) -> dict[str, Any]:
    return _extract_figures_v2(book_id, job_id)


register_task("extract_figures_v2", _extract_figures_v2)


# ---------------------------------------------------------------------------
# regenerate_figures_v2_section
# ---------------------------------------------------------------------------

def _regenerate_figures_v2_section(
    book_id: str,
    section_ref: str,
    params_json: str,
    job_id: str,
) -> dict[str, Any]:
    """Regenerate EVERY figure in one section.

    `params_json` is a JSON dict with knobs:
      {
        "style": "enhanced" | "original",       (default: enhanced)
        "custom_instructions": str | null,
        "watermark_clean": bool,                 (default: true)
        "overlay": bool,                          (default: true)
        "image_model": str | null,                (default: env)
        "ocr_model": str | null,                  (default: env)
      }

    Per Q8 (latest-only): writes to Figure.regen_image_bytes, bumps
    regen_version, sets regen_status. Old variant is replaced; no
    history retained beyond regen_meta JSON snapshot.

    Content-hash cache: if (source_hash + params + prompt_version) matches
    Figure.regen_cache_key, skip Gemini, reuse existing regen_image_bytes.
    """
    from app.services.figures import regenerator as fig_regen
    from app.services.figures import watermark as fig_wm
    from app.services.figures import overlay as fig_overlay
    from app.services.figures import cache as fig_cache

    book_uuid = UUID(book_id)
    job_uuid = UUID(job_id)
    try:
        params = json.loads(params_json) if params_json else {}
    except json.JSONDecodeError:
        params = {}

    style = params.get("style") or "enhanced"
    custom = (params.get("custom_instructions") or "").strip() or None
    # Default OFF: the v2 pipeline regenerates a fresh image (no NCERT
    # watermark to begin with), and Gemini's safety filter has been silently
    # rejecting watermark-removal requests with an empty response → 100%
    # failure rate. Users who genuinely want the stage can opt in by passing
    # watermark_clean=True explicitly.
    watermark_clean = bool(params.get("watermark_clean", False))
    # Overlay step OCRs the original figure's labels and paints them back
    # onto the regenerated image. It was intended to preserve label accuracy
    # but in practice it made regenerated images visually indistinguishable
    # from originals (since labels dominate the visual signal). Default OFF
    # so the reviewer sees the actual Gemini output. Callers can opt in.
    overlay = bool(params.get("overlay", False))
    image_model = params.get("image_model") or None
    ocr_model = params.get("ocr_model") or None
    effective_image_model = image_model or "gemini-3.1-flash-image-preview"

    Session = _sync_session()
    with Session() as session:
        # Catch every figure that touches this section — either it was
        # anchored here at extraction time (Figure.section_id) OR the
        # embedder placed a reference to it from this section's theory /
        # questions (FigureReference.section_ref). The embedder sometimes
        # re-homes a figure to a different section based on label-match,
        # which used to make reseed miss those figures silently.
        anchored = list(session.execute(
            select(Figure)
            .where(Figure.book_id == book_uuid)
            .where(Figure.section_id == section_ref)
        ).scalars().all())
        referenced = list(session.execute(
            select(Figure)
            .join(FigureReference, FigureReference.figure_id == Figure.id)
            .where(Figure.book_id == book_uuid)
            .where(FigureReference.section_ref == section_ref)
            .distinct()
        ).scalars().all())
        # Union, preserve order
        seen: set = set()
        figures: list[Figure] = []
        for f in anchored + referenced:
            if f.id in seen:
                continue
            seen.add(f.id)
            figures.append(f)
        total = len(figures)
        if total == 0:
            _update_job(
                session, job_uuid,
                status="succeeded", progress=100,
                message=f"No figures in section {section_ref!r}",
                finished_at=datetime.utcnow(),
            )
            return {"status": "succeeded", "regenerated": 0, "skipped": 0}

        _update_job(
            session, job_uuid,
            status="running", progress=5,
            message=f"Regenerating {total} figures in {section_ref}",
        )

    regenerated = 0
    cached_hits = 0
    failed = 0
    failures: list[dict[str, Any]] = []

    # Per-figure work — fresh session per write so we don't hold a long-lived txn
    for idx, fig in enumerate(figures, start=1):
        with _sync_session()() as session:
            # Re-load to ensure freshness
            fig_row = session.get(Figure, fig.id)
            if fig_row is None or not fig_row.image_bytes:
                logger.warning(
                    "regen: figure %s has no source bytes — skipping",
                    fig.id,
                )
                failed += 1
                failures.append({"figure_id": str(fig.id), "reason": "no source bytes"})
                continue

            cache_key = fig_cache.cache_key(
                source_bytes=fig_row.image_bytes,
                style=style,
                custom_instructions=custom,
                watermark_clean=watermark_clean,
                overlay=overlay,
                model=effective_image_model,
            )
            # Cache hit — same source + same params + same prompts -> reuse
            if (
                fig_row.regen_cache_key == cache_key
                and fig_row.regen_image_bytes
                and fig_row.regen_status == "ready"
            ):
                cached_hits += 1
                # Still bump progress so the UI feels alive
                _update_job(
                    session, job_uuid,
                    progress=5 + int(90 * idx / total),
                    message=f"[{idx}/{total}] cached {fig_row.figure_id_text}",
                )
                continue

            # Mark this row as in-flight
            fig_row.regen_status = "extracting"
            session.commit()

            figure_meta = {
                "id": fig_row.figure_id_text,
                "figure_label": fig_row.figure_number,
                "caption": fig_row.caption,
                "page": fig_row.page_number,
                "context": fig_row.context_hint,
            }

            try:
                # Per-figure timeout — if Gemini hangs on one figure we
                # mark it failed and continue with the next. Without this,
                # a single stuck call would freeze the entire section reseed.
                new_bytes = _run_with_timeout(
                    lambda: fig_regen.regenerate(
                        fig_row.image_bytes,
                        style=style,
                        custom_instructions=custom,
                        figure_meta=figure_meta,
                        model=image_model,
                    ),
                    timeout_s=PER_FIGURE_REGEN_TIMEOUT_S,
                )
                if watermark_clean:
                    try:
                        new_bytes = fig_wm.clean(new_bytes, model=image_model)
                    except Exception as e_wm:
                        # Non-fatal: skip the watermark stage and keep the
                        # regenerated image as-is. The v2 pipeline produces
                        # a fresh image that typically has no watermarks
                        # anyway, and Gemini's safety filter sometimes
                        # silently rejects this prompt with an empty
                        # response — we don't want that to fail the run.
                        logger.warning(
                            "regen: watermark cleanup failed for %s, using uncleaned regen (%s: %s)",
                            fig_row.id, type(e_wm).__name__, e_wm,
                        )
                if overlay:
                    new_bytes, _ = fig_overlay.overlay(
                        fig_row.image_bytes, new_bytes, ocr_model=ocr_model,
                    )
                fig_row.regen_image_bytes = new_bytes
                fig_row.regen_status = "ready"
                fig_row.regen_version = (fig_row.regen_version or 0) + 1
                fig_row.regen_cache_key = cache_key
                regen_meta_snapshot = {
                    "style": style,
                    "custom_instructions": custom,
                    "watermark_clean": watermark_clean,
                    "overlay": overlay,
                    "image_model": effective_image_model,
                    "ocr_model": ocr_model,
                    "regenerated_at": datetime.utcnow().isoformat(),
                }
                fig_row.regen_meta = regen_meta_snapshot
                # Q-style regen folder: persist one FigureRegeneration row per
                # successful regen attempt so the UI can show a history of
                # runs grouped by (section, timestamp). Image bytes stay on
                # Figure.regen_image_bytes (latest-only) — historical images
                # are NOT recoverable in option-B minimal storage.
                session.add(FigureRegeneration(
                    book_id=book_uuid,
                    figure_id=fig_row.id,
                    section_id=section_ref,
                    image_url=None,
                    style_params=regen_meta_snapshot,
                    model_used=effective_image_model,
                    status="ready",
                ))
                session.commit()
                regenerated += 1
            except Exception as e:
                logger.exception("regen: figure %s failed", fig_row.id)
                fig_row.regen_status = "failed"
                err_str = f"{type(e).__name__}: {e}"
                fig_row.regen_meta = {
                    **(fig_row.regen_meta or {}),
                    "last_error": err_str,
                }
                # Also log this failed attempt in the regen folder so the UI
                # history shows it (with status="failed" and the error inside
                # style_params for visibility).
                session.add(FigureRegeneration(
                    book_id=book_uuid,
                    figure_id=fig_row.id,
                    section_id=section_ref,
                    image_url=None,
                    style_params={
                        "style": style,
                        "custom_instructions": custom,
                        "watermark_clean": watermark_clean,
                        "overlay": overlay,
                        "image_model": effective_image_model,
                        "last_error": err_str,
                    },
                    model_used=effective_image_model,
                    status="failed",
                ))
                session.commit()
                failed += 1
                failures.append({
                    "figure_id": str(fig_row.id),
                    "reason": err_str,
                })

            _update_job(
                session, job_uuid,
                progress=5 + int(90 * idx / total),
                message=f"[{idx}/{total}] regenerated {fig_row.figure_id_text}",
            )

    with _sync_session()() as session:
        status = "succeeded" if failed == 0 else (
            "partial" if regenerated + cached_hits > 0 else "failed"
        )
        # Re-run figure embedder — regen may have produced new variants;
        # placement metadata stays the same but the embedder also picks
        # up any variant changes for the renderer. Non-fatal.
        try:
            from app.services.figure_embedder import embed_figures_for_book_sync
            embed_counters = embed_figures_for_book_sync(session, book_uuid)
            session.commit()
            logger.info("figure_embedder after regen: %s", embed_counters)
        except Exception as e:
            logger.warning("figure_embedder after regen failed: %s", e)
        _update_job(
            session, job_uuid,
            status=status,
            progress=100,
            message=(
                f"Done — regenerated {regenerated}, cached {cached_hits}, "
                f"failed {failed} (section: {section_ref})"
            ),
            error=json.dumps(failures) if failures else None,
            finished_at=datetime.utcnow(),
        )
    return {
        "status": status,
        "regenerated": regenerated,
        "cached_hits": cached_hits,
        "failed": failed,
        "failures": failures,
    }


# Celery-mode wrapper. Inline path uses _regenerate_figures_v2_section directly.
@celery_app.task(name="regenerate_figures_v2_section", bind=True)
def regenerate_figures_v2_section_task(
    self,
    book_id: str,
    section_ref: str,
    params_json: str,
    job_id: str,
) -> dict[str, Any]:
    return _regenerate_figures_v2_section(book_id, section_ref, params_json, job_id)


register_task("regenerate_figures_v2_section", _regenerate_figures_v2_section)


# ---------------------------------------------------------------------------
# discard_figure_regen — instant DB op, no Gemini
# ---------------------------------------------------------------------------

def _discard_figure_regen(figure_id: str) -> dict[str, Any]:
    """Clear the regen_image_bytes for a single figure — UI uses this when
    user picks 'Keep original'. Resets to version 0, no-cache."""
    fig_uuid = UUID(figure_id)
    with _sync_session()() as session:
        fig = session.get(Figure, fig_uuid)
        if fig is None:
            return {"status": "not_found"}
        fig.regen_image_bytes = None
        fig.regen_status = "none"
        fig.regen_version = 0
        fig.regen_cache_key = None
        fig.regen_meta = {
            **(fig.regen_meta or {}),
            "discarded_at": datetime.utcnow().isoformat(),
        }
        session.commit()
        return {"status": "discarded", "figure_id": figure_id}
