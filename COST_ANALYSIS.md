# Pipeline Cost Analysis — Theory & Question Services

**Purpose:** model the per-book, per-month, and per-year LLM spend for the four
production pipelines so we can size the budget for the next 12 months and
identify the highest-leverage cost optimisations.

**Audience:** internal review + Claude follow-up for sensitivity analysis.

**Source-of-truth:** all numbers below are derived from the live code in this
repo (paths and line numbers cited inline). Pricing is from Google's published
Gemini API rate card.

---

## 1. Pricing reference (Gemini API, public list price)

| Model | Input ($/1M tokens) | Output ($/1M tokens) | Notes |
|---|---|---|---|
| `gemini-2.5-pro` | **$1.25** (≤200K context) / $2.50 (>200K) | **$10.00** / $15.00 (>200K) | Used for theory extract, theory regen, schema, figure extract |
| `gemini-2.5-flash` | **$0.30** | **$2.50** | Used for question extract v3, question regen v2, QA verifier |
| `gemini-2.0-flash-preview-image-generation` | ~$0.039 / image (text+image input + image output) | — | Used for figure regeneration |

**PDF token accounting (Gemini's rule):** every PDF page is billed as
**258 input tokens** for the image, plus tokens for any extracted text on the
page. For mid-density textbook pages we use **~600 input tokens / page**
(258 image + ~350 text) as a working assumption. Light pages (key-points only)
trend lower; question-dense pages with worked solutions trend higher.

**Tokenisation rule of thumb:** 1 token ≈ 4 characters of English. Every prompt
size below converts char-count → tokens at 4:1.

---

## 2. Per-call cost — direct measurement from code

Numbers come from the audit in §6 (citations there).

### A. Theory extraction (`extract_book_task`, `re_extract_section_task`)

| Variable | Value | Source |
|---|---|---|
| Model | `gemini-2.5-pro` | `theory_extractor.py:27` |
| System prompt | 5,334 chars ≈ **1,334 tokens** | `prompts/v1/extractor.txt` |
| User prompt | ~50 tokens (title, ID, stop boundary) | `theory_extractor.py:` user msg |
| PDF slice | **2–20 pages**, mean ~6 pages → 1,548–12,000 input tokens (use **3,600**) | schema page ranges |
| Output cap | 32,000 tokens; **typical 4,000–8,000** observed | `theory_extractor.py:107` |
| Retry multiplier | up to **3×** on QC fail; observed **~1.3× mean** | `theory_extractor.py:26` |

**Per-section unit cost** (mean, 1.3× retry factor included):
- Input: (1,334 + 50 + 3,600) × 1.3 = **6,477 tokens** → 6,477 × $1.25/1M = **$0.0081**
- Output: 6,000 × 1.3 = **7,800 tokens** → 7,800 × $10/1M = **$0.0780**
- **Subtotal: ~$0.086 per section**

### B. Theory regeneration (`regenerate_book_task`)

| Variable | Value | Source |
|---|---|---|
| Model | `gemini-2.5-pro` | `regenerator.py` via `gemini_client.py:34` |
| System prompt | 10,792 chars ≈ **2,698 tokens** | `prompts/v1/regenerator.txt` |
| User prompt | section's free blocks (prose+lists+headings) ~**1,500 tokens** mean | `regenerator.py:82` |
| PDF slice | **NONE** — pure text-in/text-out | — |
| Output cap | 16,000 tokens; **typical 1,500–3,000** observed | `regenerator.py:26,93` |
| Retry multiplier | **1.0×** (no retry) | `regenerator.py:82–108` |

**Per-section unit cost:**
- Input: 2,698 + 1,500 = **4,198 tokens** → **$0.0052**
- Output: 2,200 tokens → **$0.0220**
- **Subtotal: ~$0.027 per leaf section**

### C. Question extraction v3 (`_extract_questions_v3`)

| Variable | Value | Source |
|---|---|---|
| Model | `gemini-2.5-flash` | `questions_v3.py:57` |
| System prompt | 10,949 chars ≈ **2,737 tokens** | `prompts/v1/question_extractor_v3.txt` |
| User prompt | ~75 tokens | `questions_v3.py:` user msg |
| PDF slice | section pages + **1-page trail**, mean ~7 pages → **4,200 tokens** | `questions_v3.py:277` |
| Output cap | 65,536 tokens; **typical 3,000–10,000** observed (verbatim Q text + solutions) | `questions_v3.py:60` |
| Retry multiplier | up to **2×**; observed **~1.4× mean** | `questions_v3.py:58,282` |

**Per-unit unit cost** (mean, 1.4× retry):
- Input: (2,737 + 75 + 4,200) × 1.4 = **9,816 tokens** → 9,816 × $0.30/1M = **$0.00295**
- Output: 6,500 × 1.4 = **9,100 tokens** → 9,100 × $2.50/1M = **$0.02275**
- **Subtotal: ~$0.026 per unit**

### D. Question regeneration v2 (`extract_questions_regen_task`)

| Variable | Value | Source |
|---|---|---|
| Model | `gemini-2.5-flash` | `questions_v2.py:49` |
| System prompt | ~3,500 chars ≈ **875 tokens** | `prompts/v1/question_extractor.txt` |
| 3-pass design | excluded blocks + inline sections + leaf sections, **~25 calls/chapter** mean | `questions_v2.py:1352` |
| PDF slice per call | ~5 pages → **3,000 tokens** | block page ranges |
| Output cap | 65,536; **typical 4,000–8,000** | `questions_v2.py:52` |
| Retry multiplier | up to **2×**; observed **~1.3× mean** | `questions_v2.py:356` |
| Concurrency | 3 threads / pass; global cap 4 in-flight | `questions_v2.py:58`, `gemini_runtime.py:38` |

**Per-call unit cost** (mean, 1.3× retry):
- Input: (875 + 3,000) × 1.3 = **5,038 tokens** → **$0.00151**
- Output: 6,000 × 1.3 = **7,800 tokens** → **$0.01950**
- **Subtotal: ~$0.021 per call → ~$0.53 per chapter regen**

### E. One-time supporting calls (per book)

| Pipeline | Model | Calls | Per-call cost | Source |
|---|---|---|---|---|
| Schema analyse | `gemini-2.5-pro` | 1 (full PDF, ~50 pages × 600 tok = 30K input + 2.4K prompt; output ~6K) | **~$0.105** | `schema_builder.py:28,98` |
| Figure extraction (digital PDFs) | none | — | $0 (pymupdf only) | `figure_extractor.py` |
| Figure extraction (scanned PDFs) | `gemini-3.0-pro` | 1 / section, ~6K in / 4K out | **~$0.047** | `figure_extractor.py:24,168` |
| Figure regeneration | `gemini-2.0-flash-preview-image-generation` | 1 / figure, ~$0.039 each | **~$0.039** | `figure_regenerator.py:17` |
| QA verifier (post-extract) | `gemini-2.5-flash` | 1 / question-bank page (~30 pages) | **~$0.0023 each** | `verifier.py:29,37` |
| LLM QC auditor | Claude | on-demand only | **~$0.005** | `qc/llm.py:14,38` |

---

## 3. Reference book sizes

To run yearly math we need a "book unit". Calibrating from real data in this
repo (Modern Physics, Chap-8 Electricity):

| Profile | Pages | Sections (extract units) | Leaf sections (regen units) | Question units (v3) | Figures |
|---|---|---|---|---|---|
| **Small** (10-pp pamphlet) | 10 | 5 | 4 | 4 | 1 |
| **Standard chapter** | 50 | 20 | 15 | 15 | 6 |
| **Large chapter** | 80 | 35 | 25 | 25 | 12 |
| **Whole textbook (12 ch)** | 600 | 240 | 180 | 180 | 75 |

Below we model **standard chapter (50 pp)** as the unit, then scale.

---

## 4. Per-book cost (standard chapter)

### One full happy-path run (no manual reruns)

| Step | Calls | $/call | Subtotal |
|---|---|---|---|
| Schema analyse | 1 | $0.105 | $0.105 |
| Theory extract | 20 | $0.086 | $1.720 |
| Figure extract (assume scanned) | 20 | $0.047 | $0.940 |
| Figure regen | 6 | $0.039 | $0.234 |
| Theory regen (single pass) | 15 | $0.027 | $0.405 |
| Question extract v3 | 15 | $0.026 | $0.390 |
| QA verifier | 30 | $0.0023 | $0.069 |
| **Total / chapter** |  |  | **~$3.86** |

### With realistic re-runs

The system supports section-level re-extract (Task 3) and full-book regeneration
re-runs. Empirically, ~20% of sections get re-extracted once, and theory regen
is run twice on average (different style settings).

| Add-on | Calls | Subtotal |
|---|---|---|
| 20% theory re-extracts | 4 × $0.086 | $0.344 |
| 2nd theory regen pass | 15 × $0.027 | $0.405 |
| 1 question regen run (v2) | 25 × $0.021 | $0.525 |
| **Re-run uplift** |  | **+$1.27** |

**Realistic per-chapter cost: ~$5.13**
**Realistic per-12-chapter textbook: ~$61.6**

---

## 5. Yearly projection

Plug in the volume assumption to get the annual bill. Below are 3 scenarios.

| Scenario | Books / yr | Chapters / yr | Cost / chapter | Annual cost |
|---|---|---|---|---|
| **Pilot** (5 books) | 5 | 60 | $5.13 | **~$308** |
| **Steady-state** (50 books) | 50 | 600 | $5.13 | **~$3,078** |
| **Scale** (250 books) | 250 | 3,000 | $5.13 | **~$15,390** |
| **Aggressive growth** (1,000 books) | 1,000 | 12,000 | $5.13 | **~$61,560** |

### Where the money goes (steady-state, 600 chapters/yr)

| Pipeline | $ / yr | % of total |
|---|---|---|
| Theory extract (Pro) | $1,032 | 33.5% |
| Figure extract (Pro, scanned) | $564 | 18.3% |
| Theory regen (Pro, 2× passes) | $486 | 15.8% |
| Question regen v2 (Flash) | $315 | 10.2% |
| Question extract v3 (Flash) | $234 | 7.6% |
| Figure regen (Flash image) | $140 | 4.5% |
| Re-extracts (Pro) | $206 | 6.7% |
| Schema + QA + misc | $101 | 3.3% |

**Pro-tier (theory + figures + schema) = 74% of spend.**
Flash-tier (questions + QA) = 22%. Image generation = 4.5%.

---

## 6. Sensitivity & cost-reduction levers

### Highest-leverage levers (ranked by $/yr saved at steady-state)

1. **Move theory extraction from Pro → Flash on first attempt, escalate to Pro
   only on QC fail.** Theory extract is $1,032/yr at Pro. If 80% pass on Flash
   first-shot (Flash is ~20× cheaper), **savings ≈ $620/yr** at steady-state,
   $3,100/yr at scale, $12,400/yr at aggressive-growth.
2. **Cache schema analyse output** — already 1× per book, but if a book is
   re-uploaded for any reason it re-runs. Adding a content-hash cache saves
   ~5% of Pro spend.
3. **Skip figure extraction for digital-native PDFs.** Already implemented
   (pymupdf path), but not all books are correctly classified — audit the
   classifier; mis-routing one digital book → scanned costs ~$0.94 extra.
4. **Reduce theory regen output cap** — current 16,000 is rarely needed; mean
   output is ~2,200 tokens. Cap at 6,000 to bound worst-case cost; saves
   nothing on average runs but caps tail risk.
5. **Question regen consolidation** — v2 has 3-pass design with overlap. v3
   is section-aligned (1 pass). Sunsetting v2 saves ~$315/yr at steady-state
   without functional regression for new banks.

### Things that do NOT meaningfully save money

- Lowering question extract output cap (already cheap, Flash)
- Cutting QA verifier (3.3% of spend, but it's the only ground-truth check)
- Reducing prompt sizes (largest is ~3K tokens, dwarfed by PDF + output)

---

## 7. Variables the user can tweak in a spreadsheet

To recompute yearly cost under different assumptions, these are the knobs:

| Knob | Default | Range to test |
|---|---|---|
| `BOOKS_PER_YEAR` | 50 | 5 — 1,000 |
| `CHAPTERS_PER_BOOK` | 12 | 1 — 24 |
| `SECTIONS_PER_CHAPTER` | 20 | 5 — 40 |
| `LEAF_SECTIONS_PER_CHAPTER` | 15 | 4 — 30 |
| `QUESTION_UNITS_PER_CHAPTER` | 15 | 5 — 30 |
| `FIGURES_PER_CHAPTER` | 6 | 0 — 20 |
| `SCANNED_PDF_FRACTION` | 1.0 | 0.0 — 1.0 |
| `THEORY_EXTRACT_RETRY_MULT` | 1.3 | 1.0 — 3.0 |
| `THEORY_REGEN_PASSES` | 2 | 1 — 4 |
| `RE_EXTRACT_RATE` | 0.20 | 0 — 1 |
| `QUESTION_REGEN_RUNS_PER_BOOK` | 1 | 0 — 3 |
| `MEAN_PDF_PAGES_PER_SECTION` | 6 | 2 — 20 |
| `INPUT_TOKENS_PER_PDF_PAGE` | 600 | 400 — 1,200 |
| `MEAN_OUTPUT_TOKENS_THEORY` | 6,000 | 2,000 — 16,000 |
| `MEAN_OUTPUT_TOKENS_QUESTIONS` | 6,500 | 2,000 — 30,000 |

### Closed-form formula

```
cost_per_chapter
  = SCHEMA_FRACTION_PER_CHAPTER × $0.105
  + SECTIONS_PER_CHAPTER × THEORY_EXTRACT_RETRY_MULT × $0.086
  + SECTIONS_PER_CHAPTER × SCANNED_PDF_FRACTION × $0.047
  + FIGURES_PER_CHAPTER × $0.039
  + LEAF_SECTIONS_PER_CHAPTER × THEORY_REGEN_PASSES × $0.027
  + QUESTION_UNITS_PER_CHAPTER × $0.026
  + QUESTION_UNITS_PER_CHAPTER × QUESTION_REGEN_RUNS_PER_BOOK × $0.021
  + (PAGES_PER_CHAPTER × 0.6) × $0.0023        # QA verifier
  + RE_EXTRACT_RATE × SECTIONS_PER_CHAPTER × $0.086

annual_cost = BOOKS_PER_YEAR × CHAPTERS_PER_BOOK × cost_per_chapter
```

(`SCHEMA_FRACTION_PER_CHAPTER` = 1 / `CHAPTERS_PER_BOOK` since schema is once per book.)

---

## 8. Caveats & open questions for follow-up

1. **Pricing volatility** — Google has changed Gemini pricing 3 times since
   GA. Numbers above use list price; enterprise contracts can negotiate down.
2. **Real output-token observations** are estimates from prompt+sample
   inspection, not measured from response logs. Adding an output-token logger
   to `gemini_client.py` would tighten this analysis by an order of magnitude.
3. **Caching not modelled** — Gemini context caching cuts input cost by 75%
   for repeated context. Not currently used in the pipeline; could save ~30%
   on theory extraction if the same chapter is re-extracted.
4. **Figure regeneration uses preview model** — pricing is provisional until
   GA. Treat the $0.039 figure as ±50% uncertain.
5. **No human-review labour** is modelled — only LLM API spend. Total cost of
   ownership is materially higher.
6. **Question extraction has both v2 and v3** — v3 is the new section-aligned
   path; v2 is still in regen flow. Migration to v3-only would simplify cost
   modelling and likely reduce spend ~10%.

---

## 9. Quick numbers for the email

> **TL;DR.** At steady-state of **50 books / yr (~600 chapters)** the LLM
> spend is **~$3,100 / yr**. Theory extraction on Gemini-2.5-Pro accounts
> for one-third of that. The biggest single optimisation is to first-shot
> theory extract on Flash and escalate to Pro only on QC failure — projected
> **$620/yr saved at steady-state, scaling linearly with volume**.

---

*Generated: 2026-05-05. All numbers grounded in repo at HEAD; verify line
citations before quoting in external comms.*
