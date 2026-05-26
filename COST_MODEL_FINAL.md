# CMDS Cost Model — DEFINITIVE EDITION

**Date:** 2026-05-26
**Codebase:** commit `2540f9f` on `claude/keen-curran`
**Provider:** 100% Google Gemini API (Anthropic Claude OFF via `ANTHROPIC_MODE=mock`)
**Scope:** Every paid API call CMDS makes for ONE chapter, across Theory, Questions, and Images pipelines, with retry overhead, real prompt sizes, and per-variable cost calculator.

This is the single source of truth. Earlier drafts (`COST_MODEL_2026.md`, `COST_MODEL_PER_CHAPTER.md`) are superseded.

**Realistic-defaults edition** — assumptions reflect actual CBSE/JEE/Advanced textbook chapters with full exercise sets, not minimal demos.

---

## 0. Executive summary

### Per chapter (all-in: API + retry buffer + infra amortised)

| Scenario | Description | Cost |
|---|---|---|
| **Best case** | Extract once. No regen at all. | **$1.20** |
| **Typical** ⭐ | Extract + 1 theory regen + 1 Q regen (all 100 source questions × 2 variants) + 30% of 15 figures regenerated | **$10.99** |
| **Heavy** | Extract + 2 theory regens + 3 Q regen passes + 80% of figures regenerated | **$29.54** |

### Annual forecast (500 books × 12 chapters = 6,000 chapters)

| Scenario | AI spend | Infra | **Yearly total** |
|---|---|---|---|
| Best | $6,576 | $600 | **$7,176** |
| Typical ⭐ | $65,344 | $600 | **$65,944** |
| Heavy | $177,047 | $600 | **$177,647** |

> **Recommended yearly budget: $66K / yr** for 500 books at typical usage.
> Reserve up to $178K / yr if heavy iteration is expected.

### Per book (12 chapters)

| Scenario | Cost |
|---|---|
| Best | **$14.36** |
| Typical | **$131.89** |
| Heavy | **$354.49** |

### Per chapter — at 50 books / year volume (alternative scale)

| Scenario | AI yr | + Infra | Year |
|---|---|---|---|
| Best | $658 | $60 | **$718** |
| Typical | $6,534 | $60 | **$6,594** |
| Heavy | $17,705 | $60 | **$17,765** |

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
```

Single provider: **Google Gemini API**.

---

## 2. EVERY paid call — exhaustive inventory

### 2.1 Active call sites

| ID | Phase | File:line | Model | Per chapter (Typical) |
|---|---|---|---|---|
| **T2** | Schema build | `services/schema_builder.py:94` | `gemini-2.5-pro` | 1 call |
| **T3** | Theory extract | `services/theory_extractor.py:167` | `gemini-2.5-pro` | 10 calls |
| **T4** | Theory regen | `services/regenerator.py:92` | `gemini-2.5-pro` | 10 × Rt = 10 |
| **T5** | QC verifier (OFF default) | `services/qa/verifier.py:65` | `gemini-2.5-flash` | 0 (off) |
| **Q1** | Question extract | `workers/questions_v3.py:730` | `gemini-2.5-flash` | 10 calls |
| **Q2** | Question regen (text) | `workers/question_regen_v3.py:365` | `gemini-2.5-flash` | (S × (1−P)) × V = 140 calls |
| **Q3** | Question regen (multimodal) | `workers/question_regen_v3.py:353` | `gemini-2.5-pro` | (S × P) × V = 60 calls |
| **I1** | Figure extract | `services/figures/extractor.py:134` | `gemini-3.1-pro-preview` | 1 call |
| **I2** | Figure regen | `services/figures/regenerator.py:141` | `gemini-3.1-flash-image-preview` | 4.5 calls (30% of 15) |
| **I-OL** | Label overlay | `services/figures/overlay.py:159` | `gemini-3.1-pro-preview` | 4.5 calls (1 per figure regen, 2 OCR each) |
| **I-WM** | Watermark cleanup | `services/figures/watermark.py:67` | OFF in prod | 0 |

### 2.2 Free / internal call sites ($0 cost)

- Analyser (mock mode — `ANTHROPIC_MODE=mock`)
- Figure embedder (pure Python regex)
- Theory regen QC drift (pure Python)
- Post-regen embedder (pure Python)
- QC LLM auditor (dead code, never imported)
- Legacy figure modules (`figure_extractor.py`, `figure_regenerator.py` — unwired)
- DOCX export (pandoc + python-docx, local)
- JSON / Markdown export (pure Python)
- Job recovery on startup
- Schema postpass / chunk builder
- Final-merge dedup logic

### 2.3 Cost-relevant knobs (env-tunable)

| Knob | Default | Effect on cost |
|---|---|---|
| `MULTIMODAL_REGEN_ENABLED` | `true` | OFF saves $4.60/ch — biggest single lever |
| `GEMINI_REGEN_MODEL` | `gemini-2.5-pro` | Change to Flash saves ~$0.40/ch |
| `FIGURE_EXTRACTION_MODEL` | `gemini-3.1-pro-preview` | n/a |
| `FIGURE_IMAGE_MODEL` | `gemini-3.1-flash-image-preview` | n/a |
| `THEORY_SECTION_CONCURRENCY` | `4` | NONE — only speed |
| `_MAX_IN_FLIGHT` (semaphore) | `4` | NONE — only speed |
| `RETRY_ATTEMPTS` (transient) | `2` (3 tries) | Up to 3× on flaky calls |
| `TRANSIENT_SUB_ATTEMPTS` (theory) | `4` | Up to 4× per attempt |
| `MAX_ATTEMPTS` (content quality) | `3` | Up to 3× per section |
| `watermark_clean` (per-call) | `false` | OFF in prod |
| `overlay` (per-call) | `true` | +$0.26/ch typical |
| `QUESTION_WORKER_VERSION` (env) | `v3` | v2 opt-in; same Flash model so cost ≈ same |

---

## 3. Gemini pricing reference (May 2026, verified)

| Model | Input $/1M | Output $/1M | Notes |
|---|---|---|---|
| **gemini-2.5-pro** | $1.25 | $10.00 | ≤200K context tier |
| **gemini-2.5-pro** (>200K) | $2.50 | $15.00 | Long context tier |
| **gemini-2.5-flash** | $0.30 | $2.50 | Fast OCR / cheap extract |
| **gemini-3.1-pro-preview** | **$2.00** | **$12.00** | Verified developer rate, ≤200K |
| **gemini-3.1-flash-image-preview** | $0.05 flat per image | n/a | Image-out model |

> Source: ai.google.dev developer pricing page, verified May 2026.

---

## 4. Realistic assumptions — drives every number

| Variable | Default | Rationale |
|---|---|---|
| Books per year | **500** | Production target |
| Chapters per book | **12** | Standard textbook |
| Sections per chapter (N) | **10** | CBSE/ICSE/JEE typical |
| Source questions (S) | **100** | Realistic large chapter (CBSE/JEE/Advanced: 50–150 typical) |
| Variants per regen (V) | **2** | UI configurable; 2 gives variety with lower cost vs 3 |
| % questions with images (P) | **30%** | Math/physics typical |
| Figures per chapter (F) | **15** | Math/physics: 10–25; bio: 15–30 |
| % figures regenerated (Fr%) | **30%** | "Fix the broken ones" workflow → Fr = 4.5 |
| Theory regen passes (Rt) | **1** | One full rewrite cycle |
| Question regen passes (Rq) | **1** | All S questions × V variants once |
| Retry buffer (R) | **30%** | Observed prod logs |
| Verifier enabled | **OFF** | Matches prod default |
| Overlay enabled | **ON** | Matches prod default |
| Infra $ per chapter | **$0.10** | Railway + Postgres + storage |

---

## 5. Per-call token economics

### 5.1 Prompt sizes (measured)

| Prompt file | Bytes | ~Tokens | Used by |
|---|---|---|---|
| `schema_gemini.txt` | 25,791 | 6,450 | T2 |
| `question_extractor_v3.txt` | 24,429 | 6,100 | Q1 |
| `question_regenerator_v3.txt` | 16,822 | 4,200 | Q2 |
| `regenerator.txt` | 10,792 | 2,700 | T4 |
| `question_regenerator_v3_image_addendum.txt` | 5,705 | 1,425 | Q3 |
| `extractor.txt` | 6,289 | 1,572 | T3 |
| `qa_verifier.txt` | 2,347 | 590 | T5 |
| `figures/figure_extraction_system.txt` | 11,634 | 2,910 | I1 |
| `figures/figure_regen_enhanced.txt` | 2,270 | 570 | I2 |
| `figures/figure_ocr_with_bbox.txt` | 761 | 190 | I-OL |

### 5.2 PDF tokens

Gemini tokenizes attached PDFs at ~258 tokens/page (OCR mode):
- 30-page chapter → ~7.5K tokens
- 3-page slice → ~0.8K tokens

### 5.3 Per-call totals

| Call | Input | Output |
|---|---|---|
| T2 schema | 14.5K | 4K |
| T3 theory / section | 7.1K | 5K |
| T4 regen / section | 8.7K | 6K |
| T5 verifier / section | 9.6K | 2K |
| Q1 extract / section | 11.6K | 4K |
| Q2 regen text / variant | 6.2K | 5K |
| Q3 multimodal / variant | 10.6K | 6K |
| I1 figure extract | 10.9K | 3K |
| I2 figure regen | flat $0.05 | 1 PNG |
| I-OL OCR / call | 2.2K | 2K |

---

## 6. Per-call cost (single call, no retry, $2.00 verified Pro Preview)

| Call | Input $ | Output $ | **Total** |
|---|---|---|---|
| T2 schema | $0.0181 | $0.0400 | **$0.058** |
| T3 theory extract | $0.0089 | $0.0500 | **$0.059** |
| T4 theory regen | $0.0109 | $0.0600 | **$0.071** |
| T5 verifier | $0.0029 | $0.0050 | **$0.008** |
| Q1 question extract | $0.0035 | $0.0100 | **$0.014** |
| Q2 regen text | $0.0019 | $0.0125 | **$0.014** |
| Q3 multimodal | $0.0132 | $0.0600 | **$0.073** |
| I1 figure extract (at $2/1M input) | $0.0218 | $0.0360 | **$0.058** |
| I2 figure regen | flat | flat | **$0.050** |
| I-OL OCR ×2 per regen (at $2/1M) | 2 × $0.0044 | 2 × $0.024 | **$0.057** (per regen) |

---

## 7. Per-chapter cost — fully composed (realistic defaults)

### 7.1 Substituted values

N=10, S=100, V=2, F=15, Fr%=30% → Fr=4.5, P=30%, Rt=1, Rq=1, R=30%, Infra=$0.10

### 7.2 Theory pipeline

| Component | Calc | Cost |
|---|---|---|
| Schema (T2) | 1 × $0.058 | $0.058 |
| Theory extract (T3) | 10 × $0.059 | $0.590 |
| Theory regen (T4) | 10 × $0.071 × Rt=1 | $0.710 |
| Verifier (T5, off) | 0 | $0.000 |
| **Subtotal** | | **$1.358** |

### 7.3 Questions pipeline

| Component | Calc | Cost |
|---|---|---|
| Q1 extract | 10 × $0.014 | $0.140 |
| Q2 text regen | 70 sources × 2 × $0.014 | $1.960 |
| Q3 multimodal regen | 30 sources × 2 × $0.073 | $4.380 |
| **Subtotal** | | **$6.480** |

> S=100 questions split 70/30 by image presence: 70 text + 30 multimodal. Each gets V=2 variants. Total Q regen calls = 100 × 2 = 200.

### 7.4 Images pipeline

| Component | Calc | Cost |
|---|---|---|
| I1 figure extract | 1 × $0.058 | $0.058 |
| I2 figure regen | 4.5 × $0.050 | $0.225 |
| I-OL overlay | 4.5 × $0.057 | $0.257 |
| **Subtotal** | | **$0.540** |

### 7.5 Combined per chapter — Typical

| Component | Cost |
|---|---|
| Theory | $1.358 |
| Questions | $6.480 |
| Images | $0.540 |
| **AI subtotal** | **$8.378** |
| + Retry buffer 30% | $2.513 |
| + Infra | $0.100 |
| **GRAND $/CHAPTER** | **$10.99** |

### 7.6 Best case (extract only, no regen)

| Component | Cost |
|---|---|
| Theory extract only | $0.648 |
| Question extract only | $0.140 |
| Image extract only | $0.058 |
| AI subtotal | $0.846 |
| + Retry 30% | $0.254 |
| + Infra | $0.100 |
| **GRAND $/CH (Best)** | **$1.20** |

### 7.7 Heavy case (2 theory regens, 3 Q regen passes, 80% figures regen)

| Component | Cost |
|---|---|
| Theory (Rt=2) | $2.078 |
| Questions (Rq=3) | $19.160 |
| Images (Fr=12 = 80% of F=15) | $1.342 |
| AI subtotal | $22.580 |
| + Retry 30% | $6.774 |
| + Infra | $0.200 |
| **GRAND $/CH (Heavy)** | **$29.54** |

### 7.8 Per book (12 chapters)

| Scenario | Per book |
|---|---|
| Best | **$14.36** |
| Typical ⭐ | **$131.89** |
| Heavy | **$354.49** |

### 7.9 Per year (500 books = 6,000 chapters)

| Scenario | AI cost | + Infra | **Year** |
|---|---|---|---|
| Best | $6,576 | $600 | **$7,176** |
| Typical ⭐ | $65,344 | $600 | **$65,944** |
| Heavy | $177,047 | $600 | **$177,647** |

---

## 8. Retry mechanics

Three retry layers, all can fire per call:

| Layer | File:line | Default | Effect |
|---|---|---|---|
| Transient (Gemini 5xx/429) | `gemini_runtime.py:44` | 2 retries (3 tries) | Pays per retry |
| Theory transient | `theory_extractor.py:48` | 4 tries | Pays per retry |
| Content quality (QC fail → re-call) | `theory_extractor.py:26`, `schema_builder.py:28` | 3 tries | Pays per failed attempt |

Worst case per section in theory: 3 × 4 = 12 calls. Average: ~1.3× (the 30% buffer).

---

## 9. Cost driver ranking — % of Typical $/chapter ($10.99)

| Rank | Driver | $/ch | % share |
|---|---|---|---|
| 1 | **Q3 multimodal regen** | $4.380 | **40%** |
| 2 | Retry buffer (30%) | $2.513 | 23% |
| 3 | Q2 text regen | $1.960 | 18% |
| 4 | T4 theory regen | $0.710 | 6% |
| 5 | T3 theory extract | $0.590 | 5% |
| 6 | I-OL label overlay | $0.257 | 2% |
| 7 | I2 figure regen | $0.225 | 2% |
| 8 | Q1 question extract | $0.140 | 1% |
| 9 | Infra | $0.100 | 1% |
| 10 | I1 figure extract | $0.058 | 1% |
| 11 | T2 schema | $0.058 | 1% |

**Q3 multimodal is 40% of the bill.** This is the single highest-leverage place to optimize.

---

## 10. Optimization levers — ranked

| # | Lever | Effort | Savings/ch | $/yr at 500 books |
|---|---|---|---|---|
| 1 | Disable `MULTIMODAL_REGEN_ENABLED` (loses image-needs-regen badge) | env | **$4.38** (–40%) | **–$26,280** |
| 2 | Drop V from 2 → 1 (one variant per source, less variety) | UI | $3.17 (–29%) | –$19,020 |
| 3 | Reduce Rq from 1 → 0.5 (skip regen on half the chapters) | workflow | $3.24 (–29%) | –$19,440 |
| 4 | Enable Gemini prompt caching (T2 + Q1 prompts) | 1 dev day | $0.30 (–3%) | –$1,800 |
| 5 | Disable overlay step | per-call | $0.26 (–2%) | –$1,500 |
| 6 | Move T3 to Flash | env | $0.42 (–4%) | –$2,500 |
| 7 | Reduce P (filter image-questions out of regen) | filter | varies | up to $4,300 |

Combined "lean prod" (#1 + #4): typical drops from $10.99 → $6.30/ch → $37.8K/yr (43% off).

---

## 11. Quality vs cost matrix

| Mode | T3 | Q regen | Figure | $/ch | Yearly @ 500 |
|---|---|---|---|---|---|
| **Cheapest viable** | Flash | Flash, no multimodal | Pro Preview extract only | $1.50 | $9K |
| **Balanced (Typical)** ⭐ | Pro | Flash + Pro multimodal | Pro Preview + Flash Image + overlay | **$10.99** | **$66K** |
| **Premium** | Pro | All Pro | Pro Preview + GA Imagen | $13.40 | $80K |

---

## 12. Edge cases & caveats

| Concern | Reality |
|---|---|
| Chapter > 200K context | Doesn't happen with 30-page chapters; 2× tier if 200-page books |
| 40+ figures / chapter | Bumps Image pipeline up; Fr% determines actual regen calls |
| Q3 dominates | Multimodal is 5× more expensive than text-only Q regen — sensitive to S × P × V |
| Preview pricing changes | 3.1 image models could re-price at GA ($0.04–$0.10/image range) |
| No prompt caching | Would save ~30% on T2 + Q1 if enabled |
| Storage egress | <$0.01/ch — negligible |
| Volume not attached on Railway | Redeploys wipe PDFs; operational only |
| v2 question worker (env-gated) | Same Flash model — flipping QUESTION_WORKER_VERSION=v2 doesn't change cost |

---

## 13. Cost calculator — plug your own numbers

```
S_text = S × (1 − P)            // questions without images
S_img  = S × P                  // questions with images
Fr     = F × FIGURE_REGEN_PCT   // figures regenerated

THEORY    = 0.058
          + N × 0.059
          + N × 0.071 × Rt
          + N × 0.008 × Rt × verifier_on

QUESTIONS = N × 0.014                  // Q1
          + S_text × V × 0.014 × Rq   // Q2
          + S_img  × V × 0.073 × Rq   // Q3

IMAGES    = 0.058                       // I1
          + Fr × 0.050                  // I2
          + Fr × 0.057 × overlay_on     // I-OL (×2 OCR per regen, $2/1M)

AI_SUB    = THEORY + QUESTIONS + IMAGES
GRAND     = AI_SUB × (1 + R) + INFRA
          = AI_SUB × 1.30 + 0.10
```

### Worked examples (S × V variants regenerated)

#### Example A — Class 6 short Math chapter
N=6, S=20, V=2, F=5, Fr%=30%, P=20%
- Theory: $0.058 + 6×0.059 + 6×0.071 = $0.838
- Questions: 6×0.014 + 16×2×0.014 + 4×2×0.073 = $0.084 + $0.448 + $0.584 = $1.116
- Images: $0.058 + 1.5×0.050 + 1.5×0.057 = $0.219
- AI: $2.173 · ×1.30 + $0.10 = **$2.92/ch**

#### Example B — Class 12 CBSE Physics
N=12, S=60, V=3, F=20, Fr%=40%, P=50%
- Theory: $0.058 + 12×0.059 + 12×0.071 = $1.618
- Questions: 12×0.014 + 30×3×0.014 + 30×3×0.073 = $0.168 + $1.260 + $6.570 = $7.998
- Images: $0.058 + 8×0.050 + 8×0.057 = $0.914
- AI: $10.530 · ×1.30 + $0.10 = **$13.79/ch**

#### Example C — Class 8 EVS light chapter
N=8, S=25, V=2, F=4, Fr%=25%, P=10%
- Theory: $0.058 + 8×0.059 + 8×0.071 = $1.098
- Questions: 8×0.014 + 22.5×2×0.014 + 2.5×2×0.073 = $0.112 + $0.630 + $0.365 = $1.107
- Images: $0.058 + 1×0.050 + 1×0.057 = $0.165
- AI: $2.370 · ×1.30 + $0.10 = **$3.18/ch**

---

## 14. Audit trail — file:line for every cost call

```
T2  services/schema_builder.py:94           gemini-2.5-pro
T3  services/theory_extractor.py:167        gemini-2.5-pro
T4  services/regenerator.py:92              gemini-2.5-pro
T5  services/qa/verifier.py:65              gemini-2.5-flash      (off by default)
Q1  workers/questions_v3.py:730             gemini-2.5-flash
Q2  workers/question_regen_v3.py:365        gemini-2.5-flash
Q3  workers/question_regen_v3.py:353        gemini-2.5-pro        (forced + image)
I1  services/figures/extractor.py:134       gemini-3.1-pro-preview
I2  services/figures/regenerator.py:141     gemini-3.1-flash-image-preview
OL  services/figures/overlay.py:159         gemini-3.1-pro-preview  (×2 per regen)
WM  services/figures/watermark.py:67        OFF in prod (watermark_clean=False)

Knobs:
  _MAX_IN_FLIGHT              gemini_runtime.py:38       4
  RETRY_ATTEMPTS              gemini_runtime.py:44       2 (3 tries)
  TRANSIENT_SUB_ATTEMPTS      theory_extractor.py:48     4
  MAX_ATTEMPTS (theory)       theory_extractor.py:26     3
  MAX_ATTEMPTS (schema)       schema_builder.py:28       3
  THEORY_SECTION_CONCURRENCY  env                        4
  QUESTION_WORKER_VERSION     core/config.py:131         v3 default
```

---

## 15. How to use this doc

1. **Customer quoting:** Typical $10.99/ch all-in (S=100, V=2 realistic).
2. **Annual budget:** $66K for 500 books at typical. Reserve $178K for heavy iteration.
3. **Vendor comparison:** at $10.99/ch you're paying for Pro-tier quality. Lower-priced SaaS uses smaller models — compare quality not price.
4. **Optimization order:** Disable multimodal regen first (#1, –40% off) — biggest lever. Then prompt caching (#4).
5. **Sensitivity analysis:** use §13 formula. Q3 multimodal dominates — every variant of every image-bearing question is +$0.073.
6. **Re-audit:** quarterly or when §14 call sites change.

---

## 16. Reconciliation note

Numbers come from the live workbook `/Users/aastha/Downloads/CMDS_COSTS_FINAL.xlsx`. All numbers in this doc derive from §6 per-call rates with assumptions in §4. §7.5/7.6/7.7 are the source of truth — if any other section differs, those win.

Earlier drafts superseded:
- `COST_MODEL_2026.md` (Anthropic baked in)
- `COST_MODEL_PER_CHAPTER.md` (intermediate)

---

*Generated 2026-05-26 from audit of `2540f9f`. Refresh when call sites change or pricing updates.*
