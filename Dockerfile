FROM python:3.12-slim

# Install system dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends git && \
    rm -rf /var/lib/apt/lists/*

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# Copy project files
COPY pyproject.toml uv.lock ./
COPY src/ src/

# Install dependencies
RUN uv sync --frozen --no-dev

# Create data directory
RUN mkdir -p /data/reports

# Default config location
COPY config.example.yaml /app/config.example.yaml

EXPOSE 8080

ENTRYPOINT ["uv", "run", "coverage-reports"]
CMD ["--help"]
