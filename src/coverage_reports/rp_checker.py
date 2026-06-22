"""ReportPortal results checker.

Queries ReportPortal for test execution results across launches
matching a bundle prefix and builds a result map. Classifies each
test as passed/failed/skipped/stale/never-executed.

Team normalization uses config (team_strip_suffixes, team_mapping) —
nothing hardcoded.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any

import requests

from coverage_reports.rp_client import RPClient

LOGGER = logging.getLogger(__name__)


@dataclass
class ItemResult:
    """Execution result for a single test item from ReportPortal.

    Attributes:
        name: Dotted RP test name.
        status: Last status (PASSED, FAILED, SKIPPED).
        last_executed: ISO timestamp of last execution.
        bundle: Bundle version from the launch attributes.
        launch_name: Name of the launch.
        source: ``manual`` if MANUAL=true in launch, else ``automated``.
        defect_type: Classified defect type for failed/skipped items.
        defect_comment: Defect comment from RP issue field.
        launch_id: RP launch identifier for log link construction.
        item_id: RP test item identifier for log link construction.
    """

    name: str
    status: str
    last_executed: str
    bundle: str
    launch_name: str
    source: str = "automated"
    defect_type: str | None = None
    defect_comment: str | None = None
    launch_id: int | None = None
    item_id: int | None = None


_DEFECT_TYPE_PREFIXES: dict[str, str] = {
    "pb": "Product Bug",
    "ab": "Automation Bug",
    "si": "System Issue",
    "ti": "To Investigate",
    "nd": "No Defect",
}


def _classify_defect(issue_type: str) -> str:
    """Classify an RP issue type locator into a human-readable label.

    Args:
        issue_type: RP issue type locator (e.g., ``pb001``).

    Returns:
        Human-readable defect classification.
    """
    if issue_type == "NOT_ISSUE":
        return "Not Issue"

    lower_type = issue_type.lower()
    for prefix, label in _DEFECT_TYPE_PREFIXES.items():
        if lower_type.startswith(prefix):
            return label

    return "Unknown"


def _extract_attribute(attributes: list[dict[str, Any]], key: str) -> str | None:
    """Extract a specific attribute value from RP attribute dicts.

    Args:
        attributes: List of attribute dicts with ``key`` and ``value`` fields.
        key: Attribute key to search for.

    Returns:
        The attribute value if found, None otherwise.
    """
    for attr in attributes:
        if attr.get("key") == key:
            return attr.get("value")
    return None


def _process_launch_items(
    launch: dict[str, Any],
    items: list[dict[str, Any]],
) -> list[ItemResult]:
    """Process items from a single launch into ItemResult objects.

    Args:
        launch: Launch dict with attributes, name, etc.
        items: List of test item dicts from RP API.

    Returns:
        List of ItemResult objects for this launch.
    """
    launch_attributes = launch.get("attributes", [])
    bundle_value = _extract_attribute(attributes=launch_attributes, key="BUNDLE") or ""
    manual_value = _extract_attribute(attributes=launch_attributes, key="MANUAL")
    source = "manual" if manual_value and manual_value.lower() == "true" else "automated"
    launch_name = launch.get("name", "")

    results: list[ItemResult] = []
    for item in items:
        item_name = item.get("name", "")
        item_status = item.get("status", "")
        item_end_time = item.get("endTime", "")

        issue = item.get("issue")
        defect_type = None
        defect_comment = None
        if issue:
            raw_issue_type = issue.get("issueType", "")
            defect_type = _classify_defect(issue_type=raw_issue_type)
            defect_comment = issue.get("comment")

        results.append(
            ItemResult(
                name=item_name,
                status=item_status,
                last_executed=str(item_end_time),
                bundle=bundle_value,
                launch_name=launch_name,
                source=source,
                defect_type=defect_type,
                defect_comment=defect_comment,
                launch_id=launch.get("id"),
                item_id=item.get("id"),
            )
        )
    return results


def normalize_rp_team(raw_team: str, strip_suffixes: list[str]) -> str:
    """Normalize an RP launch TEAM attribute to a canonical team name.

    Strips trailing dashes and platform/storage suffixes so that different
    CI job variants all collapse to the base team name.

    Args:
        raw_team: Raw TEAM attribute value from RP launch.
        strip_suffixes: Suffixes to strip, ordered most-specific-first.

    Returns:
        Normalized team name (uppercase).
    """
    normalized = raw_team.rstrip("-")
    for suffix in strip_suffixes:
        if normalized.endswith(suffix):
            return normalized[: -len(suffix)]
    return normalized


def get_display_team(rp_team: str, team_mapping: dict[str, str]) -> str:
    """Map a normalized RP team name to its display team name.

    Args:
        rp_team: Normalized RP team name.
        team_mapping: Config-driven mapping of RP team → display name.

    Returns:
        Display team name, or the original name if no mapping exists.
    """
    return team_mapping.get(rp_team, rp_team)


def node_id_to_rp_name(node_id: str) -> str:
    """Convert a pytest node ID to the RP-style dotted name.

    Transforms path separators and pytest delimiters into the dotted
    format used by ReportPortal.

    Example:
        ``tests/network/foo/test_bar.py::TestClass::test_method``
        → ``tests.network.foo.test_bar.TestClass.test_method``

    Args:
        node_id: Pytest-style node ID.

    Returns:
        RP-style dotted test name.
    """
    name = node_id.replace("::", ".").replace("/", ".")
    if name.endswith(".py"):
        name = name[:-3]
    name = name.replace(".py.", ".")
    return name


def check_coverage(
    rp_client: RPClient,
    bundle_prefix: str,
    max_launches: int = 0,
    since_days: int = 0,
    max_workers: int = 10,
    progress_callback: Any | None = None,
) -> tuple[dict[str, ItemResult], list[dict[str, Any]]]:
    """Query ReportPortal for test results matching the bundle prefix.

    Fetches launches matching the bundle, then fetches test items
    in parallel using a thread pool. Most recent result wins when
    a test appears in multiple launches.

    Args:
        rp_client: Authenticated RPClient instance.
        bundle_prefix: Bundle version prefix to match.
        max_launches: Maximum number of recent launches to process (0 = all).
        since_days: Only fetch launches from the last N days (0 = all).
        max_workers: Thread pool size for parallel item fetching.
        progress_callback: Optional callable(current, total) for progress.

    Returns:
        Tuple of (result_map, launches) where result_map maps RP test name
        to its most recent ItemResult, and launches is the list of launch
        dicts used.
    """
    launches = rp_client.get_launches(bundle_prefix=bundle_prefix, since_days=since_days)
    launches.sort(key=lambda launch: launch.get("startTime", 0))

    total_launches = len(launches)
    if max_launches > 0 and total_launches > max_launches:
        LOGGER.info(f"Using {max_launches} most recent launches out of {total_launches}")
        launches = launches[-max_launches:]

    result_map: dict[str, ItemResult] = {}

    def _fetch_launch_items(launch: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        items = rp_client.get_test_items(launch_id=launch["id"])
        return launch, items

    completed = 0
    launch_count = len(launches)

    launch_results: list[tuple[dict[str, Any], list[ItemResult]]] = []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_fetch_launch_items, launch): launch for launch in launches}
        for future in as_completed(futures):
            try:
                launch, raw_items = future.result()
            except requests.RequestException:
                failed_launch = futures[future]
                LOGGER.warning(
                    f"Failed to fetch items for launch {failed_launch.get('id', 'unknown')}, skipping"
                )
                completed += 1
                if progress_callback:
                    progress_callback(current=completed, total=launch_count)
                continue

            launch_results.append((launch, _process_launch_items(launch=launch, items=raw_items)))
            completed += 1
            if progress_callback:
                progress_callback(current=completed, total=launch_count)

    # Sort by startTime so most recent overwrites older
    launch_results.sort(key=lambda lr: lr[0].get("startTime", 0))
    for _launch, items in launch_results:
        for item_result in items:
            result_map[item_result.name] = item_result

    LOGGER.info(f"Found results for {len(result_map)} unique tests across {launch_count} launches")
    return result_map, launches
