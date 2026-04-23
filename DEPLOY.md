# Deploy to Railway

Two Railway services from the same repo:
- **backend** → `backend/` (Dockerfile.prod) → exposes FastAPI on `$PORT`
- **frontend** → `frontend/` (Dockerfile.prod) → serves built Vite bundle on `$PORT`

Shareable URL = whatever Railway assigns to the frontend service.

---

## One-time setup

### 1. Install CLI + log in
```bash
brew install railway
railway login
```

### 2. Create the project
From repo root:
```bash
railway init
# Pick: "Empty project"
# Name it something like "cmds-extraction"
```

### 3. Add services

**Backend service:**
```bash
railway service create backend
railway link   # select the project + backend service
cd backend
railway up     # pushes and builds Dockerfile.prod
```

**Frontend service:**
```bash
railway service create frontend
cd ../frontend
railway up
```

### 4. Add a Postgres database (one click)
```bash
railway add   # pick "PostgreSQL"
```
Railway auto-injects `DATABASE_URL` into every service. The backend picks it up via `settings.DATABASE_URL` (already uses env).

---

## Environment variables

### Backend service (`railway variables set KEY=VAL -s backend`)
| Key | Value | Notes |
|---|---|---|
| `APP_ENV` | `production` | silences the dev warning |
| `GEMINI_API_KEY` | `<your key>` | required for extraction + regen |
| `GEMINI_EXTRACT_MODEL` | `gemini-2.5-flash` | default fine |
| `GEMINI_REGEN_MODEL` | `gemini-2.5-pro` | high-quality regen |
| `ANTHROPIC_API_KEY` | `<your key>` | for analyser |
| `DATABASE_URL` | (auto) | from Railway Postgres plugin |
| `CORS_ORIGINS` | `https://<frontend>.up.railway.app` | lock down after you know URL |
| `STORAGE_BACKEND` | `local` | OR `s3` if you wire MinIO/S3 |
| `TASK_EXECUTOR` | `inline` | keeps single-service simple |

### Frontend service
| Key | Value |
|---|---|
| `VITE_API_BASE` | `https://<backend>.up.railway.app` |

> **IMPORTANT:** `VITE_API_BASE` is baked in at **build time** (Vite inlines env into bundle). If you change it, trigger a rebuild: `railway up` again or hit "Redeploy" in the dashboard.

---

## Post-deploy checklist

1. Hit `https://<backend>.up.railway.app/api/health` → should return `{"status":"ok","env":"production"}`.
2. Open the frontend URL → upload a test PDF → verify full flow (analyse → extract → regen → docx export).
3. Once verified, share the **frontend** URL with stakeholders.
4. Lock down `CORS_ORIGINS` on the backend to the frontend URL only.

---

## What's NOT in this deploy (keep it simple first)

- **Celery + Redis + MinIO** (TASK_EXECUTOR=inline uses a thread pool; fine for demos, not for 10+ concurrent users).
- **MinIO / S3 storage** (files live on the backend's local FS — ephemeral on Railway unless you attach a volume).
- **Custom domain** (Railway assigns `.up.railway.app`; add a CNAME later if needed).

Add these when you outgrow the demo stage.

---

## Gotchas

- **pandoc** is installed in `Dockerfile.prod` — don't remove it or docx export breaks.
- **Local SQLite** (`cmds.db` in repo root) is **not** used in production — `DATABASE_URL` from Railway Postgres is picked up automatically.
- **Storage ephemeral**: uploaded PDFs in `backend/storage/` disappear on redeploy. For the demo this is fine; for persistence, attach a Railway Volume or move to S3.
- **Regen jobs run inline** (same process as API). A long regen blocks other requests on that worker. Fine for 1-5 concurrent demo users.
