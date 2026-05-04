"""Stage 3 question extraction worker — section-aligned (mirrors theory).

This worker replaces the v2 excluded-block-driven approach with a clean
section-aligned pattern that mirrors how theory extraction already works:

  for each section in the approved schema:
      slice PDF to section's pages
      ask Gemini to extract questions for THIS section, stop at next heading
      structurally filter the LLM output
      persist Question rows tagged to that section
      compare extracted count vs schema's expected_question_count

Why this design solves the question-bank pain points:

  • Missing/incomplete  → schema's `expected_question_count` is the target
                          and "extracted N of M" is now reportable.
  • Cross-section dups  → impossible by construction; each Q belongs to one
                          section because we slice one section at a time.
  • Hallucinated/wrong  → tighter prompt (v3) + post-extraction structural
                          filter rejects items that aren't questions.
  • "What's happening?" → per-section status (complete/partial/failed) +
                          per-section retry surface for the UI.

The v2 task is left in place so existing banks keep working; new banks default
to v3 (controlled by a feature flag in a follow-up step).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import create_engine, delete, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings
from app.core.storage import download_pdf
from app.models.book import Book
from app.models.job import Job
from app.models.question import Question
from app.models.question_bank import QuestionBank
from app.schemas.analyser import BookSchema, ExcludedSection, SchemaSection
from app.services.prompt_loader import load_raw
from app.services.questions.dedup import dedup_bank
from app.services.questions.structural_filter import filter_items
from app.utils.json_parse import parse_json
from app.workers.runner import register as register_task

logger = logging.getLogger(__name__)

_sync_engine = create_engine(settings.SYNC_DATABASE_URL, pool_pre_ping=True)
SyncSession = sessionmaker(bind=_sync_engine, class_=Session, autoflush=False)

GEMINI_MODEL = "gemini-2.5-flash"
MAX_ATTEMPTS = 2
GEMINI_TIMEOUT_S = 150
MAX_OUTPUT_TOKENS = 65536


# ---------------------------------------------------------------------------
# Job + bank update helpers (heartbeat-aware)
# ---------------------------------------------------------------------------
def _update_job(session: Session, job_id: UUID, **fields: Any) -> None:
    job = session.get(Job, job_id)
    if job is None:
        return
    for k, v in fields.items():
        setattr(job, k, v)
    job.last_heartbeat_at = datetime.now(timezone.utc)
    session.commit()


def _update_bank(session: Session, bank_id: UUID, **fields: Any) -> None:
    bank = session.get(QuestionBank, bank_id)
    if bank is None:
        return
    for k, v in fields.items():
        setattr(bank, k, v)
    session.commit()


# ---------------------------------------------------------------------------
# PDF slicing — same as theory_extractor's helper, copied here to keep this
# worker self-contained.
# ---------------------------------------------------------------------------
def _slice_pdf(pdf_bytes: bytes, page_start: int | None, page_end: int | None) -> bytes:
    try:
        import pymupdf

        doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
        total = len(doc)
        p0 = max(0, (page_start or 1) - 1)
        p1 = min(total - 1, (page_end or total) - 1)
        if p0 > p1 or p0 >= total:
            doc.close()
            return pdf_bytes
        out = pymupdf.open()
        out.insert_pdf(doc, from_page=p0, to_page=p1)
        result = out.tobytes()
        out.close()
        doc.close()
        return result
    except Exception as e:
        logger.warning("PDF slice failed (pages %s-%s): %s — using full PDF",
                       page_start, page_end, e)
        return pdf_bytes


# ---------------------------------------------------------------------------
# Section iteration — flatten the schema into the units we extract from.
# Includes: every regular section, every excluded section. Excluded sections
# (chapter-end exercise blocks) are by far the densest source of questions.
# ---------------------------------------------------------------------------
class _Unit:
    """One extraction unit: a section or excluded section with page range."""

    __slots__ = ("kind", "id", "title", "page_start", "page_end",
                 "expected", "next_title")

    def __init__(
        self,
        kind: str,                    # "section" | "excluded"
        ref_id: str,
        title: str,
        page_start: int | None,
        page_end: int | None,
        expected: int | None,
    ) -> None:
        self.kind = kind
        self.id = ref_id
        self.title = title
        self.page_start = page_start
        self.page_end = page_end
        self.expected = expected
        self.next_title: str | None = None


def _flatten_sections(schema: BookSchema) -> list[_Unit]:
    """Walk the schema and return units worth extracting from, in printed order.

    A unit is worth extracting iff:
      - it has a non-zero expected_question_count, OR
      - it's an excluded section (almost always end-of-chapter exercises), OR
      - expected_question_count is None (back-compat — let LLM tell us)

    Sections with expected_question_count == 0 are skipped: schema explicitly
    said "this is pure theory, no questions live here".
    """
    units: list[_Unit] = []

    def _walk(secs: list[SchemaSection]) -> None:
        for s in secs:
            ec = s.expected_question_count
            if ec is None or ec > 0:
                units.append(_Unit(
                    kind="section",
                    ref_id=s.id,
                    title=s.title,
                    page_start=s.page_start,
                    page_end=s.page_end,
                    expected=ec,
                ))
            if s.subsections:
                _walk(s.subsections)

    _walk(schema.sections)

    for ex in schema.excluded_sections or []:
        ec = ex.expected_question_count
        # Always extract excluded blocks unless the schema explicitly said 0.
        if ec == 0:
            continue
        units.append(_Unit(
            kind="excluded",
            ref_id=ex.title or "",
            title=ex.title or "",
            page_start=ex.page_start,
            page_end=ex.page_end,
            expected=ec,
        ))

    units.sort(key=lambda u: (u.page_start or 0, u.page_end or 0))

    for i, u in enumerate(units):
        u.next_title = units[i + 1].title if i + 1 < len(units) else None

    return units


# ---------------------------------------------------------------------------
# Gemini call (sync, runs inside asyncio.to_thread for the heartbeat to tick)
# ---------------------------------------------------------------------------
def _build_user_prompt(unit: _Unit) -> str:
    stop = (
        f"\nSTOP extracting when you reach the heading \"{unit.next_title}\" — "
        "do NOT include any content from that heading onwards."
        if unit.next_title
        else ""
    )
    return (
        f"Extract every question from the section titled: \"{unit.title}\" "
        f"(ID: {unit.id}).\n"
        f"START extracting at the heading \"{unit.title}\" — "
        f"include every question from that heading onwards.{stop}\n\n"
        "These PDF pages may contain content from adjacent sections. "
        "Extract ONLY the questions that belong to this section.\n"
        "Transcribe every question verbatim — pure OCR, no summarisation.\n\n"
        f"Return JSON with section_id=\"{unit.id}\" and "
        f"section_title=\"{unit.title}\"."
    )


def _call_gemini_sync(pdf_slice: bytes, system_prompt: str, user_prompt: str) -> str:
    from app.core.gemini_runtime import call_gemini_with_pdf

    return call_gemini_with_pdf(
        pdf_bytes=pdf_slice,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        model=GEMINI_MODEL,
        timeout_s=GEMINI_TIMEOUT_S,
        max_output_tokens=MAX_OUTPUT_TOKENS,
        temperature=0.0,
        display_name="section.pdf",
    )


# ---------------------------------------------------------------------------
# Per-unit extraction
# ---------------------------------------------------------------------------
async def _extract_unit(
    unit: _Unit,
    pdf_bytes: bytes,
    system_prompt: str,
) -> dict[str, Any]:
    """Returns a per-unit result dict suitable for persisting + reporting."""
    # +1 page trailing pad: schemas frequently put a section's last question
    # on the page that the next section "officially" starts on. Padding the
    # slice by one page lets the LLM see the trailing question; the prompt
    # tells it to STOP at the next-section heading, so the pad is safe.
    padded_end = (unit.page_end + 1) if unit.page_end is not None else None
    pdf_slice = _slice_pdf(pdf_bytes, unit.page_start, padded_end)
    user_prompt = _build_user_prompt(unit)

    last_err = ""
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            raw = await asyncio.to_thread(
                _call_gemini_sync, pdf_slice, system_prompt, user_prompt
            )
            data = parse_json(raw)
            if not isinstance(data, dict):
                raise ValueError("response was not a JSON object")

            identified = int(data.get("identified_total") or 0)
            extracted = list(data.get("extracted") or [])

            # Structural filter — drop anything that isn't actually a question.
            fr = filter_items(extracted)
            kept = fr.kept
            rejected = fr.rejected

            return {
                "ok": True,
                "attempts": attempt,
                "identified_total": identified,
                "extracted": kept,
                "rejected": rejected,
                "rejected_count": len(rejected),
                "raw_response_size": len(raw or ""),
            }
        except Exception as e:
            last_err = f"attempt {attempt}: {e}"
            logger.warning("v3 extract failed (%s/%s): %s",
                           unit.kind, unit.id, last_err)

    return {
        "ok": False,
        "attempts": MAX_ATTEMPTS,
        "identified_total": 0,
        "extracted": [],
        "rejected": [],
        "rejected_count": 0,
        "error": last_err,
    }


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------
def _persist_unit(
    session: Session,
    bank_id: UUID,
    book_id: UUID,
    unit: _Unit,
    result: dict[str, Any],
) -> int:
    """Replace existing rows for this (bank, section_ref) with the new ones.

    Returns the number of Question rows inserted.
    """
    session.execute(
        delete(Question).where(
            Question.bank_id == bank_id,
            Question.section_ref == unit.id,
        )
    )

    inserted = 0
    for item in result.get("extracted", []):
        raw_text = (item.get("raw_text") or "").strip()
        if not raw_text:
            continue
        q = Question(
            bank_id=bank_id,
            book_id=book_id,
            section_ref=unit.id,
            section_title=unit.title,
            page_start=item.get("page") or unit.page_start,
            page_end=unit.page_end,
            raw_text=raw_text,
            qc_local={"pass": True, "score": 1.0, "failures": []},
            attempts=int(result.get("attempts") or 1),
            status="passed",
            question_number=item.get("question_number"),
            exercise_ref=item.get("exercise_ref"),
            kind=str(item.get("kind") or "exercise"),
            has_options=bool(item.get("has_options")),
            solution_text=item.get("solution") or None,
            has_solution=bool(item.get("has_solution")),
            identified_total=int(result.get("identified_total") or 0),
        )
        session.add(q)
        inserted += 1
    session.commit()
    return inserted


# ---------------------------------------------------------------------------
# Status classification — drives the per-section badge in the UI.
# ---------------------------------------------------------------------------
def _legacy_block_status(v3_status: str) -> str:
    """Map v3's per-section status to the legacy block status the UI expects.

    v3 → legacy:
        complete → ok
        partial  → partial   (already understood by the type union)
        empty    → empty
        failed   → failed
    """
    return {"complete": "ok"}.get(v3_status, v3_status)


def _classify_unit(
    expected: int | None, kept: int, identified: int, ok: bool
) -> str:
    """Return one of: "complete" | "partial" | "empty" | "failed".

    "complete" — extraction succeeded and (no expected count OR kept >= expected)
    "partial"  — extraction succeeded but kept < expected (missing questions)
    "empty"    — extraction succeeded with zero items (which is fine if expected==0)
    "failed"   — extraction call errored after retries
    """
    if not ok:
        return "failed"
    if kept == 0:
        return "empty" if (expected or 0) == 0 else "partial"
    if expected and kept < expected:
        return "partial"
    return "complete"


# ---------------------------------------------------------------------------
# Main task
# ---------------------------------------------------------------------------
async def _run_v3(book_id: UUID, bank_id: UUID, job_id: UUID) -> dict[str, Any]:
    with SyncSession() as session:
        book = session.get(Book, book_id)
        if book is None:
            raise ValueError(f"Book {book_id} not found")
        bank = session.get(QuestionBank, bank_id)
        if bank is None:
            raise ValueError(f"Bank {bank_id} not found")
        if not book.schema:
            raise ValueError("Book has no approved schema — analyse first")

        schema = BookSchema(**book.schema)
        units = _flatten_sections(schema)
        total = len(units)
        if total == 0:
            _update_bank(session, bank_id, status="ready",
                         extraction_stats={"sections": [], "totals": {
                             "expected_total": 0, "extracted_total": 0,
                             "complete": 0, "partial": 0, "empty": 0, "failed": 0,
                         }})
            _update_job(session, job_id, status="succeeded", progress=100,
                        message="No question-bearing sections in schema",
                        finished_at=datetime.utcnow())
            return {"ok": True, "sections_processed": 0}

        pdf_bytes = download_pdf(book.pdf_url or "")
        system_prompt = load_raw("question_extractor_v3")

        _update_bank(session, bank_id, status="extracting")
        _update_job(session, job_id, status="running", progress=5,
                    message=f"v3 extraction — {total} section(s) to process")

    # Process units sequentially — keeps progress predictable and SQLite happy.
    # Concurrency is added in a follow-up step via the gemini_runtime semaphore.
    section_reports: list[dict[str, Any]] = []
    expected_total = 0
    extracted_total = 0
    counts = {"complete": 0, "partial": 0, "empty": 0, "failed": 0}

    for i, unit in enumerate(units, start=1):
        progress = 5 + int(90 * (i - 1) / max(total, 1))
        with SyncSession() as session:
            _update_job(
                session, job_id,
                progress=progress,
                message=f"Extracting {unit.title} ({i}/{total})",
            )

        result = await _extract_unit(unit, pdf_bytes, system_prompt)
        kept = len(result.get("extracted") or [])
        identified = int(result.get("identified_total") or 0)
        status = _classify_unit(unit.expected, kept, identified, bool(result.get("ok")))

        with SyncSession() as session:
            inserted = _persist_unit(session, bank_id, book_id, unit, result)

        counts[status] += 1
        expected_total += int(unit.expected or 0)
        extracted_total += kept

        section_reports.append({
            "section_ref": unit.id,
            "section_title": unit.title,
            "kind": unit.kind,
            "page_start": unit.page_start,
            "page_end": unit.page_end,
            "expected": unit.expected,
            "identified": identified,
            "extracted": kept,
            "rejected": result.get("rejected_count", 0),
            "rejected_items": result.get("rejected") or [],
            "status": status,
            "attempts": result.get("attempts", 0),
            "error": result.get("error"),
        })

        # Persist rolling stats so the UI can show progress mid-run.
        # We emit BOTH the new section-shaped stats and a legacy
        # "blocks"-shaped view so the existing live extraction panel keeps
        # rendering without a frontend rewrite.
        legacy_blocks = [
            {
                "excluded_block_index": idx,
                "title": s["section_title"],
                "section_ref": s["section_ref"],
                "page_start": s["page_start"],
                "page_end": s["page_end"],
                "identified": s["identified"],
                "extracted": s["extracted"],
                "missed": max(0, (s.get("expected") or s["identified"]) - s["extracted"]),
                "status": _legacy_block_status(s["status"]),
            }
            for idx, s in enumerate(section_reports)
        ]
        with SyncSession() as session:
            _update_bank(session, bank_id, extraction_stats={
                # New shape (v3-aware UI reads these)
                "sections": section_reports,
                "totals": {
                    "expected_total": expected_total,
                    "extracted_total": extracted_total,
                    **counts,
                },
                # Legacy shape (existing UI components keep working)
                "blocks": legacy_blocks,
                "total_identified": sum(s["identified"] for s in section_reports),
                "total_extracted": extracted_total,
                "missed": sum(b["missed"] for b in legacy_blocks),
                "worker_version": "v3",
            })

    # Cross-section dedup pass — safety net. v3's section-aligned design makes
    # duplicates rare, but overlapping page ranges and reprinted exercises can
    # still produce them. Earliest-page wins.
    with SyncSession() as session:
        dedup_stats = dedup_bank(session, bank_id)
        if dedup_stats["dropped"]:
            extracted_total -= dedup_stats["dropped"]
            bank = session.get(QuestionBank, bank_id)
            if bank is not None:
                stats = dict(bank.extraction_stats or {})
                stats["dedup"] = dedup_stats
                stats.setdefault("totals", {})["extracted_total"] = extracted_total
                bank.extraction_stats = stats
                session.commit()

    # Final job status
    with SyncSession() as session:
        bank_status = "ready" if counts["failed"] == 0 else "partial"
        _update_bank(session, bank_id, status=bank_status)
        dropped = dedup_stats["dropped"] if dedup_stats else 0
        dedup_note = f" (deduped {dropped})" if dropped else ""
        msg = (
            f"Extracted {extracted_total} of {expected_total or '?'} expected "
            f"across {total} section(s){dedup_note} — "
            f"{counts['complete']} complete, {counts['partial']} partial, "
            f"{counts['empty']} empty, {counts['failed']} failed"
        )
        _update_job(
            session, job_id,
            status="succeeded",
            progress=100,
            message=msg,
            finished_at=datetime.utcnow(),
        )

    return {
        "ok": True,
        "sections_processed": total,
        "expected_total": expected_total,
        "extracted_total": extracted_total,
        **counts,
    }


def _extract_questions_v3(book_id: str, bank_id: str, job_id: str) -> dict[str, Any]:
    """Sync entrypoint registered with the dispatch table."""
    book_uuid = UUID(book_id)
    bank_uuid = UUID(bank_id)
    job_uuid = UUID(job_id)
    try:
        return asyncio.run(_run_v3(book_uuid, bank_uuid, job_uuid))
    except Exception as e:
        logger.exception("extract_questions_v3 crashed")
        with SyncSession() as session:
            _update_job(
                session, job_uuid,
                status="failed",
                error=str(e)[:2000],
                finished_at=datetime.utcnow(),
            )
            _update_bank(session, bank_uuid, status="failed",
                         last_error=str(e)[:2000])
        return {"ok": False, "error": str(e)}


register_task("extract_questions_v3", _extract_questions_v3)


# ---------------------------------------------------------------------------
# Per-section retry — re-runs _extract_unit for one section_ref and updates
# the matching entry in extraction_stats. Used by the diagnostic UI's
# "Retry section" button on partial / failed sections.
# ---------------------------------------------------------------------------
async def _run_section_retry(
    bank_id: UUID, section_ref: str, job_id: UUID
) -> dict[str, Any]:
    with SyncSession() as session:
        bank = session.get(QuestionBank, bank_id)
        if bank is None:
            raise ValueError(f"Bank {bank_id} not found")
        book = session.get(Book, bank.book_id)
        if book is None or not book.schema:
            raise ValueError("Book or schema missing")

        schema = BookSchema(**book.schema)
        units = _flatten_sections(schema)
        unit = next((u for u in units if u.id == section_ref), None)
        if unit is None:
            raise ValueError(f"Section {section_ref!r} not in schema")

        pdf_bytes = download_pdf(book.pdf_url or "")
        system_prompt = load_raw("question_extractor_v3")
        existing_stats = dict(bank.extraction_stats or {})

        _update_job(session, job_id, status="running", progress=10,
                    message=f"Retrying {unit.title}")

    result = await _extract_unit(unit, pdf_bytes, system_prompt)
    kept = len(result.get("extracted") or [])
    identified = int(result.get("identified_total") or 0)
    status = _classify_unit(unit.expected, kept, identified, bool(result.get("ok")))

    with SyncSession() as session:
        _persist_unit(session, bank_id, book.id, unit, result)

        # Update the matching section entry in extraction_stats in place
        sections = list(existing_stats.get("sections") or [])
        new_section = {
            "section_ref": unit.id,
            "section_title": unit.title,
            "kind": unit.kind,
            "page_start": unit.page_start,
            "page_end": unit.page_end,
            "expected": unit.expected,
            "identified": identified,
            "extracted": kept,
            "rejected": result.get("rejected_count", 0),
            "rejected_items": result.get("rejected") or [],
            "status": status,
            "attempts": result.get("attempts", 0),
            "error": result.get("error"),
        }
        replaced = False
        for i, s in enumerate(sections):
            if s.get("section_ref") == unit.id:
                sections[i] = new_section
                replaced = True
                break
        if not replaced:
            sections.append(new_section)

        # Recompute totals
        counts = {"complete": 0, "partial": 0, "empty": 0, "failed": 0}
        expected_total = 0
        extracted_total = 0
        for s in sections:
            counts[s["status"]] = counts.get(s["status"], 0) + 1
            expected_total += int(s.get("expected") or 0)
            extracted_total += int(s.get("extracted") or 0)

        legacy_blocks = [
            {
                "excluded_block_index": idx,
                "title": s["section_title"],
                "section_ref": s["section_ref"],
                "page_start": s["page_start"],
                "page_end": s["page_end"],
                "identified": s["identified"],
                "extracted": s["extracted"],
                "missed": max(0, (s.get("expected") or s["identified"]) - s["extracted"]),
                "status": _legacy_block_status(s["status"]),
            }
            for idx, s in enumerate(sections)
        ]
        existing_stats.update({
            "sections": sections,
            "totals": {
                "expected_total": expected_total,
                "extracted_total": extracted_total,
                **counts,
            },
            "blocks": legacy_blocks,
            "total_identified": sum(s["identified"] for s in sections),
            "total_extracted": extracted_total,
            "missed": sum(b["missed"] for b in legacy_blocks),
            "worker_version": "v3",
        })

        bank = session.get(QuestionBank, bank_id)
        if bank is not None:
            bank.extraction_stats = existing_stats
            # Bank stays "ready" unless a section is now failed
            bank.status = "partial" if counts["failed"] > 0 else "ready"
            session.commit()

        _update_job(
            session, job_id,
            status="succeeded",
            progress=100,
            message=f"Retried {unit.title}: {kept} extracted ({status})",
            finished_at=datetime.utcnow(),
        )

    return {"ok": True, "section_ref": section_ref, "status": status,
            "extracted": kept, "identified": identified}


def _re_extract_section_v3(bank_id: str, section_ref: str, job_id: str) -> dict[str, Any]:
    bank_uuid = UUID(bank_id)
    job_uuid = UUID(job_id)
    try:
        return asyncio.run(_run_section_retry(bank_uuid, section_ref, job_uuid))
    except Exception as e:
        logger.exception("re_extract_section_v3 crashed")
        with SyncSession() as session:
            _update_job(
                session, job_uuid,
                status="failed",
                error=str(e)[:2000],
                finished_at=datetime.utcnow(),
            )
        return {"ok": False, "error": str(e)}


register_task("re_extract_section_v3", _re_extract_section_v3)
