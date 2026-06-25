# OpenShift Virtualization Coverage Reports

Test coverage report generator for OpenShift Virtualization — queries ReportPortal, collects tests, and generates static HTML reports.

## Overview

This tool:
1. Clones test repositories at specific branches
2. Collects tests via `pytest --collect-only`
3. Queries ReportPortal for test execution results
4. Generates static HTML coverage reports
5. Serves reports via a built-in HTTP server

## Quickstart

### Prerequisites
- Python 3.12+
- [uv](https://docs.astral.sh/uv/)
- Git
- Access to a ReportPortal instance

### Installation

```bash
git clone https://github.com/RedHatQE/openshift-virtualization-coverage-reports.git
cd openshift-virtualization-coverage-reports
uv sync
```

### Configuration

1. Copy the example config:
   ```bash
   cp config.example.yaml config.yaml
   ```

2. Set environment variables:
   ```bash
   export RP_URL="https://reportportal.example.com"
   export RP_PROJECT="your-project"
   export RP_TOKEN="your-api-token"
   # Optional: for private repos
   export GIT_TOKEN="your-git-token"
   ```

3. Edit `config.yaml` to match your setup (repos, versions, teams).

### Usage

#### Generate Reports
```bash
# Generate HTML reports using config
uv run coverage-reports generate --config config.yaml

# Filter by team
uv run coverage-reports generate --config config.yaml --team network

# Filter by version
uv run coverage-reports generate --config config.yaml --version 4.22

# Dry run (collect tests only, no RP query)
uv run coverage-reports generate --config config.yaml --dry-run

# Gate mode (exit non-zero on failures)
uv run coverage-reports generate --config config.yaml --gate

# Custom output directory
uv run coverage-reports generate --config config.yaml --output-dir ./my-reports
```

#### Serve Reports
```bash
# Serve generated reports
uv run coverage-reports serve --dir ./reports --port 8080
```

## Docker

### Build

```bash
docker build -t coverage-reports .
```

### Run

#### Generate Reports

```bash
docker run --rm \
  -e RP_URL="https://reportportal.example.com" \
  -e RP_PROJECT="your-project" \
  -e RP_TOKEN="your-api-token" \
  -v $(pwd)/config.yaml:/app/config.yaml:ro \
  -v $(pwd)/reports:/data/reports \
  coverage-reports \
  uv run --no-sync coverage-reports generate --config config.yaml
```

> Add `-e GIT_TOKEN="..."` if accessing private repositories.

#### Serve Reports

```bash
docker run --rm -p 8080:8080 \
  -v $(pwd)/reports:/data/reports:ro \
  coverage-reports
```

The default command serves reports on port 8080. Mount your generated reports directory to `/data/reports`.

#### Generate and Serve

```bash
# Generate reports first
docker run --rm \
  -e RP_URL="$RP_URL" \
  -e RP_PROJECT="$RP_PROJECT" \
  -e RP_TOKEN="$RP_TOKEN" \
  -v $(pwd)/config.yaml:/app/config.yaml:ro \
  -v $(pwd)/reports:/data/reports \
  coverage-reports \
  uv run --no-sync coverage-reports generate --config config.yaml

# Then serve them
docker run --rm -d -p 8080:8080 \
  --name coverage-server \
  -v $(pwd)/reports:/data/reports:ro \
  coverage-reports
```

To stop the server: `docker stop coverage-server`

## Config Reference

See `config.example.yaml` for the full structure. Key sections:

| Section | Description |
|---------|-------------|
| `repos` | Test repositories to clone and collect from |
| `repos[].versions` | Branch-to-version mappings |
| `rp` | ReportPortal query settings (since_days, max_bundles, stale_days, arch) |
| `team_mapping` | RP TEAM attribute → display team name mapping |
| `team_strip_suffixes` | Suffixes to strip during team normalization |
| `server` | HTTP server settings (port, output_dir) |

### Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `RP_URL` | Yes | ReportPortal base URL |
| `RP_PROJECT` | Yes | ReportPortal project name |
| `RP_TOKEN` | Yes | ReportPortal API token |
| `GIT_TOKEN` | No | Git token for private repo access |

## CLI Reference

### `coverage-reports generate`

Generate coverage reports by collecting tests and querying ReportPortal.

| Flag | Default | Description |
|------|---------|-------------|
| `--config` | `config.yaml` | Path to YAML config file |
| `--team` | all | Filter to specific team |
| `--version` | all | Filter to specific version |
| `--output-format` | `html` | Output format: text, json, html |
| `--output-dir` | from config | Override output directory |
| `--dry-run` | false | Collect tests only, skip RP query |
| `--gate` | false | Exit non-zero on coverage failures |

### `coverage-reports serve`

Start HTTP server to serve generated reports.

| Flag | Default | Description |
|------|---------|-------------|
| `--port` | from config/8080 | Server port |
| `--dir` | from config | Directory to serve |

## Architecture

```
┌─────────────┐     ┌──────────────┐     ┌─────────────┐
│  Config YAML │────▶│   Collectors  │────▶│  RP Client   │
│  + Env Vars  │     │ (pytest etc)  │     │  (API calls) │
└─────────────┘     └──────────────┘     └──────┬──────┘
                                                 │
                    ┌──────────────┐     ┌───────▼──────┐
                    │ HTML Reports │◀────│  RP Checker   │
                    │  (Jinja2)    │     │  + Analysis   │
                    └──────┬───────┘     └──────────────┘
                           │
                    ┌──────▼───────┐
                    │  HTTP Server  │
                    │  (serve cmd)  │
                    └──────────────┘
```

## Development

```bash
# Install dev dependencies
uv sync --group dev

# Run tests
uv run pytest

# Run with coverage
uv run pytest --cov=coverage_reports
```

## License

Apache-2.0
