/**
 * Railway Infrastructure as Code (IaC) configuration.
 *
 * Defines the complete project graph:
 * - Managed PostgreSQL database service
 * - Resova FastAPI web service (single replica, pre-deploy migrations, healthcheck)
 *
 * Usage with Railway CLI:
 *   railway config plan   # Preview infrastructure changes
 *   railway config apply  # Apply configuration live to Railway
 */

import { defineRailway, postgres, project, service } from "railway/iac";

export default defineRailway((_ctx) => {
  // 1. Managed PostgreSQL database instance
  const db = postgres("resova-db");

  // 2. Resova core application service
  const app = service("resova", {
    build: {
      dockerfilePath: "Dockerfile",
    },
    start:
      "uvicorn resova.api.app:app --host 0.0.0.0 --port $PORT --workers 1 --log-level info --proxy-headers --forwarded-allow-ips '*'",
    // Decoupled pre-deploy migration: executes once before replicas boot
    preDeploy: "alembic upgrade head",
    // Strictly enforced single replica (scheduler leader safety)
    replicas: 1,
    // HTTP health probe for deployment verification
    healthcheck: "/health",
    healthcheckTimeout: 10,
    restartPolicy: "ON_FAILURE",
    restartPolicyMaxRetries: 5,
  });

  return project("resova", {
    resources: [db, app],
  });
});
