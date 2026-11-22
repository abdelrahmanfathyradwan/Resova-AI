/**
 * Railway Infrastructure as Code (IaC) configuration.
 *
 * Single Source of Truth for Resova's Railway project graph:
 * - Managed PostgreSQL database service (`resova-db`)
 * - Resova FastAPI web service (`resova`)
 * - Dynamic linkage: DATABASE_URL -> db.env.DATABASE_URL (Railway private networking)
 * - Single replica constraint (replicas: 1)
 * - Pre-deploy migration (preDeploy: "alembic upgrade head")
 * - Health check endpoint (healthcheck: "/health")
 *
 * Usage with Railway CLI:
 *   railway config plan   # Preview planned infrastructure changes
 *   railway config apply  # Apply configuration live to Railway
 */

import { defineRailway, postgres, project, service } from "railway/iac";

export default defineRailway(() => {
  // 1. Managed PostgreSQL database instance
  const db = postgres("resova-db");

  // 2. Resova core application service
  const app = service("resova", {
    build: {
      builder: "DOCKERFILE",
      dockerfilePath: "Dockerfile",
    },
    // Decoupled pre-deploy migration: executes once before replicas boot
    preDeploy: "alembic upgrade head",
    // Strictly enforced single replica (scheduler leader safety)
    replicas: 1,
    // HTTP health probe for deployment verification
    healthcheck: "/health",
    healthcheckTimeout: 10,
    restartPolicy: "ON_FAILURE",
    restartPolicyMaxRetries: 5,
    // Dynamic binding to PostgreSQL via Railway private networking
    env: {
      APP_ENV: "production",
      LOG_LEVEL: "INFO",
      DATABASE_URL: db.env.DATABASE_URL,
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
