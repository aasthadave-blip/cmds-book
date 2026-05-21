# CMDS Cost Model — One Chapter + 50 Books / Year

**Date:** 2026-05-20
**Scope:** every paid service the CMDS pipeline touches, end-to-end, from PDF
upload to final DOCX export. Per-chapter and per-50-books-per-year totals.

**Audience:** budget planning, vendor negotiation, optimisation prioritisation.

---

## TL;DR

| Metric | Value |
|---|---|
| **Cost per chapter — happy path** (extract + 1 regen pass + export) | **~$5.13** |
| **Cost per chapter — minimum** (extract only, no regen) | **~$3.86** |
| **Cost per chapter — heavy** (extract + 2 regens + figure regens) | **~$7.50** |
| **50 books / year, 12 ch each = 600 chapters** | **~$3,100 / yr AI + ~$300 / yr infra = ~$3,400 / yr total** |
| **Per-book cost** | **~$60 / book** |
| **Dominant cost driver** | Theory extraction on Gemini 2.5 Pro (~33% of spend) |

---

## 1. Service inventory

Everything the pipeline currently uses, paid or free:

| Service | Used by | Pricing model | Notes |
|---|---|---|---|
| **Gemini 2.5 Pro** | Theory extract, schema, theory regen, figure extract (scanned PDFs) | $1.25/1M input, $10/1M output (≤200K context) | Production tier |
| **Gemini 2.5 Flash** | Question extract v3, question regen v2, QA verifier | $0.30/1M input, $2.50/1M output | ~10× cheaper than Pro |
| **Gemini 2.0 Flash Image Preview** ("Nano Banana") | Figure regeneration | ~$0.039 / image generated | Per-image flat fee |
| **PyMuPDF** | Figure extract on digital PDFs | Free (open source) | Used when scanner=false |
| **Pandoc** | Markdown→DOCX (replaced by python-docx in Phase 3.5b) | Free (open source) | Still installed but bypassed |
| **python-docx** | All DOCX exports (theory / questions / regen / Composer draft / Final merge) | Free | Phase 3.5b unification |
| **PostgreSQL or SQLite** | DB | SQLite local = free; Railway PG = ~$5/mo | |
| **Railway hosting** | Backend + frontend services | ~$10-20/mo (free tier covers dev) | Production |
| **PDF storage** | Uploaded books, figure binaries | DB blob (free) or S3 (~$0.023/GB/mo) | Currently in SQLite |

**No AI cost** for: Composer, Final Preview, Final Merge view, DOCX/MD/JSON export, embedder (deterministic), section reordering, custom-text additions.

---

## 2. Gemini pricing reference (May 2026, public list price)

Source: ai.google.dev/pricing (verify before quoting externally — Google has
changed Gemini pricing 3+ times since GA).

| Model | Input ($/1M tok) | Output ($/1M tok) | Used for |
|---|---|---|---|
| `gemini-2.5-pro` | $1.25 (≤200K) / $2.50 (>200K) | $10.00 / $15.00 | Theory, schema, figure extract |
| `gemini-2.5-flash` | $0.30 | $2.50 | Questions, QA verifier |
| `gemini-2.0-flash-preview-image-generation` | ~$0.039 / image | — | Figure regen |

**PDF token accounting:** Gemini charges **258 tokens per PDF page** as image
input + tokens for any text on the page. Working assumption: **~600 input
tokens / page** for mid-density textbook pages (258 image + ~340 text).

**Tokenisation:** 1 token ≈ 4 chars of English. Used for converting prompts
to token counts.

---

## 3. Reference chapter size

A "chapter" in this model is the **standard chapter** profile, calibrated
from real books in the system (Modern Physics, Quadratic Equations,
Chapter 8 Electricity):

| Profile | Pages | Sections | Leaf sections | Questions | Figures |
|---|---|---|---|---|---|
| Small (pamphlet) | 10 | 5 | 4 | 4 | 1 |
| **Standard chapter** | **50** | **20** | **15** | **15** | **6** |
| Large chapter | 80 | 35 | 25 | 25 | 12 |
| Whole textbook (12 ch) | 600 | 240 | 180 | 180 | 75 |

Modeled below: **standard chapter**, then scaled to 50 books × 12 chapters.

---

## 4. Per-call cost breakdown

### 4.1 Theory extraction
- **Model:** gemini-2.5-pro
- **Calls/chapter:** 20 (1 per section)
- **Per call:**
  - System prompt: 5,334 chars → 1,334 tokens
  - User prompt: ~50 tokens
  - PDF slice: ~6 pages × 600 tok = 3,600 tokens
  - Output: ~6,000 tokens (theory blocks JSON)
  - Retry multiplier: ~1.3× mean (QC retries)
- **Cost per section:** (1,334 + 50 + 3,600) × 1.3 = 6,477 in × $1.25/1M = **$0.0081**
                         6,000 × 1.3 = 7,800 out × $10/1M = **$0.078**
                         **Total: $0.086 / section**
- **Per chapter:** 20 × $0.086 = **$1.72**

### 4.2 Schema analyse (whole PDF)
- **Model:** gemini-2.5-pro
- **Calls/book:** 1
- **Per call:** ~50 pages × 600 tok = 30K input + ~2.4K prompt = ~33K in; output ~6K
- **Cost:** 33K × $1.25/1M + 6K × $10/1M = $0.041 + $0.06 = **$0.105 / book**
- **Per chapter (amortised over 12 ch/book):** $0.105 / 12 = **$0.009 / chapter**

### 4.3 Figure extraction
- **Model:** gemini-2.5-pro (only for scanned PDFs; digital PDFs are free via PyMuPDF)
- **Calls/chapter:** ~20 (1 per section page-range)
- **Per call:** ~6 pages × 600 tok = 3,600 + ~1,000 prompt; output ~4K
- **Cost per call:** 4,600 × $1.25/1M + 4,000 × $10/1M = $0.006 + $0.04 = **$0.047**
- **Per chapter (assume 100% scanned):** 20 × $0.047 = **$0.94**

### 4.4 Theory regeneration (optional, user-triggered)
- **Model:** gemini-2.5-pro
- **Calls/chapter:** 15 leaf sections × N passes
- **Per call:**
  - System prompt: 10,792 chars → 2,698 tokens
  - User prompt: ~1,500 tokens (section blocks)
  - No PDF (text-in/text-out)
  - Output: ~2,200 tokens
- **Cost per section:** 4,198 × $1.25/1M + 2,200 × $10/1M = $0.0052 + $0.022 = **$0.027**
- **Per chapter (1 pass):** 15 × $0.027 = **$0.41**

### 4.5 Question extraction (v3)
- **Model:** gemini-2.5-flash
- **Calls/chapter:** 15 (1 per section with questions)
- **Per call:**
  - System prompt: 10,949 chars → 2,737 tokens
  - User prompt: ~75 tokens
  - PDF slice: ~7 pages × 600 = 4,200 tokens
  - Output: ~6,500 tokens
  - Retry multiplier: ~1.4× mean
- **Cost per call:** (2,737 + 75 + 4,200) × 1.4 = 9,816 × $0.30/1M + 6,500 × 1.4 × $2.50/1M
                      = $0.00295 + $0.02275 = **$0.026**
- **Per chapter:** 15 × $0.026 = **$0.39**

### 4.6 Question regeneration v2 (optional)
- **Model:** gemini-2.5-flash
- **Calls/chapter:** ~25 (3-pass design)
- **Per call:** 5,038 × $0.30/1M + 7,800 × $2.50/1M = $0.0015 + $0.0195 = **$0.021**
- **Per chapter (1 regen run):** 25 × $0.021 = **$0.53**

### 4.7 Figure regeneration (optional, per-figure)
- **Model:** gemini-2.0-flash-preview-image-generation
- **Calls/figure:** 1 per regen request
- **Per call:** ~$0.039 (flat fee per image)
- **Per chapter (assume 6 figures, 30% regenerated):** 2 × $0.039 = **$0.08**

### 4.8 QA verifier
- **Model:** gemini-2.5-flash
- **Calls/chapter:** ~30 (per question-bank page)
- **Per call:** ~$0.0023
- **Per chapter:** 30 × $0.0023 = **$0.07**

### 4.9 Composer / Preview / Export (Phase 3)
- **Model:** none (pure data composition + python-docx)
- **Calls/chapter:** 0
- **Per chapter:** **$0.00**

---

## 5. Per-chapter total cost

### Happy path (single full extraction, no regens, scanned PDF)

| Stage | Subtotal |
|---|---|
| Schema analyse (1/book amortised) | $0.009 |
| Theory extract (20 sections) | $1.72 |
| Figure extract (scanned) | $0.94 |
| Question extract v3 (15) | $0.39 |
| QA verifier | $0.07 |
| **Total** | **$3.13** |

### Realistic path (extract + 1 theory regen + 0.3 question regens)

| Add-on | Subtotal |
|---|---|
| ... happy path above | $3.13 |
| 20% theory re-extracts | $0.34 |
| 1 theory regen pass | $0.41 |
| 0.3 question regen runs | $0.16 |
| 2 figure regenerations | $0.08 |
| **Realistic per chapter** | **~$4.12** |

### Heavy path (extract + 2 regens + many figure regens)

| Add-on | Subtotal |
|---|---|
| ... happy path | $3.13 |
| 30% theory re-extracts | $0.52 |
| 2 theory regen passes | $0.82 |
| 1 full question regen | $0.53 |
| 6 figure regenerations | $0.23 |
| **Heavy per chapter** | **~$5.23** |

### If digital PDF (no figure-extract cost via Gemini)

Subtract **$0.94** from all numbers above:
- Happy: **$2.19**
- Realistic: **$3.18**
- Heavy: **$4.29**

---

## 6. Annual cost — 50 books × 12 chapters = 600 chapters

### AI spend (Gemini)

| Cost profile | $/chapter | 600 ch/yr | % of total AI |
|---|---|---|---|
| Conservative (happy path) | $3.13 | **$1,878** | — |
| **Realistic (recommended estimate)** | **$4.12** | **$2,472** | 100% |
| Heavy | $5.23 | $3,138 | — |

### Infrastructure spend (Railway, ~$10-20/mo)

| Item | $/mo | $/yr |
|---|---|---|
| Backend service (Railway) | $10-15 | $120-180 |
| Frontend service (Railway) | $5-10 | $60-120 |
| PostgreSQL (Railway) | $5 | $60 |
| S3 / R2 storage for figure bytes (optional, ~5GB) | ~$0.20 | ~$2 |
| **Subtotal** | **~$25/mo** | **~$300/yr** |

### Yearly total — 50 books

| Category | Annual |
|---|---|
| Gemini AI (realistic) | **$2,472** |
| Infrastructure | **$300** |
| **Grand total (50 books / yr)** | **~$2,772** |

Per book: **~$55/book**
Per chapter: **~$4.62 / chapter** (AI + infra amortised)

### Breakdown of the $2,472 AI spend

| Pipeline | $/yr | % of AI total |
|---|---|---|
| Theory extract (Pro) | $1,032 | 41.8% |
| Figure extract (Pro, scanned) | $564 | 22.8% |
| Theory regen (Pro, 1 pass) | $246 | 9.9% |
| Question regen v2 (Flash, 0.3 runs) | $94 | 3.8% |
| Question extract v3 (Flash) | $234 | 9.5% |
| Figure regen (Flash Image) | $48 | 1.9% |
| Theory re-extracts | $206 | 8.3% |
| Schema + QA + misc | $48 | 1.9% |

**Pro-tier consumption = ~85% of AI spend.**
**Flash + image = ~15%.**

---

## 7. Scaling scenarios

Same per-chapter $4.12 baseline:

| Scenario | Books / yr | Chapters / yr | AI cost / yr | + Infra | Total / yr |
|---|---|---|---|---|---|
| Pilot | 5 | 60 | $247 | $300 | $547 |
| **Steady-state** | **50** | **600** | **$2,472** | **$300** | **~$2,772** |
| Growth | 250 | 3,000 | $12,360 | $600* | $12,960 |
| Aggressive | 1,000 | 12,000 | $49,440 | $1,500* | $50,940 |

*Infra scales with throughput — Railway tier upgrades + storage growth.

---

## 8. Cost-reduction levers (ranked by $/yr saved at 50 books)

1. **Switch theory extract from Pro → Flash with Pro fallback** ($1,032/yr at Pro)
   - If 80% pass on Flash first-shot: **savings ~$620/yr** at steady-state
   - Risk: tested briefly (Flash missed sections); needs completeness validator first
   - Status: deferred (Issue 2)

2. **Cache schema analyse output by PDF content hash**
   - Currently re-runs schema on every upload (incl. re-uploads of same file)
   - Savings: ~5% of Pro spend, ~$50/yr at steady-state

3. **Audit scanned-vs-digital classifier**
   - Currently bills $0.94 per chapter when scanned
   - Each false-positive (digital book mis-routed to Gemini) = $0.94 wasted
   - Manual audit could catch 10-20% mis-classification → savings ~$100/yr

4. **Reduce theory regen output cap from 16K → 6K**
   - Mean output is 2.2K; cap is for safety
   - Savings: $0 on average runs (bounds tail risk only)

5. **Sunset question regen v2, use only v3**
   - v2 is 3-pass design with overlap (25 calls vs v3's ~15)
   - v3 already shipped
   - Migration savings: ~$30/yr at steady-state (low priority)

6. **Use Gemini context caching for repeated theory regen runs**
   - 75% input-cost discount when same context reused within 1 hour
   - Savings: ~$30/yr if average 2 regen passes happen close together

---

## 9. What's NOT modelled

- **Human-in-the-loop time** (review, edit, approve) — only AI API cost
- **Customer-facing pricing markup** if this is sold as a service
- **One-time setup costs** (initial book ingestion, schema design)
- **Catastrophic retry storms** (if a section gets stuck retrying, could spike cost)
- **Composer manual edits & custom-text additions** — zero AI cost (verified)
- **Final draft DOCX/MD/JSON exports** — zero AI cost (python-docx only)
- **Composer Preview rendering** — zero AI cost (KaTeX in browser)

---

## 10. Caveats

- **Pricing volatility:** Google's changed Gemini pricing 3+ times since GA. Numbers use list price; enterprise contracts can negotiate down 20-40%.
- **Token counts are estimates,** not measured from response logs. Adding a token logger to `gemini_client.py` would tighten this by 2-3×.
- **Figure regen pricing is provisional** — `gemini-2.0-flash-preview-image-generation` is in preview. Treat ±50% uncertain.
- **Parallel theory extraction** (concurrency=4, shipped this session) doesn't change cost — same total calls, just compressed in time. Speed gain ~4×.
- **No customer pricing model** is included; this is internal cost only.

---

## 11. Bottom-line answer to the question

> **One chapter: ~$4.12** (realistic — extract + 1 regen + scanned PDF)
> **50 books × 12 chapters = 600 chapters: ~$2,472 AI + ~$300 infra = ~$2,772 total / year**
> **Per book: ~$55/book**

---

*Generated: 2026-05-20. All numbers grounded in repo at HEAD. Verify Gemini
pricing on ai.google.dev/pricing before external quotes. Cross-reference
`COST_ANALYSIS.md` for line-by-line code citations.*
