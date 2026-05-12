# HANDOFF — Question Extraction v3

> Living handoff doc for the v3 question-extraction pipeline. Pick this up cold
> and continue without re-reading transcripts.

---

## 1. What this project is

CMDS PDF → Question Bank app. Two independent flows in one FastAPI + React app:

- **Theory extraction** (existing, untouched) — extracts theory blocks per section.
- **Question Bank extraction** (this project) — OCR-extracts every question
  printed in the PDF (exercises, MCQs, worked examples, problems) using
  Gemini 2.5 Flash, stores them in `question_banks` + `questions` tables,
  serves them in a dedicated `/questions` page.

The plan that bootstrapped this work is at
`/Users/aastha/.claude/plans/luminous-splashing-kazoo.md` and is fully
implemented. Items 1–10 of the build order are done; items 11+ (worker v3
rebuild, diagnostic UI, quality fixes) are what this handoff covers.

---

## 2. Architecture (current)

### Backend
- FastAPI + async SQLAlchemy + Alembic + SQLite
- Gemini 2.5 Flash via `google-genai` SDK (PDF upload → JSON response)
- Worker: section-aligned **v3** — one Gemini call per schema section
- Stats taxonomy per section: `complete | partial | empty | failed`
- Cross-section dedup safety net via SHA1 fingerprint over normalised text

### Frontend
- React + Vite + React Query + TypeScript
- Live polling while bank status is `extracting` (sections appear block-by-block)
- Per-section diagnostic UI (status pill, retry button, rejected items panel)

### Data model (unchanged from plan)
- `question_banks` — one bank per book per extraction run
- `questions` — verbatim OCR rows linked to bank + section_ref + page

---

## 3. Worker v3 — what it does

`backend/app/workers/questions_v3.py`

Per book:
1. `flatten_sections(schema)` → list of `_Unit` (one per non-excluded section + each excluded block)
2. For each unit, slice the PDF to its page range (with **+1 page trailing pad**)
3. Send slice + `question_extractor_v3.txt` system prompt + per-unit user prompt to Gemini
4. Parse JSON, classify each item (`exercise|mcq|example|problem|try_it|review|short|long|other`)
5. Filter rejects via heuristics (`_reject_reason` recorded for diagnostic UI)
6. Persist Question rows (delete-then-insert per section_ref, idempotent)
7. After all sections: run `dedup_bank()` cross-section safety net
8. Compute per-section stats (expected/identified/extracted/rejected/status) + totals + dedup info
9. Write `bank.stats` JSON + flip `bank.status` to `ready` / `failed`

Worker registered as `"extract_questions_v3"` task. Recovery handler in
`backend/app/main.py` startup re-dispatches in-flight jobs after restart.

### Per-section retry
`backend/app/workers/questions_v3.py:_run_section_retry()` re-extracts ONE
section, replaces only that section's Question rows, and updates the matching
`extraction_stats.sections` entry in place. Triggered by:
- API: `POST /api/question-banks/{bank_id}/sections/{section_ref}/retry`
- UI: `↺ Retry section` button on partial/failed section cards

Task name: `"re_extract_section_v3"`.

---

## 4. Files shipped in this conversation

### Backend
| File | What |
|------|------|
| `backend/app/workers/questions_v3.py` | v3 worker (whole file). Adds `_run_section_retry`, `_re_extract_section_v3`, register `re_extract_section_v3`. **+1 page pad** at line ~240 (`padded_end = unit.page_end + 1`). Bug fixes: `book.book_schema` → `book.schema`, `book.pdf_uri` → `book.pdf_url` (replace_all). |
| `backend/app/services/questions/dedup.py` | TeX-aware fingerprint at line 34. Strips `\text{...}`, `\mu`, `\frac{}{}`, `\,`, `$`, `{}` before SHA1 so `$25 \mu C$` and `$25\,\mu C$` collapse to the same hash. |
| `backend/prompts/v1/question_extractor_v3.txt` | Stronger START/STOP rules at line 19. Explicit "If content appears BEFORE the heading, SKIP IT entirely. Return 0 if heading is absent from these pages." Kills cross-section bleed. |
| `backend/app/api/question_banks.py` | New endpoint `POST /{bank_id}/sections/{section_ref}/retry` → dispatches `re_extract_section_v3`. |
| `backend/app/main.py` | Registers v3 worker module + recovery handler for `extract_questions_v3` and `re_extract_section_v3`. |

### Frontend
| File | What |
|------|------|
| `frontend/src/api/client.ts` | Extended `ExtractionStats` with `worker_version`, `totals`, `sections`, `dedup`. New types `ExtractionRejectedItem`, `ExtractionSectionStats`. New `api.retrySection(bankId, sectionRef)`. |
| `frontend/src/api/hooks.ts:227` | `useQuestions(bankId, { bankStatus })` — polls every 2s while bank is `extracting`/`pending`, stops on `ready`/`failed`. New `useRetrySection()` mutation invalidates questionBank + questions queries. |
| `frontend/src/pages/QuestionsPage.tsx` | `V3StatsStrip` (status pill counts + dedup), `v3StatsBySection` map, status pill + `↺ Retry section` button on each `SectionBlock`, collapsible `Show N rejected` panel listing each rejected item with `_reject_reason` + `raw_text`. Threads `bankStatus` into `useQuestions`. |

### Migration
No new migrations in this conversation — `0005_question_banks_and_questions.py`
already existed from the original plan implementation.

---

## 5. The 4 quality fixes (mapped to user screenshots)

User reported 4 issues from a real Chap-8 Electricity extraction. All fixed:

| # | Issue | Cause | Fix | File |
|---|-------|-------|-----|------|
| 1 | Sections didn't appear in UI while extracting | `useQuestions` not polling | Poll every 2s while `bankStatus` is `extracting`/`pending` | `frontend/src/api/hooks.ts:227` + `frontend/src/pages/QuestionsPage.tsx:62` |
| 2 | Electric Current section missed Q8.17 (printed on next-section's start page) | Schema page boundaries misaligned by 1 | +1 page trailing pad on every PDF slice | `backend/app/workers/questions_v3.py:240` |
| 3a | Q8.5/8.6/8.7 from Electric Field bled into Electric Potential | Gemini ignored "skip content before heading" rule | Hardened prompt: explicit skip-before-heading instruction + "return 0 if heading absent" | `backend/prompts/v1/question_extractor_v3.txt:19` |
| 3b | Q8.8 / Q8.11 appeared twice (TeX-spacing differences) | Fingerprint kept TeX commands, so `$25 \mu C$` ≠ `$25\,\mu C$` | Strip `\text{}`, `\mu`, `\frac{}{}`, `\,`, `$`, `{}` before SHA1 | `backend/app/services/questions/dedup.py:34` |

All 4 are live on the running backend (`--reload`) and Vite frontend (HMR).

---

## 6. Diagnostic UI (item #9 from the original plan)

Three sub-tasks shipped earlier in this conversation, before the 4 quality fixes:

1. **Status-counts strip** at top of QuestionsPage — `V3StatsStrip` shows `complete | partial | empty | failed` pill counts + dedup count when `stats.totals` exists.
2. **Status pill + rejected items panel** per section — color-coded badge, collapsible "Show N rejected" listing each rejected question's `_reject_reason` and `raw_text`.
3. **Per-section retry button** — `↺ Retry section` on partial/failed cards, calls `useRetrySection()` mutation.

---

## 7. Current state

### Working & verified
- Backend running on port 8001 with `--reload`. Frontend on port 5174 (Vite HMR).
- Existing theory extraction flow untouched and still works.
- Question bank for Chap-8 Electricity exists at bank id `4cc66bb9-f7de-4545-8404-7bbcaa875a68`, status `ready`, 147 questions, with full v3 stats payload.
- `_fingerprint`, `_run_section_retry`, `_extract_unit` all syntax-checked OK (`python3 -c "import ast; ast.parse(...)"`).
- No frontend console errors after HMR.

### Not yet verified end-to-end
- The 4 quality fixes are LIVE but have not yet been re-tested on a fresh extraction. **Next user action**: delete the existing Chap-8 bank and re-extract; verify (1) sections populate progressively, (2) Q8.17 captured, (3) clean section boundaries, (4) no Q8.8/Q8.11 duplicates.

### Known good test fixture
- Book id: `a1de8117-2717-422d-aecc-ab376cf01f31`
- Title: "Chap-8 Electricity" / Physics / Class 10
- 46 pages, 9 schema sections + excluded blocks
- Existing bank stats show `expected_total: 177, extracted_total: 140` — pre-fix baseline. Re-extract after fixes should improve those numbers (especially the 17-expected `8` chapter intro section that got 0).

---

## 8. Open issues / next steps

None blocking. Suggested verification + follow-up:

1. **Re-extract Chap-8 Electricity** to validate all 4 fixes empirically. Compare new totals against the baseline above.
2. **Watch for**: schemas where `page_end` is the last PDF page — `+1 pad` is bounded by `min(total - 1, ...)` in `_slice_pdf`, so safe, but worth confirming with a chapter-final section.
3. **Watch for**: TeX-strip fingerprint over-collapsing distinct questions (e.g. two questions whose only difference is a numerical value rendered in TeX). If observed, tighten the fingerprint to keep digits intact (current impl already keeps digits via `_NON_ALNUM` keeping `0-9`, so this is unlikely).
4. **Optional**: add a "Retry whole bank" button next to the status strip for failed banks. Currently the only path is delete-then-recreate.
5. **Optional**: surface the `dedup.groups` payload in a "Duplicates dropped" expandable panel so user can audit what got merged.

---

## 9. Key decisions & why

| Decision | Why |
|----------|-----|
| Section-aligned worker (one Gemini call per section) | The previous monolithic call hallucinated cross-section content. Slicing forces locality. |
| Keep both `blocks` (legacy) and `sections`/`totals` (v3) in stats JSON | Avoid breaking the older Sidebar component that reads `blocks`. Frontend prefers `sections` when present. |
| Delete-then-insert per section_ref on retry | Idempotent; avoids partial-update accounting bugs. |
| Cross-section dedup as a safety net rather than primary mechanism | Section slicing should make duplicates impossible by construction. Dedup catches the two real edge cases: schema page overlap + reprinted content. |
| TeX-strip fingerprint instead of disabling fingerprint length cap | Length cap (240 chars) is needed to ignore trailing solution noise. TeX strip is the cheapest fix that handles real-world cases. |
| +1 page trailing pad universal (not opt-in per section) | Schemas come from a separate vision pass with its own off-by-one error budget. Universal pad costs ~5% more pages per call but eliminates the failure mode. |
| Prompt hardened with explicit "skip before heading" rather than tightening the slice | Slice tightening would re-introduce the missed-question bug. Trust the LLM to honour the heading rule when told emphatically. |
| Dynamic-poll `useQuestions` instead of WebSocket | Backend already has no streaming infra. 2s poll is cheap, simple, gated to extracting state only. |

---

## 10. Files another Claude should read first

If you're picking this up cold:

1. `backend/app/workers/questions_v3.py` — the heart of the pipeline. ~640 lines.
2. `backend/prompts/v1/question_extractor_v3.txt` — the prompt is part of the contract.
3. `backend/app/services/questions/dedup.py` — small, self-contained.
4. `backend/app/api/question_banks.py` — all bank endpoints incl. retry.
5. `frontend/src/pages/QuestionsPage.tsx` — primary UI surface.
6. `frontend/src/api/client.ts` — type contracts.
7. `/Users/aastha/.claude/plans/luminous-splashing-kazoo.md` — original plan (still accurate for items 1–10; v3 worker rewrite is post-plan).

---

## 11. How to run

Backend (port 8001, auto-reload):
```bash
cd backend && uvicorn app.main:app --reload --port 8001
```

Frontend (port 5174, Vite HMR):
```bash
cd frontend && npm run dev
```

Both are typically already running in this worktree's preview servers.

DB lives at `backend/cmds.db` (SQLite). Migrations auto-apply on startup.

Gemini API key in env: `GOOGLE_API_KEY`. Model: `gemini-2.5-flash` (per user
memory: flash for question OCR + QA verifier, NOT pro).

---

## 12. Conversation chronology (for context)

1. User asked to ship items #9 (diagnostic UI) — three sub-tasks, all done.
2. User reported `'Book' object has no attribute 'book_schema'` crash. Fixed via `book.schema` rename + `pdf_url` rename.
3. User shared 4 screenshots showing real-world quality issues from Chap-8 Electricity (no live UI updates, missing Q8.17, Electric Field bleed into Electric Potential, Q8.8/Q8.11 dupes).
4. Diagnosed each → proposed 4 fixes → user approved → all 4 shipped.
5. User asked for this HANDOFF.md.

---

_Last updated: 2026-05-04. State: 4 quality fixes live, awaiting re-extraction verification._

---

## 13. PENDING TASKS — Theory extraction improvements (planned, not yet shipped)

User-approved on 2026-05-04. To be implemented once question pipeline verification is complete.

### TASK A — Question/Example placeholders in theory _(approved: ALL such items become placeholders, name-matched)_
**Goal:** Theory extraction should NOT transcribe the body of any worked example, exercise, or question. Insert a placeholder identifier at the exact spot, so theory + question bank can be merged later by matching identifiers.

**Files to change:**
- `backend/prompts/v1/extractor.txt`
- `backend/prompts/v1/extractor_attempt2.txt`
- `backend/prompts/v1/extractor_attempt3.txt`
- `backend/app/services/qc.py` (accept new block types)
- `frontend/src/...` reader components (render placeholder pill, optional click-to-jump-to-question)

**New block schema (proposed):**
```json
{ "type": "example_ref",  "label": "Example 5.2",     "number": "5.2" }
{ "type": "exercise_ref", "label": "Exercise 8.3 Q4", "number": "8.3.4" }
{ "type": "question_ref", "label": "Try It #2",       "number": "2" }
```

**Prompt rule to add:** "When you encounter a worked example, exercise, or question prompt, do NOT transcribe its body. Emit a placeholder block with just the printed identifier (e.g. 'Example 5.2'). Position it where the body would have been. The full body is captured by the question extraction pipeline; theory only marks the location."

**Preserved:** body text, definitions, equations, key_points, headings, figures — untouched.

### TASK B — Section-level theory re-extract _(approved)_
**Goal:** A `↺ Re-extract section` button on each theory section. Re-runs OCR for just that section's pages and replaces its blocks. Mirror of the question-bank section retry already shipped.

**Files to add/change:**
- New worker task `re_extract_theory_section` in `backend/app/workers/extract.py`
- New API endpoint `POST /api/sections/{section_id}/re-extract`
- Frontend reader → add the retry button on each section card
- Section status taxonomy: `extracting | ready | failed | rerunning`

### TASK C — Defensive figure prompt tightening _(approved)_
**Goal:** Confirm we never describe diagram contents from training knowledge — only transcribe printed label + caption verbatim.

**File:** `backend/prompts/v1/extractor.txt` — add explicit "NEVER describe what is depicted in a diagram. Only transcribe the printed caption verbatim. Use exact label like 'Figure 5.1' as it appears."

### TASK D — Whole-book extraction reliability _(awaiting failing-book ID from user)_
**Goal:** Some books silently don't extract. User will share specific failing book IDs. Then root-cause and add a "Retry all" button.

**Likely fixes:**
- Surface partial-completion status (X of Y sections done, Z failed)
- Add `POST /api/books/{id}/re-extract` whole-book retry
- Investigate dispatch / recovery edge cases

### Verification checklist (after Tasks A–C ship)
- [ ] Theory of any chapter — every Example/Exercise/Question is now a placeholder
- [ ] Bodies of those items still appear in question bank (matched by identifier)
- [ ] Section retry button works on theory cards (re-OCR, replaces blocks)
- [ ] Figure blocks still hold exact printed label + caption (no hallucinated descriptions)
- [ ] No regression: theory body / definitions / equations / key_points still extracted verbatim

