"""Failure analysis statistics from launch data.

Aggregates execution and defect statistics from RP launches,
grouped by (bundle, team, tier). All team normalization reads
from config — nothing hardcoded.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from coverage_reports.rp_checker import get_display_team, normalize_rp_team

LOGGER = logging.getLogger(__name__)


@dataclass
class LaunchAnalysisRecord:
    """Aggregated failure-analysis statistics for one (bundle, team, tier) slice.

    Attributes:
        bundle: Bundle version string.
        team: Normalized RP TEAM name.
        display_team: Collapsed display team name (from config mapping).
        tier: ``Gating`` for gating launches, ``All`` for all launches combined.
        arch: Architecture filter applied (or ``all``).
        launches: Number of launches aggregated.
        total: Total test executions.
        passed: Passed test count.
        failed: Failed test count.
        skipped: Skipped test count.
        analyzed: Failures classified as PB/AB/SI/ND.
        to_investigate: Failures still marked To Investigate.
        product_bug: Failures classified as Product Bug.
        automation_bug: Failures classified as Automation Bug.
        system_issue: Failures classified as System Issue.
        no_defect: Failures classified as No Defect.
    """

    bundle: str
    team: str
    display_team: str
    tier: str
    arch: str
    launches: int
    total: int
    passed: int
    failed: int
    skipped: int
    analyzed: int
    to_investigate: int
    product_bug: int
    automation_bug: int
    system_issue: int
    no_defect: int


_COUNTER_FIELDS = (
    "launches",
    "total",
    "passed",
    "failed",
    "skipped",
    "product_bug",
    "automation_bug",
    "system_issue",
    "no_defect",
    "to_investigate",
)


def _new_counters() -> dict[str, int]:
    """Create a fresh zero-initialized counter dict."""
    return {field: 0 for field in _COUNTER_FIELDS}


def collect_analysis_stats(
    launches: list[dict[str, Any]],
    team_mapping: dict[str, str],
    strip_suffixes: list[str],
    arch_filter: str | None = None,
    max_bundles: int | None = None,
) -> list[LaunchAnalysisRecord]:
    """Aggregate failure-analysis statistics from launch metadata.

    Groups launches by (bundle, normalized-team, tier) and sums up
    execution and defect statistics. Produces two tier rows per team:
    ``Gating`` and ``All`` (all launches combined).

    Args:
        launches: List of RP launch dicts (with attributes and statistics).
        team_mapping: Config-driven RP TEAM → display name mapping.
        strip_suffixes: Suffixes to strip during team normalization.
        arch_filter: If set, only include launches whose ARCH attribute matches.
        max_bundles: If set, limit to the N most recent bundles.

    Returns:
        List of LaunchAnalysisRecord sorted by (bundle, team, tier).
    """
    arch_label = arch_filter or "all"

    # Accumulator keyed by (bundle, normalized_team, is_gating)
    raw_stats: dict[tuple[str, str, bool], dict[str, int]] = {}

    for launch in launches:
        attrs = {
            attr["key"]: attr.get("value", "")
            for attr in launch.get("attributes", [])
            if "key" in attr
        }
        arch = attrs.get("ARCH", "")
        if arch_filter and arch != arch_filter:
            continue

        bundle = attrs.get("BUNDLE", "")
        raw_team = attrs.get("TEAM", "")
        normalized_team = normalize_rp_team(raw_team=raw_team, strip_suffixes=strip_suffixes)
        is_gating = "gating" in launch.get("name", "").lower()

        key = (bundle, normalized_team, is_gating)
        if key not in raw_stats:
            raw_stats[key] = _new_counters()

        stats = launch.get("statistics", {})
        executions = stats.get("executions", {})
        defects = stats.get("defects", {})

        counters = raw_stats[key]
        counters["launches"] += 1
        counters["total"] += executions.get("total", 0)
        counters["passed"] += executions.get("passed", 0)
        counters["failed"] += executions.get("failed", 0)
        counters["skipped"] += executions.get("skipped", 0)
        counters["product_bug"] += defects.get("product_bug", {}).get("total", 0)
        counters["automation_bug"] += defects.get("automation_bug", {}).get("total", 0)
        counters["system_issue"] += defects.get("system_issue", {}).get("total", 0)
        counters["no_defect"] += defects.get("no_defect", {}).get("total", 0)
        counters["to_investigate"] += defects.get("to_investigate", {}).get("total", 0)

    if max_bundles:
        all_bundles = sorted({key[0] for key in raw_stats}, reverse=True)
        keep_bundles = set(all_bundles[:max_bundles])
        raw_stats = {key: val for key, val in raw_stats.items() if key[0] in keep_bundles}

    # Build "All" rows by summing gating + non-gating per (bundle, team)
    all_stats: dict[tuple[str, str], dict[str, int]] = {}
    for (bundle, team, _is_gating), counters in raw_stats.items():
        all_key = (bundle, team)
        if all_key not in all_stats:
            all_stats[all_key] = _new_counters()
        for field_name in _COUNTER_FIELDS:
            all_stats[all_key][field_name] += counters[field_name]

    records: list[LaunchAnalysisRecord] = []

    # Add Gating rows
    for (bundle, team, is_gating), counts in sorted(raw_stats.items()):
        if not is_gating:
            continue
        analyzed = counts["product_bug"] + counts["automation_bug"] + counts["system_issue"] + counts["no_defect"]
        display = get_display_team(rp_team=team, team_mapping=team_mapping)
        records.append(
            LaunchAnalysisRecord(
                bundle=bundle,
                team=team,
                display_team=display,
                tier="Gating",
                arch=arch_label,
                launches=counts["launches"],
                total=counts["total"],
                passed=counts["passed"],
                failed=counts["failed"],
                skipped=counts["skipped"],
                analyzed=analyzed,
                to_investigate=counts["to_investigate"],
                product_bug=counts["product_bug"],
                automation_bug=counts["automation_bug"],
                system_issue=counts["system_issue"],
                no_defect=counts["no_defect"],
            )
        )

    # Add All rows
    for (bundle, team), counts in sorted(all_stats.items()):
        analyzed = counts["product_bug"] + counts["automation_bug"] + counts["system_issue"] + counts["no_defect"]
        display = get_display_team(rp_team=team, team_mapping=team_mapping)
        records.append(
            LaunchAnalysisRecord(
                bundle=bundle,
                team=team,
                display_team=display,
                tier="All",
                arch=arch_label,
                launches=counts["launches"],
                total=counts["total"],
                passed=counts["passed"],
                failed=counts["failed"],
                skipped=counts["skipped"],
                analyzed=analyzed,
                to_investigate=counts["to_investigate"],
                product_bug=counts["product_bug"],
                automation_bug=counts["automation_bug"],
                system_issue=counts["system_issue"],
                no_defect=counts["no_defect"],
            )
        )

    records.sort(key=lambda record: (record.bundle, record.display_team, record.tier))
    LOGGER.info(f"Collected {len(records)} analysis records")
    return records


def aggregate_analysis_by_display_team(
    records: list[LaunchAnalysisRecord],
) -> dict[str, list[LaunchAnalysisRecord]]:
    """Aggregate analysis records by display team, summing across bundles.

    Groups by the collapsed display team name, tier, and architecture.
    Each display team gets one row per (tier, arch) combination.

    Args:
        records: List of LaunchAnalysisRecord to aggregate.

    Returns:
        Dict mapping display team name to list of aggregated records,
        sorted by team name.
    """
    stats_accumulator: dict[tuple[str, str, str], dict[str, int]] = {}

    for record in records:
        key = (record.display_team, record.tier, record.arch)
        if key not in stats_accumulator:
            stats_accumulator[key] = _new_counters()
        counters = stats_accumulator[key]
        for field_name in _COUNTER_FIELDS:
            counters[field_name] += getattr(record, field_name)

    grouped: dict[str, list[LaunchAnalysisRecord]] = {}
    for (display_team, tier, arch), counters in sorted(stats_accumulator.items()):
        analyzed = (
            counters["product_bug"]
            + counters["automation_bug"]
            + counters["system_issue"]
            + counters["no_defect"]
        )
        aggregated = LaunchAnalysisRecord(
            bundle="ALL",
            team=display_team,
            display_team=display_team,
            tier=tier,
            arch=arch,
            launches=counters["launches"],
            total=counters["total"],
            passed=counters["passed"],
            failed=counters["failed"],
            skipped=counters["skipped"],
            analyzed=analyzed,
            to_investigate=counters["to_investigate"],
            product_bug=counters["product_bug"],
            automation_bug=counters["automation_bug"],
            system_issue=counters["system_issue"],
            no_defect=counters["no_defect"],
        )
        grouped.setdefault(display_team, []).append(aggregated)

    return grouped
