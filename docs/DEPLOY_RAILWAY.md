# Resova — Railway Deployment Guide

> [!IMPORTANT]
> This guide prepares everything for Railway deployment. No actual deployment
> or account creation is required — follow the steps when you are ready to go live.

## Architecture on Railway

```
┌──────────────────────────────────────────────┐
│                   Railway                     │
│                                               │
│  ┌─────────────────────┐  ┌───────────────┐  │
│  │  resova (Service)    │  │  PostgreSQL   │  │
│  │  FastAPI + Scheduler │──│  (Plugin)     │  │
│  │  1 worker            │  │  Managed DB   │  │
│  └─────────────────────┘  └───────────────┘  │
│       │                                       │
│       │ Health: GET /health                   │
│       │ PORT injected by Railway              │
└───────│───────────────────────────────────────┘
        │
   Telegram Bot API  ←→  Owner's Telegram
   OpenAI API        ←→  gpt-4o-mini
```

### Why PostgreSQL (not SQLite)?

Railway containers are **ephemeral** — redeployments replace the filesystem.
While Railway supports persistent volumes, managed PostgreSQL is:

- More reliable (automated backups, no corruption risk on container restart).
- Required for future multi-worker scaling.
- Already supported by Resova — `DATABASE_URL` is the only change needed.

### Why 1 worker?

Resova runs an **in-process asyncio scheduler** (`PollerScheduler`). Multiple
uvicorn workers would spawn duplicate schedulers. Use `--workers 1` to guarantee
exactly one scheduler instance. This is sufficient for a single-user agent.

---

## Prerequisites

1. A [Railway account](https://railway.app/) (Hobby plan: $5/month).
2. A private GitHub repository containing the Resova code.
3. The following secrets ready (never commit them):
   - OpenAI API key
   - Telegram bot token
   - Telegram user ID

---

## Step 1: Create the Railway Project

1. Log in to [railway.app](https://railway.app/).
2. Click **New Project → Deploy from GitHub repo**.
3. Select your private `resova` repository.
4. Railway detects the `Dockerfile` automatically.

## Step 2: Add PostgreSQL

1. In the Railway project dashboard, click **+ New → Database → PostgreSQL**.
2. Railway creates a managed PostgreSQL instance and injects `DATABASE_URL`
   automatically as a service variable.
3. Verify the variable name is `DATABASE_URL` (Railway's default). If Railway
   names it differently (e.g., `POSTGRESQL_URL`), create a reference variable:
   ```
   DATABASE_URL = ${{Postgres.DATABASE_URL}}
   ```

## Step 3: Set Environment Variables

In the Railway service settings → **Variables**, add:

| Variable | Value | Required |
|---|---|---|
| `APP_ENV` | `production` | ✓ |
| `LOG_LEVEL` | `INFO` | ✓ |
| `DATABASE_URL` | *(auto-injected by PostgreSQL plugin)* | ✓ |
| `PROFILE_PATH` | `config/profile.yaml` | ✓ |
| `ENABLE_EMAIL_INGESTION` | `false` | ✓ |
| `ENABLE_LISTING_POLLER` | `false` | ✓ |
| `ENABLE_AI_ANALYSIS` | `true` | ✓ |
| `OPENAI_API_KEY` | `sk-proj-...` | ✓ |
| `OPENAI_MODEL` | `gpt-4o-mini` | ✓ |
| `DAILY_AI_ANALYSIS_LIMIT` | `100` | ✓ |
| `ENABLE_TELEGRAM` | `true` | ✓ |
| `TELEGRAM_BOT_TOKEN` | `8982618527:AAH...` | ✓ |
| `TELEGRAM_ALLOWED_USER_ID` | `5975310413` | ✓ |

> [!CAUTION]
> Never set `ENABLE_LISTING_POLLER=true` or `LISTING_POLLER_ACK=true` in
> production without explicit approval. The poller sends HTTP requests to
> external platforms.

## Step 4: Configure Health Check

In **Settings → Deploy**:

- **Health Check Path:** `/health`
- **Health Check Timeout:** 10 seconds

Railway uses this to verify the service is healthy after each deployment.
The endpoint returns `{"status": "ok", ...}` with HTTP 200, or
`{"status": "degraded", ...}` with HTTP 503 if the database is unreachable.

## Step 5: Deploy

1. Push to the connected GitHub branch (default: `main`).
2. Railway builds the Docker image and deploys automatically.
3. The startup command runs `alembic upgrade head` before starting uvicorn,
   so the database schema is always up to date.
4. Check the deployment log for: `Resova 0.1.0 started (env=production, db=postgresql)`.

## Step 6: Verify

```bash
curl https://your-app.up.railway.app/health
# Expected: {"status":"ok","version":"0.1.0","database":"ok","environment":"production"}
```

---

## Cost Estimate

### Railway Hobby Plan — $5/month

Resova is a lightweight single-user agent. Expected resource consumption:

| Resource | Estimated Usage | Monthly Cost |
|---|---|---|
| **CPU** | ~0.1 vCPU average (idle + periodic polls) | ~$2.00 |
| **Memory** | ~128–256 MB (FastAPI + SQLAlchemy) | ~$1.30–$2.60 |
| **PostgreSQL** | <100 MB storage | ~$0.02 |
| **Network** | <1 GB egress | ~$0.05 |
| **Total** | | **~$3.40–$4.70** |

The $5 subscription includes $5 of usage credit, so **Resova should run
within the included credit** most months. Expected monthly bill: **$5 flat**.

### External API Costs (not Railway)

| Service | Estimated Usage | Cost |
|---|---|---|
| **OpenAI (gpt-4o-mini)** | ~100 analyses/day × ~1K tokens | ~$0.30/day ≈ $9/month |
| **Telegram Bot API** | Free | $0 |

---

## GitHub Private Repository Setup

1. Create a private repository on GitHub (e.g., `yourusername/resova`).
2. Add Railway's GitHub App with access to this repository only.
3. In Railway, connect the repository and select the branch to deploy from.

### Pushing Local Code

```bash
# From C:\dev\resova
git remote add origin https://github.com/yourusername/resova.git
git push -u origin main
```

> [!WARNING]
> Ensure `.env` is in `.gitignore` (it already is). Never push secrets to GitHub.

---

## Files Created for Deployment

| File | Purpose |
|---|---|
| `Dockerfile` | Multi-stage Docker build (builder + runtime) |
| `.dockerignore` | Excludes secrets, tests, and dev files from the Docker context |
| `railway.json` | Railway service configuration (health check, restart policy) |
| `docs/DEPLOY_RAILWAY.md` | This deployment guide |

## Remaining Manual Steps

1. ☐ Create a Railway account and Hobby plan ($5/month).
2. ☐ Create a private GitHub repository and push the code.
3. ☐ Connect the repository to Railway.
4. ☐ Add the PostgreSQL plugin.
5. ☐ Set the environment variables listed above.
6. ☐ Deploy and verify with `curl /health`.

---

## Troubleshooting

### Alembic migration fails
If the database schema is out of sync, check the Railway deploy logs.
Common fix: ensure `DATABASE_URL` points to the PostgreSQL instance.

### Health check returns 503
The database is unreachable. Verify the PostgreSQL plugin is running and
`DATABASE_URL` is correctly injected.

### Scheduler runs duplicate tasks
This cannot happen with `--workers 1`. If you scale to multiple workers in
the future, extract the scheduler into a separate Railway service.

### Container runs out of memory
Increase the memory limit in Railway settings. Resova typically uses <256 MB.
