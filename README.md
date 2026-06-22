# OpenShift Virtualization Coverage Reports

Test coverage report generator for OpenShift Virtualization — queries ReportPortal, collects tests, and generates static HTML reports.

## Overview

This tool:
1. Clones test repositories at specific branches
2. Collects tests via `pytest --collect-only`
3. Queries ReportPortal for test execution results
4. Generates static HTML coverage reports
5. Serves reports via a built-in HTTP server

Deployed as a Kubernetes CronJob (generate) + Deployment (serve), sharing a PersistentVolume.

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

## Deployment (Kubernetes)

The tool ships as a single container image with two commands:
- `generate` — run as a CronJob (default: every 6 hours)
- `serve` — run as a Deployment

Both share a PersistentVolumeClaim for report storage.

```bash
# Apply manifests
kubectl apply -f deploy/pvc.yaml
kubectl apply -f deploy/secret.yaml   # create from secret.yaml.example
kubectl apply -f deploy/deployment.yaml
kubectl apply -f deploy/cronjob.yaml
```

See `deploy/` for Kubernetes manifests.

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
