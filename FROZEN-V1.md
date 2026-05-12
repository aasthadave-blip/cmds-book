# FROZEN v1 — Question Bank + Regeneration

Frozen 2026-05-12. This document is the canonical list of files comprising
the stable v1 surface for the question extraction + regeneration feature.

**Invariant**: do NOT change any file in this manifest without:
1. Explicit user approval naming the file
2. Updating the SHA256 in this file in the same commit
3. Re-running the full QA suite (T1–T23 in this session's transcript)

## Architecture summary

```
Backend (FastAPI + async SQLAlchemy, SQLite)
├── API routers
│   ├── question_banks.py     — bank lifecycle, sections, exports, retry
│   ├── question_regenerations.py — regen lifecycle, save, retry-section, exports
│   └── books.py / qa.py / sections.py — unchanged adjacent surfaces
├── Workers (in-process dispatcher)
│   ├── questions_v3.py       — bank extraction (dedup DISABLED)
│   └── question_regen_v3.py  — regen (mirrors source structure)
├── Prompts (Gemini)
│   ├── question_extractor_v3.txt   — SHARED-CONTEXT + match-the-column rules
│   └── question_regenerator_v3.txt — Rule 8 STRUCTURE MIRRORING
└── Core
    ├── core/db.py            — engine + SQLite FK PRAGMA hook
    └── models/question.py    — has source_question_id (mig 0014)

Frontend (Vite + React + Zustand)
├── pages/
│   ├── QuestionsPage.tsx     — RegenView, RegenRunBar, QuestionList
│   └── SchemaPage.tsx        — renders V3SummaryTable post-extraction
├── components/Sidebar.tsx    — Theory + ✨ Regenerated mirror trees
├── stores/ui.ts              — view + selectBank + selectQuestionRegen
└── api/{client,hooks}.ts     — typed API surface

```

## Service quality invariants

1. **Pure OCR** — no dedup (cross-section or in-chunk); whatever Gemini
   transcribes is kept. See `questions_v3.py:1479` (dedup disabled).
2. **No fabrication** — extractor prompt Rules A + B; regenerator prompt
   Rule 8. Empty `extracted` for theory-only sections is correct.
3. **Section anchor sovereignty** — worker stamps `section_ref` and
   `source_question_id`, never Gemini. See `_persist_regen_items`.
4. **Original ⊥ Regenerated** — `GET /banks/{id}/questions` filters
   `regen_id IS NULL`; regen variants only via regen endpoints.
5. **FK cascade enforced** — SQLite PRAGMA foreign_keys=ON on every
   connection; deleting a bank cascades to its regens + questions.
6. **No silent state drift** — `useEffect` on `regen.status` refetches
   `regenData` on extract→ready transitions.

## Frozen file manifest (SHA256)

- `backend/prompts/v1/question_extractor_v3.txt`  
  sha256=`c3b83e9e37d2c34cfc501c595e2a43dcecf63a8a813384d85d2fd6dd73843ce8`  lines=362
- `backend/prompts/v1/extractor.txt` (theory extractor)  
  sha256=`a1ead534460afa7ae802f10e1f2fbdc9794fa7b104d036416368de7da3f01f4f`  lines=98
- `backend/app/services/theory_extractor.py` (under-include boundary rule)  
  sha256=`a31d2870e25a2916d59515cba01e51751faf5ed1c79f7bbc47b99ca6e8a4ecf9`  lines=287
- `backend/app/services/docx_export.py` (native python-docx builder)  
  sha256=`15013590316ee176b145450b1a0c691e8ed57096b00231cdede697888a1c2813`  lines=551
- `backend/prompts/v1/question_regenerator_v3.txt`  
  sha256=`b445a3aa119cdb97f7d38c95ebdb4cd7685a72243093351a4d1104fc39528ec8`  lines=257
- `backend/app/api/question_banks.py`  
  sha256=`44c74101e117bf0e4c912305016f3d7aaee9050335c3c8fd6b271037c385c124`  lines=880
- `backend/app/api/question_regenerations.py`  
  sha256=`9cc8f60a6b19157e5f6f3dad8ffd9fe35f4a1ace894d88781e151f559ab2d462`  lines=634
- `backend/app/workers/questions_v3.py`  
  sha256=`a24e2545cc46a74a1e128e5c510364f25ac56b9b1156ba5dd6a5a2053b9446d3`  lines=1682
- `backend/app/workers/question_regen_v3.py`  
  sha256=`fe7d1cfe7061ca0a7fe2d141b7d694e2200b2f61c792be69eaad20d49b7e65a5`  lines=911
- `backend/app/core/db.py`  
  sha256=`ef2dc4a6ca5d7ffea9e68e1d361eca324d1121754182ab3262b2fc94549e5140`  lines=56
- `backend/app/models/question.py`  
  sha256=`6a135315cfbf9744db66477afc32ce5f4954154ac07c5b7f6f008d4f801c764a`  lines=117
- `backend/app/models/question_bank.py`  
  sha256=`5f62bf18adc97d36ffd0c80646dd6543b8b55a679a233ef4ae9d8b2954104f13`  lines=49
- `backend/app/models/question_regeneration.py`  
  sha256=`93d6ae1913dae2f6f1095fcb3ec061eac513ae9764aa60b83063af7c705a8d7a`  lines=73
- `backend/alembic/versions/0013_question_regen_params.py`  
  sha256=`752c7c94e30927cbf11febce95e483dae49cb1d505c10d69e63a2468e0fbbe59`  lines=42
- `backend/alembic/versions/0014_question_source_link.py`  
  sha256=`92bbbb75e45282726c63f59630755bfe6e38d52bbdaca7cce749d0e54f8454e6`  lines=45
- `frontend/src/pages/QuestionsPage.tsx`  
  sha256=`c92a2e6c676a9ab4a987390e8a624269daa22c6f928c2d3525e6bde4fe4ef4df`  lines=2666
- `frontend/src/pages/SchemaPage.tsx`  
  sha256=`8c9e55eb6fa45027735030788e46feb787a5263e50bf22cbec51fd5d70db8512`  lines=987
- `frontend/src/components/Sidebar.tsx`  
  sha256=`8d0c9369d69e753035d76c41e74e3a5af81c303adb7baa1b5a8021d147e3580b`  lines=834
- `frontend/src/stores/ui.ts`  
  sha256=`259ae8b9f9fc99059e8858c6e7aaf1f034fae5ca43542a76278429e2e1aae1d3`  lines=87
- `frontend/src/api/client.ts`  
  sha256=`5f27164c6b6d5d7cabfd7327ed92c02443d0086f70b1506bed0bc2b0ba1a54cb`  lines=609
- `frontend/src/api/hooks.ts`  
  sha256=`2fc65d36e651241fd9f317604d4152f2de55579d264c32d13e9908016ac4de0e`  lines=449


## Known non-blockers (NOT freezing — backlog)

1. **UUID format inconsistency** — `question_banks.id` stored hyphenless,
   FK columns stored hyphenated. SQLite text-comparison handles both;
   orphan check returns 0. Latent footgun if anyone introduces strict
   comparisons. Normalize on write in a future pass.

2. **`sources[]` lives under each section**, not top-level — current
   frontend consumer reads it correctly. If we ever flatten the API
   response, this will need updating.

## QA results

23/23 tests passed in the session transcript. Re-run before any change
to a frozen file.

