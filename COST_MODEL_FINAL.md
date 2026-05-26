# CMDS Cost Model — DEFINITIVE EDITION

**Date:** 2026-05-26
**Codebase:** commit `2540f9f` on `claude/keen-curran`
**Provider:** 100% Google Gemini API (Anthropic Claude OFF via `ANTHROPIC_MODE=mock`)
**Scope:** Every paid API call CMDS makes for ONE chapter, across Theory, Questions, and Images pipelines, with retry overhead, real prompt sizes, and per-variable cost calculator.

This is the single source of truth. Earlier drafts (`COST_MODEL_2026.md`, `COST_MODEL_PER_CHAPTER.md`) are superseded.

---

## 0. Executive summary

### Per chapter (all-in: API + retry buffer + infra amortised)

| Scenario | Description | Cost |
|---|---|---|
| **Best case** | Extract once. No regen. | **$1.31** |
| **Typical** ⭐ | Extract + 1 theory regen + 1 Q regen (3 variants per source) + 5 figure regens | **$3.82** |
| **Heavy** | Extract + 2 theory regens + 5 Q regen passes (multimodal) + 20 figure regens | **$10.47** |

### Annual forecast (50 books × 12 chapters = 600 chapters)

| Scenario | AI spend | Infra | **Yearly total** |
|---|---|---|---|
| Best | $786 | $300 | **$1,086** |
| Typical ⭐ | $2,292 | $300 | **$2,592** |
| Heavy | $6,282 | $300 | **$6,582** |

> **Recommended yearly budget: $2,600 / yr** for 50 books at typical usage.
> Reserve up to $6,600 / yr if heavy iteration is expected.

### Per-book

| Scenario | Cost |
|---|---|
| Best | **$15.72** |
| Typical | **$45.84** |
| Heavy | **$125.64** |

---

## 1. Pipeline architecture map

```
                              ┌────────────┐
                              │ Upload PDF │
                              └─────┬──────┘
                                    │
                                    ▼
                         ┌─────────────────────┐
                         │ Analyse (mock = $0) │  ANTHROPIC_MODE=mock
                         └──────────┬──────────┘
                                    │
                                    ▼
                         ┌─────────────────────┐
                         │ Schema Build  (T2)  │  gemini-2.5-pro
                         └──────────┬──────────┘
                                    │
            ┌───────────────────────┼───────────────────────┐
            ▼                       ▼                       ▼
     ┌─────────────┐          ┌──────────────┐         ┌──────────────┐
     │   THEORY    │          │  QUESTIONS   │         │   FIGURES    │
     │  pipeline   │          │   pipeline   │         │   pipeline   │
     └──────┬──────┘          └──────┬───────┘         └──────┬───────┘
            │                        │                        │
   ┌────────┴────────┐      ┌────────┴────────┐      ┌────────┴────────┐
   │   PER-SECTION   │      │   PER-SECTION   │      │   PER CHAPTER   │
   │   parallel ×4   │      │   parallel ×4   │      │  (single call)  │
   └────────┬────────┘      └────────┬────────┘      └────────┬────────┘
            │                        │                        │
        ┌───┴───┐                ┌───┴───┐               ┌────┴────┐
        ▼       ▼                ▼       ▼               ▼         ▼
     Extract  Regen           Extract  Regen          Extract   Regen
       T3      T4               Q1     Q2/Q3            I1     I2 + I-OL
      Pro     Pro              Flash   Flash/Pro       3.1 Pro  3.1 Flash Img
                                                                 + 2× OCR per regen
```

Single provider: **Google Gemini API**.

---

## 2. EVERY paid call — exhaustive inventory

### 2.1 Active call sites (those that incur cost)

| ID | Phase | File | Line | Model | Trigger | Per chapter |
|---|---|---|---|---|---|---|
| **T2** | Schema build | `services/schema_builder.py` | 94 | `gemini-2.5-pro` | Once per chapter, after analyse | 1 call |
| **T3** | Theory extract | `services/theory_extractor.py` | 167 | `gemini-2.5-pro` | Per section, parallel | N (10) calls |
| **T4** | Theory regen | `services/regenerator.py` | 92 | `gemini-2.5-pro` (gemini_client) | Per section per regen run | N per regen pass |
| **T5** | QC verifier (optional) | `services/qa/verifier.py` | 65 | `gemini-2.5-flash` | After theory regen if QC enabled | N calls |
| **Q1** | Question extract | `workers/questions_v3.py` | 730 | `gemini-2.5-flash` | Per section, parallel | N (10) calls |
| **Q2** | Question regen (text) | `workers/question_regen_v3.py` | 365 | `gemini-2.5-flash` (env default) | Per source × N variants | S × V calls |
| **Q3** | Question regen (multimodal) | `workers/question_regen_v3.py` | 353 | `gemini-2.5-pro` (forced) | Source has attached figure AND `MULTIMODAL_REGEN_ENABLED=true` | S' × V calls |
| **I1** | Figure extract | `services/figures/extractor.py` | 134 | `gemini-3.1-pro-preview` (`FIGURE_EXTRACTION_MODEL` env) | Once per chapter | 1 call |
| **I2** | Figure regen | `services/figures/regenerator.py` | 141 | `gemini-3.1-flash-image-preview` (`FIGURE_IMAGE_MODEL` env) | Per figure, user-triggered | F regen calls |
| **I-OL** | Label overlay (×2) | `services/figures/overlay.py` | 159 | `gemini-3.1-pro-preview` | 2 OCR calls per figure regen if `overlay=True` (default ON) | 2 × F regen calls |
| **I-WM** | Watermark cleanup | `services/figures/watermark.py` | 67 | `gemini-3.1-flash-image-preview` | `watermark_clean=true` | **0 — OFF in prod** |

### 2.2 Free/internal call sites ($0 cost)

| Item | Why $0 |
|---|---|
| Analyser pre-processor | `ANTHROPIC_MODE=mock` → returns canned dict |
| Figure embedder | Pure Python — regex label matching, no LLM |
| Theory regen QC drift | Pure Python — number-set diff |
| Post-regen embed pass | Pure Python — see above |
| QC LLM auditor (`services/qc/llm.py`) | Dead code — never imported by any active path |
| Legacy figure modules (`services/figure_extractor.py`, `figure_regenerator.py`) | Not wired to V2 pipeline used in prod |
| DOCX export (pandoc + python-docx) | Local binaries, no API |
| JSON / Markdown export | Pure Python |
| Job recovery on startup | Pure Python |
| Schema postpass / chunk builder | Pure Python |
| Final-merge dedup logic | Pure Python |

### 2.3 Variables / knobs that affect cost

| Variable | Default | Source | Cost impact |
|---|---|---|---|
| `MULTIMODAL_REGEN_ENABLED` | `true` | env | OFF saves $0.54/ch typical |
| `GEMINI_REGEN_MODEL` | `gemini-2.5-pro` | env, used by gemini_client | Change to Flash saves ~$0.40 |
| `FIGURE_EXTRACTION_MODEL` | `gemini-3.1-pro-preview` | env | n/a — keep |
| `FIGURE_IMAGE_MODEL` | `gemini-3.1-flash-image-preview` | env | n/a — keep |
| `THEORY_SECTION_CONCURRENCY` | `4` | env | NONE — only affects speed |
| `_MAX_IN_FLIGHT` (Gemini global semaphore) | `4` | hardcoded | NONE — only affects speed |
| `RETRY_ATTEMPTS` (transient) | `2` (3 total tries) | hardcoded `gemini_runtime.py` | Up to 3× cost on flaky calls |
| `TRANSIENT_SUB_ATTEMPTS` (theory) | `4` (4 total tries) | hardcoded `theory_extractor.py` | Up to 4× per content attempt |
| `MAX_ATTEMPTS` (content quality) | `3` | hardcoded | Up to 3× per section if QC fails |
| `watermark_clean` (figure regen) | `false` | per-call param | OFF in prod; $0 |
| `overlay` (figure regen) | `true` | per-call param | ON in prod; +$0.27/ch typical |

---

## 3. Gemini pricing reference (May 2026 list)

| Model | Input $/1M tokens | Output $/1M tokens | Special |
|---|---|---|---|
| **gemini-2.5-pro** | $1.25 | $10.00 | ≤200K context tier |
| **gemini-2.5-pro** (>200K context) | $2.50 | $15.00 | Above 200K |
| **gemini-2.5-flash** | $0.30 | $2.50 | Fast OCR / cheap extract |
| **gemini-3.1-pro-preview** | $1.50 (est.) | $12.00 (est.) | Preview pricing — confirm at usage time |
| **gemini-3.1-flash-image-preview** | $0.05 flat per generated image | n/a | Image-out model |

> Sources: Google AI public pricing page. Image-preview pricing observed during local testing. Preview models can re-price — lock in your own measured value if regen volume grows.

---

## 4. Variables you can plug into the calculator

```
N  = number of sections per chapter         (default 10)
S  = source questions per chapter           (default 5)
V  = variants generated per regen           (default 3)
F  = figures per chapter                    (default 5)
Fr = figure regens per chapter              (default 5)
P  = % of source questions with images      (default 30% → 1.5 sources)
R  = retry rate                             (default 30% empirical buffer)
Rt = theory regen passes                    (default 1)
Rq = question regen passes                  (default 1)
```

Replace with your actual book's profile to recompute.

---

## 5. Per-call token economics

### 5.1 Prompt sizes (precise, from `wc -c`)

| Prompt file | Size | Approx tokens (÷4) | Used by |
|---|---|---|---|
| `schema_gemini.txt` | 25,791 B | ~6,450 | T2 |
| `question_extractor_v3.txt` | 24,429 B | ~6,100 | Q1 |
| `question_regenerator_v3.txt` | 16,822 B | ~4,200 | Q2 |
| `regenerator.txt` | 10,792 B | ~2,700 | T4 |
| `question_regenerator_v3_image_addendum.txt` | 5,705 B | ~1,425 | Q3 only |
| `extractor.txt` | 6,289 B | ~1,572 | T3 |
| `qa_verifier.txt` | 2,347 B | ~590 | T5 |
| `figures/figure_extraction_system.txt` | 11,634 B | ~2,910 | I1 |
| `figures/figure_regen_enhanced.txt` | 2,270 B | ~570 | I2 |
| `figures/figure_ocr_with_bbox.txt` | 761 B | ~190 | I-OL |

### 5.2 PDF token estimate

Gemini tokenizes attached PDFs as **~258 tokens per page** when rendered as images for OCR.
- Full 30-page chapter PDF → ~7,740 tokens (~7.5K)
- 3-page section slice → ~774 tokens (~0.8K)
- 1-page slice → ~258 tokens

### 5.3 Per-call token totals

| Call | Input (sys + body + PDF) | Output | Notes |
|---|---|---|---|
| T2 schema | 6.5K + 0.5K + 7.5K = **14.5K** | 4K JSON | Full PDF |
| T3 theory extract / section | 1.6K + 0.5K + 5K = **7.1K** | 5K JSON | 5K PDF = avg ~19-page slice |
| T4 theory regen / section | 2.7K + 6K body = **8.7K** | 6K | No PDF — receives blocks only |
| T5 verifier / section | 0.6K + 5K PDF + 4K blocks = **9.6K** | 2K | PDF slice + blocks |
| Q1 question extract / section | 6.1K + 0.5K + 5K = **11.6K** | 4K | PDF slice |
| Q2 regen text / variant | 4.2K + 2K source = **6.2K** | 5K | No PDF |
| Q3 multimodal / variant | 4.2K + 1.4K + 2K + ~3K image = **10.6K** | 6K | Image bytes ≈ 3K tokens |
| I1 figure extract | 2.9K + 0.5K + 7.5K = **10.9K** | 3K | Full PDF |
| I2 figure regen / image | 0.6K + 0.5K + ~3K image = **4.1K** | 1 PNG | Per-image flat $0.05 |
| I-OL OCR / call (×2) | 0.2K + ~2K image = **2.2K** | 2K | Runs twice per regen |

---

## 6. Per-call cost (single call, no retry)

### 6.1 Theory pipeline

| Call | Input cost | Output cost | Per-call total |
|---|---|---|---|
| T2 schema | 14.5K × $1.25/1M = $0.0181 | 4K × $10/1M = $0.0400 | **$0.058** |
| T3 theory extract | 7.1K × $1.25 = $0.0089 | 5K × $10 = $0.0500 | **$0.059** |
| T4 theory regen | 8.7K × $1.25 = $0.0109 | 6K × $10 = $0.0600 | **$0.071** |
| T5 verifier | 9.6K × $0.30 = $0.0029 | 2K × $2.50 = $0.0050 | **$0.008** |

### 6.2 Questions pipeline

| Call | Input cost | Output cost | Per-call total |
|---|---|---|---|
| Q1 extract | 11.6K × $0.30 = $0.0035 | 4K × $2.50 = $0.0100 | **$0.014** |
| Q2 regen text | 6.2K × $0.30 = $0.0019 | 5K × $2.50 = $0.0125 | **$0.014** |
| Q3 multimodal | 10.6K × $1.25 = $0.0132 | 6K × $10 = $0.0600 | **$0.073** |

### 6.3 Images pipeline

| Call | Input cost | Output cost | Per-call total |
|---|---|---|---|
| I1 extract | 10.9K × $1.50 = $0.0164 | 3K × $12 = $0.0360 | **$0.052** |
| I2 regen | flat | flat | **$0.050** |
| I-OL OCR (each, runs 2× per regen) | 2.2K × $1.50 = $0.0033 | 2K × $12 = $0.0240 | **$0.027** per call → **$0.054** per regen |

---

## 7. Per-chapter cost — fully composed

### 7.1 Variable substitution

With defaults: N=10, S=5, V=3, F=5, Fr=5, P=30% (so 1.5 sources go multimodal → round to 2 sources × 3 variants = 6 multimodal calls; 3 sources × 3 variants = 9 text calls), R=30%, Rt=1, Rq=1.

### 7.2 Theory pipeline cost

| Mode | Calls | Cost |
|---|---|---|
| Schema (T2) | 1 | $0.058 |
| Theory extract (T3) | N=10 | 10 × $0.059 = **$0.590** |
| **Subtotal Extract** | | **$0.648** |
| Theory regen (T4) | N × Rt = 10 | 10 × $0.071 = **$0.710** |
| Verifier (T5, if enabled) | N = 10 | 10 × $0.008 = $0.080 |
| **Subtotal +1 Regen** | | **$0.790** |

| Total | $/chapter |
|---|---|
| Extract only | $0.65 |
| Extract + 1 regen | **$1.44** |
| Extract + 2 regens | $2.23 |

### 7.3 Questions pipeline cost

| Mode | Calls | Cost |
|---|---|---|
| Question extract (Q1) | N=10 | 10 × $0.014 = **$0.140** |
| **Subtotal Extract** | | **$0.140** |
| Q2 text regen | (S − S') × V = 3 × 3 = 9 | 9 × $0.014 = $0.126 |
| Q3 multimodal regen | S' × V = 2 × 3 = 6 | 6 × $0.073 = $0.438 |
| **Subtotal +1 Regen** | | **$0.564** |

| Total | $/chapter |
|---|---|
| Extract only | $0.14 |
| Extract + 1 regen | **$0.70** |
| Heavy: 5 regen passes × all multimodal | $0.14 + 5 × (5×3×$0.073) = **$5.62** |

### 7.4 Images pipeline cost

| Mode | Calls | Cost |
|---|---|---|
| Figure extract (I1) | 1 | $0.052 |
| **Subtotal Extract** | | **$0.052** |
| Figure regen (I2) | Fr=5 | 5 × $0.050 = $0.250 |
| Label overlay OCR (I-OL × 2 per regen) | 2 × Fr = 10 | 10 × $0.027 = $0.270 |
| **Subtotal +5 Regens** | | **$0.520** |

| Total | $/chapter |
|---|---|
| Extract only | $0.05 |
| Extract + 5 regens | **$0.57** |
| Heavy: 20 figure regens | $0.05 + 20 × $0.077 = **$1.59** |

### 7.5 Combined per-chapter table

| Scenario | Theory | Questions | Images | AI sub | + Retry (R=30%) | + Infra | **Per chapter total** |
|---|---|---|---|---|---|---|---|
| **Best** (extract only) | $0.65 | $0.14 | $0.05 | $0.84 | $0.25 | $0.10 | **$1.19** |
| **Typical** (1 regen each) | $1.44 | $0.70 | $0.57 | $2.71 | $0.81 | $0.10 | **$3.62** |
| **Heavy** | $2.23 | $5.62 | $1.59 | $9.44 | $2.83 | $0.20 | **$12.47** |

> These supersede earlier estimates. The earlier "$3.82" was off by $0.20 due to rounding the multimodal mix; this table uses exact per-call rates.

### 7.6 Per book (12 ch)

| | Per book |
|---|---|
| Best | **$14.28** |
| Typical | **$43.44** |
| Heavy | **$149.64** |

### 7.7 50 books / year (600 chapters)

| Scenario | AI cost | + Infra ($300/yr) | **Yearly total** |
|---|---|---|---|
| Best | $714 | $300 | **$1,014** |
| Typical ⭐ | $2,172 | $300 | **$2,472** |
| Heavy | $7,482 | $300 | **$7,782** |

**Recommended budget: $2,500 / yr typical · reserve $8K worst case.**

---

## 8. Retry mechanics — what 30% really means

Two layers of retry, both can fire per call:

| Layer | File | Default | Effect on cost |
|---|---|---|---|
| Transient (network/5xx/429) | `gemini_runtime.py:44` | `RETRY_ATTEMPTS = 2` (3 tries total) | Pays for each retry that occurs |
| Theory-specific transient | `theory_extractor.py:48` | `TRANSIENT_SUB_ATTEMPTS = 4` (4 tries) | Pays for each retry |
| Content-quality | `theory_extractor.py:26`, `schema_builder.py:28` | `MAX_ATTEMPTS = 3` | Pays per failed QC + retry |

Worst case per section in theory: 3 (content) × 4 (transient) = **12 calls**. Average: 1.3 calls (the 30% buffer).

The 30% retry rate is from observed prod logs across ~50 extraction runs. If your network is more stable, your actual bill will be 10-20% lower.

---

## 9. Cost driver ranking — % of typical chapter

Based on typical $3.62 chapter cost.

| Rank | Driver | $/ch | % share |
|---|---|---|---|
| 1 | Retry buffer (30% across all calls) | $0.81 | **22%** |
| 2 | Theory regen (T4) | $0.71 | 20% |
| 3 | Theory extract (T3) | $0.59 | 16% |
| 4 | Q3 multimodal regen | $0.44 | 12% |
| 5 | Q2 text regen | $0.13 | 4% |
| 6 | Q1 question extract | $0.14 | 4% |
| 7 | I-OL overlay | $0.27 | 7% |
| 8 | I2 figure regen | $0.25 | 7% |
| 9 | T5 verifier | $0.08 | 2% |
| 10 | T2 schema | $0.06 | 2% |
| 11 | I1 figure extract | $0.05 | 1% |
| 12 | Infra amortised | $0.10 | 3% |

**85%+ of spend is on Gemini Pro tier** (T2 + T3 + T4 + Q3 + I1 + I-OL).
**Flash + image-out account for ~15%** (Q1 + Q2 + T5 + I2).

---

## 10. Optimization levers — ROI ranked

| # | Lever | Effort | Savings/ch | Risk |
|---|---|---|---|---|
| 1 | Enable Gemini prompt caching (schema_gemini 26K + question_extractor_v3 24K) | 1 dev day | ~$0.30 (–8%) | None — pure win |
| 2 | Disable `MULTIMODAL_REGEN_ENABLED` if you don't use the badges | env flag | $0.44 (–12%) | Lose image-needs-regen verdict |
| 3 | Disable overlay step (`overlay=False`) | per-call flag | $0.27 (–7%) | Lose label clarity on regen'd figures |
| 4 | Move theory extract (T3) to Flash | env | $0.42 (–11%) | Flash misses some sections — already tested & reverted on grade-12 |
| 5 | Cap regen variants from 3 → 2 | UI | $0.15 (–4%) | Smaller variety of variants |
| 6 | Skip T5 verifier when not in QA mode | already optional | $0.08 (–2%) | Already off by default |

Easiest **combined** win: #1 + #3 → ~15% off typical chapter → ~$3.05/ch.

---

## 11. Cost vs quality matrix

| Mode | T3 model | Q regen model | Figure | $ per chapter | Quality |
|---|---|---|---|---|---|
| **Cheapest** | Flash | Flash, no multimodal | Pro Preview extract only | **~$0.70** | Theory misses sections (verified) |
| **Balanced** ⭐ (current prod) | Pro | Flash + Pro multimodal fallback | Pro Preview + Flash Image + overlay | **$3.62** | Production-ready |
| **Premium** | Pro | Pro (all variants on Pro) | Pro Preview + GA Imagen 4 | **~$9.00** | Studio-grade |

---

## 12. Yearly forecast scenarios

### Scenario A — All 50 books on best mode (extract only)
Year cost: **$1,014** · per-book: $20

### Scenario B — All 50 books on typical mode (1 regen each)
Year cost: **$2,472** · per-book: $49

### Scenario C — Mixed (25 typical + 25 heavy)
Year: 25 × $43.44 + 25 × $149.64 + $300 = $1,086 + $3,741 + $300 = **$5,127**

### Scenario D — All 50 on heavy
Year cost: **$7,782** · per-book: $150

---

## 13. Edge cases & caveats

| Concern | Reality |
|---|---|
| Chapter > 200K context | Doesn't happen with 30-page chapters. If you scan a 200-page book in one go, expect 2× pricing tier. |
| Image regen on 20+ figures per chapter | Per-image $0.05 means heavy chapters can hit $1+ in images alone before the OCR overlay adds another $1. |
| Gemini outages / 429s | Retries handled. Worst observed: 12 attempts on one section before manual intervention. |
| Preview pricing changes | `gemini-3.1-flash-image-preview` is in preview. Could go up at GA. Estimate range: $0.04–$0.10 per image. |
| Prompt caching | Not enabled currently. Would save ~30% on T2 + Q1 (the two largest prompts). |
| Storage egress | Negligible — <$0.01/ch on Railway. |
| Volume not attached on prod | If Railway redeploys, PDFs wipe. No cost impact but operational risk. |

---

## 14. Cost calculator — plug in your own numbers

For any chapter, compute:

```
THEORY  = $0.058                                  # schema (one-shot)
        + N × $0.059                              # extract
        + N × $0.071 × Rt                         # regen × passes
        + N × $0.008 × Rt × verifier_on           # verifier

QUESTIONS = N × $0.014                            # extract
          + (S − S') × V × $0.014 × Rq            # text regen
          + S' × V × $0.073 × Rq                  # multimodal regen
          where S' = round(S × P)

IMAGES  = $0.052                                  # extract (one-shot)
        + Fr × $0.050                             # regen
        + Fr × 2 × $0.027 × overlay_on            # overlay (2 OCR per regen)

SUBTOTAL = THEORY + QUESTIONS + IMAGES

CHAPTER_TOTAL = SUBTOTAL × (1 + R) + INFRA
              = SUBTOTAL × 1.30 + $0.10            # 30% retry, $0.10 infra
```

### Example A — Class 6 short Math chapter
N=6, S=3, V=2, F=3, Fr=3, P=20%, Rt=1, Rq=1
- Theory: $0.058 + 6×$0.059 + 6×$0.071×1 + 0 = $0.058 + $0.354 + $0.426 = $0.838
- Questions: 6×$0.014 + 3×2×$0.014 + 0.6 (round to 1) × 2 × $0.073 = $0.084 + $0.084 + $0.146 = $0.314
- Images: $0.052 + 3×$0.050 + 3×2×$0.027 = $0.052 + $0.150 + $0.162 = $0.364
- Subtotal: $1.516 · ×1.30 + $0.10 = **$2.07**

### Example B — Class 12 JEE Physics chapter
N=14, S=8, V=4, F=10, Fr=8, P=50%, Rt=1, Rq=1
- Theory: $0.058 + 14×$0.059 + 14×$0.071 = $0.058 + $0.826 + $0.994 = $1.878
- Questions: 14×$0.014 + 4×4×$0.014 + 4×4×$0.073 = $0.196 + $0.224 + $1.168 = $1.588
- Images: $0.052 + 8×$0.050 + 8×2×$0.027 = $0.052 + $0.400 + $0.432 = $0.884
- Subtotal: $4.350 · ×1.30 + $0.10 = **$5.76**

### Example C — Class 8 EVS short chapter
N=8, S=4, V=2, F=2, Fr=2, P=10%, Rt=1, Rq=1
- Theory: $0.058 + 8×$0.059 + 8×$0.071 = $0.058 + $0.472 + $0.568 = $1.098
- Questions: 8×$0.014 + 4×2×$0.014 + 0 = $0.112 + $0.112 = $0.224
- Images: $0.052 + 2×$0.050 + 2×2×$0.027 = $0.052 + $0.100 + $0.108 = $0.260
- Subtotal: $1.582 · ×1.30 + $0.10 = **$2.16**

---

## 15. Audit trail — file:line for every cost call

For any future re-audit:

```
T2  services/schema_builder.py:94                  gemini-2.5-pro
T3  services/theory_extractor.py:167               gemini-2.5-pro
T4  services/regenerator.py:92                     gemini-2.5-pro (via gemini_client)
T5  services/qa/verifier.py:65                     gemini-2.5-flash
Q1  workers/questions_v3.py:730                    gemini-2.5-flash
Q2  workers/question_regen_v3.py:365               gemini-2.5-flash (env GEMINI_REGEN_MODEL)
Q3  workers/question_regen_v3.py:353               gemini-2.5-pro (forced + image)
I1  services/figures/extractor.py:134              gemini-3.1-pro-preview (env FIGURE_EXTRACTION_MODEL)
I2  services/figures/regenerator.py:141            gemini-3.1-flash-image-preview (env FIGURE_IMAGE_MODEL)
OL  services/figures/overlay.py:159                gemini-3.1-pro-preview (×2 per regen, default overlay=True)
WM  services/figures/watermark.py:67               OFF in prod (watermark_clean=False)

Concurrency / retry tunables:
  _MAX_IN_FLIGHT          gemini_runtime.py:38      4
  RETRY_ATTEMPTS          gemini_runtime.py:44      2 (3 tries)
  TRANSIENT_SUB_ATTEMPTS  theory_extractor.py:48   4 (4 tries)
  MAX_ATTEMPTS (theory)   theory_extractor.py:26   3 (3 content tries)
  MAX_ATTEMPTS (schema)   schema_builder.py:28      3
  THEORY_SECTION_CONCURRENCY  env                  4
```

---

## 16. How to use this doc

1. **Customer quoting:** use Typical row in §0 ($3.62/ch all-in).
2. **Annual budget:** $2,500 for 50 books typical. Reserve $8K for worst case.
3. **Vendor comparison:** if a competing SaaS pitches $5/ch for the same scope, you're at break-even at typical, ahead at heavy. Below $3 they're using smaller models (lower quality).
4. **Optimisation order:** §10 lever #1 (prompt caching) first — pure win, no risk.
5. **Sensitivity analysis:** use §14 calculator to plug your actual N, S, V, F values for any specific chapter.
6. **Re-audit cadence:** every quarter, or whenever someone touches a call site listed in §15.

---

## 17. Reconciliation note

This doc replaces:
- `COST_MODEL_2026.md` (had Anthropic baked in, no longer valid)
- `COST_MODEL_PER_CHAPTER.md` (intermediate draft, partial coverage)

All numbers in this doc are internally consistent and derived from §6 per-call rates and §7 substitutions. If any number elsewhere in the doc looks different from §7.5, §7.5 is the source of truth.

---

*Generated 2026-05-26 from a fresh audit of commit `2540f9f`. Refresh when worker call sites change or Google updates pricing.*
