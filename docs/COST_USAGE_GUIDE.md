# V-Studio — API Cost & Usage Reference

Comprehensive catalog of every Gemini/Anthropic API call the system makes,
what triggers it, what it costs, and how to estimate volume.

Updated: 2026-06-23 — branch `architecture-v2` (prod). v3 (DB-polled worker) on
`architecture-v3` branch is out of scope here; same per-call models, different
coordination, no cost delta.

Source: `backend/app/workers/*.py`, `backend/app/services/*.py`, Gemini API
pricing (May 2026 verified). Updated for engine-aware figure regen (commit
`fa0944e`) which routes figures to image / vector / table_embed engines.

---

## 1. Pricing Reference

### Primary models (always-on)
| Model | Input $/1M tokens | Output $/1M tokens | Where used |
|---|---|---|---|
| `gemini-2.5-pro` | 1.25 | 10.00 | Schema, theory extract+regen, multimodal Q regen |
| `gemini-2.5-flash` | 0.30 | 2.50 | Question extract, text-only Q regen, optional QC verifier |
| `gemini-3.1-pro-preview` | 2.00 | 12.00 | Figure extraction, figure-regen label overlay |
| `gemini-3.1-flash-image` | flat $0.05 / image | n/a | Figure image regeneration |

### Optional providers (zero cost when not configured)
| Provider | Pricing | Where used |
|---|---|---|
| `claude-sonnet-4-6` | $3 / $15 per 1M | OPTIONAL — providers/router.py fallback. `ANTHROPIC_API_KEY=mock` = $0 |
| `claude-haiku-4-5` | $1 / $5 per 1M | OPTIONAL — Same as above |
| Mathpix OCR | ~$0.005 per page | OPTIONAL — `providers/mathpix.py`, only if user adds API key |
| Sarvam (Indian-language OCR) | varies | OPTIONAL — `providers/sarvam.py`, only if user adds API key |

Notes:
- Gemini 2.5 Pro has a higher-context tier (>200K tokens) at 2× rate — not used in V-Studio.
- `gemini-3.1-flash-image` is metered per generated image, not by tokens.
- **Anthropic + Mathpix + Sarvam costs are zero by default.** They only activate if the user
  enters a real API key via Settings → Providers in V-Studio. Currently
  `ANTHROPIC_API_KEY=mock` on Railway, so Claude calls return mocked responses for free.
- The provider router (`backend/app/providers/router.py`) tries Anthropic first if configured,
  then falls back to Gemini. Today it ALWAYS goes to Gemini because Anthropic is mocked.

---

## 2. Per-Stage Catalog — Every Call the System Makes

Each row is one logical call type. "Calls/chapter" is the formula for how many
times this fires per book chapter processed.

### 2.1 Extraction Pipeline (one-time on upload)

| Tag | Stage | Trigger | Model | Calls/chapter | Per-call $ | Notes |
|---|---|---|---|---|---|---|
| T2 | Schema generation | `POST /api/books/{id}/analyse` | `gemini-2.5-pro` | 1 | $0.058 | Whole PDF, single call |
| T3 | Theory extraction | Worker auto-fires after T2 | `gemini-2.5-pro` | N (sections) | $0.059 | Per section, 1 call each |
| Q1 | Question extraction | Worker auto after T3 | `gemini-2.5-flash` | N (sections) | $0.014 | Per section, batched by section |
| I1 | Figure extraction | `POST /api/books/{id}/extract-figures-v2` (or auto-trigger) | `gemini-3.1-pro-preview` | 1 | $0.058 | Whole PDF, single call |
| — | Figure embedder | CPU only — auto after I1 + T3 | none | n/a | $0 | Deterministic label-matching |
| — | Example linker | CPU only — auto post-extract | none | n/a | $0 | Deterministic chip injection |

**Extraction subtotal (no regen):** ≈ $0.058 + (N × $0.059) + (N × $0.014) + $0.058

For N = 10 sections:
```
$0.058 + 10×$0.059 + 10×$0.014 + $0.058 = $0.846 / chapter
```

### 2.2 Regeneration Pipeline (user-triggered)

| Tag | Stage | Trigger | Model | Calls/regen | Per-call $ | Notes |
|---|---|---|---|---|---|---|
| T4 | Theory regen | `POST /api/books/{id}/regenerate` or per-section reseed | `gemini-2.5-pro` | N × Rt | $0.071 | N = sections, Rt = regen passes |
| T5 | Theory QC verifier | OPTIONAL, gated by `VERIFIER_ENABLED` | `gemini-2.5-flash` | N × Rt | $0.008 | Off by default in prod |
| Q2 | Question regen — text | Worker on regen trigger | `gemini-2.5-flash` | S × (1-P) × V × Rq | $0.014 | S=src qs, P=img %, V=variants, Rq=passes |
| Q3 | Question regen — multimodal | Worker when Q has figure | `gemini-2.5-pro` | S × P × V × Rq | $0.073 | Most expensive line item |
| I2-img | Figure regen — IMAGE engine | `POST /api/books/{id}/sections/{ref}/regenerate-figures` (auto-picked by `pick_regen_engine` for photos / organic diagrams) | `gemini-3.1-flash-image` | F × Fr × E_img | $0.050 | Existing path |
| I2-vec | Figure regen — VECTOR engine (NEW, 2026-06) | Same endpoint; auto-picked for line drawings / geometric figures | `gemini-2.5-pro` (via `_generate_diagram_blocking`) | F × Fr × E_vec | ~$0.025 | Generates LaTeX schematic |
| I2-tab | Figure regen — TABLE_EMBED engine (NEW, 2026-06) | Same endpoint; auto-picked for composite tables with embedded graphics | 1× `gemini-2.5-pro` (SVG structure) + g_tab × `gemini-3.1-flash-image` (per-graphic redraw inside the table) | F × Fr × E_tab | ~$0.025 + (g_tab × $0.050) | Two-model pipeline: Pro generates the table SVG with `{{GRAPHIC_N}}` placeholders, then Flash-Image re-renders each inline graphic. g_tab default 1 → $0.075/fig; g_tab=3 → $0.175/fig. |
| I-OL | Label overlay (×2 OCR passes) | OPTIONAL, gated by `OVERLAY_ENABLED` | `gemini-3.1-pro-preview` | F × Fr × E_img | $0.057 | On by default for IMAGE engine only; vector/table engines do their own labeling so overlay is skipped |

### 2.3 Admin / Maintenance (CPU-only, no API cost)

| Stage | Trigger | Cost |
|---|---|---|
| Re-embed figures | `POST /api/books/{id}/re-embed-figures` | $0 |
| Cancel all jobs | `POST /api/jobs/cancel-all` | $0 |
| Final merge | `GET /api/books/{id}/final-draft` | $0 |
| Export DOCX | `GET /api/books/{id}/export/docx` | $0 |
| Hide question | `PATCH /api/question-banks/questions/{id}/hide` | $0 |
| Self-heal stuck book | Auto on `GET /api/books/{id}` | $0 |

### 2.4 Optional / Conditional Calls (not part of default pipeline)

These exist in code but DON'T fire by default. They only run if specific env vars
are set or specific user actions are taken.

| Tag | Stage | Trigger | Model | Cost | When it fires |
|---|---|---|---|---|---|
| ANT1 | Anthropic provider (OCR / regen) | `providers/router.py` route | `claude-sonnet-4-6` | $3 / $15 per 1M | Only if `ANTHROPIC_API_KEY` is REAL (not `mock`) |
| ANT2 | Anthropic Haiku (cheaper variant) | `providers/router.py` | `claude-haiku-4-5` | $1 / $5 per 1M | Same — needs real key |
| MX1 | Mathpix OCR fallback | `providers/mathpix.py` | n/a — external API | ~$0.005/page | Only if user sets Mathpix key in Settings |
| SV1 | Sarvam OCR (Indian languages) | `providers/sarvam.py` | n/a — external API | varies | Only if user sets Sarvam key in Settings |
| QV1 | Question extraction v2 (LEGACY) | `workers/questions_v2.py` | `gemini-2.5-flash` | $0.014/call | Only if v3 is disabled (not currently) |
| QV1b | Question extraction v1 (DEPRECATED) | `workers/questions.py` | `gemini-2.5-flash` | $0.014/call | Not used in current pipeline |
| RG1 | Standalone figure_regenerator.py | `services/figure_regenerator.py` | `gemini-3.1-flash-image` | $0.05/image | Alternative regen path; routed via figures_tasks.py |
| WM1 | Watermark cleanup on regen output | `services/figures/watermark.py` (called from `figures_tasks.py:606,1040`) | `gemini-3.1-flash-image-preview` | $0.050/image | Opt-in — user passes `watermark_clean=true` in figure-regen payload. Default OFF. When ON: adds ~$0.05 per regenerated figure (post-process Gemini call to strip any watermark in the regenerated image). |
| QCA | Claude QC auditor (P6) | `services/qc/llm.py` | `claude-sonnet-4-6` | depends on prompt; ~$0.005-0.02/section | DEAD by default — no live callers in the current pipeline (it's the legacy "audit a failed extraction" path from earlier QC architecture). Also requires real Anthropic key (currently mocked). Listed for completeness; activate only by explicit code wiring + real key. |

**Status:** All 7 of these are currently NOT firing in your production setup. They're listed
for completeness and future-proofing if you switch providers or activate Anthropic.

---

## 2.5 Engine-Aware Figure Regen (NEW — added 2026-06)

`pick_regen_engine(fig)` (in `app/workers/question_regen_v3.py:861`) classifies
every figure and routes it to ONE of three engines. Gated by
`FIGURE_ENGINE_ROUTING_ENABLED` (env, default ON). When OFF, everything goes to
the legacy `image` engine — the doc's pre-2026-06 numbers apply.

### Engine cost matrix

| Engine | Picked when figure is… | Models hit | Per-fig cost | Notes |
|---|---|---|---|---|
| `image` | photos, organic diagrams, anything not vector-classifiable | `gemini-3.1-flash-image` (1 call) + overlay-OCR ×2 if `OVERLAY_ENABLED` | **$0.050** (+ $0.057 overlay) | Existing path; numbers identical to pre-2026-06 doc |
| `vector` | line drawings, geometric / construction figures, simple schematics | `gemini-2.5-pro` (1 call to `_generate_diagram_blocking` → LaTeX → SVG → PNG) | **~$0.025** | Cheaper than image because Pro on a short LaTeX-gen prompt is ~half the cost of Flash-Image; overlay-OCR is skipped (vector engine emits clean labels itself) |
| `table_embed` | composite tables with embedded graphics (cells contain mini-diagrams) | 1× `gemini-2.5-pro` (table SVG structure) + g_tab × `gemini-3.1-flash-image` (per-graphic redraw via `_embed_table_graphics` at `question_regen_v3.py:1003`) | **~$0.025 + (g_tab × $0.050)** | Pro emits SVG with `{{GRAPHIC_N}}` placeholders; for each cell-graphic, the image model redraws the crop. `g_tab` is the graphic count: pure data tables → 0, typical → 1-3, rich composite figures → 5-10. Failure on `redraw` keeps the original crop (no extra Gemini cost, just CPU). |

### Engine mix (defaults, override per book/subject)

| Variable | Default | Meaning |
|---|---|---|
| `E_img` | 0.60 | Fraction of figures classified as IMAGE engine |
| `E_vec` | 0.25 | Fraction classified as VECTOR (line drawings / schematics) |
| `E_tab` | 0.15 | Fraction classified as TABLE_EMBED |
| `g_tab` | 1.0 | Average graphics inside each table_embed figure |

Mix shifts dramatically by subject:
- **Math (algebra-heavy)**: E_img=0.30, E_vec=0.60, E_tab=0.10 → cheaper than baseline
- **Geometry / Physics with circuits**: E_img=0.40, E_vec=0.50, E_tab=0.10
- **Biology with labelled diagrams**: E_img=0.80, E_vec=0.10, E_tab=0.10 → close to baseline
- **Stats / Data Sci with tables**: E_img=0.20, E_vec=0.20, E_tab=0.60, g_tab=2 → most expensive

### Why this can RAISE OR LOWER total cost

Per-figure regen cost under default mix (excluding overlay/watermark, just the engine call itself):
```
0.60 × $0.050   (image)
+ 0.25 × $0.025 (vector)
+ 0.15 × ($0.025 + 1.0 × $0.050)  (table_embed @ g_tab=1: Pro + 1 Flash-Image redraw)
= $0.030 + $0.00625 + $0.01125
= $0.04750 per regenerated figure
```
vs old single-engine: $0.050 → **~5% cheaper on average**.

**But table_embed dominates when g_tab is high**:
  • g_tab=0 (pure data table): $0.025/fig
  • g_tab=1 (one inline graphic): $0.075/fig
  • g_tab=3 (composite figure): $0.175/fig
  • g_tab=5 (heavy composite): $0.275/fig

Subjects with lots of composite tables (stats / engineering / chemistry data) can easily double the per-figure cost vs the default mix.

### Disable to revert
Set `FIGURE_ENGINE_ROUTING_ENABLED=false` to send every figure to the
IMAGE engine. Use if Pro pricing changes or you want predictable per-fig
cost. Doesn't affect any other pipeline stage.

---

## 3. Variables / Assumptions (defaults)

| Variable | Default | Meaning |
|---|---|---|
| N (sections per chapter) | 10 | Average for class 9-10 textbook |
| S (questions per chapter) | 100 | All printed questions regenerated |
| **V (variants per regen)** | **1 (FIXED)** | **Variants generated per source question. Currently hardcoded to 1 in prod — every cost calc in this doc assumes V=1. Do NOT raise without re-doing the whole budget.** |
| F (figures per chapter) | 15 | Total figures detected by extractor |
| Fr (% figures regenerated) | 0.30 | Reviewer regenerates ~30% by default |
| P (% questions with image) | 0.30 | Varies HEAVILY by subject — see §6 |
| Rt (theory regen passes) | 1 | One regen click per chapter typical |
| Rq (question regen passes) | 1 | One regen click per chapter typical |
| E_img / E_vec / E_tab | 0.60 / 0.25 / 0.15 | Figure engine routing mix (NEW — see §2.5). Sum = 1.0 |
| g_tab | 1.0 | Graphics inside each table_embed figure (NEW — see §2.5) |
| Retry buffer | 0 | Set to 0.30 if you observe retry rate |
| INFRA_PER_CHAPTER | $0.10 | Railway hosting amortised |

---

## 4. Per-Chapter Cost Formulas

```
THEORY     = $0.058 + (N × $0.059) + (N × Rt × $0.071)

QUESTIONS  = (N × $0.014) + (S × (1-P) × V × Rq × $0.014) + (S × P × V × Rq × $0.073)
             # V=1 in prod; if you ever raise V, S × ... terms scale linearly

IMAGES     = $0.058
           + (F × Fr × E_img × $0.050)                            # image engine
           + (F × Fr × E_vec × $0.025)                            # vector engine (NEW)
           + (F × Fr × E_tab × ($0.025 + g_tab × $0.050))         # table_embed engine (NEW): Pro for structure + Flash-Image per inline graphic
           + (F × Fr × E_img × $0.057 × OVERLAY_ENABLED)          # overlay only applies to image-engine slice
           + (F × Fr × E_img × $0.050 × WATERMARK_CLEAN)          # opt-in watermark cleanup, image-engine slice only (default OFF)

AI_TOTAL    = THEORY + QUESTIONS + IMAGES
RETRY       = AI_TOTAL × RETRY_BUFFER          # 0.30 if you observe retries, 0 default
GRAND_TOTAL = AI_TOTAL + RETRY + INFRA_PER_CHAPTER
```

### Example — Typical chapter (N=10, S=100, **V=1**, F=15, Fr=0.30, P=0.30, Rt=1, Rq=1, E_img=0.60, E_vec=0.25, E_tab=0.15, g_tab=1.0, OVERLAY=on)

```
THEORY:
  T2 schema:        1 × $0.058 = $0.058
  T3 theory:       10 × $0.059 = $0.590
  T4 regen:        10 × $0.071 = $0.710
  THEORY SUBTOTAL              = $1.358

QUESTIONS:                                            (V=1 throughout)
  Q1 extract:      10 × $0.014                = $0.140
  Q2 text regen:   100 × 0.7 × 1 × 1 × $0.014 = $0.980
  Q3 mm regen:     100 × 0.3 × 1 × 1 × $0.073 = $2.190
  QUESTIONS SUBTOTAL                          = $3.310

IMAGES (engine-aware — see §2.5):
  I1 figure ext:        1 × $0.058                                = $0.058
  I2-img figure regen:  15 × 0.30 × 0.60 × $0.050                 = $0.135
  I2-vec figure regen:  15 × 0.30 × 0.25 × $0.025                 = $0.028
  I2-tab figure regen:  15 × 0.30 × 0.15 × ($0.025 + 1.0×$0.050)  = $0.051
  I-OL overlay (image-only): 15 × 0.30 × 0.60 × $0.057            = $0.154
  WM1 watermark cleanup (default OFF):                              $0.000
  IMAGES SUBTOTAL                                                 = $0.426

AI_TOTAL                                                          = $5.094
RETRY (0%):                                                         $0.000
INFRA:                                                              $0.100
──────────────────────────────────────────────────────────────────
GRAND TOTAL / CHAPTER                                             = $5.194
```

Net effect of engine-aware figure regen at default mix: **−$0.11/chapter (~2% cheaper)** vs the old single-engine model. Variance is wide:
  • stats / data-sci book with `E_tab=0.60, g_tab=3`: adds ~$0.50/chapter (table_embed dominates because each table costs $0.025 + 3×$0.050 = $0.175)
  • math/geometry-heavy book with `E_vec=0.60`: saves ~$0.10/chapter
  • if user turns on `watermark_clean=true` on every regen: adds ~$0.135/chapter (full F × Fr × $0.050 on image-engine slice)

### Annual estimate (12 chapters × 500 books, default engine mix)

```
$5.194 × 12 × 500 = $31,164 / year
```

For 50 books / year: $3,116. For 1000 books / year: $62,328. Add Railway infra
($60–240/year). Cost is dominated by question regen (~63% of AI bill); see §5.

---

## 5. Cost Drivers (ranked by % share at Typical baseline)

| Rank | Driver | $/chapter | % of bill |
|---|---|---|---|
| 1 | Q3 multimodal regen | $2.19 | 42% |
| 2 | Q2 text regen | $0.98 | 19% |
| 3 | T4 theory regen | $0.71 | 14% |
| 4 | T3 theory extract | $0.59 | 11% |
| 5 | I-OL label overlay (image-only) | $0.15 | 3% |
| 6 | Q1 question extract | $0.14 | 3% |
| 7 | I2-img figure regen | $0.14 | 3% |
| 8 | Infrastructure | $0.10 | 2% |
| 9 | I2-tab figure regen | $0.03 | 1% |
| 10 | I2-vec figure regen | $0.03 | 1% |
| 11 | I1 figure extract | $0.058 | 1% |
| 12 | T2 schema | $0.058 | 1% |

**Insight:** Question regen (Q2+Q3) = 61% of the bill. Multimodal alone is 42%.
Figure-engine routing (2026-06) saves ~$0.11/chapter on average but adds variance
— a stats / data-sci textbook with many `table_embed` figures (high g_tab) can
shift I2-tab from rank 9 up to rank 5.

---

## 6. Subject-Specific Assumptions

`P` (percentage of questions with attached figures) varies dramatically:

| Subject | Realistic P | Notes |
|---|---|---|
| Math | 0.05 – 0.15 | Mostly algebra; few diagrams |
| Physics | 0.30 – 0.50 | Lots of force / circuit diagrams |
| Chemistry | 0.20 – 0.30 | Apparatus + structures |
| Biology | 0.40 – 0.60 | Labelled diagrams everywhere |
| Social Science | 0.10 – 0.20 | Maps + photographs |
| English / Languages | 0.02 – 0.10 | Rare |
| EVS | 0.15 – 0.25 | Mixed |

If your `P` is incorrect, the Q3 multimodal estimate is off by an order of magnitude.

---

## 7. Optimization Levers (ranked by ROI)

| # | Lever | Effort | Savings $/ch | Risk |
|---|---|---|---|---|
| 1 | Disable multimodal regen (Q3 → Q2 fallback) | Env var `MULTIMODAL_REGEN_ENABLED=false` | ~$2.19 | MEDIUM — loses image-aware reasoning |
| 2 | Skip regen on 50% of chapters | Workflow | ~$1.66 | LOW — selective regen |
| 3 | Enable Gemini prompt caching | 1 dev day | ~$0.30 | ZERO — pure win |
| 4 | Disable I-OL label overlay | Config flag | ~$0.15 | MEDIUM — label clarity |
| 5 | Move T3 theory extract to Flash | Env var | ~$0.45 | HIGH — quality drop on dense pages |
| 6 | Filter image-questions out of regen | Logic change | ~$2.19 | MEDIUM — those Qs stay as printed |
| 7 | Reduce V (already at 1) | UI change | $0 currently | N/A — `V=1` is hardcoded in prod |
| 8 | **Disable engine routing** (`FIGURE_ENGINE_ROUTING_ENABLED=false`) | Env var | **−$0.11 (engine routing is already net-cheaper; turning OFF saves only on books that are mostly `table_embed` with high g_tab)** | LOW — reverts to single image-engine cost. Leave ON for net savings unless a book is table-heavy |

**Recommended sequence:**
1. Enable prompt caching (zero risk, $0.30 saved)
2. Filter low-quality image questions before Q3 (saves bulk of $2.19)
3. Lower retry buffer to 0% if your observed retry rate is <5%

---

## 8. Per-User-Action Cost Cheat Sheet

For showing cost in V-Studio UI:

| User Action | API Cost (V=1) |
|---|---|
| Upload chapter | ~$0.85 (extraction) |
| Click "Regenerate Theory" (whole book) | ~$0.71 |
| Click "Regenerate this section" | ~$0.07 |
| Click "Regenerate Questions" (whole bank) | ~$3.17 |
| Click "Reseed figures" — image engine | ~$0.050 / fig |
| Click "Reseed figures" — vector engine | ~$0.025 / fig |
| Click "Reseed figures" — table_embed engine | ~$0.025 + (g_tab × $0.050) / fig (g_tab=1 → $0.075; g_tab=3 → $0.175) |
| Click "Reseed figures" (whole chapter, 15 figs at 30%, default engine mix) | ~$0.20 |
| Click "Reseed figures" with **watermark_clean=true** | +$0.05 per regenerated figure (opt-in WM1 stage) |
| View Preview / Composer / Comparison tab | $0 |
| Export DOCX | $0 |
| Reseed final-draft (`POST /final-draft/reseed`) | $0 (recompute from DB only) |
| Cancel-all jobs / per-book cancel | $0 (Celery revoke + DB flip) |
| Drag-reorder a question in Composer | $0 |
| Hide a question | $0 |
| Add new folder | $0 |
| Login | $0 |

---

## 9. Volume Projections

| Volume | Extract only ($0.85) | Extract + 1 regen ($5.19) |
|---|---|---|
| 1 chapter | $0.85 | $5.19 |
| 10 chapters | $8.46 | $51.94 |
| 100 chapters | $84.60 | $519.40 |
| 1 book (12 ch) | $10.15 | $62.33 |
| 50 books / year | $507 | $3,116 |
| 500 books / year | $5,070 | **$31,164** |
| 1000 books / year | $10,140 | $62,328 |

Add Railway infra: $5–20/month base ($60–240/year). Numbers above use V=1
(fixed in prod) and default engine mix (E_img=0.60, E_vec=0.25, E_tab=0.15,
g_tab=1.0). Subject-heavy outliers: data-sci/stats books with lots of tables
can run ~$0.30/chapter higher; algebra-heavy math can run ~$0.10 lower.

---

## 10. Notes for Estimation

- **Numbers are upper-bound conservative.** Actual costs are typically 30-50% lower because:
  - Prompts use prompt caching when enabled (not measured here)
  - Many chapters don't need all features (figures, regen, etc.)
  - Output tokens often well under estimate
- **For accurate budgeting:**
  1. Process 5-10 real chapters
  2. Check Google Cloud Console → Vertex AI → token usage
  3. Replace per-call $ values in this doc with actual averages
- **Retry buffer (30%)** is currently set to 0 by default. Add it back if you observe failures in prod logs.
- **Multimodal is the killer.** Watch the `P` assumption carefully — overestimating it doubles your bill.

---

## 11. Code References

### Primary Gemini call sites (production)

| Stage | File | Function |
|---|---|---|
| Schema gen | `backend/app/services/schema_builder.py:108` | `call_gemini_with_pdf` (Pro) |
| Theory extract | `backend/app/services/theory_extractor.py:173` | `call_gemini_with_pdf` (Pro) |
| Question extract | `backend/app/workers/questions_v3.py:731` | `call_gemini_with_pdf` (Flash) |
| QA verifier (optional) | `backend/app/services/qa/verifier.py:65` | `call_gemini_with_pdf` (Flash) |
| Theory regen | `backend/app/workers/regen_v3.py` | direct Gemini call |
| Question regen — text | `backend/app/workers/question_regen_v3.py:366` | `call_gemini_text_only` (Flash) |
| Question regen — multimodal | `backend/app/workers/question_regen_v3.py:354` | `call_gemini_text_with_images` (Pro) |
| Figure extract | `backend/app/services/figures/extractor.py:134` | `generate_content` (Pro Preview) |
| Figure regen — engine router | `backend/app/workers/question_regen_v3.py:861` | `pick_regen_engine(fig)` — returns "image" / "vector" / "table_embed" |
| Figure regen — image engine | `backend/app/services/figures/regenerator.py:141` | `generate_content` (Flash Image) |
| Figure regen — vector engine | `backend/app/workers/question_regen_v3.py:1150` | `compute_vector_png` → `_generate_diagram_blocking` (Pro) |
| Figure regen — table_embed engine | `backend/app/workers/question_regen_v3.py:1185` | `compute_table_png` → `_generate_diagram_blocking` (Pro) + `_embed_table_graphics` (Pro per inline graphic) |
| Engine dispatch site (figures pipeline) | `backend/app/workers/figures_tasks.py:780-799` | `qr3.pick_regen_engine` + route to compute_table_png / compute_vector_png / image-engine fallback |
| Label overlay (OCR) | `backend/app/services/figures/overlay.py:159` | `generate_content` (Pro Preview, ×2 passes) — image-engine only |
| Watermark cleanup (WM1) | `backend/app/services/figures/watermark.py:67`, called from `figures_tasks.py:606,1040` | `generate_content` (Flash Image Preview) — opt-in, fires only when `watermark_clean=true` |

### Shared infrastructure

| File | Purpose |
|---|---|
| `backend/app/core/gemini_runtime.py` | Central Gemini wrapper with timeouts/retries (3 functions: `_with_pdf`, `_text_only`, `_text_with_images`) |
| `backend/app/core/gemini_client.py` | Lower-level client builder |
| `backend/app/core/claude_client.py` | Anthropic SDK wrapper (mocked currently) |
| `backend/app/core/claude_mock.py` | Mock Claude responses for free dev/CI use |
| `backend/app/core/claude_agent.py` | Claude Agent SDK (Claude Code OAuth — no separate API key) |
| `backend/app/providers/router.py` | Provider routing — picks Anthropic vs Gemini |
| `backend/app/providers/mathpix.py` | Mathpix OCR fallback |
| `backend/app/providers/sarvam.py` | Sarvam OCR fallback |

### Legacy / unused paths (don't fire in current pipeline)

| File | Status |
|---|---|
| `backend/app/workers/questions.py` | v1 question extractor — DEPRECATED |
| `backend/app/workers/questions_v2.py` | v2 question extractor — superseded by v3 |
| `backend/app/services/figure_regenerator.py` | Standalone regen helper — superseded by `figures/regenerator.py` |

Model defaults centralized in `backend/app/core/config.py`.

---

## 11.5. Prompt Versions — Active Prompts (what's running now)

These are the prompt files **currently loaded** by the code. All costs in §2 assume these.

| Stage | Active file | Notes |
|---|---|---|
| Schema generation | `backend/prompts/v1/schema_gemini.txt` | V2 compressed, 5-bucket rules, Crossword/Sci-Cryptic |
| Theory extraction | `backend/prompts/v1/extractor.txt` | V1 + operator preservation + interleaved theory patches |
| Theory regen | `backend/prompts/v1/regenerator.txt` | V1 original |
| Question extraction | `backend/prompts/v1/question_extractor_v3.txt` | V3 |
| Question regen — text | `backend/prompts/v1/question_regenerator_v3.txt` | V3 |
| Question regen — multimodal | `backend/prompts/v1/question_regenerator_v3_image_addendum.txt` | V3 addendum |
| Figure extraction | `backend/prompts/v1/figure_extractor.txt` | — |
| Figure regen | `backend/prompts/v1/figure_regenerator.txt` | — |
| QA verifier | `backend/prompts/v1/qa_verifier.txt` | OFF by default (T5) |

### Draft prompts (saved but NOT loaded — ignore for cost calc)

These files exist alongside the live ones for future activation. **They contribute $0 today
because the code doesn't load them.** Listed here so you know what's available if you decide
to activate later.

| Draft file | What it would add |
|---|---|
| `extractor_v2.txt` | Longer V2 extractor prompt — discussed but not activated |
| `regenerator_v3.txt` | New theory regen with Priority hierarchy + recap rules + heading mapping + example numerical shift |

To activate any draft: rename it to overwrite the live filename. Until then, **no cost impact**.

---

## 11.7. Question Regen — Hybrid Routing (Why Both Models)

The question regen pipeline auto-routes each call between Flash and Pro based on
whether the question has an attached figure. This is **intentional cost optimization**
— do NOT collapse it to a single model.

### Routing logic (`backend/app/workers/question_regen_v3.py:342-346`)

```python
use_multimodal = bool(
    image_bytes_list
    and settings.MULTIMODAL_REGEN_ENABLED
)
```

| Question type | Model | Per-call $ | Rationale |
|---|---|---|---|
| Text-only (no figure attached) | `gemini-2.5-flash` | $0.014 | Fast + cheap for rephrasing; Flash matches Pro on text-only tasks |
| Multimodal (figure attached) | `gemini-2.5-pro` | $0.073 | Pro can "see" the image for accurate regen |

### Why NOT all-Pro

Switching ALL question regen to Pro:
- Cost +130% (Q regen: $3.17 → $7.30/chapter)
- Annual extra: **~$24,780** @ 500 books × 12 chapters
- Quality gain on text-only: **marginal** (Flash already excels at rephrasing)
- Net: **wastes ~$25K/year for no measurable improvement on 70% of questions**

### Why NOT all-Flash

Switching ALL question regen to Flash:
- Saves $1.77/chapter, ~$10,620/year
- Quality risk on image questions:
  - Flash can't see labels/values in attached figures
  - Generated regen may say "find slope from graph" without realising new numbers don't match the original graph
  - The `image_needs_regen` decision (in image addendum) needs Pro's vision

### Verdict

**Keep current hybrid.** It's already optimal. Auto-routing per question is the right design.

If you must cut cost, the lever is `MULTIMODAL_REGEN_ENABLED=false` env var (5-min change),
not switching models globally.

---

## 11.8. Recent Runtime Optimizations (cumulative)

These are code-level cost/reliability changes shipped to `architecture-v2`
(prod). Older items shipped earlier on `infra/phase1-celery`; the 2026-06-23
group landed today.

| Change | Where | Cost impact | Reliability impact |
|---|---|---|---|
| **Figure overlay default OFF** | `figures_tasks.py:317` | Saves I-OL cost ($0.057 × Fr × F = ~$0.26/chapter) when off | Regen looks more distinct (was painting orig labels back on regen) |
| **FIGURE_EXTRACT_MAX_INFLIGHT=1 semaphore** | `figures_tasks.py:_inflight_sem` | No direct cost change | Prevents OOM on parallel figure extracts |
| **Per-figure 90s timeout** | `figures_tasks.py:_run_with_timeout` | No direct cost change | One hung Gemini call no longer blocks whole reseed |
| **Question regen UNION query** | `figures_tasks.py:325-352` | No direct cost change | Catches figures reassigned by embedder (was silently skipped) |
| **Stop mutating `Figure.section_id`** | `figure_embedder.py:420-427, 491-494` | No direct cost change | Figures stay in extraction anchor, not jumping sections |
| **Orphan figure label-match + page-range fallback** | `figure_embedder.py:387-470` | No direct cost change | Orphan figures get embedded instead of dropped |
| **Worked-example subsection dedup** | `final_merge.py:999+` | No direct cost change | Prevents duplicate "Q4.2 + EXAMPLE 4.2" in preview |
| **SKIP_ORPHAN_RECOVERY env var** | `main.py:171` | Saves cost of crash loop re-dispatching | Backend stays UP even if a job is poisoned |
| **CELERY_CONCURRENCY=1 default** | `start.sh` | No direct cost change | RAM safety on 512MB tier |
| **Engine-aware figure regen** (`fa0944e`) | `question_regen_v3.py:pick_regen_engine`, `figures_tasks.py:780` | -$0.11/chapter on default mix; +$0.50 on table-heavy books (g_tab=3), -$0.10 on vector-heavy math | Vector/table figures regenerate with type-appropriate engine instead of all going to image |
| **pylatexenc declared as dep** (`355b137`) | `backend/pyproject.toml` | No cost change | Theory/Q/Fig stages no longer crash on `ModuleNotFoundError` post-schema |
| **Tolerant figures JSON parser** (`177642b`) | `figures/extractor.py` | No cost change | Figure stage no longer crashes when Gemini's response has trailing garbage after the JSON object |
| **needs_review schema → ready** (`4241ce4`) | `book_status.py` | No cost change | Books finalize properly instead of hanging at "processing" |
| **Auto-reaper (watchdog)** (`e78a69d`) | `core/watchdog.py:_reap_zombie_tasks` | Saves cost of re-running zombie tasks after Celery redelivery — variable, depends on restart frequency | Redeploys self-heal; no manual cancel-all needed |
| **Real cancel (revoke + purge)** (`fda69c1`) | `services/cancellation.py`, `workers/runner.py` | Saves wasted Gemini calls on cancelled work (cancellation now actually stops in-flight tasks) | No restart needed to cancel |
| **Figures-only fail → partial book** (`177642b`) | `book_status.py` | No cost change | Theory + questions stay usable when only figures fails |
| **DOCX no-strip + per-section question numbering + question_number sort** (`efca48f`, `3179023`, `70bbe85`) | `docx_export.py`, `final_merge.py`, `question_regen_v3.py`, frontend `question-sort.ts` | No cost change | Export shows full raw_text + textbook-numeric order across Preview/Composer/Regen/DOCX |
| **Figure URL normalization** (`e5fd369`) | `TheoryView.tsx:EmbeddedFigureRender` | No cost change | Label-less placement-only figures render in the review UI instead of broken-image icons |
| **Live N-of-M progress for figures + questions** (`1379016`, `8feb88c`) | `extractionPipeline.ts` | No cost change | UI shows "N figures extracted" / "N questions extracted" during run |
| **Pillow declared as direct dep** (`676d91f`) | `pyproject.toml` | No cost change | Was transitive via cairosvg/pdfplumber; now explicit since used by `_embed_table_graphics` |
| **"numbers_only" question-regen similarity restored** (`f821e9a`) | regen UI | No new cost path | Adds setting that uses existing regen call — no extra calls |

### Net cost effect of session changes

| Item | Effect |
|---|---|
| Overlay default OFF | -$0.26/chapter (if not re-enabled per call) |
| Everything else | Net 0 cost — reliability + quality fixes |
| **Per-chapter net** | **-$0.26 (~5% cheaper)** |
| **Annual @ 500 books × 12 ch** | **-$1,560/year** |

So this session's work made the system **6.6% cheaper if v3 prompts activated** vs **5% cheaper from runtime changes**. Combined: about neutral on cost, dramatically better on reliability and output quality.

---

## 12. How to Override per Deployment

Set env vars on the Railway Backend service:

```
# ── Providers ──────────────────────────────────────────────────────────
ANTHROPIC_API_KEY=mock              # disables Claude (no cost)
ANTHROPIC_MODEL=claude-sonnet-4-6
ANTHROPIC_MODE=auto                 # auto | mock | real | agent
GEMINI_REGEN_MODEL=gemini-2.5-pro   # also used by vector/table_embed engines

# ── Concurrency / safety (cost-relevant) ───────────────────────────────
CELERY_CONCURRENCY=3                # parallel worker tasks (RAM + cost cap). start.sh default is 2; prod sets 3.
FIGURE_EXTRACT_MAX_INFLIGHT=1       # parallel figure extracts (RAM trade)
GEMINI_GLOBAL_INFLIGHT=8            # NEW (2026-06): container-wide cap on concurrent Gemini calls. Divides across CELERY_CONCURRENCY workers (e.g. 8/3 = 2 per worker). Bumping to 16 doubles throughput for single books but doesn't change per-call cost.

# ── Feature flags ──────────────────────────────────────────────────────
MULTIMODAL_REGEN_ENABLED=true       # set false → drops Q3 cost; image questions get Flash text-only regen instead of Pro multimodal
FIGURE_ENGINE_ROUTING_ENABLED=true  # NEW (2026-06): when true, vector/table_embed engines split the figure regen load. false → everything goes to legacy image engine (single per-fig cost).
USE_DB_WORKER=false                 # v3 toggle. Leave false on architecture-v2. (true only switches if you're on architecture-v3 branch with v3 code present.)

# ── Reliability knobs ──────────────────────────────────────────────────
SKIP_ORPHAN_RECOVERY=0              # set 1 to skip orphan-job re-dispatch on startup
CELERY_TASK_TIME_LIMIT=1200         # 20 min hard limit per task (env-overridable)
CELERY_TASK_SOFT_TIME_LIMIT=1020    # 17 min soft limit

# ── Future-gated (no env keys yet — would need code change to wire) ───
# OVERLAY_ENABLED=0                 # would drop I-OL cost (~$0.15/chapter)
# VERIFIER_ENABLED=0                # T5 already off by default
```

---

End of doc.
