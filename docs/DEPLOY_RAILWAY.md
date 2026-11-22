# Resova — Railway Production Deployment Guide

> [!IMPORTANT]
> This guide outlines everything prepared for production deployment on Railway.
> No accounts have been created and no fees have been charged. Follow these instructions when you are ready to deploy.

---

## 1. System Architecture on Railway

```
┌───────────────────────────────────────────────────────────┐
│                      Railway Project                      │
│                                                           │
│  ┌──────────────────────────┐     ┌────────────────────┐  │
│  │      resova (Web)        │     │  PostgreSQL Plugin │  │
│  │   FastAPI + Uvicorn      │────▶│    (Managed DB)    │  │
│  │   + PollerScheduler      │     │  Automated Backups │  │
│  │   (Replicas: 1 default)  │     │  SSL Connections   │  │
│  └──────────────────────────┘     └────────────────────┘  │
│       │               ▲                                   │
│       │               │ preDeployCommand:                 │
│       │               │ alembic upgrade head              │
│       │               │ (runs once before instances boot) │
│       │                                                   │
│       │ Health Check Probe:                               │
│       │ GET /health -> {"status": "ok", ...}              │
└───────┼───────────────────────────────────────────────────┘
        │
        ├──▶ Telegram Bot API ──▶ Freelancer's Telegram (User ID)
        └──▶ OpenAI API (gpt-4o-mini) ──▶ Opportunity Evaluation
```

---

## 2. Key Production Safeguards

### A. Database & URL Normalization
- **Managed PostgreSQL:** Provides persistent storage with automatic daily backups and SSL encryption.
- **Dialect Normalization:** Railway commonly provides URLs prefixed with `postgres://...`. Resova's configuration and database session layers automatically normalize `postgres://` to `postgresql://` so that SQLAlchemy 2.x and `psycopg[binary]` connect smoothly without syntax errors.
- **Connection Health:** `pool_pre_ping=True` and connection recycling (`pool_recycle=300`) prevent dropped connections from causing HTTP 500 errors.

### B. Safe Single-Run Migrations (`preDeploy`)
- Migrations are decoupled from container startup.
- In both `.railway/railway.ts` (`preDeploy: "alembic upgrade head"`) and `railway.json` (`preDeployCommand`), migrations execute once as an isolated pre-deploy step before any application replica starts serving traffic.
- If a migration fails, the deployment aborts safely, protecting active traffic.

### C. Distributed Scheduler Lock (PostgreSQL Advisory Lock)
- To prevent scheduled tasks from firing multiple times if multiple replicas or workers exist, `PollerScheduler` implements a **session-level PostgreSQL Advisory Lock** (`RESOVA_SCHEDULER_LOCK_ID = 849201948201`).
- In addition, both `.railway/railway.ts` (`replicas: 1`) and `railway.json` (`numReplicas: 1`) enforce a single replica default.
- Only the instance that acquires the lock acts as the **active scheduler leader**. Other replicas stand by.
- If the active container restarts, PostgreSQL immediately releases the advisory lock, allowing a standby instance to take over leadership.

### D. Zero-Data Scraping & Listing Poller Disabled
- `ENABLE_LISTING_POLLER=false` and `LISTING_POLLER_ACK=false` are enforced in production settings.
- No public scraping or automatic proposal submission can occur.

---

## 3. Infrastructure as Code: `.railway/railway.ts` (Single Source of Truth)

Railway services use **Infrastructure as Code (IaC)** via TypeScript in `.railway/railway.ts`. Legacy `railway.json` has been decommissioned to avoid dual-configuration conflicts.

* **Project Graph in `.railway/railway.ts`:**
  Defines the complete project graph (PostgreSQL resource + Resova web service) with dynamic private network binding:
  ```typescript
  import { defineRailway, postgres, project, service } from "railway/iac";

  export default defineRailway(() => {
    const db = postgres("resova-db");

    const app = service("resova", {
      build: {
        builder: "DOCKERFILE",
        dockerfilePath: "Dockerfile",
      },
      preDeploy: "alembic upgrade head",
      replicas: 1,
      healthcheck: "/health",
      healthcheckTimeout: 10,
      restartPolicy: "ON_FAILURE",
      restartPolicyMaxRetries: 5,
      env: {
        APP_ENV: "production",
        LOG_LEVEL: "INFO",
        DATABASE_URL: db.env.DATABASE_URL, // Dynamic private networking URL
        PROFILE_PATH: "config/profile.yaml",
        ENABLE_AI_ANALYSIS: "true",
        OPENAI_MODEL: "gpt-4o-mini",
        DAILY_AI_ANALYSIS_LIMIT: "100",
        ENABLE_TELEGRAM: "true",
        ENABLE_EMAIL_INGESTION: "false",
        ENABLE_LISTING_POLLER: "false",
        LISTING_POLLER_ACK: "false",
      },
    });

    return project("resova", {
      resources: [db, app],
    });
  });
  ```
  Manage via Railway CLI / npm scripts:
  ```bash
  npm run plan   # Or: railway config plan (preview planned infrastructure changes)
  npm run apply  # Or: railway config apply (apply configuration live to Railway)
  ```

---

## 4. Cost Analysis & Resource Requirements

### Railway Costs (Hobby Plan)
* **Base Plan Price:** **$5.00 / month** (minimum monthly subscription).
* **Included Credits:** Includes **$5.00 of usage credit** per month.
* **Estimated Metered Consumption:**
  * **Application Container:** 1 instance @ ~0.1 vCPU average (~$2.00/mo) and ~200MB RAM (~$2.00/mo).
  * **PostgreSQL Storage:** < 100 MB (~$0.02/mo).
  * **Egress Bandwidth:** < 1 GB (~$0.05/mo).
  * **Estimated Usage:** ~$4.07 / month.
* **Net Railway Bill:** Since usage is within the $5.00 credit, the expected charge is the flat **$5.00 / month**.

### OpenAI API Costs (`gpt-4o-mini`)
* **Pricing Rates:** $0.15 per 1M input tokens, $0.60 per 1M output tokens.
* **Token Assumptions per Opportunity Analysis:**
  * **Input:** ~800 tokens (project title, description, skills from client profile).
  * **Output:** ~250 tokens (structured JSON evaluation with score, band, rationale).
  * **Cost per Analysis:** `(800 × $0.00000015) + (250 × $0.00000060) ≈ $0.00027` (less than 3 hundredths of a cent).
* **Monthly Estimates:**
  * At 30 opportunities/day (~900/month): **~$0.24 / month**.
  * At max limit of 100 opportunities/day (~3,000/month): **~$0.81 / month**.
* **Proposal Draft Generation (Optional):** Only generated for high matches (~5/day @ ~1,200 tokens) adds **~$0.15 / month**.
* **Total Expected OpenAI Bill:** **~$0.50 – $1.20 / month**.

### Summary of Total Monthly Cost
| Service | Expected Monthly Cost |
|---|---|
| **Railway (Hobby Plan)** | $5.00 (flat) |
| **OpenAI API (gpt-4o-mini)** | ~$0.80 |
| **Telegram Bot API** | $0.00 (free) |
| **Total Estimated Operating Cost** | **~$5.80 / month** |

---

## 5. Production Environment Variables Reference

Configure these in Railway under **Project → Service → Variables**:

| Variable | Recommended Production Value | Required | Notes |
|---|---|---|---|
| `APP_ENV` | `production` | Yes | Disables public `/docs` and enables production optimizations |
| `LOG_LEVEL` | `INFO` | Yes | Redacted logging |
| `DATABASE_URL` | *(Injected by Railway)* | Yes | Railway PostgreSQL plugin provides this |
| `PROFILE_PATH` | `config/profile.yaml` | Yes | Bundled in container |
| `ENABLE_AI_ANALYSIS` | `true` | Yes | Evaluates matches with OpenAI |
| `OPENAI_API_KEY` | `sk-proj-...` | Yes | **Secret** (Your OpenAI API Key) |
| `OPENAI_MODEL` | `gpt-4o-mini` | Yes | Standard cost-effective model |
| `DAILY_AI_ANALYSIS_LIMIT` | `100` | Yes | Daily budget guardrail |
| `ENABLE_TELEGRAM` | `true` | Yes | Sends instant alerts |
| `TELEGRAM_BOT_TOKEN` | `8982618527:AAH...` | Yes | **Secret** (Bot token from @BotFather) |
| `TELEGRAM_ALLOWED_USER_ID` | `5975310413` | Yes | **Secret** (Single user security ID) |
| `ENABLE_EMAIL_INGESTION` | `false` | Yes | Kept disabled until mailbox is set up |
| `ENABLE_LISTING_POLLER` | `false` | Yes | **Must be false** |
| `LISTING_POLLER_ACK` | `false` | Yes | **Must be false** |

---

## 6. Deployment Step-by-Step (When Ready)

### Option A: Using Railway CLI (Infrastructure as Code)
```bash
# 1. Login to Railway
railway login

# 2. Preview infrastructure plan from .railway/railway.ts
railway config plan

# 3. Apply infrastructure (provisions PostgreSQL + Resova service)
railway config apply
```

### Option B: Using Railway Web Dashboard
### Step 1: Create a Private GitHub Repository
```bash
# Verify working tree is clean
git status

# Add your private remote and push
git remote add origin https://github.com/<your-username>/resova.git
git branch -M main
git push -u origin main
```
> [!NOTE]
> `.env` is listed in `.gitignore` and `.dockerignore`. It will never be committed or included in Docker builds.

### Step 2: Deploy to Railway
1. Sign in to [Railway](https://railway.app/).
2. Select **+ New Project → Deploy from GitHub repo**.
3. Choose your private `resova` repository.
4. Click **+ New → Database → PostgreSQL** to add the managed database.
5. In the `resova` service settings:
   - Under **Variables**, add the environment variables listed in Section 4.
   - Verify `DATABASE_URL` points to the attached PostgreSQL plugin.
6. Trigger deployment: Railway will run `alembic upgrade head`, start the container, and verify `/health`.

### Step 3: Verify the Live Service
```bash
curl https://<your-railway-domain>.up.railway.app/health
# Expected JSON response:
# {"status":"ok","version":"0.1.0","database":"ok","environment":"production"}
```
