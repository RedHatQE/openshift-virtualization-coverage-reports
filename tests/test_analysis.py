"""Tests for failure analysis statistics."""

from __future__ import annotations

from coverage_reports.analysis import (
    LaunchAnalysisRecord,
    aggregate_analysis_by_display_team,
    collect_analysis_stats,
)


TEAM_MAPPING = {
    "VIRT-NODE": "Virt",
    "VIRT-CLUSTER": "Virt",
    "NETWORK": "Network",
    "STORAGE": "Storage",
}

STRIP_SUFFIXES = ["-OVN-OCS-S390X", "-OCS-S390X", "-OCS", "-S390X"]


def _make_launch(
    launch_id: int,
    name: str,
    bundle: str,
    team: str,
    arch: str = "amd64",
    total: int = 100,
    passed: int = 90,
    failed: int = 8,
    skipped: int = 2,
    product_bug: int = 3,
    automation_bug: int = 2,
    system_issue: int = 1,
    no_defect: int = 1,
    to_investigate: int = 1,
) -> dict:
    """Create a mock launch dict with realistic structure."""
    return {
        "id": launch_id,
        "name": name,
        "attributes": [
            {"key": "BUNDLE", "value": bundle},
            {"key": "TEAM", "value": team},
            {"key": "ARCH", "value": arch},
        ],
        "statistics": {
            "executions": {"total": total, "passed": passed, "failed": failed, "skipped": skipped},
            "defects": {
                "product_bug": {"total": product_bug},
                "automation_bug": {"total": automation_bug},
                "system_issue": {"total": system_issue},
                "no_defect": {"total": no_defect},
                "to_investigate": {"total": to_investigate},
            },
        },
    }


class TestCollectAnalysisStats:
    """Tests for collect_analysis_stats function."""

    def test_basic_aggregation(self) -> None:
        launches = [
            _make_launch(launch_id=1, name="NETWORK-gating", bundle="v4.22.0-100", team="NETWORK"),
            _make_launch(launch_id=2, name="NETWORK-nongating", bundle="v4.22.0-100", team="NETWORK"),
        ]
        records = collect_analysis_stats(
            launches=launches,
            team_mapping=TEAM_MAPPING,
            strip_suffixes=STRIP_SUFFIXES,
        )
        # Should have Gating + All rows for NETWORK
        gating = [rec for rec in records if rec.tier == "Gating"]
        all_rows = [rec for rec in records if rec.tier == "All"]
        assert len(gating) == 1
        assert len(all_rows) == 1
        assert gating[0].display_team == "Network"
        assert all_rows[0].total == 200  # Both launches combined

    def test_arch_filtering(self) -> None:
        launches = [
            _make_launch(launch_id=1, name="NETWORK-gating", bundle="v4.22.0", team="NETWORK", arch="amd64"),
            _make_launch(launch_id=2, name="NETWORK-gating", bundle="v4.22.0", team="NETWORK", arch="s390x"),
        ]
        records = collect_analysis_stats(
            launches=launches,
            team_mapping=TEAM_MAPPING,
            strip_suffixes=STRIP_SUFFIXES,
            arch_filter="amd64",
        )
        # Only amd64 launch included
        all_rows = [rec for rec in records if rec.tier == "All"]
        assert len(all_rows) == 1
        assert all_rows[0].total == 100

    def test_max_bundles(self) -> None:
        launches = [
            _make_launch(launch_id=1, name="NETWORK-gating", bundle="v4.22.0-100", team="NETWORK"),
            _make_launch(launch_id=2, name="NETWORK-gating", bundle="v4.22.0-200", team="NETWORK"),
            _make_launch(launch_id=3, name="NETWORK-gating", bundle="v4.22.0-300", team="NETWORK"),
        ]
        records = collect_analysis_stats(
            launches=launches,
            team_mapping=TEAM_MAPPING,
            strip_suffixes=STRIP_SUFFIXES,
            max_bundles=2,
        )
        bundles = {rec.bundle for rec in records}
        assert len(bundles) <= 2
        # Most recent bundles kept (descending sort)
        assert "v4.22.0-100" not in bundles

    def test_team_normalization_with_suffixes(self) -> None:
        launches = [
            _make_launch(launch_id=1, name="NETWORK-gating", bundle="v4.22.0", team="NETWORK-OCS-S390X"),
        ]
        records = collect_analysis_stats(
            launches=launches,
            team_mapping=TEAM_MAPPING,
            strip_suffixes=STRIP_SUFFIXES,
        )
        all_rows = [rec for rec in records if rec.tier == "All"]
        assert all_rows[0].team == "NETWORK"
        assert all_rows[0].display_team == "Network"

    def test_empty_launches(self) -> None:
        records = collect_analysis_stats(
            launches=[],
            team_mapping=TEAM_MAPPING,
            strip_suffixes=STRIP_SUFFIXES,
        )
        assert records == []


class TestAggregateByDisplayTeam:
    """Tests for aggregate_analysis_by_display_team."""

    def test_collapses_teams(self) -> None:
        records = [
            LaunchAnalysisRecord(
                bundle="v4.22.0", team="VIRT-NODE", display_team="Virt",
                tier="Gating", arch="amd64", launches=2, total=100,
                passed=90, failed=8, skipped=2, analyzed=6,
                to_investigate=2, product_bug=3, automation_bug=2,
                system_issue=1, no_defect=0,
            ),
            LaunchAnalysisRecord(
                bundle="v4.22.0", team="VIRT-CLUSTER", display_team="Virt",
                tier="Gating", arch="amd64", launches=1, total=50,
                passed=45, failed=4, skipped=1, analyzed=3,
                to_investigate=1, product_bug=1, automation_bug=1,
                system_issue=1, no_defect=0,
            ),
        ]
        grouped = aggregate_analysis_by_display_team(records=records)
        assert "Virt" in grouped
        assert len(grouped["Virt"]) == 1
        virt_gating = grouped["Virt"][0]
        assert virt_gating.launches == 3
        assert virt_gating.total == 150
        assert virt_gating.failed == 12
        assert virt_gating.arch == "amd64"

    def test_preserves_tiers(self) -> None:
        records = [
            LaunchAnalysisRecord(
                bundle="v4.22.0", team="NETWORK", display_team="Network",
                tier="Gating", arch="amd64", launches=1, total=50,
                passed=45, failed=4, skipped=1, analyzed=3,
                to_investigate=1, product_bug=2, automation_bug=1,
                system_issue=0, no_defect=0,
            ),
            LaunchAnalysisRecord(
                bundle="v4.22.0", team="NETWORK", display_team="Network",
                tier="All", arch="amd64", launches=3, total=200,
                passed=180, failed=15, skipped=5, analyzed=10,
                to_investigate=5, product_bug=5, automation_bug=3,
                system_issue=2, no_defect=0,
            ),
        ]
        grouped = aggregate_analysis_by_display_team(records=records)
        assert len(grouped["Network"]) == 2
        tiers = {rec.tier for rec in grouped["Network"]}
        assert tiers == {"Gating", "All"}

    def test_preserves_arch_dimension(self) -> None:
        """Records with different arch values should not be collapsed together."""
        records = [
            LaunchAnalysisRecord(
                bundle="v4.22.0", team="NETWORK", display_team="Network",
                tier="Gating", arch="amd64", launches=2, total=100,
                passed=90, failed=8, skipped=2, analyzed=6,
                to_investigate=2, product_bug=3, automation_bug=2,
                system_issue=1, no_defect=0,
            ),
            LaunchAnalysisRecord(
                bundle="v4.22.0", team="NETWORK", display_team="Network",
                tier="Gating", arch="s390x", launches=1, total=50,
                passed=45, failed=4, skipped=1, analyzed=3,
                to_investigate=1, product_bug=1, automation_bug=1,
                system_issue=1, no_defect=0,
            ),
        ]
        grouped = aggregate_analysis_by_display_team(records=records)
        assert "Network" in grouped
        assert len(grouped["Network"]) == 2
        arches = {rec.arch for rec in grouped["Network"]}
        assert arches == {"amd64", "s390x"}
        amd64_rec = [rec for rec in grouped["Network"] if rec.arch == "amd64"][0]
        assert amd64_rec.total == 100
        s390x_rec = [rec for rec in grouped["Network"] if rec.arch == "s390x"][0]
        assert s390x_rec.total == 50
