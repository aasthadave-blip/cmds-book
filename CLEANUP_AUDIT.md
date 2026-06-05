# Phase 8 — Cleanup Audit

> Status: **audit only — no deletions yet**
> Branch: `architecture-v2`
> Created: 2026-06-05

Phases 1–7 are strangler-fig — new contracts added alongside old code.
This document catalogues what the new contracts make deletable, when
each deletion becomes safe, and what risk remains.

**Nothing here is deleted yet. This is the deletion plan.**

---

## Deletion criteria

A piece of code is safe to delete when ALL of:

1. The new contract path is proven on at least 5 production books
2. No grandfathered book in prod still depends on the old path
3. A rollback exists (revert commit or feature flag)
4. The deletion doesn't depend on another in-flight phase

---

## Catalogue of deletable code

### Tier A — Delete after 1 week of stable v2 in prod

These are the "patch" files / functions whose purpose was to work
around the Identity / State contract violations. With v2's UUID FKs
and per-stage status, they no longer have a job.

| File | LOC | Why it exists today | Why it's deletable after v2 |
|---|---|---|---|
| `app/services/schema_alignment.py` | ~200 | Re-IDs sections after re-analyse to match existing rows | UUIDs never change; nothing to align |
| `final_merge.py` slug 3-tier fallback (lines 1058-1066) | ~50 | Excluded-section lookup by title because slug doesn't match | section_uuid FK is single canonical key |
| `figure_embedder.py` anchor 3-way match (lines 407-501) | ~150 | Multiple slug systems for question→figure attachment | section_uuid FK on FigureReference is direct |
| `extract.py:228-244` schema_alignment try/except wrap | ~20 | Catches alignment failure and continues with fresh IDs | Alignment unnecessary; remove call entirely |
| `extract.py:578` ↔ `extract.py:649` book.status = "ready" / "failed" direct writes | ~5 | Single status field forced into one of two states | Derived via book_status.derive_book_status() |

**Total Tier A: ~425 LOC**

### Tier B — Delete after 2 weeks of stable + all prod books re-extracted

These touch the data model — deleting them requires that no row in
prod still references the old columns.

| File / column | Why it exists | When safe to delete |
|---|---|---|
| `Question.section_ref` column | Old slug-based join key | After Phase 4 reader migration sees 0 fallback hits for 2 weeks |
| `Figure.section_id` (string slug) | Misleadingly-named slug field | Same |
| `FigureReference.section_ref` | Same | Same |
| `api/books.py:219-236` GET-endpoint self-heal | Auto-flips schema_ready → ready | Phase 5e makes this redundant |
| Multiple `extract_questions_*` versions (questions.py, questions_v2.py) | Historical worker versions | After v3 proven on all book types |
| `Section.attempts` + `Section.qc_local` + `Section.qc_llm` columns | Per-section telemetry that nothing reads | After extraction_log table replaces them (future Phase 9) |

### Tier C — Delete after deeper architecture work

These need a follow-up phase before they can go.

| Thing | Why it stays for now |
|---|---|
| `Regeneration` overlay system (3 tables) | Part 2 (regen rewrite) lives here. Don't delete until Part 2 ships. |
| `final_drafts` table + seed_draft_items_from_merge | Canonical document builder (Phase 9 — renderer unification) needs to land first |
| Auto-heal on every read in `final_merge.py:704-722` | Replaced when stage-status-driven embedder lands |
| `book.raw_text` column | Schema postpass uses it; remove with schema_postpass deletion |
| Multiple renderer walks (preview/composer/docx/md) | Wait for canonical document model |

---

## What's NOT deletable (intentional)

These look like candidates but are needed:

| Thing | Why we KEEP it |
|---|---|
| `book.status` field | Frontend reads it; consumers depend on the single field. Keep populated via derive_book_status. |
| `book.schema_ready` literal | Schema-review gate UX still depends on this exact value |
| `Section.section_id` slug column | Display-only after v2 — but still useful for breadcrumbs and human-readable URLs |
| `FigureReference.section_ref` (after Phase 4) | Kept for one release as fallback during grandfather cutover |

---

## Suggested deletion PR sequence

```
PR 1 (week 1 of stable v2):
  - Delete schema_alignment.py
  - Remove the try/except wrapper in extract.py:228-244
  - Verify: re-analyse a book; ensure UUIDs survive

PR 2 (week 1):
  - Delete the slug 3-tier fallback in final_merge.py
  - Delete the anchor 3-way match in figure_embedder.py
  - Replace with single section_uuid lookup
  - Verify: /quality reports show no fallback hits

PR 3 (week 2):
  - Drop legacy slug columns (Question.section_ref, Figure.section_id, FR.section_ref)
  - Migration with downgrade path
  - Verify: 0 readers reference the old columns

PR 4 (week 3+):
  - Multiple extract_questions versions cleanup
  - Section.attempts/qc_* columns
  - Heavy review needed; high-risk if old test data still in DB
```

---

## Phase 8 outcome

**No code deleted yet** — that's intentional. This audit is the
authoritative list. When the time comes (v2 stable in prod, observed
clean), each deletion is a small focused PR with a 1-line verifier.

The actual deletion is owed to a future "cleanup sprint" with proper
observability data behind it.

---

## What WAS done in Phase 8 today

1. This audit document
2. Catalogued ~425 LOC immediately deletable after stable v2
3. Identified Tier B + C with clear gates for safety
4. Sequenced into 4 deletion PRs

Nothing more. Cleanup deserves caution.
