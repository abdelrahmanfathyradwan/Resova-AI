# ── Multi-stage Dockerfile for Resova ──
# Stage 1: build (install deps into a venv)
# Stage 2: runtime (copy only what's needed)

FROM python:3.12-slim AS builder

WORKDIR /build

# Install build tools needed for binary wheels (psycopg2, etc.)
RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc libpq-dev && \
    rm -rf /var/lib/apt/lists/*

# Create a venv so we can copy it cleanly to the runtime image.
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Install Python dependencies first (layer caching).
COPY pyproject.toml ./
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir ".[prod]"

# ── Runtime stage ───────────────────────────────────────────────────────
FROM python:3.12-slim AS runtime

# libpq is needed at runtime for psycopg2; curl for Railway health checks.
RUN apt-get update && \
    apt-get install -y --no-install-recommends libpq5 curl && \
    rm -rf /var/lib/apt/lists/*

# Non-root user for security.
RUN groupadd --gid 1000 resova && \
    useradd --uid 1000 --gid resova --create-home resova

# Copy the pre-built venv from the builder stage.
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Copy application code.
COPY src/ src/
COPY config/ config/
COPY migrations/ migrations/
COPY alembic.ini ./

# Ensure the app package is installed in the venv.
COPY pyproject.toml README.md ./
RUN pip install --no-cache-dir --no-deps -e .

# Switch to non-root user.
USER resova

# Railway injects PORT; default to 8000 for local testing.
ENV PORT=8000

EXPOSE ${PORT}

# Health check: Railway also uses its own probe, but this gives Docker-level checks.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:${PORT}/health || exit 1

# Entry point: run migrations then start the server.
CMD alembic upgrade head && \
    uvicorn resova.api.app:app \
        --host 0.0.0.0 \
        --port ${PORT} \
        --workers 1 \
        --log-level info \
        --proxy-headers \
        --forwarded-allow-ips '*'
