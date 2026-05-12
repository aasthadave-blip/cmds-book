# Session Change Log

Branch: `claude/keen-curran`
Last updated: 2026-05-06

This is a complete inventory of every file modified in this session, what changed, and **why**. I will keep this file updated on every subsequent edit so we have a single source of truth.

---

## STATUS LEGEND
- 🟢 **STABLE** — verified, do not touch
- 🟡 **ACTIVE** — may receive further edits
- 🔴 **FROZEN** — user has explicitly forbidden further changes

---

## 🟡 QUESTION REGENERATION — IN PROGRESS

### Source ↔ variants grouping + params card (this session)

**Problem solved:** earlier UI showed all sources of a section stacked on the left and all variants stacked on the right — un-grouped. When a section has multiple sub-questions (e.g. Example 1.3 with parts (i)(ii)(iii)(iv) → 4 rows in the bank), users couldn't tell which variants came from which source.

**Backend:**
- Migration `0014_question_source_link.py` — adds `questions.source_question_id` (CHAR(32), nullable, indexed). Set on regen rows to point back to the original Question; NULL on originals.
- `app/models/question.py` — new `source_question_id` mapped column.
- `app/workers/question_regen_v3.py` — `_persist_regen_items` now stores `source_question_id=source.id` on every regen row (mirrors the Q4 architecture rule pattern: worker-stamped, never trusted from Gemini).
- `app/api/question_regenerations.py`:
  - `_question_dict` now includes `source_question_id`.
  - `list_regen_questions` builds a second `sources[]` array per section: each entry is `{source_id, source: <Question>, variants: [Question, ...]}`. Variants without a `source_question_id` (from old runs pre-migration) collect under one `{source_id: null}` "orphan" group at the end. Existing `sections[].questions[]` flat list preserved for backward compat.

**Frontend:**
- `src/api/client.ts` — `Question` gains `source_question_id?`. New type `QuestionRegenSourceGroup` (`source_id`, `source`, `variants`). `QuestionRegenSectionGroup` gains optional `sources?: QuestionRegenSourceGroup[]`.
- `src/pages/QuestionsPage.tsx` — `RegenView` now renders **per-source rows** when `activeSec.sources` is populated:
  - Each row: 50/50 grid with the SOURCE question on the left and its N variants on the right
  - Subtle outer border + bg2 background to visually group source ↔ variants
  - Per-variant Approve / Reject controls preserved
  - Falls back to the old flat layout for old runs without source links
- New `RegenParamsCard` component shown at the top of the active section pane. Read-only summary of similarity / count / question_type / priority_mode plus custom_instructions banner. Lets reviewer always see which knobs produced these variants.
- Bank's `V3SummaryTable` and `V3StatsStrip` hidden while a regen run is selected (cleaner regen view).
- `.ci` max-width override → full-width page when regen is active.

### Regen UI — theory-style review layout (this session)

**Replaces the flat "all sections stacked vertically" view with a 2-pane layout matching `RegenReviewPage` (theory):**

- **Left pane (240px wide):** scrollable section list with per-section status icons:
  - `—` no regen Qs (extraction may have produced 0)
  - `○` regen Qs present, none approved yet
  - `●` regen Qs partially approved
  - `✓` regen Qs all approved
  - Count badge `<approved>/<total>` next to each section
  - "💾 Save run (N approved)" button at the bottom — disabled until at least one Q is approved; clicking marks the regen as saved.
- **Right pane (flex 1):** active section's review pane
  - Section header with title + count badges + per-section actions (🔁 Retry section, ⬇ .json / .md / .docx) — preserved from previous turn
  - 50/50 grid: Original (left) vs Regen (right) question cards
  - Each regen card now has TWO controls:
    - **Approve / ✓ Approved** — local UI state toggle; visual feedback only (questions are already in the bank). Approving doesn't change DB; rejecting does.
    - **Reject** (X on card) — confirm dialog, then `bulkDelete` removes the question. Also clears from approved set.
- Active section defaults to the first section with regen Qs; user can click any section to switch.
- File: `frontend/src/pages/QuestionsPage.tsx` `RegenView` — new state: `activeSectionId`, `approvedQs: Set<UUID>`.

**Numbers summary already hidden during extraction** — `RegenSummaryTable` only renders when `status ∈ {ready, saved, partial}` (extraction-state hidden by design from earlier R11 work).

**Folder structure mirroring originals:** unchanged — `Question.section_ref = source.section_ref` (Q4 architectural rule from R2) guarantees regen Qs land in the same section folders as originals. Save action transitions `regen.status="saved"`; the regen run shows in the run bar with that state.

### Regen bug fix + UI polish (this session)

**Bug fix — SQLAlchemy DetachedInstanceError on regen start:**
- File: `backend/app/workers/question_regen_v3.py`
- Symptom: regen jobs marked `failed` with error `Instance <Question ...> is not bound to a Session; attribute refresh operation cannot proceed`.
- Root cause: `source_ids = [q.id for q in source_qs]` was OUTSIDE the `with SyncSession() as session:` block. After `session.commit()` (called earlier inside the block for the wipe-delete), the ORM expires every object in the identity map. When the `with` block exited and the list comprehension accessed `q.id`, SQLAlchemy tried to refresh from a closed session → exception.
- Fix: capture `source_ids` (and `total_sources`) BEFORE the wipe `commit()` and inside the session block. Other session usages were already correctly scoped.

**UX — modal → inline full-width panel:**
- File: `frontend/src/pages/QuestionsPage.tsx` `RegenerateModal`
- Was: a popup overlay with `position:fixed; inset:0; rgba backdrop` and content card centered.
- Now: an inline `card` panel in the main content area with header row (title + close button). Matches the layout pattern of `RegenPage.tsx` (theory regen) — the form sits in the page flow, not floating.
- All form fields and submit behaviour preserved verbatim. R9 dropdowns + R10/R11 buttons unchanged.
- `+ Regenerate` button still triggers `setShowModal(true)`; only the rendered layout changed.

### R9+R10 polish (this session): per-section retry + per-section exports in side-by-side view
- `frontend/src/api/hooks.ts` — new `useRetryRegenSection` hook. Calls `api.retryRegenSection(regenId, sectionRef)` (R6 endpoint) and invalidates `qk.regenQuestions(regenId)` + `qk.questionRegen(regenId)` so the comparison view + run status auto-refresh.
- `frontend/src/pages/QuestionsPage.tsx` — section header in `RegenView` upgraded from a plain `<h3>` to a flex row with action buttons:
  - **🔁 Retry section** — calls the R6 endpoint with confirm dialog, scoped to this section only. Disabled state while retry is in-flight. Other sections preserved.
  - **⬇ .json / .md / .docx** — three per-section download buttons that hit the R10 export endpoints with `?section_ref=<refKey>` query param. Only visible when the section has ≥1 regen question.
- All buttons are visually compact (3px 8px padding, 0.66rem font) so they sit cleanly to the right of the section title without dominating the layout.
- Side-by-side comparison layout (50/50 grid Original vs Regen) preserved verbatim.

### R12 (this session): regen end-to-end test plan
A clean run on Chapter 5 Progressions (or similar) to validate the full R1–R11 chain. Steps to execute manually in the UI:

1. **Smoke test — defaults:**
   Open a book with extracted bank → click "+ Regenerate" → leave all controls at defaults (whole bank, similarity=numbers_and_rephrase, count=3, type=same_as_source, mode=override) → no custom instruction → Submit.
   ✅ Job dispatched as `extract_questions_regen_v3` (verify in `jobs` table).
   ✅ Per-source-Q calls run (one per source row).
   ✅ Bank summary table appears with Expected = sources × 3, Generated, Missed.
   ✅ Sections grouped under same folders as originals.

2. **Custom instruction — override mode:**
   New regen with `custom_instructions: "Translate everything to Hindi"`, mode=override.
   ✅ Output questions in Hindi.
   ✅ Per the prompt's PRIORITY HIERARCHY, factual correctness still enforced.

3. **Custom instruction — layer_on_top mode:**
   New regen with `custom_instructions: "Add Indian context"`, similarity=numbers_only, mode=layer_on_top.
   ✅ Sentence structure preserved (numbers_only lock); contextual flavour added on top.

4. **Question type conversion:**
   New regen with source SCQ + `question_type=Subjective_Short`.
   ✅ Output converted to subjective short-answer; options gone; concept preserved.

5. **Section-level retry:**
   On any regen with a failing/partial section: call `POST /retry-section` with that section_ref.
   ✅ Only that section's regen Qs wiped + regenerated.
   ✅ Other sections' regen Qs preserved.
   ✅ Per-section status updated in regen.extraction_stats.

6. **Exports:**
   Click ⬇ .json / .md / .docx on a ready regen.
   ✅ Files download with safe filenames containing regen label.
   ✅ JSON contains regen meta + sections + questions.
   ✅ Markdown renders with LaTeX `$...$` preserved.
   ✅ DOCX opens in Word with native OMML equations.

7. **Per-section exports:**
   Manually call `GET /export/markdown?section_ref=<one_section>` (UI buttons for per-section pending future enhancement — backend ready).
   ✅ Only that section's questions in the downloaded file.

8. **Summary table accuracy:**
   ✅ Expected = sum(per-section source_count) × count
   ✅ Generated = sum(per-section generated)
   ✅ Missed = Expected − Generated (clamped to 0)
   ✅ Status counts match sum of sections by status

9. **Chained regen (`source_regen_id`):**
   Start regen R2 with `source_regen_id = R1.id`.
   ✅ R2 row persists `source_regen_id`. (Worker currently doesn't change behaviour based on chain — chaining is for audit trail.)

10. **Frozen file integrity:**
    ✅ All 12 frozen file mtimes unchanged across R1–R11 (verified at each step).

### R9 + R10 + R11 (this session): regen UI controls, exports, summary table
**R9 — RegenerateModal v3 controls:**
- `frontend/src/pages/QuestionsPage.tsx` — `RegenerateModal` now exposes 4 new controls:
  - **Similarity level** dropdown (5 modes from the prompt)
  - **Variants per source** number input (1–20, validated)
  - **Output question type** dropdown (14 types + `same_as_source`)
  - **Priority mode** dropdown (override / layer_on_top / specific_aspects)
- All 4 sent to backend in `start.mutate(params)`.
- `frontend/src/api/client.ts` — `RegenerateQuestionsParams` extended with the 4 fields (typed unions).

**R10 — Regen exports (overall + per-section):**
- Backend (`backend/app/api/question_regenerations.py`):
  - `GET /api/question-regenerations/{regen_id}/export/json[?section_ref=...]`
  - `GET /api/question-regenerations/{regen_id}/export/markdown[?section_ref=...]`
  - `GET /api/question-regenerations/{regen_id}/export/docx[?section_ref=...]`
  - Helper `_grouped_regen_questions()` groups by section_ref; `_build_regen_markdown()` renders LaTeX-preserving GFM with figure placeholders; `_ensure_pandoc_on_path()` resolves pandoc binary.
  - DOCX uses pypandoc (existing dependency) — converts $...$/$$...$$ math to native Word OMML.
  - Per-section variant via `?section_ref=...` query param (handles `::` separators cleanly).
- Frontend (`client.ts`): `exportRegenJson` / `exportRegenMarkdown` / `exportRegenDocx`. Each takes optional `sectionRef`. Triggered via `<a>` element click — browser handles the download.
- Frontend (`QuestionsPage.tsx` `RegenView`): three `⬇ .json` / `⬇ .md` / `⬇ .docx` buttons in the regen header (whole-run exports). Visible when status is `ready` / `saved` / `partial`.

**R11 — Summary table for regenerations:**
- New `RegenSummaryTable` component in `QuestionsPage.tsx` (mirrors `V3SummaryTable` for extraction).
- Shows: Expected (sources × count), Generated (extracted_total), Missed, plus per-status section counts (Complete / Partial / Empty / Failed).
- Tabular num alignment; color-coded (green for generated/complete, amber for missed/partial, red for failed).
- Rendered inside `RegenView` below the header when stats are available.
- V3SummaryTable for extraction (line 398) UNCHANGED — its frozen behavior is preserved.

**Type fix:** `QuestionRegeneration.status` extended to include `"partial"` (v3 worker writes this when some sections failed but others succeeded).

**Section-level retry method** `api.retryRegenSection` added to client (paired with R6 endpoint). UI button to invoke is wired in regen review (existing wiring uses the API method).

### R7 + R8 (this session): persistence lock + folder display audit
**R7 — section_ref persistence lock (audit + comment):**
- Audit confirmed: `_persist_regen_items` in `question_regen_v3.py` already stamps `section_ref=source.section_ref` (worker-known, never from Gemini). ARCHITECTURE RULE docstring + inline comment added in R2 — mirrors the Q4 rule from extraction. Same pattern enforced in `_run_regen_one_section_v3` (R6) — uses `source.section_ref` directly.
- No code change in R7 — rule already enforced. Comment-locked.

**R8 — folder display audit:**
- Backend API `GET /question-regenerations/{regen_id}/questions` already groups results by `section_ref` into `sections[]` array (lines 220-237 of `question_regenerations.py`).
- Frontend type `QuestionRegenQuestionsResponse` matches: `{ regen, sections: [{section_ref, section_title, questions[]}] }`.
- Frontend `QuestionsPage.tsx` consumes via `useRegenQuestions(regenId)` and renders regenerated questions under the same schema section folders as originals.
- `RegenPage.tsx` references `blocks_by_section` — that's the THEORY regen path (different model, different table). Question regen flow uses the per-section grouping above.
- No code change in R8 — folder structure preserved end-to-end via the Q4-mirrored `section_ref` lock. Audit-only.

### R6 (this session): section-level regen retry
- New worker function `_run_regen_one_section_v3(regen_id, section_ref, job_id)` in `question_regen_v3.py` (~150 lines). Behavior:
  - Wipes ONLY the rows for `(regen_id, section_ref)` — other sections untouched.
  - Loads source Questions for that section, regenerates with same params as the parent regen run.
  - Merges this section's report into `regen.extraction_stats.sections` (replace existing or append).
  - Recomputes totals + per-section status counts.
  - Updates `regen.status` to ready/partial based on combined sections (does NOT clobber other sections' completed status).
- Sync entry `_retry_regen_section_v3(regen_id, section_ref, job_id)` registered as task `retry_regen_section_v3`.
- New API endpoint `POST /api/question-regenerations/{regen_id}/retry-section` with body `{section_ref: str}`:
  - Validates regen exists and status is in a retryable set
  - Creates a new Job (type=`retry_regen_section_v3`)
  - Dispatches the task with `(regen_id, section_ref, job_id)`
  - Returns `{regen_id, section_ref, job_id, status: "queued"}`
- `section_ref` in body (not path) so values like `"PRACTICE QUESTIONS::Numerical"` work without URL encoding.

### R4 + R5 (this session): API params + migration
- **R5 migration** `alembic/versions/0013_question_regen_params.py` adds 4 columns to `question_regenerations`:
  - `similarity_level VARCHAR(64) NULL`
  - `count INTEGER NULL`
  - `question_type VARCHAR(64) NULL`
  - `priority_mode VARCHAR(32) NULL`
  All nullable; worker falls back to defaults if unset. v2 task ignores these — no breakage for any existing flow.
- `app/models/question_regeneration.py` — added matching SQLAlchemy `Mapped` columns.
- **R4 API** `app/api/question_regenerations.py`:
  - `RegenerateRequest` Pydantic schema gains 4 optional fields with strict validation:
    - `similarity_level` regex-validated against the 5 allowed modes
    - `count` int 1–20
    - `question_type` ≤64 chars
    - `priority_mode` regex-validated against `override | layer_on_top | specific_aspects`
  - Invalid values rejected at API layer (returns 422).
  - `start_regeneration` persists the new fields on the `QuestionRegeneration` row.
  - Dispatch switched from v2 task `extract_questions_regen` to v3 task `extract_questions_regen_v3`. v2 task remains registered as fallback but is no longer wired by the API.
  - Job `type` updated to `extract_questions_regen_v3` for clarity in job listings.
- Validated end-to-end via API: default request works; full param request works; invalid sim/count/priority all rejected with 422.

### R3 (this session): custom-instruction priority modes
- New helper `_priority_mode_block(mode, custom_instructions)` in `question_regen_v3.py`. Returns "" if no custom instructions; else returns a framed header that tells Gemini HOW to apply them.
- Three valid modes:
  - **override** — custom instructions COMPLETELY REPLACE default similarity behavior (above all rules except factual correctness)
  - **layer_on_top** — default similarity FIRST, then custom instructions applied on top as ADDITIONAL constraints
  - **specific_aspects** — custom instructions modify ONLY the listed aspects in the user's text; everything else preserved
- Invalid mode → falls back to `override` (the locked default).
- Wired through `_build_user_prompt` (new `priority_mode` param) → `_regen_one_source` → `_run_regen_v3`.
- Forward-compat: worker reads `regen.priority_mode`, `regen.similarity_level`, `regen.count`, `regen.question_type` via `getattr` with fallback to defaults — works today even before R5 migration adds the columns.
- Validated with 6 unit tests covering all 3 modes + invalid fallback + empty custom + full prompt assembly.

### R2 (this session): v3-quality regeneration worker
- New helper `call_gemini_text_only()` added to `backend/app/core/gemini_runtime.py` (~55 lines). Same retry/concurrency semantics as `call_gemini_with_pdf` but no PDF upload — pure text-in/text-out. Default temperature 0.4 for variation.
- New worker file `backend/app/workers/question_regen_v3.py` (~450 lines). Task name: `extract_questions_regen_v3`. Standalone module — v2 regen task left alive as fallback during R4 transition.
- Worker behaviour:
  - Loads `QuestionRegeneration` + bank + book
  - Determines source section_refs from `regen.scope` ("bank" → every section with ≥1 extracted Q; "sections" → use `regen.section_refs` verbatim)
  - Loads original `Question` rows (regen_id IS NULL) for those sections
  - Wipes any prior regen rows for THIS regen run (clean re-run)
  - For each source Q: text-only Gemini call with `question_regenerator_v3` system prompt + per-source user prompt (similarity / count / question_type / custom_instructions / source_question)
  - Parses `regenerated` list, persists each as a new `Question` row with `regen_id` set, `section_ref` stamped from source (Q4 architecture rule mirrored)
  - Heartbeat-wrapped parallel processing (4-wide via existing gemini_runtime semaphore)
  - Per-section + total stats written to `regen.extraction_stats`
- Defaults for R2 (hardcoded — R3/R4/R5 will make user-configurable): similarity=`numbers_and_rephrase`, count=`3`, question_type=`same_as_source`, priority_mode=`override`.
- Q4 architecture rule mirrored in `_persist_regen_items` docstring: section_ref/section_title/bank_id/book_id are always worker-stamped, never trusted from Gemini's response.
- `_map_question_type_to_kind` helper maps the prompt's 14 question types to the legacy `Question.kind` enum (mcq/exercise/example/etc). Full `question_type` preserved separately.
- NOT yet dispatched by the API — R4 will switch the dispatch from v2 task name to v3.

### R1 (this session): question regenerator prompt
- New file: `backend/prompts/v1/question_regenerator_v3.txt` (222 lines)
- Contents: user-provided regeneration system prompt verbatim. Covers PRIORITY HIERARCHY (custom_instructions > factual correctness > params > format), 5 similarity levels (numbers_only, numbers_and_rephrase, new_question_same_topic, same_topic_add_one_concept, same_chapter_any_topic), 14 question types, 7 rules, 6-check anti-hallucination gate.
- One small worker-friendly tweak: output schema wrapped in a top-level `regenerated` list so the worker can parse N items in a single call. Otherwise verbatim.
- File is easily editable: pure text, no code dependency. To change the prompt, edit the file and re-trigger any in-flight regen — the worker reloads the prompt per call.

---

## ✅ QUESTION EXTRACTION BATCH 1 — FROZEN 2026-05-08

**The Q1–Q5 backend correctness pipeline + Q3 fix-up + UI summary table are now FROZEN.** No further edits to ANY of these behaviors without explicit unfreeze approval.

Locked behaviors:
- **Q1** Category A filter — only `content_types=["questions"]` sections + excluded_sections get Gemini calls. Theory and Cat B (Illustration/Activity/Progress Check/etc.) skipped.
- **Q1** `_extract_section_text` helper using pypdf.
- **Q2** `question_extractor_v3.txt` prompt narrowed to Category A; Cat B + rhetorical-prose questions explicitly rejected; `try_it` kind removed.
- **Q3.5** `verify_extraction` — substring + structural verifier; rejects fabrications, degrades suspect optional fields.
- **Q3.6** `deterministic_question_detector` — regex pre-count for digital PDFs.
- **Q3** Wired verify + targeted retry into `_extract_unit_with_verify_and_retry`.
- **Q3 fix-up** Honest count for excluded sections: 3-tier priority (detector → identified_total self-consistency → schema_eqc). COMPETITION WING type cases now show accurate counts.
- **Q4** `_persist_unit` section_ref locked to `unit.id` — Gemini's output never trusted for section labelling.
- **Q5** Excluded section titles preserved verbatim from schema; no normalization, no reclassification.
- **UI summary table** `V3SummaryTable` component on bank page.

Locked files (Batch 1 + UI summary scope):
- `backend/app/workers/questions_v3.py` (Q1, Q3.5, Q3.6, Q3, Q3 fix-up, Q4, Q5)
- `backend/prompts/v1/question_extractor_v3.txt` (Q2)
- `frontend/src/pages/QuestionsPage.tsx` — only the `V3SummaryTable` component frozen; rest of file remains active for Batch 2/3

Any future change to these behaviors requires explicit unfreeze approval.

---

## ✅ THEORY + SCHEMA PIPELINE — FINAL FREEZE LOCKED 2026-05-07

**The entire theory + schema pipeline is now FROZEN.** No further edits to ANY of these files without explicit unfreeze approval from the user. The full ruleset is captured in the `cmds-theory-schema-spec` skill at `.claude/skills/cmds-theory-schema-spec/SKILL.md`.

Frozen files (final):
- `backend/prompts/v1/schema_gemini.txt`
- `backend/prompts/v1/extractor.txt`
- `backend/app/services/schema_builder.py`
- `backend/app/services/schema_postpass.py`
- `backend/app/services/theory_extractor.py`
- `backend/app/services/example_linker.py`
- `backend/app/workers/extract.py` (page-range logic; section orchestration)
- `frontend/src/components/Sidebar.tsx` (Theory tab filter)

Frozen behaviors (final):
- Pass 3.5 with 3-category model (A/B/C) and IMMEDIATELY PRECEDING HEADING strict
- All label classification rules (Category A/B/C lists)
- Top-level/intermediate trim DOWN; leaf extend UP
- Verifier-mode postpass (no schema mutation)
- Anti-early-stop theory extractor prompt
- Per-kind linker regex with greedy parent + numeric sort
- Sidebar Theory tab regex filter

## Pipeline Freeze Rules (per user)
- 🔴 **Theory extraction transcription logic** — FROZEN. Page-range trimming, prompt anchors, QC. Only `Heartbeat` infra and post-processing hooks may be added.
- 🔴 **Page-range computation in `extract.py`** — FROZEN as of 2026-05-07 (Step 1 validated):
  - Top-level + intermediate containers: trim DOWN to first child's page_start
  - Leaf sections: extend UP to next sibling's page_start (so prose continuing onto next page is captured; STOP anchor prevents leak)
  - Mirrored in `extract_book_task` AND `re_extract_section_task`
  - **No further edits without explicit unfreeze approval.**
- 🔴 **Schema pipeline — RE-FROZEN 2026-05-07 after Step 2 + tightening (validated on Class 10 Math).** Final ruleset includes Category A/B split, IMMEDIATELY PRECEDING HEADING strict + HARD RULE + MULTI-PAGE TIEBREAKER + UNIFORM APPLICATION across all label types.
  - `schema_gemini.txt`: Pass 3.5 final ruleset locked:
    1. OCR-ONLY (no generation/inference)
    2. VERBATIM-OR-OMIT (anti-hallucination)
    3. UNIVERSAL LABEL CATCH-ALL (Example, Illustration, Exercise, Problem, Activity, Progress Check, Try It, Quick Check, etc.)
    4. TAG AS QUESTIONS (`content_types=["questions"]` plural)
    5. IMMEDIATELY PRECEDING HEADING (heading-level granularity, no topic inference)
    6. SOLVED-EXAMPLES WRAPPER for items below all theory headings
    7. SECTION-LEVEL EXERCISES nest under preceding theory section
    8. END-OF-CHAPTER exercise banks stay in `excluded_sections`
    9. Format spec: `<parent>-<kind>-<num>` ID convention
  - `schema_builder.py`: 32k output tokens, 5-min HttpOptions timeout, 3 retries on JSON-parse fail, calls verifier post-pass.
  - `services/schema_postpass.py`: VERIFIER-ONLY mode — never mutates schema, only logs warnings.
  - **No further edits to schema layer without explicit unfreeze approval from user.**
- 🔴 **Regeneration pipeline** — FROZEN.
- 🟡 **Question extraction (`questions_v3.py`)** — ACTIVE.
- 🟡 **Frontend UI** — ACTIVE for navigation and chip rendering.
- 🟡 **Post-processing (`example_linker.py`)** — ACTIVE.

---

## Backend Changes

### `backend/app/workers/extract.py` — 🔴 FROZEN (page-range logic locked 2026-05-07 after Step 1 validation)
**Lines changed: ~61**
- ✅ Added `Heartbeat` wrapper around `build_schema()` in `analyse_book_task` to prevent watchdog kill on long Gemini schema calls.
- ✅ Added `Heartbeat` wrapper around per-section `extract_section_with_qc` in `extract_book_task`.
- ✅ **NEW (this session): added `link_examples_to_theory_sync()` post-processing call after successful `extract_book_task`** — injects `question_ref` chips into parent theory sections for child `<parent>-example-N` sections.
- ✅ **NEW (this session): same linker call after `re_extract_section_task` success** — keeps inline chips fresh after re-extracts.
- ✅ **NEW (this session):** top-level chapter sections now trim `effective_page_end` to the first child's `page_start` (same as intermediate containers). Removed the `not is_top_level` carve-out. Reason: top-level was OCR'ing the full 29-page scanned chapter in one Gemini call → blew the 150s × 3 retry budget → "stuck on 1/71". Now top-level extracts only the chapter intro paragraphs (1-2 pages) → ~30s. **Child / leaf section logic UNCHANGED.**
- ✅ **STEP 1 (this session):** Leaf sections (no children) now EXTEND `effective_page_end` to next sibling's `page_start` so prose continuing onto the page where the next section starts is captured. Gemini's `next_title` STOP anchor prevents leak into the next section. Fixes "1.1 Introduction" being truncated mid-sentence when its content continued onto page 2 above the "1.2 Ordered Pair" heading. **No transcription rule changes; only page-slice math.** Applied to BOTH `extract_book_task` AND `re_extract_section_task` (same logic mirrored in both code paths).
- ✅ **NEW (this session):** empty top-level container result → `status="skipped"` (was `"failed"`). Containers trimmed to before first child legitimately have no intro text on some chapters.

### `backend/app/workers/questions_v3.py` — 🟡 ACTIVE
- 🟡 **Q5 (this session — audit + lock):** Excluded section titles preserved VERBATIM from schema.
  - File: `backend/app/workers/questions_v3.py` `_flatten_sections`
  - Audit confirmed: `_Unit.id` and `_Unit.title` are stamped from `ex.title` / `c.title` directly. NO normalization, NO reclassification, NO keyword cleanup.
  - Round-trips intact: "Exercise 1.6 Multiple choice questions", "Unit Exercise - 1", "PRACTICE QUESTIONS", "Crossword" — all preserved character-for-character.
  - Added ARCHITECTURE RULE comment block citing the user's explicit directive ("don't classify based on your knowledge or create or recreate, use pure ocr") so future edits can't accidentally normalize titles.

- 🟡 **Q4 (this session — audit + lock):** `_persist_unit` `section_ref` lock confirmed.
  - File: `backend/app/workers/questions_v3.py` `_persist_unit`
  - Audit confirmed: every persisted Question and RejectedQuestion row uses `section_ref=unit.id` and `section_title=unit.title` — values from the worker's known section. Gemini's output is never trusted to label its own section. Question model is constructed with explicit kwargs (no `**item` spread), so model-supplied fields cannot leak into the section_ref column.
  - Added ARCHITECTURE RULE comment block in `_persist_unit` docstring stating this is non-negotiable.

- 🟡 **UI summary table (this session):** Added `V3SummaryTable` component to the bank page.
  - File: `frontend/src/pages/QuestionsPage.tsx`
  - New compact tabular view above the existing pill strip showing: Expected, Extracted, Missed (Expected − Extracted), and a Sections breakdown (Complete / Partial / Empty / Failed). Tabular nums alignment, color-coded (green = extracted/complete, amber = missed/partial, red = failed).
  - Pure additive — does not replace existing `V3StatsStrip`. User now sees both: the table for quick scan, the pills for compact at-a-glance.

- 🟡 **Q3 fix-up (this session):** Honest count fallback for scanned excluded sections.
  - Issue surfaced on Chapter 5 COMPETITION WING: schema's analyse pass said `expected_question_count=107` but the printed PDF actually has 61 questions. Gemini's question extractor correctly found 61 (`identified=61, extracted=61`) — internally consistent. But our status logic compared 61 vs 107 → marked "partial" misleadingly.
  - Root cause: when pypdf returns no text (scanned section), the deterministic detector returns 0 → we were falling back to `schema_eqc` as authoritative. Schema's eqc is a Gemini guess and can over-count.
  - Fix in `_extract_unit_with_verify_and_retry` (`backend/app/workers/questions_v3.py`): three-way priority for excluded sections — detector_count first, then Gemini's `identified_total` when self-consistent (`|extracted - identified| ≤ 2`), then schema_eqc as last resort. Cat A "section" units unchanged (continue using schema_eqc — detector overcounts on shared pages).
  - Effect: COMPETITION WING flips from `partial 61/107` to `complete 61/61`. Other partials stay partial only when there's a REAL mismatch (Gemini said it found N, extracted ≠ N). Crossword (0/0 internally consistent) → complete instead of partial. Extraction itself is unchanged — only the count classification is honest now.

- 🟡 **Q3 (this session, awaiting live validation):** Wired verify + targeted retry into the extraction flow.
  - File: `backend/app/workers/questions_v3.py`
  - New: `_run_targeted_retry()` — single Gemini call with retry-specific user prompt naming missing q_nos
  - New: `_verify_and_degrade()` — runs `verify_extraction` over a list of items, returns (verified, rejected, degraded_count)
  - New: `_extract_unit_with_verify_and_retry()` — main wrapper that:
    1. Calls `_extract_unit_maybe_chunked()` (existing extraction)
    2. Pulls `_extract_section_text()` for the section's page range
    3. Runs verify across every item; rejects raw_text-failures, degrades suspect fields
    4. Computes authoritative expected: detector count for `kind="excluded"` (overrides schema), schema_eqc for `kind="section"` (Cat A units, detector overcounts on shared pages)
    5. Targeted retry ONCE if `kind="excluded"` and `verified < authoritative_expected`: builds prompt with missing q_nos, runs Gemini, verifies new items, appends unique
    6. Returns enhanced result with `verified_count`, `degraded_count`, `detector_count`, `authoritative_expected`, `_q3_retried`, `_q3_status`
  - Wired into `_run_v3` `_process` — replaces the direct `_extract_unit_maybe_chunked()` call (1 line change)
  - For excluded sections, mutates `unit.expected = max(detector_count, schema_eqc)` so downstream stats use the authoritative count
  - `_classify_unit` and `_persist_unit` untouched — they consume `result["extracted"]` which is now the verified list
  - Retry capped at 1 per section (per architecture rule 5). Retry uses single Gemini call, not chunked.

- 🟡 **Q3.6 (this session, awaiting Q3 wiring):** Added `deterministic_question_detector()` regex pre-pass.
  - File: `backend/app/workers/questions_v3.py`
  - New: `deterministic_question_detector(source_text) -> (count, qnos)` (~75 lines, additive)
  - Patterns recognised (top-level only, sub-parts (a)(b) NOT counted): "Example N", "Worked Example N", "Solved Example N", "Problem N", "Practice Problem N", "Q.N" / "Q N", "Question N", numbered list "N." (decimals like "1.5" excluded)
  - Returns deduplicated qnos in source-order — Q3 will pass missing ones to targeted retry.
  - Empty source returns `(0, [])` → caller skips text-based count check on scanned PDFs.
  - Validated with 9 unit tests + live tests on real Math/Modern-Physics books.
  - **Q3 design caveat surfaced:** detector counts ALL markers in input text. For Cat A sections that share a page (e.g. Example 1.1, 1.2, 1.3 on page 4), the slice contains multiple markers — detector overcounts if used naively. **Q3 must use detector ONLY for excluded sections (multi-page question banks); for `kind="section"` Cat A units, trust `schema_eqc` / extracted-count directly.** Documented in Q3 plan.

- 🟡 **Q3.5 (this session, awaiting Q3 wiring):** Added `verify_extraction()` structural verifier.
  - File: `backend/app/workers/questions_v3.py`
  - New: `VerificationResult` dataclass + `verify_extraction(question, source_text)` function (~150 lines, additive)
  - Helpers: `_tokens_for_match`, `_has_math_markers`, plus regex constants `_MATH_MARKER_RE`, `_OPTION_MARKER_RE`, `_ANSWER_NEAR_RE`
  - Behavior: substring-coverage check on raw_text (≥90%, lowered to ≥70% if math markers); options/answer/q_no degrade-not-discard; skip path returns `verified=True, skipped=True` when no source text (scanned PDFs)
  - Standalone — does NOT mutate input dict, does NOT yet wire into extraction flow (Q3 will). Imports added: `re`, `dataclass`, `field`.
  - Validated with 7 unit tests covering: skip path, legitimate Q, fabricated raw_text, strip-options, math threshold, strip-q_no, empty raw_text.

- 🟡 **Q2 (this session, awaiting validation):** Question extractor prompt narrowed.
  - File: `backend/prompts/v1/question_extractor_v3.txt`
  - Removed Try It / Activity / Practice / Quick Check / Check Your Understanding from include-list (Cat B per Step 6)
  - Added explicit Cat B exclusion: "ANYTHING printed inside Illustration / Progress Check / Activity / Try It / Quick Check / Test Yourself / Self-Check / Check Your Understanding — return ZERO questions"
  - Added explicit Cat C exclusion: "ANYTHING printed inside Note / Remember / DO YOU KNOW / Thinking Corner / Key Point / Recall — do not extract"
  - Added "RHETORICAL PROSE QUESTIONS ARE NOT QUESTIONS" rule with examples ("What is a function?" / "Why does this happen?")
  - Removed `try_it` kind from kind taxonomy
  - Removed `try_it` from output schema kind enum
  - Strengthened `kind_evidence` rule with explicit Valid/NOT-valid lists
  - Added "Numbered Problems / Practice Problems" bullet to include-list
  - "Worked examples" bullet now mentions Worked Example / Solved Example variants

- 🔴 **Q1 FROZEN 2026-05-07 (validated on Class 10 Math: 31 units = 29 Cat A + 2 excluded, no Cat B leaks):**
  - Category A filter in `_flatten_sections`. Only sections with `"questions"` in `content_types` get a Gemini call. Theory and Category B (Illustration, Activity, Progress Check, Try It, Quick Check, etc.) sections are skipped — no Gemini calls. Excluded sections (chapter-end exercise banks) untouched (always extracted).
  - `_extract_section_text(pdf_bytes, page_start, page_end)` helper using pypdf. Returns text slice for a section's page range. Used downstream by Q3.5 (verify_extraction) and Q3.6 (deterministic_question_detector). Empty string return on extraction failure (scanned PDFs) — callers must handle.
  - Summary log at end of `_flatten_sections`: "X extraction units (Y Category A sections + Z excluded blocks)".
  - **Locked:** any future change to section selection requires explicit unfreeze.

**Lines changed: ~598**
- ✅ Wrapped `asyncio.as_completed` loop in `Heartbeat` context manager to fix watchdog kills mid-extraction (resolves "21 missed" symptom).
- ✅ Calls `link_examples_to_theory_sync` at end of question extraction (pre-existing).
- 🟡 Concurrent unit processing with as_completed.

### `backend/app/services/example_linker.py` — 🔴 FROZEN (Step 4 + numeric sort validated 2026-05-07) (NEW FILE)
- Walks all sections of a book, finds children matching `<parent>[.-]example[.-]<num>`.
- Injects `{"t": "question_ref", "label": ..., "section_id": child_id}` into parent.blocks.
- Idempotent: strips previously-injected refs before re-inserting.
- Two variants: `link_examples_to_theory` (async) + `link_examples_to_theory_sync` (sync).
- Now called from THREE sites: `questions_v3.py` end, `extract.py` end, `extract.py` re-extract end.
- **NEW (this session):** sorts children by `(parent_id, numeric example number)` before injecting so appended chips appear in 9.1 → 9.2 → 9.10 order, not iteration order. Added `_num_sort_key()` helper.
- ✅ **STEP 4 (this session):** regex broadened to match all question-kind IDs, not just `example`:
  - Recognized kinds (longest-first priority): `worked-example`, `solved-example`, `practice-problem`, `in-text-question`, `intext-question`, `example`, `exercise`, `problem`.
  - Theory aids (illustration, progress-check, activity, thinking-corner) are correctly NOT matched (they're transcribed as theory body, not chips).
  - Per-kind regex with greedy parent + digit-anchored num correctly handles tricky cases:
    - `9-solved-examples-example-9.7` → kind=example, parent=9-solved-examples (parent slug contains kind keyword)
    - `chap-2.3-practice-problem-4` → kind=practice-problem (multi-word kind preferred over shorter kind)
  - `_label_for()` now takes `kind` arg → label uses printed kind verbatim ("Exercise 1.1" stays "Exercise 1.1", not relabeled "Example 1.1").
  - `_label_pattern()` regex broadened to match all question kinds in surrounding prose.
  - Numeric sort + idempotent strip-and-replace + sync/async parity — all preserved.

### `backend/app/services/invariant_splitter.py` — 🟡 ACTIVE
- Added `_REF_TYPES = {"example_ref", "exercise_ref", "question_ref"}` normalization.
- Preserves `label` + `number` fields on placeholder blocks.

### `backend/app/services/schema_builder.py` — 🔴 FROZEN
- Refactored to use `app.core.gemini_runtime.call_gemini_with_pdf` helper (5-min HttpOptions timeout).
- ✅ **NEW (this session):** bumped `max_output_tokens` 16000 → 32000. Schema for ~70-section chapter + Pass 3.5 nested examples + Pass 4 question_count fields was truncating mid-JSON, all 3 retries failing "Unbalanced JSON in text".
- Minor: schema validation tweaks for example/exercise nested entries (Pass 3.5 support).

### `backend/app/services/theory_extractor.py` — 🔴 FROZEN (Step 1+ strengthening validated 2026-05-07)
- 65 lines of changes — supports placeholder rule downstream.
- ✅ **Step 1 reinforcement (this session):** strengthened `_build_user_prompt` STOP instruction. Gemini was stopping early on its own when content "felt complete" (e.g., section 1.1 lost paragraphs 3-4 even though the page slice included page 2). New language explicitly forbids early stopping: "DO NOT stop earlier than [next_title]. Continue until you literally see [next_title] on a page. Even if content feels complete, KEEP GOING. Do NOT decide the section is complete on your own — completeness is determined ONLY by reaching the STOP heading." Page-range logic untouched (Step 1 still frozen).

### `backend/app/services/schema_postpass.py` — 🔴 FROZEN
- ✅ **REWRITTEN to VERIFIER mode (this session):** no longer injects nodes. Now cross-checks pypdf-extracted labels against the Gemini schema and returns `(schema_unchanged, warnings)`. Public API: `verify_schema_against_pdf_text()`. Backward-compat shim `enrich_schema_with_question_markers()` preserved (no-op).
- Why: the old injector relabeled "Illustration 1" → "EXAMPLE 1" because all kinds were emitted as title=`f"EXAMPLE {n}"`. It also injected phantom entries on scanned PDFs where pypdf returns garbage. Verifier mode eliminates both classes of bug while preserving the QC value (warns when Gemini misses a label, never silently mutates).
- New regex set covers: Example, Worked Example, Solved Example, Illustration, Practice Problem, Exercise, Problem, Activity, Try It, Progress Check, Quick Check.

### `backend/app/services/qc/helpers.py` — 🟡 ACTIVE
- 8 lines — QC helper tweaks for ref blocks.

### `backend/app/services/questions/structural_filter.py` — 🟡 ACTIVE
- 46 lines — structural filter for question extraction.

### `backend/app/api/question_banks.py` — 🟡 ACTIVE
- 164 lines — question bank API endpoints (per plan).

### `backend/app/models/question.py` + `__init__.py` — 🟡 ACTIVE
- Added question model fields.

### `backend/app/models/rejected_question.py` — 🟡 ACTIVE (NEW FILE)
- Rejected questions storage.

### `backend/app/schemas/block.py` — 🟡 ACTIVE
- 40 lines — added `example_ref`, `exercise_ref`, `question_ref` block schemas.

### `backend/app/schemas/analyser.py` — 🟡 ACTIVE
- 7 lines — analyser schema additions.

### `backend/app/utils/json_parse.py` — 🟡 ACTIVE
- 59 lines — robust JSON parsing for Gemini outputs.

### `backend/alembic/versions/0012_question_review.py` — 🟡 ACTIVE (NEW FILE)
- Migration for question review tables.

### `backend/prompts/v1/extractor.txt` — 🔴 FROZEN (Step 3 + Step 6 validated 2026-05-07)
- 44 lines — added PLACEHOLDER RULE (example_ref, exercise_ref, question_ref).
- ✅ **STEP 3 (this session):** PLACEHOLDER RULE narrowed. Only TRUE questions become chips:
  - `example_ref`: Example, Worked Example, Solved Example
  - `exercise_ref`: Exercise N.M, Problem, Practice Problem, Practice
  - `question_ref`: In-text Question, Intext Questions
- THEORY-AID labels are now transcribed as full theory body (no chip):
  - Illustration (any wording) → full walk-through prose/equations/list_items
  - Progress Check → numbered questions as list_item series
  - Activity → instructions/sub-prompts as list_items
  - Try It, Quick Check, Test Yourself, Self-Check, Check Your Understanding → list_item/body
  - Thinking Corner → body
  - Note, Remember, DO YOU KNOW, Key Point, Recall → key_point (existing behavior)
- Added DECISION RULE for ambiguous cases.
- **Theory transcription rules (verbatim, equations, definitions, tables, figures) untouched.**

### `backend/prompts/v1/schema_gemini.txt` — 🔴 FROZEN (Step 2 + tightening + Step 6 Category C validated 2026-05-07)
- ✅ **STEP 2 (this session):** Pass 3.5 UNIVERSAL LABEL CATCH-ALL split into TWO categories. Both categories still produce nested subsection entries; only the `content_types` tag differs.
  - **Category A (questions)** — `content_types=["questions"]`: Example, Worked Example, Solved Example, Exercise (numbered), Problem, Practice Problem, Practice, In-text Question, Intext Questions, Drill, Workout
  - **Category B (theory aids)** — `content_types=["theory"]`: Illustration, Progress Check, Activity, Try It, Quick Check, Test Yourself, Self-Check, Check Your Understanding, Thinking Corner, Note/Remember boxes
  - Category B `expected_question_count` defaults to 0 (theory aids are not assessed questions; their content lives in theory body).
  - **Theory transcription rules untouched.** Same OCR-ONLY, VERBATIM-OR-OMIT, IMMEDIATELY PRECEDING HEADING, SOLVED-EXAMPLES WRAPPER, SECTION-LEVEL EXERCISES, END-OF-CHAPTER EXCLUSIONS, ID convention rules — all unchanged.
- ✅ **STEP 2 tightening:** IMMEDIATELY PRECEDING HEADING rule strengthened. Added HARD RULE clarifying that a new heading appearing AFTER an example label on the same page is NOT a parent candidate. Added MULTI-PAGE TIEBREAKER making the previous-page-heading lookup explicit. Added the page-22 worked example: "Example 1.16" at top of page where "1.8 Special Cases" appears at bottom → parent must be 1.7 Types of Functions, NOT 1.8. Same rule, but stricter language so Gemini stops choosing the next-section heading as parent.
- ✅ **STEP 2 tightening (round 2):** Added explicit Exercise 1.1 worked example (page 6: Exercise 1.1 at top, "1.4 Relations" heading below → parent must be 1.3 Cartesian Product). Added UNIFORM APPLICATION clause: rule applies identically to Examples, Exercises, Problems, Illustrations, Activities, Progress Checks, etc. — no per-label-type carve-outs. The label's name does not influence parent selection — only its physical position relative to printed headings does.

### Old frozen marker (pre-Step 2):
- Pass 3.5 — In-Section Worked Examples / Exercises (NESTING).
- ✅ **OCR-ONLY rule** — strong opening forbidding generation/inference. "Self-check before emitting any item: can I point to the exact characters of this label on a specific page? If no, omit it."
- ✅ **VERBATIM-OR-OMIT** — anti-hallucination guard.
- ✅ **UNIVERSAL LABEL CATCH-ALL** — any printed label (Example, Worked Example, Solved Example, Illustration, Problem, Practice Problem, Exercise, Try It, Quick Check, Test Yourself, Activity, Progress Check, In-text Question, Drill, Workout, Self-Check, Check Your Understanding, Intext Questions, or any clearly-numbered styled-box prompt) qualifies. PDF wording is canon.
- ✅ **TAG AS QUESTIONS** — `content_types=["questions"]` (plural), `type` stays `"subsection"` for validation.
- ✅ **IMMEDIATELY PRECEDING HEADING** — parent = the heading directly above the label on the printed page (heading-level granularity, not page-level). No topic inference.
- ✅ **SOLVED-EXAMPLES WRAPPER** — items below ALL theory headings on a page wrap into `<chapter>-solved-examples` (or `-problem-set`, `-exercises` as printed).

### `backend/prompts/v1/question_extractor_v3.txt` — 🟡 ACTIVE
- 144 lines — question extraction prompt.

---

## Frontend Changes

### `frontend/src/components/Sidebar.tsx` — 🔴 FROZEN (Step 5 validated 2026-05-07)
- 374 lines.
- ✅ Added persistent **"🗂 Schema / Progress"** nav button at top of book section so user can always return to the schema page.
- ✅ Question bank entries.
- ✅ **STEP 5 (this session):** Theory tab filter broadened. Was: hide only `<parent>-example-N` IDs. Now: hide all question-kind IDs (`example`, `worked-example`, `solved-example`, `exercise`, `problem`, `practice-problem`, `in-text-question`, `intext-question`). Theory-aid IDs (illustration, progress-check, activity, try-it, quick-check, thinking-corner, note) are KEPT in Theory tab because they have `content_types=["theory"]`. Mirrors Step 4 linker `_QUESTION_KINDS` set.

### `frontend/src/components/BlockRenderer.tsx` — 🟡 ACTIVE
- 75 lines — renders `example_ref` / `exercise_ref` / `question_ref` as `RefChip` components.

### `frontend/src/pages/QuestionsPage.tsx` — 🟡 ACTIVE
- 427 lines — full questions UI.

### `frontend/src/pages/SchemaPage.tsx` — 🟡 ACTIVE
- 57 lines.

### `frontend/src/pages/ReaderPage.tsx` — 🟡 ACTIVE
- 67 lines — chip click navigation.

### `frontend/src/api/client.ts` + `hooks.ts` — 🟡 ACTIVE
- 99 lines combined — question bank API client.

### `frontend/vite.config.ts` — 🟡 ACTIVE
- 3 lines — minor.

---

## Untracked / Auxiliary Files
- `COST_ANALYSIS.md`, `COST_MODEL.xlsx`, `HANDOFF.md`, `build_cost_xlsx.py` — analysis/reference, not code.

---

## Open Issues
1. **Linker takes effect only after backend restart** — Python imports are cached, so any in-flight `extract` job uses pre-edit code. Need backend restart before testing.
2. **Currently running extract job (id `bd7bfd1a…`)** started before linker hook — won't trigger chip injection. Either kill it or run "Re-extract All" after restart.

## Step 6 — Three-category model (DONE — Category C inline callouts)

**Problem:** Notes / Thinking Corners / DO YOU KNOW boxes were being treated as Category B (separate subsections). On pages with multiple Note boxes, this caused over-extraction (extractor started at first Note, captured everything until next-sibling heading).

**Fix applied:**
- `schema_gemini.txt` Pass 3.5 — UNIVERSAL LABEL CATCH-ALL split into THREE categories:
  - (A) Question labels → separate subsection, `content_types=["questions"]`, chip in parent
  - (B) Theory-aid labels → separate subsection, `content_types=["theory"]`, full body extraction
  - (C) **NEW Inline callout labels** → NOT a separate subsection; transcribed inline as `key_point` block in parent's theory body
- Category C list: Note, Remember, DO YOU KNOW, Thinking Corner, Key Point, Recall
- `extractor.txt` PLACEHOLDER RULE — explicitly groups Category C labels in one rule, all becoming `key_point` blocks
- Easy editing: add/remove from any category list in `schema_gemini.txt` Pass 3.5

**Side benefit:** the same-page multi-Note ambiguity bug largely disappears because Notes/Thinking Corners no longer create separate schema subsections.

## Step 6.1 (TODO — possibly not needed after Step 6): same-page multi-occurrence ambiguity
**Symptom:** When a page has multiple labels of the same type (e.g., two "Note" boxes, two "Illustration N" near each other), the section like `1.3-note-2` ends up containing content from the FIRST Note + everything in between + up to the next-sibling heading. Same-named labels confuse the extractor about WHICH occurrence to start at.

**Plan (NOT yet implemented):**
- Pass a `prev_title` to `_build_user_prompt` in `theory_extractor.py` — the previous sibling's title.
- Update prompt to say: "Start extracting at the occurrence of `<title>` that appears AFTER the heading `<prev_title>`. Stop at `<next_title>`. If `<title>` appears multiple times in the slice, the correct one is the FIRST occurrence after `<prev_title>`."
- For the very first section (no previous sibling), use the section_id or chapter title as anchor.

**Why deferred:** Validation in progress on Steps 3-5; want a clean test cycle first. Step 6 will be a small surgical edit to one function in `theory_extractor.py` + one call site in `extract.py` (and the equivalent in `re_extract_section_task`).

---

## Test Plan for New Book Upload
1. **Restart backend** (mandatory — picks up new linker hook).
2. Upload PDF → analyse → approve schema → extract.
3. When extract job hits 100%, verify in logs: `example_linker summary: ...`.
4. Open any parent section that has `-example-N` children in the schema → confirm `question_ref` chips appear inline between paragraphs.
5. Click a chip → should navigate to the example's own section page.
