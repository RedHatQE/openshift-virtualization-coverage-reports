"""CLI entry point for coverage reports.

Provides two commands:
- ``generate`` — clone repos, collect tests, query RP, render reports
- ``serve`` — start HTTP file server for generated reports
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import click

LOGGER = logging.getLogger(__name__)


@click.group(help="OpenShift Virtualization Test Coverage Reports")
@click.option("--verbose", "-v", is_flag=True, help="Enable verbose logging")
def main(verbose: bool) -> None:
    """Top-level CLI group."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
        datefmt="%H:%M:%S",
    )


@main.command(help="Generate coverage reports")
@click.option(
    "--config",
    "config_path",
    type=click.Path(exists=True, path_type=Path),
    default=Path("config.yaml"),
    help="Path to YAML config file",
)
@click.option("--team", type=str, default=None, help="Filter to specific team")
@click.option("--version", "version_filter", type=str, default=None, help="Filter to specific version")
@click.option(
    "--output-format",
    type=click.Choice(choices=["text", "json", "html"]),
    default="html",
    help="Output format",
)
@click.option("--output-dir", type=click.Path(path_type=Path), default=None, help="Override output directory")
@click.option("--dry-run", is_flag=True, default=False, help="Collect tests only, skip RP query")
@click.option("--gate", is_flag=True, default=False, help="Exit non-zero on coverage failures")
def generate(
    config_path: Path,
    team: str | None,
    version_filter: str | None,
    output_format: str,
    output_dir: Path | None,
    dry_run: bool,
    gate: bool,
) -> None:
    """Generate coverage reports by collecting tests and querying RP."""
    from coverage_reports.analysis import collect_analysis_stats
    from coverage_reports.collectors.pytest_collector import PytestCollector
    from coverage_reports.config import AppConfig, ConfigError, load_config
    from coverage_reports.report import (
        VersionReportData,
        render_version_report,
        write_reports,
    )
    from coverage_reports.rp_checker import check_coverage, node_id_to_rp_name
    from coverage_reports.rp_client import RPClient

    try:
        config = load_config(config_path=config_path, require_rp=not dry_run)
    except (ConfigError, FileNotFoundError) as exc:
        click.echo(message=f"Configuration error: {exc}", err=True)
        sys.exit(2)

    effective_output_dir = output_dir or Path(config.server.output_dir)
    git_token = __import__("os").environ.get("GIT_TOKEN")

    all_gate_passed = True
    repo_versions: dict[str, list[VersionReportData]] = {}
    version_htmls: dict[str, str] = {}

    for repo_config in config.repos:
        collector = PytestCollector(git_token=git_token)
        repo_versions[repo_config.name] = []

        for ver_config in repo_config.versions:
            if version_filter and ver_config.version != version_filter:
                continue

            click.echo(message=f"\n{'='*60}")
            click.echo(message=f"Processing {repo_config.name} — version {ver_config.version} (branch: {ver_config.branch})")
            click.echo(message=f"{'='*60}")

            # Collect tests
            try:
                tests = collector.collect(
                    repo_url=repo_config.url,
                    branch=ver_config.branch,
                    exclude_teams=repo_config.exclude_teams,
                )
            except Exception as exc:
                click.echo(message=f"Error collecting tests: {exc}", err=True)
                LOGGER.exception("Test collection failed")
                continue

            if team:
                tests = [test for test in tests if test.team == team]

            automated = [test for test in tests if not test.is_manual and not test.is_quarantined]
            manual = [test for test in tests if test.is_manual]
            quarantined = [test for test in tests if test.is_quarantined]

            click.echo(message=f"  Tests collected: {len(tests)} (automated: {len(automated)}, manual: {len(manual)}, quarantined: {len(quarantined)})")

            if dry_run:
                click.echo(message="  Dry run — skipping RP query")
                continue

            # Query RP
            rp_client = RPClient(
                base_url=config.rp.base_url,
                project=config.rp.project,
                token=config.rp.token,
            )

            bundle_prefix = f"v{ver_config.version}"

            def _progress(current: int, total: int) -> None:
                click.echo(message=f"\r  Fetching items from launch {current}/{total}...", nl=False)
                if current == total:
                    click.echo(message="")

            rp_results, rp_launches = check_coverage(
                rp_client=rp_client,
                bundle_prefix=bundle_prefix,
                max_launches=config.rp.max_launches,
                since_days=config.rp.since_days,
                progress_callback=_progress,
            )

            # Collect analysis per arch
            all_analysis: list = []
            for arch in config.rp.arch:
                arch_analysis = collect_analysis_stats(
                    launches=rp_launches,
                    team_mapping=config.team_mapping,
                    strip_suffixes=config.team_strip_suffixes,
                    arch_filter=arch,
                    max_bundles=config.rp.max_bundles,
                )
                all_analysis.extend(arch_analysis)

            if output_format == "html":
                html_content, ver_data = render_version_report(
                    version=ver_config.version,
                    branch=ver_config.branch,
                    repo_name=repo_config.name,
                    tests=tests,
                    rp_results=rp_results,
                    stale_days=config.rp.stale_days,
                    analysis_records=all_analysis if all_analysis else None,
                    rp_url=config.rp.base_url,
                    rp_project=config.rp.project,
                    team_aliases=config.team_aliases,
                )
                version_htmls[ver_data.report_filename] = html_content
                repo_versions[repo_config.name].append(ver_data)
                click.echo(message=f"  Report generated: {ver_data.report_filename}")

                if gate and ver_data.never_executed > 0:
                    all_gate_passed = False

            elif output_format == "json":
                import json

                # Basic JSON output
                summary = {
                    "version": ver_config.version,
                    "branch": ver_config.branch,
                    "total_tests": len(tests),
                    "rp_results_count": len(rp_results),
                }
                click.echo(message=json.dumps(obj=summary, indent=2))

            else:
                click.echo(message=f"  Total: {len(tests)}, RP results: {len(rp_results)}")

    if output_format == "html" and version_htmls and not dry_run:
        write_reports(
            output_dir=effective_output_dir,
            repo_versions=repo_versions,
            version_htmls=version_htmls,
        )
        click.echo(message=f"\nReports written to: {effective_output_dir}/")

    if gate and not all_gate_passed:
        click.echo(message="\nGATE: FAILED — coverage gaps found", err=True)
        sys.exit(1)

    if gate:
        click.echo(message="\nGATE: PASSED")


@main.command(help="Serve generated reports via HTTP")
@click.option(
    "--config",
    "config_path",
    type=click.Path(exists=True, path_type=Path),
    default=None,
    help="Path to YAML config file (for defaults)",
)
@click.option("--port", type=int, default=None, help="Server port (default: 8080)")
@click.option("--dir", "serve_dir", type=click.Path(exists=True, path_type=Path), default=None, help="Directory to serve")
def serve(
    config_path: Path | None,
    port: int | None,
    serve_dir: Path | None,
) -> None:
    """Start HTTP file server for generated reports."""
    from coverage_reports.server import start_server

    effective_port = port or 8080
    effective_dir = serve_dir

    if config_path:
        from coverage_reports.config import load_config

        config = load_config(config_path=config_path, require_rp=False)
        if not effective_port or effective_port == 8080:
            effective_port = config.server.port
        if not effective_dir:
            effective_dir = Path(config.server.output_dir)

    if not effective_dir:
        effective_dir = Path("/data/reports")

    start_server(directory=effective_dir, port=effective_port)


if __name__ == "__main__":
    main()
