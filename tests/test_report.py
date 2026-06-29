"""Tests for HTML report rendering."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from coverage_reports.collectors.base import TestInfo
from coverage_reports.report import (
    VersionReportData,
    _detect_matrix,
    _extract_urls,
    _get_team_from_node_id,
    _group_parameterized_items,
    _matrix_primary_section,
    _parse_param_dimensions,
    render_index,
    render_version_report,
)
from coverage_reports.rp_checker import ItemResult

# Use a recent timestamp so tests don't become stale
_RECENT_TS = (datetime.now(tz=UTC) - timedelta(days=2)).isoformat()


class TestGetTeamFromNodeId:
    """Tests for team extraction from node IDs."""

    def test_standard_path(self) -> None:
        assert (
            _get_team_from_node_id(node_id="tests/network/test_foo.py::test_bar")
            == "network"
        )

    def test_nested_path(self) -> None:
        assert (
            _get_team_from_node_id(
                node_id="tests/virt/migration/test_live.py::TestMigrate::test_it"
            )
            == "virt"
        )

    def test_no_tests_prefix(self) -> None:
        assert _get_team_from_node_id(node_id="other/path/test.py::test_x") == "other"

    def test_single_component(self) -> None:
        assert _get_team_from_node_id(node_id="test_foo.py::test_bar") == "other"


class TestGroupParameterizedItems:
    """Tests for parameterized test grouping."""

    def test_no_params(self) -> None:
        items = [
            {"node_id": "tests/net/test_a.py::test_foo", "status": "PASSED"},
            {"node_id": "tests/net/test_b.py::test_bar", "status": "FAILED"},
        ]
        result = _group_parameterized_items(items=items)
        assert len(result) == 2
        assert all(not item["is_group"] for item in result)

    def test_groups_parameterized(self) -> None:
        items = [
            {
                "node_id": "tests/net/test_a.py::test_foo[param1]",
                "status": "PASSED",
                "bundle": "b1",
            },
            {
                "node_id": "tests/net/test_a.py::test_foo[param2]",
                "status": "PASSED",
                "bundle": "b1",
            },
            {
                "node_id": "tests/net/test_a.py::test_foo[param3]",
                "status": "FAILED",
                "bundle": "b2",
            },
        ]
        result = _group_parameterized_items(items=items)
        assert len(result) == 1
        group = result[0]
        assert group["is_group"] is True
        assert group["group_count"] == 3
        assert group["node_id"] == "tests/net/test_a.py::test_foo"
        assert len(group["group_items"]) == 3

    def test_single_param_not_grouped(self) -> None:
        items = [
            {"node_id": "tests/net/test_a.py::test_foo[only]", "status": "PASSED"},
        ]
        result = _group_parameterized_items(items=items)
        assert len(result) == 1
        assert result[0]["is_group"] is False

    def test_mixed_grouped_and_plain(self) -> None:
        items = [
            {"node_id": "tests/test_a.py::test_bar", "status": "PASSED"},
            {
                "node_id": "tests/test_a.py::test_foo[p1]",
                "status": "PASSED",
                "bundle": "b",
            },
            {
                "node_id": "tests/test_a.py::test_foo[p2]",
                "status": "FAILED",
                "bundle": "b",
            },
        ]
        result = _group_parameterized_items(items=items)
        assert len(result) == 2
        assert result[0]["is_group"] is False
        assert result[0]["node_id"] == "tests/test_a.py::test_bar"
        assert result[1]["is_group"] is True
        assert result[1]["group_count"] == 2

    def test_preserves_order(self) -> None:
        items = [
            {"node_id": "tests/b.py::test_b[p1]", "status": "PASSED", "bundle": "x"},
            {"node_id": "tests/b.py::test_b[p2]", "status": "PASSED", "bundle": "x"},
            {"node_id": "tests/a.py::test_a", "status": "PASSED"},
        ]
        result = _group_parameterized_items(items=items)
        assert len(result) == 2
        assert result[0]["node_id"] == "tests/b.py::test_b"
        assert result[1]["node_id"] == "tests/a.py::test_a"


class TestMatrixPrimarySection:
    """Tests for _matrix_primary_section."""

    def test_all_passed(self) -> None:
        assert _matrix_primary_section(status_counts={"PASSED": 3}) == "passed"

    def test_failed_takes_priority(self) -> None:
        assert (
            _matrix_primary_section(status_counts={"PASSED": 2, "FAILED": 1})
            == "failed"
        )

    def test_stale_over_never_executed(self) -> None:
        assert (
            _matrix_primary_section(status_counts={"STALE": 1, "NEVER_EXECUTED": 2})
            == "stale"
        )

    def test_never_executed_over_skipped(self) -> None:
        assert (
            _matrix_primary_section(status_counts={"NEVER_EXECUTED": 1, "SKIPPED": 1})
            == "never_executed"
        )

    def test_skipped_over_passed(self) -> None:
        assert (
            _matrix_primary_section(status_counts={"SKIPPED": 1, "PASSED": 5})
            == "skipped"
        )

    def test_empty_counts_returns_passed(self) -> None:
        assert _matrix_primary_section(status_counts={}) == "passed"


class TestCrossSectionGrouping:
    """Tests for cross-section parameterized test grouping in _build_team_data."""

    def test_mixed_status_params_grouped_in_primary_section(self) -> None:
        """Parameterized test with 2 passed + 1 never-executed should appear only in never_executed."""
        tests = [
            TestInfo(
                node_id="tests/network/test_foo.py::test_param[a]", team="network"
            ),
            TestInfo(
                node_id="tests/network/test_foo.py::test_param[b]", team="network"
            ),
            TestInfo(
                node_id="tests/network/test_foo.py::test_param[c]", team="network"
            ),
        ]
        rp_results = {
            "tests.network.test_foo.test_param[a]": ItemResult(
                name="tests.network.test_foo.test_param[a]",
                status="PASSED",
                last_executed=_RECENT_TS,
                bundle="v4.22.0",
                launch_name="run",
                launch_id=1,
                item_id=10,
            ),
            "tests.network.test_foo.test_param[b]": ItemResult(
                name="tests.network.test_foo.test_param[b]",
                status="PASSED",
                last_executed=_RECENT_TS,
                bundle="v4.22.0",
                launch_name="run",
                launch_id=1,
                item_id=11,
            ),
            # test_param[c] has no RP result -> NEVER_EXECUTED
        }

        html, ver_data = render_version_report(
            version="4.22",
            branch="cnv-4.22",
            repo_name="test-repo",
            tests=tests,
            rp_results=rp_results,
            stale_days=30,
        )

        # All 3 variants should be in the group
        assert "3 params" in html
        # Counts should reflect individual test statuses
        assert ver_data.passed == 2
        assert ver_data.never_executed >= 1

    def test_all_passed_params_stay_in_passed(self) -> None:
        """When all variants pass, group should be in passed section."""
        tests = [
            TestInfo(
                node_id="tests/network/test_foo.py::test_param[a]", team="network"
            ),
            TestInfo(
                node_id="tests/network/test_foo.py::test_param[b]", team="network"
            ),
        ]
        rp_results = {
            "tests.network.test_foo.test_param[a]": ItemResult(
                name="tests.network.test_foo.test_param[a]",
                status="PASSED",
                last_executed=_RECENT_TS,
                bundle="v4.22.0",
                launch_name="run",
                launch_id=1,
                item_id=10,
            ),
            "tests.network.test_foo.test_param[b]": ItemResult(
                name="tests.network.test_foo.test_param[b]",
                status="PASSED",
                last_executed=_RECENT_TS,
                bundle="v4.22.0",
                launch_name="run",
                launch_id=1,
                item_id=11,
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

        assert "2 params" in html
        assert ver_data.passed == 2
        assert ver_data.failed == 0
        assert ver_data.never_executed == 0

    def test_failed_variant_promotes_group_to_failed(self) -> None:
        """A single failed variant should move the entire group to failed section."""
        tests = [
            TestInfo(
                node_id="tests/network/test_foo.py::test_param[a]", team="network"
            ),
            TestInfo(
                node_id="tests/network/test_foo.py::test_param[b]", team="network"
            ),
            TestInfo(
                node_id="tests/network/test_foo.py::test_param[c]", team="network"
            ),
        ]
        rp_results = {
            "tests.network.test_foo.test_param[a]": ItemResult(
                name="tests.network.test_foo.test_param[a]",
                status="PASSED",
                last_executed=_RECENT_TS,
                bundle="v4.22.0",
                launch_name="run",
                launch_id=1,
                item_id=10,
            ),
            "tests.network.test_foo.test_param[b]": ItemResult(
                name="tests.network.test_foo.test_param[b]",
                status="FAILED",
                last_executed=_RECENT_TS,
                bundle="v4.22.0",
                launch_name="run",
                launch_id=1,
                item_id=11,
            ),
            "tests.network.test_foo.test_param[c]": ItemResult(
                name="tests.network.test_foo.test_param[c]",
                status="PASSED",
                last_executed=_RECENT_TS,
                bundle="v4.22.0",
                launch_name="run",
                launch_id=1,
                item_id=12,
            ),
        }

        _html, ver_data = render_version_report(
            version="4.22",
            branch="cnv-4.22",
            repo_name="test-repo",
            tests=tests,
            rp_results=rp_results,
            stale_days=30,
        )

        # Individual counts still track per-test
        assert ver_data.passed == 2
        assert ver_data.failed == 1


class TestParseParamDimensions:
    """Tests for _parse_param_dimensions helper."""

    def test_multi_dim(self) -> None:
        assert _parse_param_dimensions("#all-images#-#cdis.cdi#") == [
            "all-images",
            "cdis.cdi",
        ]

    def test_single_dim(self) -> None:
        assert _parse_param_dimensions("#hostpath-csi-basic#") is None

    def test_no_hash(self) -> None:
        assert _parse_param_dimensions("sap_hana_vm0") is None

    def test_three_dims(self) -> None:
        assert _parse_param_dimensions("#a#-#b#-#c#") == ["a", "b", "c"]


class TestDetectMatrix:
    """Tests for _detect_matrix helper."""

    def test_2d_matrix(self) -> None:
        base = "tests/test_a.py::test_x"
        items = [
            {"node_id": f"{base}[#r1#-#c1#]", "status": "PASSED"},
            {"node_id": f"{base}[#r1#-#c2#]", "status": "FAILED"},
            {"node_id": f"{base}[#r2#-#c1#]", "status": "PASSED"},
        ]
        result = _detect_matrix(group_items=items, base_name=base)
        assert result["is_matrix"] is True
        assert sorted(result["matrix_rows"]) == ["r1", "r2"]
        assert sorted(result["matrix_cols"]) == ["c1", "c2"]
        assert result["matrix_cells"]["r1"]["c1"]["status"] == "PASSED"
        assert result["matrix_cells"]["r1"]["c2"]["status"] == "FAILED"
        assert result["matrix_cells"]["r2"]["c1"]["status"] == "PASSED"

    def test_single_dim_not_matrix(self) -> None:
        base = "tests/test_a.py::test_x"
        items = [
            {"node_id": f"{base}[#val1#]", "status": "PASSED"},
            {"node_id": f"{base}[#val2#]", "status": "PASSED"},
        ]
        result = _detect_matrix(group_items=items, base_name=base)
        assert result["is_matrix"] is False

    def test_mixed_dims_not_matrix(self) -> None:
        base = "tests/test_a.py::test_x"
        items = [
            {"node_id": f"{base}[#r1#-#c1#]", "status": "PASSED"},
            {"node_id": f"{base}[plain_param]", "status": "PASSED"},
        ]
        result = _detect_matrix(group_items=items, base_name=base)
        assert result["is_matrix"] is False


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

    def test_parameterized_tests_grouped_in_html(self) -> None:
        tests = [
            TestInfo(
                node_id="tests/network/test_foo.py::test_param[a]", team="network"
            ),
            TestInfo(
                node_id="tests/network/test_foo.py::test_param[b]", team="network"
            ),
            TestInfo(
                node_id="tests/network/test_foo.py::test_param[c]", team="network"
            ),
        ]
        rp_results = {
            "tests.network.test_foo.test_param[a]": ItemResult(
                name="tests.network.test_foo.test_param[a]",
                status="PASSED",
                last_executed=_RECENT_TS,
                bundle="v4.22.0",
                launch_name="run",
                launch_id=1,
                item_id=10,
            ),
            "tests.network.test_foo.test_param[b]": ItemResult(
                name="tests.network.test_foo.test_param[b]",
                status="PASSED",
                last_executed=_RECENT_TS,
                bundle="v4.22.0",
                launch_name="run",
                launch_id=1,
                item_id=11,
            ),
            "tests.network.test_foo.test_param[c]": ItemResult(
                name="tests.network.test_foo.test_param[c]",
                status="PASSED",
                last_executed=_RECENT_TS,
                bundle="v4.22.0",
                launch_name="run",
                launch_id=1,
                item_id=12,
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

        # Group should appear in HTML
        assert "3 params" in html
        assert "group-row" in html
        assert "param-table" in html
        # Individual param suffixes should be in sub-table
        assert "[a]" in html
        assert "[b]" in html
        assert "[c]" in html
        # Counts should still reflect all 3
        assert ver_data.passed == 3


class TestTeamAliases:
    """Tests for team_aliases in render_version_report."""

    def test_alias_resolves_team(self) -> None:
        tests = [
            TestInfo(
                node_id="tests/observability/test_metrics.py::test_metric",
                team="observability",
            ),
        ]
        rp_results = {
            "tests.observability.test_metrics.test_metric": ItemResult(
                name="tests.observability.test_metrics.test_metric",
                status="PASSED",
                last_executed=_RECENT_TS,
                bundle="v4.22.0-100",
                launch_name="OBS-gating",
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
            team_aliases={"observability": "install_upgrade_operators"},
        )

        assert "install_upgrade_operators" in html
        # The original team name should not appear as a team heading
        assert ver_data.passed == 1

    def test_missing_alias_returns_original(self) -> None:
        tests = [
            TestInfo(node_id="tests/network/test_foo.py::test_pass", team="network"),
        ]
        rp_results = {
            "tests.network.test_foo.test_pass": ItemResult(
                name="tests.network.test_foo.test_pass",
                status="PASSED",
                last_executed=_RECENT_TS,
                bundle="v4.22.0-100",
                launch_name="NET-gating",
                launch_id=1,
                item_id=10,
            ),
        }

        html, _ = render_version_report(
            version="4.22",
            branch="cnv-4.22",
            repo_name="test-repo",
            tests=tests,
            rp_results=rp_results,
            stale_days=30,
            team_aliases={"observability": "install_upgrade_operators"},
        )

        assert "network" in html

    def test_none_team_aliases_handled(self) -> None:
        tests = [
            TestInfo(node_id="tests/network/test_foo.py::test_pass", team="network"),
        ]
        rp_results = {
            "tests.network.test_foo.test_pass": ItemResult(
                name="tests.network.test_foo.test_pass",
                status="PASSED",
                last_executed=_RECENT_TS,
                bundle="v4.22.0-100",
                launch_name="NET-gating",
                launch_id=1,
                item_id=10,
            ),
        }

        html, _ = render_version_report(
            version="4.22",
            branch="cnv-4.22",
            repo_name="test-repo",
            tests=tests,
            rp_results=rp_results,
            stale_days=30,
            team_aliases=None,
        )

        assert "network" in html

    def test_empty_team_aliases_handled(self) -> None:
        tests = [
            TestInfo(node_id="tests/network/test_foo.py::test_pass", team="network"),
        ]
        rp_results = {
            "tests.network.test_foo.test_pass": ItemResult(
                name="tests.network.test_foo.test_pass",
                status="PASSED",
                last_executed=_RECENT_TS,
                bundle="v4.22.0-100",
                launch_name="NET-gating",
                launch_id=1,
                item_id=10,
            ),
        }

        html, _ = render_version_report(
            version="4.22",
            branch="cnv-4.22",
            repo_name="test-repo",
            tests=tests,
            rp_results=rp_results,
            stale_days=30,
            team_aliases={},
        )

        assert "network" in html


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
                VersionReportData(
                    version="4.99",
                    branch="main",
                    repo_name="test-repo",
                    total_tests=1100,
                    passed=900,
                    failed=40,
                    never_executed=120,
                    stale=20,
                    quarantined=20,
                    coverage_pct=87.3,
                    report_filename="report_4.99.html",
                ),
            ],
        }

        html = render_index(repo_versions=repo_versions)
        assert "4.22" in html
        assert "4.99" in html
        assert "report_4.22.html" in html
        assert "report_4.99.html" in html


class TestExtractUrls:
    """Tests for _extract_urls."""

    def test_empty_string(self) -> None:
        assert _extract_urls("") == []

    def test_none_input(self) -> None:
        assert _extract_urls(None) == []

    def test_no_urls(self) -> None:
        assert _extract_urls("plain text without urls") == []

    def test_single_url(self) -> None:
        assert _extract_urls("see https://example.com/path") == [
            "https://example.com/path"
        ]

    def test_multiple_urls(self) -> None:
        text = "links: https://a.com and http://b.org/page"
        assert _extract_urls(text) == ["https://a.com", "http://b.org/page"]

    def test_trailing_period_stripped(self) -> None:
        assert _extract_urls("visit https://example.com.") == ["https://example.com"]

    def test_trailing_comma_stripped(self) -> None:
        assert _extract_urls("see https://example.com, then") == ["https://example.com"]

    def test_trailing_semicolon_stripped(self) -> None:
        assert _extract_urls("url: https://example.com;") == ["https://example.com"]

    def test_trailing_colon_stripped(self) -> None:
        assert _extract_urls("ref: https://example.com:") == ["https://example.com"]

    def test_url_with_port_preserved(self) -> None:
        assert _extract_urls("at https://example.com:8080/path") == [
            "https://example.com:8080/path"
        ]

    def test_url_with_query_params(self) -> None:
        assert _extract_urls("https://example.com/path?key=value&other=1") == [
            "https://example.com/path?key=value&other=1"
        ]
