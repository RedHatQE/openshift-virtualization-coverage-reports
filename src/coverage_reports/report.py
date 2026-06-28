"""HTML report rendering.

Generates static HTML coverage reports using Jinja2 templates.
Produces a dashboard index page and per-version detail pages.
"""

from __future__ import annotations

import logging
import operator
import stat
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from jinja2 import Environment, PackageLoader, select_autoescape

from coverage_reports.analysis import (
    LaunchAnalysisRecord,
    aggregate_analysis_by_display_team,
)
from coverage_reports.collectors.base import TestInfo
from coverage_reports.rp_checker import ItemResult

LOGGER = logging.getLogger(__name__)

_STATUS_CSS: dict[str, str] = {
    "PASSED": "passed",
    "FAILED": "failed",
    "NEVER_EXECUTED": "never",
    "STALE": "stale",
    "SKIPPED": "skipped",
    "QUARANTINED": "quarantined",
}

_STATUS_LABELS: dict[str, str] = {
    "PASSED": "PASSED",
    "FAILED": "FAILED",
    "NEVER_EXECUTED": "NEVER EXECUTED",
    "STALE": "STALE",
    "SKIPPED": "SKIPPED",
    "QUARANTINED": "QUARANTINED",
}


@dataclass
class TeamReportData:
    """Per-team report data for template rendering.

    Attributes:
        name: Team name.
        total: Total test count.
        passed: Passed count.
        failed: Failed count.
        skipped: Skipped count.
        never_executed: Never-executed count.
        stale: Stale count.
        quarantined: Quarantined count.
        coverage_pct: Coverage percentage.
        failed_items: Failed test items for rendering.
        stale_items: Stale test items for rendering.
        quarantined_items: Quarantined test items for rendering.
        never_executed_items: Never-executed test items for rendering.
        passed_items: Passed test items for rendering.
        skipped_items: Skipped test items for rendering.
    """

    name: str
    total: int
    passed: int
    failed: int
    skipped: int
    never_executed: int
    stale: int
    quarantined: int
    coverage_pct: float
    failed_items: list[dict[str, Any]]
    stale_items: list[dict[str, Any]]
    quarantined_items: list[dict[str, Any]]
    never_executed_items: list[dict[str, Any]]
    passed_items: list[dict[str, Any]]
    skipped_items: list[dict[str, Any]]


@dataclass
class VersionReportData:
    """Data for a single version's coverage report.

    Attributes:
        version: Version label (e.g., ``4.22``).
        branch: Git branch name.
        repo_name: Repository name.
        total_tests: Total test count.
        passed: Passed count.
        failed: Failed count.
        never_executed: Never-executed count.
        stale: Stale count.
        quarantined: Quarantined count.
        coverage_pct: Coverage percentage.
        report_filename: Name of the generated HTML file.
    """

    version: str
    branch: str
    repo_name: str
    total_tests: int
    passed: int
    failed: int
    never_executed: int
    stale: int
    quarantined: int
    coverage_pct: float
    report_filename: str


_NODE_ID_KEY = operator.itemgetter("node_id")


def _sort_and_group(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Sort items by node_id and group parameterized tests."""
    return _group_parameterized_items(items=sorted(items, key=_NODE_ID_KEY))


def _flatten_grouped_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Flatten grouped items back to individual items for re-aggregation."""
    result = []
    for item in items:
        if item.get("is_group"):
            result.extend(item["group_items"])
        else:
            result.append(item)
    return result


def _group_parameterized_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group parameterized test items by their base name.

    Items whose ``node_id`` contains ``[`` are considered parameterized.
    Groups of 2+ are collapsed into a single group item with metadata;
    singletons and non-parameterized items pass through unchanged.

    Args:
        items: Sorted list of template item dicts.

    Returns:
        New list with parameterized groups collapsed.
    """
    groups: dict[str, list[dict[str, Any]]] = {}

    for item in items:
        node_id: str = item["node_id"]
        bracket = node_id.find("[")
        if bracket != -1:
            base_name = node_id[:bracket]
            groups.setdefault(base_name, []).append(item)

    result: list[dict[str, Any]] = []
    seen_bases: set[str] = set()

    for item in items:
        node_id = item["node_id"]
        bracket = node_id.find("[")
        if bracket != -1:
            base_name = node_id[:bracket]
            if base_name in seen_bases:
                continue
            seen_bases.add(base_name)
            group_items = groups[base_name]
            if len(group_items) >= 2:
                group_item = {
                    **group_items[0],
                    "node_id": base_name,
                    "is_group": True,
                    "group_count": len(group_items),
                    "group_items": group_items,
                    "bundle": None,
                    "last_executed": None,
                    "launch_id": None,
                    "item_id": None,
                    "defect_type": None,
                    "defect_comment": None,
                }
                result.append(group_item)
            else:
                single = {**group_items[0], "is_group": False}
                result.append(single)
        else:
            if "is_group" not in item:
                result.append({**item, "is_group": False})
            else:
                result.append(item)

    return result


def _get_team_from_node_id(node_id: str) -> str:
    """Extract the team name from a pytest node ID.

    Args:
        node_id: Pytest-style node ID.

    Returns:
        Team name, or ``other`` if not determinable.
    """
    parts = node_id.split("/")
    if len(parts) >= 2 and parts[0] == "tests":
        return parts[1]
    return "other"


def _result_to_template_item(
    node_id: str,
    result: ItemResult,
    status_override: str | None = None,
) -> dict[str, Any]:
    """Convert a test result to a template-friendly dict.

    Args:
        node_id: Pytest node ID.
        result: ItemResult from RP.
        status_override: Optional status to override (e.g., ``STALE``).

    Returns:
        Dict with all fields needed by the template.
    """
    status = status_override or result.status.upper()
    return {
        "node_id": node_id,
        "status": status,
        "status_css": _STATUS_CSS.get(status, "never"),
        "status_label": _STATUS_LABELS.get(status, status),
        "bundle": result.bundle,
        "last_executed": result.last_executed[:10] if result.last_executed else "",
        "launch_name": result.launch_name,
        "defect_type": result.defect_type,
        "defect_comment": result.defect_comment,
        "launch_id": result.launch_id,
        "item_id": result.item_id,
    }


def _test_info_to_template_item(test_info: TestInfo) -> dict[str, Any]:
    """Convert a TestInfo to a template-friendly dict.

    Args:
        test_info: TestInfo from collector.

    Returns:
        Dict with fields needed by the template.
    """
    if test_info.is_quarantined:
        status = "QUARANTINED"
    elif test_info.is_manual:
        status = "NEVER_EXECUTED"
    else:
        status = "NEVER_EXECUTED"

    return {
        "node_id": test_info.node_id,
        "status": status,
        "status_css": _STATUS_CSS.get(status, "never"),
        "status_label": _STATUS_LABELS.get(status, status),
        "quarantine_reason": test_info.quarantine_reason,
        "quarantine_jira": test_info.quarantine_jira,
    }


def _build_team_data(
    team_name: str,
    tests: list[TestInfo],
    rp_results: dict[str, ItemResult],
    stale_days: int,
    node_id_to_rp_name_fn: Any,
) -> TeamReportData:
    """Build per-team report data by classifying tests.

    Args:
        team_name: Name of the team.
        tests: List of TestInfo for this team.
        rp_results: RP result map.
        stale_days: Threshold for stale tests.
        node_id_to_rp_name_fn: Function to convert node_id to RP name.

    Returns:
        TeamReportData with all categorized items.
    """
    now = datetime.now(tz=UTC)

    passed_items: list[dict[str, Any]] = []
    failed_items: list[dict[str, Any]] = []
    skipped_items: list[dict[str, Any]] = []
    stale_items: list[dict[str, Any]] = []
    never_executed_items: list[dict[str, Any]] = []
    quarantined_items: list[dict[str, Any]] = []

    for test in tests:
        if test.is_quarantined:
            quarantined_items.append(_test_info_to_template_item(test_info=test))
            continue

        rp_name = node_id_to_rp_name_fn(node_id=test.node_id)
        result = rp_results.get(rp_name)

        if result is None:
            never_executed_items.append(_test_info_to_template_item(test_info=test))
            continue

        # Check staleness
        is_stale = False
        if result.last_executed:
            try:
                executed_time = datetime.fromisoformat(result.last_executed)
                if executed_time.tzinfo is None:
                    executed_time = executed_time.replace(tzinfo=UTC)
                age_days = (now - executed_time).days
                if age_days > stale_days:
                    is_stale = True
            except (ValueError, TypeError):
                pass

        if is_stale:
            stale_items.append(_result_to_template_item(
                node_id=test.node_id,
                result=result,
                status_override="STALE",
            ))
            continue

        status_upper = result.status.upper()
        item = _result_to_template_item(node_id=test.node_id, result=result)

        if status_upper == "PASSED":
            passed_items.append(item)
        elif status_upper == "FAILED":
            failed_items.append(item)
        elif status_upper == "SKIPPED":
            skipped_items.append(item)

    total = len(tests)
    passed_count = len(passed_items)
    failed_count = len(failed_items)
    skipped_count = len(skipped_items)
    executed = passed_count + failed_count + skipped_count
    coverage_pct = (executed / total * 100) if total > 0 else 0.0

    return TeamReportData(
        name=team_name,
        total=total,
        passed=passed_count,
        failed=failed_count,
        skipped=skipped_count,
        never_executed=len(never_executed_items),
        stale=len(stale_items),
        quarantined=len(quarantined_items),
        coverage_pct=round(coverage_pct, 1),
        failed_items=_sort_and_group(items=failed_items),
        stale_items=_sort_and_group(items=stale_items),
        quarantined_items=_sort_and_group(items=quarantined_items),
        never_executed_items=_sort_and_group(items=never_executed_items),
        passed_items=_sort_and_group(items=passed_items),
        skipped_items=_sort_and_group(items=skipped_items),
    )


def _get_jinja_env() -> Environment:
    """Create the Jinja2 template environment.

    Returns:
        Configured Jinja2 Environment.
    """
    return Environment(
        loader=PackageLoader(package_name="coverage_reports", package_path="templates"),
        autoescape=select_autoescape(enabled_extensions=["html"]),
    )


def render_version_report(
    version: str,
    branch: str,
    repo_name: str,
    tests: list[TestInfo],
    rp_results: dict[str, ItemResult],
    stale_days: int,
    analysis_records: list[LaunchAnalysisRecord] | None = None,
    rp_url: str = "",
    rp_project: str = "",
    team_aliases: dict[str, str] | None = None,
) -> tuple[str, VersionReportData]:
    """Render an HTML report for a single version.

    Args:
        version: Version label.
        branch: Git branch name.
        repo_name: Repository name.
        tests: All collected TestInfo for this version.
        rp_results: RP result map.
        stale_days: Stale threshold in days.
        analysis_records: Optional failure analysis records.
        rp_url: RP base URL for log links.
        rp_project: RP project name for log links.
        team_aliases: Optional mapping of directory team names to target team names.

    Returns:
        Tuple of (html_content, version_summary_data).
    """
    from coverage_reports.rp_checker import node_id_to_rp_name

    env = _get_jinja_env()
    template = env.get_template(name="version.html")

    # Group tests by team
    tests_by_team: dict[str, list[TestInfo]] = {}
    for test in tests:
        team = test.team or _get_team_from_node_id(node_id=test.node_id)
        if team_aliases and team in team_aliases:
            team = team_aliases[team]
        tests_by_team.setdefault(team, []).append(test)

    team_data_list: list[TeamReportData] = []
    for team_name in sorted(tests_by_team):
        team_tests = tests_by_team[team_name]
        team_data = _build_team_data(
            team_name=team_name,
            tests=team_tests,
            rp_results=rp_results,
            stale_days=stale_days,
            node_id_to_rp_name_fn=node_id_to_rp_name,
        )
        team_data_list.append(team_data)

    # Aggregate summary
    total_tests = sum(td.total for td in team_data_list)
    total_passed = sum(td.passed for td in team_data_list)
    total_failed = sum(td.failed for td in team_data_list)
    total_skipped = sum(td.skipped for td in team_data_list)
    total_never = sum(td.never_executed for td in team_data_list)
    total_stale = sum(td.stale for td in team_data_list)
    total_quarantined = sum(td.quarantined for td in team_data_list)
    total_executed = total_passed + total_failed + total_skipped
    coverage_pct = (total_executed / total_tests * 100) if total_tests > 0 else 0.0

    # Collect all items for the "All Teams" tab
    all_failed = []
    all_stale = []
    all_quarantined = []
    all_never = []
    all_passed = []
    all_skipped = []
    all_gating: list[dict[str, Any]] = []

    for td in team_data_list:
        all_failed.extend(_flatten_grouped_items(items=td.failed_items))
        all_stale.extend(_flatten_grouped_items(items=td.stale_items))
        all_quarantined.extend(_flatten_grouped_items(items=td.quarantined_items))
        all_never.extend(_flatten_grouped_items(items=td.never_executed_items))
        all_passed.extend(_flatten_grouped_items(items=td.passed_items))
        all_skipped.extend(_flatten_grouped_items(items=td.skipped_items))

    # Apply parameterized grouping to the aggregated lists
    all_failed = _sort_and_group(items=all_failed)
    all_stale = _sort_and_group(items=all_stale)
    all_quarantined = _sort_and_group(items=all_quarantined)
    all_never = _sort_and_group(items=all_never)
    all_passed = _sort_and_group(items=all_passed)
    all_skipped = _sort_and_group(items=all_skipped)

    # Identify gating gaps (never-executed or stale gating tests)
    gating_node_ids = {test.node_id for test in tests if test.is_gating}
    for item in all_never:
        node_id = item["node_id"]
        if item.get("is_group"):
            # Check if any sub-item is a gating test
            for sub in item["group_items"]:
                if sub["node_id"] in gating_node_ids:
                    gating_item = {**item, "status": "NEVER_EXECUTED", "status_css": "never", "status_label": "NEVER EXECUTED"}
                    all_gating.append(gating_item)
                    break
        elif node_id in gating_node_ids:
            gating_item = {**item, "status": "NEVER_EXECUTED", "status_css": "never", "status_label": "NEVER EXECUTED"}
            all_gating.append(gating_item)
    for item in all_stale:
        node_id = item["node_id"]
        if item.get("is_group"):
            for sub in item["group_items"]:
                if sub["node_id"] in gating_node_ids:
                    gating_item = {**item, "status": "STALE", "status_css": "stale", "status_label": "STALE"}
                    all_gating.append(gating_item)
                    break
        elif node_id in gating_node_ids:
            gating_item = {**item, "status": "STALE", "status_css": "stale", "status_label": "STALE"}
            all_gating.append(gating_item)

    all_gating = _sort_and_group(items=all_gating)

    # Build aggregated analysis records for display
    display_analysis: list[LaunchAnalysisRecord] = []
    if analysis_records:
        grouped = aggregate_analysis_by_display_team(records=analysis_records)
        for _team, recs in sorted(grouped.items()):
            display_analysis.extend(recs)
        # Add total rows
        total_by_tier: dict[str, dict[str, int]] = {}
        for rec in display_analysis:
            if rec.tier not in total_by_tier:
                total_by_tier[rec.tier] = {
                    "launches": 0, "total": 0, "passed": 0, "failed": 0,
                    "skipped": 0, "analyzed": 0, "to_investigate": 0,
                    "product_bug": 0, "automation_bug": 0, "system_issue": 0, "no_defect": 0,
                }
            for counter_field in total_by_tier[rec.tier]:
                total_by_tier[rec.tier][counter_field] += getattr(rec, counter_field)
        for tier, counters in sorted(total_by_tier.items()):
            display_analysis.append(LaunchAnalysisRecord(
                bundle="ALL",
                team="TOTAL",
                display_team="TOTAL",
                tier=tier,
                arch="all",
                **counters,
            ))

    generated_at = datetime.now(tz=UTC).strftime("%Y-%m-%d %H:%M UTC")
    report_filename = f"report_{version}.html"

    # Count manual tests
    total_manual = sum(1 for test in tests if test.is_manual)
    total_automated = total_tests - total_manual - total_quarantined

    # Build per-team gating items and manual items
    gating_by_team: dict[str, list[dict[str, Any]]] = {}
    manual_by_team: dict[str, list[dict[str, Any]]] = {}
    for item in all_gating:
        team = _get_team_from_node_id(node_id=item["node_id"])
        if team_aliases and team in team_aliases:
            team = team_aliases[team]
        gating_by_team.setdefault(team, []).append(item)
    for test in tests:
        if test.is_manual:
            team = test.team or _get_team_from_node_id(node_id=test.node_id)
            if team_aliases and team in team_aliases:
                team = team_aliases[team]
            manual_by_team.setdefault(team, []).append(_test_info_to_template_item(test_info=test))

    html = template.render(
        version=version,
        branch=branch,
        repo_name=repo_name,
        generated_at=generated_at,
        stale_days=stale_days,
        summary={
            "total_tests": total_tests,
            "automated": total_automated,
            "manual": total_manual,
            "executed": total_executed,
            "passed": total_passed,
            "failed": total_failed,
            "skipped": total_skipped,
            "never_executed": total_never,
            "stale": total_stale,
            "quarantined": total_quarantined,
            "coverage_pct": round(coverage_pct, 1),
        },
        teams=[{
            "name": td.name,
            "total": td.total,
            "passed": td.passed,
            "failed": td.failed,
            "skipped": td.skipped,
            "never_executed": td.never_executed,
            "stale": td.stale,
            "quarantined": td.quarantined,
            "coverage_pct": td.coverage_pct,
            "gating_items": _sort_and_group(items=gating_by_team.get(td.name, [])),
            "failed_items": td.failed_items,
            "stale_items": td.stale_items,
            "quarantined_items": td.quarantined_items,
            "manual_items": _sort_and_group(items=manual_by_team.get(td.name, [])),
            "never_executed_items": td.never_executed_items,
            "passed_items": td.passed_items,
            "skipped_items": td.skipped_items,
        } for td in team_data_list],
        analysis_records=display_analysis,
        rp_url=rp_url.rstrip("/"),
        rp_project=rp_project,
    )

    version_data = VersionReportData(
        version=version,
        branch=branch,
        repo_name=repo_name,
        total_tests=total_tests,
        passed=total_passed,
        failed=total_failed,
        never_executed=total_never,
        stale=total_stale,
        quarantined=total_quarantined,
        coverage_pct=round(coverage_pct, 1),
        report_filename=report_filename,
    )

    return html, version_data


def render_index(
    repo_versions: dict[str, list[VersionReportData]],
) -> str:
    """Render the dashboard index page.

    Args:
        repo_versions: Dict mapping repo name to list of version summaries.

    Returns:
        HTML content for the index page.
    """
    env = _get_jinja_env()
    template = env.get_template(name="index.html")
    generated_at = datetime.now(tz=UTC).strftime("%Y-%m-%d %H:%M UTC")

    repos = []
    for repo_name, versions in repo_versions.items():
        repos.append({
            "name": repo_name,
            "versions": [
                {
                    "version": ver.version,
                    "branch": ver.branch,
                    "total_tests": ver.total_tests,
                    "passed": ver.passed,
                    "failed": ver.failed,
                    "never_executed": ver.never_executed,
                    "quarantined": ver.quarantined,
                    "coverage_pct": ver.coverage_pct,
                    "report_path": ver.report_filename,
                }
                for ver in versions
            ],
        })

    return template.render(repos=repos, generated_at=generated_at)


def _write_group_writable(path: Path, content: str) -> None:
    """Write text content to a file and ensure it is group-writable.

    On OpenShift, containers run as a random UID in GID 0. Setting
    group-write permission allows subsequent container runs with a
    different UID (but same GID 0) to overwrite the file.

    Before writing, any existing file is unlinked first. This handles
    the migration case where old files were written without group-write
    permission and are owned by a different UID. Unlinking works because
    the parent directory is group-writable, so any GID-0 user can remove
    entries from it regardless of file ownership.

    Args:
        path: File path to write.
        content: Text content to write.
    """
    path.unlink(missing_ok=True)
    path.write_text(data=content, encoding="utf-8")
    try:
        path.chmod(path.stat().st_mode | stat.S_IWGRP)
    except OSError:
        LOGGER.warning(f"Could not set group-writable permission on {path}")


def write_reports(
    output_dir: Path,
    repo_versions: dict[str, list[VersionReportData]],
    version_htmls: dict[str, str],
) -> None:
    """Write all generated HTML reports to the output directory.

    Args:
        output_dir: Directory to write reports into.
        repo_versions: Dict mapping repo name to version summaries.
        version_htmls: Dict mapping report filename to HTML content.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    index_html = render_index(repo_versions=repo_versions)
    index_path = output_dir / "index.html"
    _write_group_writable(path=index_path, content=index_html)
    LOGGER.info(f"Wrote dashboard index to {index_path}")

    for filename, html_content in version_htmls.items():
        report_path = output_dir / filename
        _write_group_writable(path=report_path, content=html_content)
        LOGGER.info(f"Wrote version report to {report_path}")
