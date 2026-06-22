"""Tests for RP checker — result matching and team normalization."""

from __future__ import annotations

from coverage_reports.rp_checker import (
    ItemResult,
    _classify_defect,
    _extract_attribute,
    _process_launch_items,
    get_display_team,
    node_id_to_rp_name,
    normalize_rp_team,
)


class TestClassifyDefect:
    """Tests for defect type classification."""

    def test_product_bug(self) -> None:
        assert _classify_defect(issue_type="pb001") == "Product Bug"

    def test_automation_bug(self) -> None:
        assert _classify_defect(issue_type="ab002") == "Automation Bug"

    def test_system_issue(self) -> None:
        assert _classify_defect(issue_type="si003") == "System Issue"

    def test_to_investigate(self) -> None:
        assert _classify_defect(issue_type="ti001") == "To Investigate"

    def test_no_defect(self) -> None:
        assert _classify_defect(issue_type="nd001") == "No Defect"

    def test_not_issue(self) -> None:
        assert _classify_defect(issue_type="NOT_ISSUE") == "Not Issue"

    def test_unknown_type(self) -> None:
        assert _classify_defect(issue_type="xx999") == "Unknown"

    def test_case_insensitive(self) -> None:
        assert _classify_defect(issue_type="PB001") == "Product Bug"


class TestExtractAttribute:
    """Tests for attribute extraction."""

    def test_found(self) -> None:
        attrs = [
            {"key": "BUNDLE", "value": "v4.22.0"},
            {"key": "ARCH", "value": "amd64"},
        ]
        assert _extract_attribute(attributes=attrs, key="BUNDLE") == "v4.22.0"

    def test_not_found(self) -> None:
        attrs = [{"key": "BUNDLE", "value": "v4.22.0"}]
        assert _extract_attribute(attributes=attrs, key="TEAM") is None

    def test_empty_list(self) -> None:
        assert _extract_attribute(attributes=[], key="BUNDLE") is None


class TestNormalizeRpTeam:
    """Tests for RP team normalization with configurable suffixes."""

    SUFFIXES = [
        "-OVN-OCS-S390X",
        "-HPPCSIBLOCK-S390X",
        "-OCS-S390X",
        "-OVN-DUAL",
        "-OVN-IPV6",
        "-OVN-OCS",
        "-HPPCSIBLOCK",
        "-IPV6",
        "-OCS",
        "-S390X",
    ]

    def test_plain_team(self) -> None:
        assert normalize_rp_team(raw_team="NETWORK", strip_suffixes=self.SUFFIXES) == "NETWORK"

    def test_strip_ocs(self) -> None:
        assert normalize_rp_team(raw_team="NETWORK-OCS", strip_suffixes=self.SUFFIXES) == "NETWORK"

    def test_strip_s390x(self) -> None:
        assert normalize_rp_team(raw_team="NETWORK-S390X", strip_suffixes=self.SUFFIXES) == "NETWORK"

    def test_strip_compound_suffix(self) -> None:
        assert normalize_rp_team(raw_team="NETWORK-OVN-OCS-S390X", strip_suffixes=self.SUFFIXES) == "NETWORK"

    def test_strip_trailing_dash(self) -> None:
        assert normalize_rp_team(raw_team="NETWORK-", strip_suffixes=self.SUFFIXES) == "NETWORK"

    def test_no_matching_suffix(self) -> None:
        assert normalize_rp_team(raw_team="CUSTOM-TEAM", strip_suffixes=self.SUFFIXES) == "CUSTOM-TEAM"


class TestGetDisplayTeam:
    """Tests for display team mapping."""

    MAPPING = {
        "VIRT-NODE": "Virt",
        "VIRT-CLUSTER": "Virt",
        "NETWORK": "Network",
    }

    def test_mapped(self) -> None:
        assert get_display_team(rp_team="VIRT-NODE", team_mapping=self.MAPPING) == "Virt"

    def test_multiple_to_same(self) -> None:
        assert get_display_team(rp_team="VIRT-CLUSTER", team_mapping=self.MAPPING) == "Virt"

    def test_unmapped_returns_original(self) -> None:
        assert get_display_team(rp_team="UNKNOWN", team_mapping=self.MAPPING) == "UNKNOWN"


class TestNodeIdToRpName:
    """Tests for node ID to RP name conversion."""

    def test_simple(self) -> None:
        result = node_id_to_rp_name(node_id="tests/network/test_foo.py::test_bar")
        assert result == "tests.network.test_foo.test_bar"

    def test_with_class(self) -> None:
        result = node_id_to_rp_name(node_id="tests/virt/test_vm.py::TestVM::test_create")
        assert result == "tests.virt.test_vm.TestVM.test_create"

    def test_with_params(self) -> None:
        result = node_id_to_rp_name(node_id="tests/network/test_foo.py::test_bar[param1]")
        assert result == "tests.network.test_foo.test_bar[param1]"


class TestProcessLaunchItems:
    """Tests for processing launch items into ItemResult."""

    def test_basic_processing(self) -> None:
        launch = {
            "id": 100,
            "name": "NETWORK-gating",
            "attributes": [
                {"key": "BUNDLE", "value": "v4.22.0"},
                {"key": "ARCH", "value": "amd64"},
            ],
        }
        items = [
            {
                "id": 200,
                "name": "tests.network.test_foo.test_bar",
                "status": "PASSED",
                "endTime": "2025-01-15T10:00:00Z",
            }
        ]
        results = _process_launch_items(launch=launch, items=items)
        assert len(results) == 1
        assert results[0].name == "tests.network.test_foo.test_bar"
        assert results[0].status == "PASSED"
        assert results[0].bundle == "v4.22.0"
        assert results[0].launch_id == 100
        assert results[0].item_id == 200
        assert results[0].source == "automated"

    def test_manual_launch(self) -> None:
        launch = {
            "id": 101,
            "name": "manual-run",
            "attributes": [
                {"key": "BUNDLE", "value": "v4.22.0"},
                {"key": "MANUAL", "value": "true"},
            ],
        }
        items = [{"id": 201, "name": "test1", "status": "PASSED", "endTime": ""}]
        results = _process_launch_items(launch=launch, items=items)
        assert results[0].source == "manual"

    def test_defect_classification(self) -> None:
        launch = {
            "id": 102,
            "name": "run",
            "attributes": [{"key": "BUNDLE", "value": "v4.22.0"}],
        }
        items = [
            {
                "id": 202,
                "name": "test_fail",
                "status": "FAILED",
                "endTime": "",
                "issue": {
                    "issueType": "pb001",
                    "comment": "Known product bug",
                },
            }
        ]
        results = _process_launch_items(launch=launch, items=items)
        assert results[0].defect_type == "Product Bug"
        assert results[0].defect_comment == "Known product bug"
