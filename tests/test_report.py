"""Tests for HTML report rendering."""

from __future__ import annotations

from pathlib import Path

from datetime import UTC, datetime, timedelta

from coverage_reports.collectors.base import TestInfo
from coverage_reports.report import (
    VersionReportData,
    _get_team_from_node_id,
    render_index,
    render_version_report,
)
from coverage_reports.rp_checker import ItemResult

# Use a recent timestamp so tests don't become stale
_RECENT_TS = (datetime.now(tz=UTC) - timedelta(days=2)).isoformat()


class TestGetTeamFromNodeId:
    """Tests for team extraction from node IDs."""

    def test_standard_path(self) -> None:
        assert _get_team_from_node_id(node_id="tests/network/test_foo.py::test_bar") == "network"

    def test_nested_path(self) -> None:
        assert _get_team_from_node_id(node_id="tests/virt/migration/test_live.py::TestMigrate::test_it") == "virt"

    def test_no_tests_prefix(self) -> None:
        assert _get_team_from_node_id(node_id="other/path/test.py::test_x") == "other"

    def test_single_component(self) -> None:
        assert _get_team_from_node_id(node_id="test_foo.py::test_bar") == "other"


class TestRenderVersionReport:
    """Tests for version report rendering."""

    def test_basic_render(self) -> None:
        tests = [
            TestInfo(node_id="tests/network/test_foo.py::test_pass", team="network"),
            TestInfo(node_id="tests/network/test_foo.py::test_never", team="network"),
            TestInfo(
                node_id="tests/virt/test_vm.py::test_quarantined",
                team="virt",
                is_quarantined=True,
                quarantine_reason="QUARANTINED: flaky",
                quarantine_jira="CNV-12345",
            ),
        ]
        rp_results = {
            "tests.network.test_foo.test_pass": ItemResult(
                name="tests.network.test_foo.test_pass",
                status="PASSED",
                last_executed=_RECENT_TS,
                bundle="v4.22.0-100",
                launch_name="NETWORK-gating",
                launch_id=1,
                item_id=10,
            ),
        }

        html, ver_data = render_version_report(
            version="4.22",
            branch="cnv-4.22",
            repo_name="test-repo",
            tests=tests,
            rp_results=rp_results,
            stale_days=30,
        )

        assert "4.22" in html
        assert "test-repo" in html
        assert "test_pass" in html
        assert "test_never" in html
        assert "test_quarantined" in html
        assert "Quarantined" in html

        assert ver_data.version == "4.22"
        assert ver_data.total_tests == 3
        assert ver_data.passed == 1
        assert ver_data.never_executed == 1
        assert ver_data.quarantined == 1

    def test_render_with_failed(self) -> None:
        tests = [
            TestInfo(node_id="tests/network/test_foo.py::test_fail", team="network"),
        ]
        rp_results = {
            "tests.network.test_foo.test_fail": ItemResult(
                name="tests.network.test_foo.test_fail",
                status="FAILED",
                last_executed=_RECENT_TS,
                bundle="v4.22.0-100",
                launch_name="NETWORK-gating",
                defect_type="Product Bug",
                defect_comment="Known issue",
                launch_id=2,
                item_id=20,
            ),
        }

        html, ver_data = render_version_report(
            version="4.22",
            branch="cnv-4.22",
            repo_name="test-repo",
            tests=tests,
            rp_results=rp_results,
            stale_days=30,
        )

        assert "test_fail" in html
        assert "Product Bug" in html
        assert ver_data.failed == 1

    def test_render_with_rp_links(self) -> None:
        tests = [
            TestInfo(node_id="tests/network/test_foo.py::test_fail", team="network"),
        ]
        rp_results = {
            "tests.network.test_foo.test_fail": ItemResult(
                name="tests.network.test_foo.test_fail",
                status="FAILED",
                last_executed=_RECENT_TS,
                bundle="v4.22.0",
                launch_name="run",
                launch_id=100,
                item_id=200,
            ),
        }

        html, _ = render_version_report(
            version="4.22",
            branch="cnv-4.22",
            repo_name="test-repo",
            tests=tests,
            rp_results=rp_results,
            stale_days=30,
            rp_url="https://rp.example.com",
            rp_project="cnv",
        )

        assert "rp.example.com" in html
        assert "/launches/all/100/200" in html


class TestRenderIndex:
    """Tests for dashboard index rendering."""

    def test_basic_index(self) -> None:
        repo_versions = {
            "test-repo": [
                VersionReportData(
                    version="4.22",
                    branch="cnv-4.22",
                    repo_name="test-repo",
                    total_tests=1000,
                    passed=800,
                    failed=50,
                    never_executed=100,
                    stale=30,
                    quarantined=20,
                    coverage_pct=85.0,
                    report_filename="report_4.22.html",
                ),
            ],
        }

        html = render_index(repo_versions=repo_versions)
        assert "test-repo" in html
        assert "4.22" in html
        assert "85.0%" in html
        assert "report_4.22.html" in html
        assert "Dashboard" in html

    def test_multiple_versions(self) -> None:
        repo_versions = {
            "test-repo": [
                VersionReportData(
                    version="4.22", branch="cnv-4.22", repo_name="test-repo",
                    total_tests=1000, passed=800, failed=50,
                    never_executed=100, stale=30, quarantined=20,
                    coverage_pct=85.0, report_filename="report_4.22.html",
                ),
                VersionReportData(
                    version="4.99", branch="main", repo_name="test-repo",
                    total_tests=1100, passed=900, failed=40,
                    never_executed=120, stale=20, quarantined=20,
                    coverage_pct=87.3, report_filename="report_4.99.html",
                ),
            ],
        }

        html = render_index(repo_versions=repo_versions)
        assert "4.22" in html
        assert "4.99" in html
        assert "report_4.22.html" in html
        assert "report_4.99.html" in html
