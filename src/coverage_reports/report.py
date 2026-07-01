"""HTML report rendering.

Generates static HTML coverage reports using Jinja2 templates.
Produces a dashboard index page and per-version detail pages.
"""

from __future__ import annotations

import logging
import operator
import re
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
        manual: Manual (unimplemented STD) test count.
        coverage_pct: Coverage percentage.
        failed_items: Failed test items for rendering.
        stale_items: Stale test items for rendering.
        quarantined_items: Quarantined test items for rendering.
        manual_items: Manual test items for rendering.
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
    manual: int
    coverage_pct: float
    failed_items: list[dict[str, Any]]
    stale_items: list[dict[str, Any]]
    quarantined_items: list[dict[str, Any]]
    manual_items: list[dict[str, Any]]
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


def _extract_urls(text: str | None) -> list[str]:
    """Extract URLs from text.

    Args:
        text: Input text possibly containing URLs, or None.

    Returns:
        List of extracted URL strings.
    """
    if not text:
        return []
    raw = re.findall(r'https?://[^\s<>"\'\)\]]+', text)
    return [url.rstrip(".,;:") for url in raw]


def _sort_and_group(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Sort items by node_id and group parameterized tests."""
    return _group_parameterized_items(items=sorted(items, key=_NODE_ID_KEY))


def _parse_param_dimensions(param_str: str) -> list[str] | None:
    """Parse multi-dimensional parameter string into dimension values.

    Multi-dim params use ``#value1#-#value2#`` format where each dimension
    value is wrapped in ``#hash#`` delimiters separated by ``-``.

    Args:
        param_str: The parameter portion without surrounding brackets,
            e.g. ``#all-images#-#cdis.cdi.kubevirt.io#``.

    Returns:
        List of dimension values if 2+ ``#value#`` segments found, else ``None``.
    """
    matches = re.findall(r"#([^#]+)#", param_str)
    return matches if len(matches) >= 2 else None


def _detect_matrix(
    group_items: list[dict[str, Any]],
    base_name: str,
) -> dict[str, Any]:
    """Detect whether grouped items form a 2-D parameter matrix.

    Parses multi-dimensional ``#value#`` segments from each item's
    parameter portion and, when every item has exactly two dimensions,
    builds a row/col/cell structure suitable for a matrix grid view.

    Args:
        group_items: Items sharing the same base test name.
        base_name: The common prefix (everything before ``[``).

    Returns:
        Dict with ``is_matrix`` key.  When ``True`` the dict also
        contains ``matrix_rows``, ``matrix_cols`` and ``matrix_cells``.
    """
    params: list[dict[str, Any]] = []
    for sub in group_items:
        param_part = sub["node_id"][len(base_name) :]
        param_str = param_part.strip("[]")
        dims = _parse_param_dimensions(param_str)
        if dims:
            params.append({"dims": dims, "item": sub})

    if len(params) == len(group_items) and all(
        len(p["dims"]) == len(params[0]["dims"]) for p in params
    ):
        num_dims = len(params[0]["dims"])
        if num_dims == 2:
            row_values = sorted({p["dims"][0] for p in params})
            col_values = sorted({p["dims"][1] for p in params})

            # Nested dict for Jinja2 compatibility (no tuple keys)
            matrix_cells: dict[str, dict[str, dict[str, Any]]] = {}
            for p in params:
                matrix_cells.setdefault(p["dims"][0], {})[p["dims"][1]] = p["item"]

            return {
                "is_matrix": True,
                "matrix_rows": row_values,
                "matrix_cols": col_values,
                "matrix_cells": matrix_cells,
            }

    return {"is_matrix": False}


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
        # Already grouped (from cross-section grouping) — pass through
        if item.get("is_group"):
            continue
        bracket = node_id.find("[")
        if bracket != -1:
            base_name = node_id[:bracket]
            groups.setdefault(base_name, []).append(item)

    result: list[dict[str, Any]] = []
    seen_bases: set[str] = set()

    for item in items:
        node_id = item["node_id"]
        # Already grouped (from cross-section grouping) — pass through.
        # Group items have node_id == base_name (no bracket).
        if item.get("is_group"):
            if node_id not in seen_bases:
                seen_bases.add(node_id)
                result.append(item)
            continue
        bracket = node_id.find("[")
        if bracket != -1:
            base_name = node_id[:bracket]
            if base_name in seen_bases:
                continue
            seen_bases.add(base_name)
            group_items = groups[base_name]
            if len(group_items) >= 2:
                result.append(
                    _build_group_item(group_items=group_items, base_name=base_name)
                )
            else:
                single = {**group_items[0], "is_group": False, "is_matrix": False}
                result.append(single)
        else:
            if "is_group" not in item:
                result.append({**item, "is_group": False, "is_matrix": False})
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
        "last_executed": result.last_executed[:10] if result.last_executed else None,
        "launch_name": result.launch_name,
        "defect_type": result.defect_type,
        "defect_comment": result.defect_comment,
        "launch_id": result.launch_id,
        "item_id": result.item_id,
        "source": result.source,
        "comment_urls": _extract_urls(result.defect_comment or ""),
        "is_manual": False,
    }


def _test_info_to_template_item(test_info: TestInfo) -> dict[str, Any]:
    """Convert a TestInfo to a template-friendly dict.

    Args:
        test_info: TestInfo from collector.

    Returns:
        Dict with fields needed by the template.
    """
    status = "QUARANTINED" if test_info.is_quarantined else "NEVER_EXECUTED"

    return {
        "node_id": test_info.node_id,
        "status": status,
        "status_css": _STATUS_CSS.get(status, "never"),
        "status_label": _STATUS_LABELS.get(status, status),
        "bundle": None,
        "last_executed": None,
        "launch_name": None,
        "defect_type": None,
        "defect_comment": None,
        "launch_id": None,
        "item_id": None,
        "source": "manual" if test_info.is_manual else "automated",
        "comment_urls": [],
        "quarantine_reason": test_info.quarantine_reason,
        "quarantine_jira": test_info.quarantine_jira,
        "is_manual": test_info.is_manual,
    }


def _build_group_item(
    group_items: list[dict[str, Any]],
    base_name: str,
) -> dict[str, Any]:
    """Build a parameterized group item from individual variant items.

    Args:
        group_items: Individual test items sharing the same base name.
        base_name: Common prefix before the ``[`` bracket.

    Returns:
        Group dict with matrix detection, status counts, and metadata.
    """
    status_counts: dict[str, int] = {}
    for sub in group_items:
        s = sub.get("status", "UNKNOWN")
        status_counts[s] = status_counts.get(s, 0) + 1

    group_item: dict[str, Any] = {
        "node_id": base_name,
        "status": "MIXED",
        "status_css": "",
        "status_label": "MIXED",
        "is_group": True,
        "group_count": len(group_items),
        "group_items": group_items,
        "bundle": None,
        "last_executed": None,
        "launch_name": None,
        "launch_id": None,
        "item_id": None,
        "defect_type": None,
        "defect_comment": None,
        "source": None,
        "is_manual": any(sub.get("is_manual") for sub in group_items),
        "comment_urls": [],
        "quarantine_reason": None,
        "quarantine_jira": None,
        "status_counts": status_counts,
    }

    matrix_info = _detect_matrix(group_items=group_items, base_name=base_name)
    group_item.update(matrix_info)
    return group_item


def _matrix_primary_section(
    status_counts: dict[str, int],
) -> str:
    """Determine which section a parameterized test primarily belongs to.

    Priority: failed > stale > never_executed > skipped > passed.

    Args:
        status_counts: Dict mapping status to count of variants.

    Returns:
        Section name: "failed", "stale", "never_executed",
        "skipped", or "passed".
    """
    if status_counts.get("FAILED"):
        return "failed"
    if status_counts.get("STALE"):
        return "stale"
    if status_counts.get("NEVER_EXECUTED"):
        return "never_executed"
    if status_counts.get("SKIPPED"):
        return "skipped"
    return "passed"


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
            stale_items.append(
                _result_to_template_item(
                    node_id=test.node_id,
                    result=result,
                    status_override="STALE",
                )
            )
            continue

        status_upper = result.status.upper()
        item = _result_to_template_item(node_id=test.node_id, result=result)

        if status_upper == "PASSED":
            passed_items.append(item)
        elif status_upper == "FAILED":
            failed_items.append(item)
        elif status_upper == "SKIPPED":
            skipped_items.append(item)

    # Separate manual tests from never_executed
    manual_items = [i for i in never_executed_items if i.get("is_manual")]
    never_executed_items = [i for i in never_executed_items if not i.get("is_manual")]

    total = len(tests)
    passed_count = len(passed_items)
    failed_count = len(failed_items)
    skipped_count = len(skipped_items)
    manual_count = len(manual_items)
    never_executed_count = len(never_executed_items)
    stale_count = len(stale_items)
    quarantined_count = len(quarantined_items)
    executed = passed_count + failed_count + skipped_count
    coverage_pct = (executed / total * 100) if total > 0 else 0.0

    # --- Parameterized test cross-section grouping ---
    # Note: quarantined_items and manual_items excluded — quarantined tests
    # are filtered out early in the classification loop, and manual tests are
    # unimplemented STDs that cannot have executed siblings across sections.
    # Neither can have parameterized siblings in other status sections.
    param_groups: dict[str, list[dict[str, Any]]] = {}
    for section_items in [
        passed_items,
        failed_items,
        skipped_items,
        stale_items,
        never_executed_items,
    ]:
        for item in section_items:
            node_id = item["node_id"]
            bracket = node_id.find("[")
            if bracket != -1:
                base = node_id[:bracket]
                param_groups.setdefault(base, []).append(item)

    # For groups with 2+ variants, consolidate into primary section
    all_variant_ids: set[str] = set()
    groups_by_section: dict[str, list[dict[str, Any]]] = {}

    for base, variants in param_groups.items():
        if len(variants) < 2:
            continue

        group_items = sorted(variants, key=lambda x: x["node_id"])
        group_item = _build_group_item(group_items=group_items, base_name=base)
        primary = _matrix_primary_section(status_counts=group_item["status_counts"])

        all_variant_ids.update(item["node_id"] for item in variants)
        groups_by_section.setdefault(primary, []).append(group_item)

    # Single-pass removal across all sections
    if all_variant_ids:
        passed_items[:] = [
            i for i in passed_items if i["node_id"] not in all_variant_ids
        ]
        failed_items[:] = [
            i for i in failed_items if i["node_id"] not in all_variant_ids
        ]
        skipped_items[:] = [
            i for i in skipped_items if i["node_id"] not in all_variant_ids
        ]
        stale_items[:] = [i for i in stale_items if i["node_id"] not in all_variant_ids]
        never_executed_items[:] = [
            i for i in never_executed_items if i["node_id"] not in all_variant_ids
        ]

    # Add groups to their primary sections
    section_map = {
        "passed": passed_items,
        "failed": failed_items,
        "skipped": skipped_items,
        "stale": stale_items,
        "never_executed": never_executed_items,
    }
    for section, groups in groups_by_section.items():
        if section in section_map:
            section_map[section].extend(groups)
    # --- End cross-section grouping ---

    return TeamReportData(
        name=team_name,
        total=total,
        passed=passed_count,
        failed=failed_count,
        skipped=skipped_count,
        never_executed=never_executed_count,
        stale=stale_count,
        quarantined=quarantined_count,
        manual=manual_count,
        coverage_pct=round(coverage_pct, 1),
        failed_items=_sort_and_group(items=failed_items),
        stale_items=_sort_and_group(items=stale_items),
        quarantined_items=_sort_and_group(items=quarantined_items),
        manual_items=_sort_and_group(items=manual_items),
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
    analysis_arch: str = "amd64",
    analysis_max_bundles: int = 5,
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
        analysis_arch: Architecture filter label for analysis display (default ``amd64``).
        analysis_max_bundles: Maximum number of recent bundles shown in analysis (default 5).

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
        all_failed.extend(td.failed_items)
        all_stale.extend(td.stale_items)
        all_quarantined.extend(td.quarantined_items)
        all_never.extend(td.never_executed_items)
        all_passed.extend(td.passed_items)
        all_skipped.extend(td.skipped_items)

    # Sort aggregated lists (groups are already built per-team)
    all_failed.sort(key=_NODE_ID_KEY)
    all_stale.sort(key=_NODE_ID_KEY)
    all_quarantined.sort(key=_NODE_ID_KEY)
    all_never.sort(key=_NODE_ID_KEY)
    all_passed.sort(key=_NODE_ID_KEY)
    all_skipped.sort(key=_NODE_ID_KEY)

    # Identify gating gaps (never-executed or stale gating tests)
    gating_node_ids = {test.node_id for test in tests if test.is_gating}
    for item in all_never:
        node_id = item["node_id"]
        if item.get("is_group"):
            # Check if any sub-item is a gating test
            for sub in item["group_items"]:
                if sub["node_id"] in gating_node_ids:
                    gating_item = {
                        **item,
                        "status": "NEVER_EXECUTED",
                        "status_css": "never",
                        "status_label": "NEVER EXECUTED",
                    }
                    all_gating.append(gating_item)
                    break
        elif node_id in gating_node_ids:
            gating_item = {
                **item,
                "status": "NEVER_EXECUTED",
                "status_css": "never",
                "status_label": "NEVER EXECUTED",
            }
            all_gating.append(gating_item)
    for item in all_stale:
        node_id = item["node_id"]
        if item.get("is_group"):
            for sub in item["group_items"]:
                if sub["node_id"] in gating_node_ids:
                    gating_item = {
                        **item,
                        "status": "STALE",
                        "status_css": "stale",
                        "status_label": "STALE",
                    }
                    all_gating.append(gating_item)
                    break
        elif node_id in gating_node_ids:
            gating_item = {
                **item,
                "status": "STALE",
                "status_css": "stale",
                "status_label": "STALE",
            }
            all_gating.append(gating_item)

    all_gating.sort(key=_NODE_ID_KEY)

    # Determine gate status
    gate_passed = len(all_gating) == 0 and total_failed == 0

    # Build aggregated analysis records for display
    display_analysis: list[LaunchAnalysisRecord] = []
    if analysis_records:
        grouped = aggregate_analysis_by_display_team(records=analysis_records)
        for _team, recs in sorted(grouped.items()):
            display_analysis.extend(recs)
        # Add total rows (per tier + arch)
        total_by_tier_arch: dict[tuple[str, str], dict[str, int]] = {}
        for rec in display_analysis:
            key = (rec.tier, rec.arch)
            if key not in total_by_tier_arch:
                total_by_tier_arch[key] = {
                    "launches": 0,
                    "total": 0,
                    "passed": 0,
                    "failed": 0,
                    "skipped": 0,
                    "analyzed": 0,
                    "to_investigate": 0,
                    "product_bug": 0,
                    "automation_bug": 0,
                    "system_issue": 0,
                    "no_defect": 0,
                }
            for counter_field in total_by_tier_arch[key]:
                total_by_tier_arch[key][counter_field] += getattr(rec, counter_field)
        for (tier, arch), counters in sorted(total_by_tier_arch.items()):
            display_analysis.append(
                LaunchAnalysisRecord(
                    bundle="ALL",
                    team="TOTAL",
                    display_team="TOTAL",
                    tier=tier,
                    arch=arch,
                    **counters,
                )
            )

    generated_at = datetime.now(tz=UTC).strftime("%Y-%m-%d %H:%M UTC")
    report_filename = f"report_{version}.html"

    # Count manual tests
    total_manual = sum(td.manual for td in team_data_list)
    total_automated = total_tests - total_manual - total_quarantined

    # Build per-team gating items
    gating_by_team: dict[str, list[dict[str, Any]]] = {}
    for item in all_gating:
        team = _get_team_from_node_id(node_id=item["node_id"])
        if team_aliases and team in team_aliases:
            team = team_aliases[team]
        gating_by_team.setdefault(team, []).append(item)

    # Build per-team analysis records
    team_analysis_map: dict[str, list[LaunchAnalysisRecord]] = {}
    if analysis_records:
        for record in analysis_records:
            for td in team_data_list:
                # Direct case-insensitive match
                if td.name.lower() == record.display_team.lower():
                    team_analysis_map.setdefault(td.name, []).append(record)
                    break
                # Check if any aliased source team matches the display team
                if team_aliases:
                    for alias_src, alias_dst in team_aliases.items():
                        if alias_dst == td.name and alias_src.lower() == record.display_team.lower():
                            team_analysis_map.setdefault(td.name, []).append(record)
                            break

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
        teams=[
            {
                "name": td.name,
                "total": td.total,
                "passed": td.passed,
                "failed": td.failed,
                "skipped": td.skipped,
                "never_executed": td.never_executed,
                "stale": td.stale,
                "quarantined": td.quarantined,
                "manual": td.manual,
                "coverage_pct": td.coverage_pct,
                "gating_items": _sort_and_group(items=gating_by_team.get(td.name, [])),
                "failed_items": td.failed_items,
                "stale_items": td.stale_items,
                "quarantined_items": td.quarantined_items,
                "manual_items": td.manual_items,
                "never_executed_items": td.never_executed_items,
                "passed_items": td.passed_items,
                "skipped_items": td.skipped_items,
                "team_analysis": sorted(
                    team_analysis_map.get(td.name, []),
                    key=lambda r: (r.bundle, r.tier),
                ),
            }
            for td in team_data_list
        ],
        analysis_records=display_analysis,
        rp_url=rp_url.rstrip("/"),
        rp_project=rp_project,
        gate_passed=gate_passed,
        analysis_arch=analysis_arch,
        analysis_max_bundles=analysis_max_bundles,
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
        repos.append(
            {
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
            }
        )

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
