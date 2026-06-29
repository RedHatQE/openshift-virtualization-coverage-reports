"""Tests for pytest_collector manual test scanning."""

from __future__ import annotations

from pathlib import Path

from coverage_reports.collectors.pytest_collector import _scan_manual_tests


class TestScanManualTests:
    """Tests for _scan_manual_tests."""

    def test_module_level_test_false(self, tmp_path: Path) -> None:
        """Module-level __test__ = False disables all tests."""
        test_file = tmp_path / "tests" / "team_a" / "test_example.py"
        test_file.parent.mkdir(parents=True)
        test_file.write_text(
            "__test__ = False\n\n"
            "def test_one(): pass\n\n"
            "def test_two(): pass\n"
        )
        result = _scan_manual_tests(tests_path=tmp_path / "tests", repo_path=tmp_path)
        assert len(result) == 2
        assert all(t.is_manual for t in result)
        node_ids = {t.node_id for t in result}
        assert "tests/team_a/test_example.py::test_one" in node_ids
        assert "tests/team_a/test_example.py::test_two" in node_ids

    def test_module_level_test_false_with_classes(self, tmp_path: Path) -> None:
        """Module-level __test__ = False disables functions and class methods."""
        test_file = tmp_path / "tests" / "team_a" / "test_mixed.py"
        test_file.parent.mkdir(parents=True)
        test_file.write_text(
            "__test__ = False\n\n"
            "def test_func(): pass\n\n"
            "class TestCls:\n"
            "    def test_method(self): pass\n"
        )
        result = _scan_manual_tests(tests_path=tmp_path / "tests", repo_path=tmp_path)
        assert len(result) == 2
        assert all(t.is_manual for t in result)
        node_ids = {t.node_id for t in result}
        assert "tests/team_a/test_mixed.py::test_func" in node_ids
        assert "tests/team_a/test_mixed.py::TestCls::test_method" in node_ids

    def test_function_level_test_false(self, tmp_path: Path) -> None:
        """func.__test__ = False disables individual functions."""
        test_file = tmp_path / "tests" / "team_a" / "test_funcs.py"
        test_file.parent.mkdir(parents=True)
        test_file.write_text(
            "def test_enabled(): pass\n\n"
            "def test_disabled(): pass\n"
            "test_disabled.__test__ = False\n"
        )
        result = _scan_manual_tests(tests_path=tmp_path / "tests", repo_path=tmp_path)
        assert len(result) == 1
        assert result[0].node_id == "tests/team_a/test_funcs.py::test_disabled"
        assert result[0].is_manual

    def test_class_level_test_false(self, tmp_path: Path) -> None:
        """Class-level __test__ = False disables all methods."""
        test_file = tmp_path / "tests" / "team_a" / "test_cls.py"
        test_file.parent.mkdir(parents=True)
        test_file.write_text(
            "class TestManual:\n"
            "    __test__ = False\n"
            "    def test_a(self): pass\n"
            "    def test_b(self): pass\n"
        )
        result = _scan_manual_tests(tests_path=tmp_path / "tests", repo_path=tmp_path)
        assert len(result) == 2
        assert all(t.is_manual for t in result)

    def test_conditional_test_false(self, tmp_path: Path) -> None:
        """func.__test__ = False inside if block is detected."""
        test_file = tmp_path / "tests" / "team_a" / "test_cond.py"
        test_file.parent.mkdir(parents=True)
        test_file.write_text(
            "def test_cond_disabled(): pass\n\n"
            "if True:\n"
            "    test_cond_disabled.__test__ = False\n"
        )
        result = _scan_manual_tests(tests_path=tmp_path / "tests", repo_path=tmp_path)
        assert len(result) == 1
        assert result[0].node_id == "tests/team_a/test_cond.py::test_cond_disabled"

    def test_no_manual_tests(self, tmp_path: Path) -> None:
        """File with only regular tests returns empty list."""
        test_file = tmp_path / "tests" / "team_a" / "test_normal.py"
        test_file.parent.mkdir(parents=True)
        test_file.write_text("def test_normal(): pass\n")
        result = _scan_manual_tests(tests_path=tmp_path / "tests", repo_path=tmp_path)
        assert len(result) == 0

    def test_class_method_level_test_false(self, tmp_path: Path) -> None:
        """method.__test__ = False inside class disables individual methods."""
        test_file = tmp_path / "tests" / "team_a" / "test_cls_method.py"
        test_file.parent.mkdir(parents=True)
        test_file.write_text(
            "class TestPartial:\n"
            "    def test_enabled(self): pass\n"
            "    def test_disabled(self): pass\n"
            "    test_disabled.__test__ = False\n"
        )
        result = _scan_manual_tests(tests_path=tmp_path / "tests", repo_path=tmp_path)
        assert len(result) == 1
        assert result[0].node_id == "tests/team_a/test_cls_method.py::TestPartial::test_disabled"
        assert result[0].is_manual

    def test_syntax_error_file_skipped(self, tmp_path: Path) -> None:
        """Files with syntax errors are silently skipped."""
        test_file = tmp_path / "tests" / "team_a" / "test_broken.py"
        test_file.parent.mkdir(parents=True)
        test_file.write_text("def test_broken(:\n")
        result = _scan_manual_tests(tests_path=tmp_path / "tests", repo_path=tmp_path)
        assert len(result) == 0

    def test_class_method_does_not_false_positive_module_function(self, tmp_path: Path) -> None:
        """method.__test__ = False in class does not disable same-named module function."""
        test_file = tmp_path / "tests" / "team_a" / "test_overlap.py"
        test_file.parent.mkdir(parents=True)
        test_file.write_text(
            "def test_overlap(): pass\n\n"
            "class TestCls:\n"
            "    def test_overlap(self): pass\n"
            "    test_overlap.__test__ = False\n"
        )
        result = _scan_manual_tests(tests_path=tmp_path / "tests", repo_path=tmp_path)
        # Only the class method should be disabled, not the module function
        assert len(result) == 1
        assert "TestCls::test_overlap" in result[0].node_id

    def test_multiple_conditional_disables(self, tmp_path: Path) -> None:
        """Multiple func.__test__ = False inside if block."""
        test_file = tmp_path / "tests" / "team_a" / "test_multi_cond.py"
        test_file.parent.mkdir(parents=True)
        test_file.write_text(
            "def test_a(): pass\n"
            "def test_b(): pass\n"
            "def test_c(): pass\n\n"
            "if not some_flag:\n"
            "    test_a.__test__ = False\n"
            "    test_b.__test__ = False\n"
        )
        result = _scan_manual_tests(tests_path=tmp_path / "tests", repo_path=tmp_path)
        assert len(result) == 2
        node_ids = {t.node_id for t in result}
        assert "tests/team_a/test_multi_cond.py::test_a" in node_ids
        assert "tests/team_a/test_multi_cond.py::test_b" in node_ids
