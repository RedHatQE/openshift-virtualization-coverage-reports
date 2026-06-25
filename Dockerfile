FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder

WORKDIR /app

# Copy project files
COPY pyproject.toml uv.lock README.md ./
COPY src/ src/

# Create venv and install dependencies
RUN uv sync --frozen --no-dev

# Production stage
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

WORKDIR /app

# Install git (runtime: `generate` clones repos and runs `uv sync` + `uv run pytest`)
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user, data directory, and set permissions
# OpenShift runs containers as a random UID in the root group (GID 0)
RUN useradd --create-home --shell /bin/bash -g 0 appuser \
    && mkdir -p /data/reports \
    && chown appuser:0 /data/reports \
    && chmod -R g+w /data/reports

# Copy the virtual environment from builder
COPY --chown=appuser:0 --from=builder /app/.venv /app/.venv

# Copy project files needed by uv
COPY --chown=appuser:0 --from=builder /app/pyproject.toml /app/uv.lock /app/README.md ./

# Copy source code
COPY --chown=appuser:0 --from=builder /app/src /app/src

# Make /app group-writable for OpenShift compatibility
RUN chmod -R g+w /app

# Switch to non-root user for runtime
USER appuser

ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1

EXPOSE 8080

# --no-sync prevents uv from attempting to modify the venv at runtime.
# Required for OpenShift where containers run as an arbitrary UID.
CMD ["uv", "run", "--no-sync", "coverage-reports", "serve"]
