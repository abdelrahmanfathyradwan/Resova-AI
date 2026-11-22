# ── Multi-stage Dockerfile for Resova ──
# Stage 1: builder (compile packages and install dependencies)
FROM python:3.12-slim AS builder

WORKDIR /build

# Install build dependencies for compiling psycopg2 and C extensions
RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc libpq-dev && \
    rm -rf /var/lib/apt/lists/*

# Create dedicated virtual environment
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copy project definition and source code for clean installation
COPY pyproject.toml README.md ./
COPY src/ src/

RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir ".[prod]"

# ── Stage 2: runtime (minimal production image) ──
FROM python:3.12-slim AS runtime

# Runtime dependency: libpq5 for PostgreSQL; curl for container health check
RUN apt-get update && \
    apt-get install -y --no-install-recommends libpq5 curl && \
    rm -rf /var/lib/apt/lists/*

# Security: run as unprivileged user
RUN groupadd --gid 1000 resova && \
    useradd --uid 1000 --gid resova --create-home resova

# Copy pre-built virtual environment from builder stage
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Copy application assets with appropriate permissions
COPY --chown=resova:resova src/ src/
COPY --chown=resova:resova config/ config/
COPY --chown=resova:resova migrations/ migrations/
COPY --chown=resova:resova alembic.ini pyproject.toml README.md ./

USER resova

# Default port (Railway injects dynamic PORT at runtime)
ENV PORT=8000

EXPOSE ${PORT}

# Internal container health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:${PORT}/health || exit 1

# Production start command (migrations are run separately via preDeployCommand)
CMD ["sh", "-c", "uvicorn resova.api.app:app --host 0.0.0.0 --port ${PORT} --workers 1 --log-level info --proxy-headers --forwarded-allow-ips '*'"]
