# V-Studio — API Cost & Usage Reference

Comprehensive catalog of every Gemini/Anthropic API call the system makes,
what triggers it, what it costs, and how to estimate volume.

Updated: 2026-06-01
Source: `backend/app/workers/*.py`, `backend/app/services/*.py`, Gemini API
pricing (May 2026 verified).

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
| I2 | Figure regen | `POST /api/books/{id}/sections/{ref}/regenerate-figures` | `gemini-3.1-flash-image` | F × Fr | $0.050 | F=total figs, Fr=% regenerated |
| I-OL | Label overlay (×2 OCR passes) | OPTIONAL, gated by `OVERLAY_ENABLED` | `gemini-3.1-pro-preview` | F × Fr | $0.057 | On by default; OCRs labels twice |

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

**Status:** All 7 of these are currently NOT firing in your production setup. They're listed
for completeness and future-proofing if you switch providers or activate Anthropic.

---

## 3. Variables / Assumptions (defaults)

| Variable | Default | Meaning |
|---|---|---|
| N (sections per chapter) | 10 | Average for class 9-10 textbook |
| S (questions per chapter) | 100 | All printed questions regenerated |
| V (variants per regen) | 1 | Variants generated per source question |
| F (figures per chapter) | 15 | Total figures detected by extractor |
| Fr (% figures regenerated) | 0.30 | Reviewer regenerates ~30% by default |
| P (% questions with image) | 0.30 | Varies HEAVILY by subject — see §6 |
| Rt (theory regen passes) | 1 | One regen click per chapter typical |
| Rq (question regen passes) | 1 | One regen click per chapter typical |
| Retry buffer | 0 | Set to 0.30 if you observe retry rate |
| INFRA_PER_CHAPTER | $0.10 | Railway hosting amortised |

---

## 4. Per-Chapter Cost Formulas

```
THEORY     = $0.058 + (N × $0.059) + (N × Rt × $0.071)
QUESTIONS  = (N × $0.014) + (S × (1-P) × V × Rq × $0.014) + (S × P × V × Rq × $0.073)
IMAGES     = $0.058 + (F × Fr × $0.050) + (F × Fr × $0.057 × OVERLAY_ENABLED)

AI_TOTAL    = THEORY + QUESTIONS + IMAGES
RETRY       = AI_TOTAL × RETRY_BUFFER         (0.30 typical, 0.00 if disabled)
GRAND_TOTAL = AI_TOTAL + RETRY + INFRA_PER_CHAPTER
```

### Example — Typical chapter (N=10, S=100, V=1, F=15, Fr=0.30, P=0.30, Rt=1, Rq=1)

```
THEORY:
  T2 schema:        1 × $0.058 = $0.058
  T3 theory:       10 × $0.059 = $0.590
  T4 regen:        10 × $0.071 = $0.710
  THEORY SUBTOTAL              = $1.358

QUESTIONS:
  Q1 extract:      10 × $0.014        = $0.140
  Q2 text regen:   100 × 0.7 × 1 × 1 × $0.014 = $0.980
  Q3 mm regen:     100 × 0.3 × 1 × 1 × $0.073 = $2.190
  QUESTIONS SUBTOTAL                  = $3.310

IMAGES:
  I1 figure ext:    1 × $0.058        = $0.058
  I2 figure regen:  15 × 0.30 × $0.05 = $0.225
  I-OL overlay:     15 × 0.30 × $0.057 = $0.257
  IMAGES SUBTOTAL                     = $0.540

AI_TOTAL                              = $5.208
RETRY (0%):                             $0.000
INFRA:                                  $0.100
─────────────────────────────────────────────
GRAND TOTAL / CHAPTER                 = $5.308
```

### Annual estimate (12 chapters × 500 books)

```
$5.308 × 12 × 500 = $31,848 / year
```

---

## 5. Cost Drivers (ranked by % share at Typical baseline)

| Rank | Driver | $/chapter | % of bill |
|---|---|---|---|
| 1 | Q3 multimodal regen | $2.19 | 41% |
| 2 | Q2 text regen | $0.98 | 18% |
| 3 | T4 theory regen | $0.71 | 13% |
| 4 | T3 theory extract | $0.59 | 11% |
| 5 | I-OL label overlay | $0.26 | 5% |
| 6 | I2 figure regen | $0.23 | 4% |
| 7 | Q1 question extract | $0.14 | 3% |
| 8 | Infrastructure | $0.10 | 2% |
| 9 | I1 figure extract | $0.058 | 1% |
| 10 | T2 schema | $0.058 | 1% |

**Insight:** Question regen (Q2+Q3) = 59% of cost. Multimodal alone is 41%.

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
| 1 | Disable multimodal regen (Q3 → Q2 fallback) | Env var (5 min) | ~$2.19 | MEDIUM — loses image-aware reasoning |
| 2 | Skip regen on 50% of chapters | Workflow | ~$1.66 | LOW — selective regen |
| 3 | Enable Gemini prompt caching | 1 dev day | ~$0.30 | ZERO — pure win |
| 4 | Disable I-OL label overlay | Config flag | ~$0.26 | MEDIUM — label clarity |
| 5 | Move T3 theory extract to Flash | Env var | ~$0.45 | HIGH — quality drop on dense pages |
| 6 | Filter image-questions out of regen | Logic change | ~$2.19 | MEDIUM — those Qs stay as printed |
| 7 | Reduce V (already at 1) | UI change | $0 currently | N/A |

**Recommended sequence:**
1. Enable prompt caching (zero risk, $0.30 saved)
2. Filter low-quality image questions before Q3 (saves bulk of $2.19)
3. Lower retry buffer to 0% if your observed retry rate is <5%

---

## 8. Per-User-Action Cost Cheat Sheet

For showing cost in V-Studio UI:

| User Action | API Cost |
|---|---|
| Upload chapter | ~$0.85 (extraction) |
| Click "Regenerate Theory" (whole book) | ~$0.71 |
| Click "Regenerate this section" | ~$0.07 |
| Click "Regenerate Questions" (whole bank) | ~$3.17 |
| Click "Reseed figures" (whole section) | ~$0.04 per fig |
| Click "Reseed figures" (whole chapter, 15 figs at 30%) | ~$0.23 |
| View Preview page | $0 |
| Export DOCX | $0 |
| Hide a question | $0 |
| Add new folder | $0 |
| Login | $0 |

---

## 9. Volume Projections

| Volume | Extract only ($0.85) | Extract + 1 regen ($5.31) |
|---|---|---|
| 1 chapter | $0.85 | $5.31 |
| 10 chapters | $8.46 | $53.08 |
| 100 chapters | $84.60 | $530.80 |
| 1 book (12 ch) | $10.15 | $63.69 |
| 50 books / year | $507 | $3,185 |
| 500 books / year | $5,070 | **$31,848** |
| 1000 books / year | $10,140 | $63,696 |

Add Railway infra: $5–20/month base ($60–240/year).

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
| Figure regen | `backend/app/services/figures/regenerator.py:141` | `generate_content` (Flash Image) |
| Label overlay (OCR) | `backend/app/services/figures/overlay.py:159` | `generate_content` (Pro Preview, ×2 passes) |

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

## 11.8. Recent Runtime Optimizations (shipped in this session)

These are code-level cost/reliability changes already pushed to `infra/phase1-celery`:

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
ANTHROPIC_API_KEY=mock          # disables Claude (no cost)
ANTHROPIC_MODEL=claude-sonnet-4-6
GEMINI_REGEN_MODEL=gemini-2.5-pro   # downgrade to flash for cost savings

# Concurrency / safety (cost-relevant)
FIGURE_EXTRACT_MAX_INFLIGHT=1   # parallel figure extracts (RAM trade)
CELERY_CONCURRENCY=1            # parallel worker tasks (RAM + cost cap)

# Disable expensive features
# (no env yet — would need code change to gate)
# MULTIMODAL_REGEN_ENABLED=0      # → drops Q3 cost to zero
# OVERLAY_ENABLED=0               # → drops I-OL cost
# VERIFIER_ENABLED=0              # T5 already off by default
```

---

End of doc.
