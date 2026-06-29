"""Pytest test collector.

Clones a repository, runs ``pytest --collect-only``, and parses the
output to build a list of TestInfo entries. Discovers teams from
``tests/`` subdirectories and identifies manual, quarantined, and
gating tests via AST analysis.
"""

from __future__ import annotations

import ast
import logging
import os
import re
import subprocess
import tempfile
from pathlib import Path

from coverage_reports.collectors.base import TestInfo

LOGGER = logging.getLogger(__name__)

_CNV_JIRA_PATTERN = re.compile(pattern=r"CNV-\d+")


def _is_test_false_assign(node: ast.AST) -> bool:
    """Check if node is ``__test__ = False`` (simple name assignment)."""
    return (
        isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "__test__"
            for target in node.targets
        )
        and isinstance(node.value, ast.Constant)
        and node.value.value is False
    )


def _is_attr_test_false(node: ast.AST) -> str | None:
    """Check if node is ``name.__test__ = False`` (attribute assignment).

    Returns the name being disabled, or None if not a match.
    """
    if (
        isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Attribute)
        and node.targets[0].attr == "__test__"
        and isinstance(node.targets[0].value, ast.Name)
        and isinstance(node.value, ast.Constant)
        and node.value.value is False
    ):
        return node.targets[0].value.id
    return None


def _manual_test_info(node_id: str) -> TestInfo:
    """Create a TestInfo for a manual/STD placeholder test."""
    return TestInfo(
        node_id=node_id,
        team=_get_team_from_node_id(node_id=node_id),
        is_manual=True,
    )


def clone_repo(
    url: str,
    branch: str,
    target_dir: Path,
    git_token: str | None = None,
) -> Path:
    """Clone a git repository at a specific branch.

    Args:
        url: Git clone URL.
        branch: Branch name to checkout.
        target_dir: Directory to clone into.
        git_token: Optional token for private repo authentication.

    Returns:
        Path to the cloned repository.

    Raises:
        subprocess.CalledProcessError: If git clone fails.
    """
    clone_url = url
    if git_token and "https://" in url:
        clone_url = url.replace("https://", f"https://x-access-token:{git_token}@")

    repo_name = url.rstrip("/").split("/")[-1].removesuffix(".git")
    repo_path = target_dir / f"{repo_name}-{branch}"

    if repo_path.exists():
        LOGGER.info(f"Repository already cloned at {repo_path}, pulling latest")
        subprocess.run(
            ["git", "pull"],
            cwd=str(repo_path),
            check=True,
            capture_output=True,
        )
        return repo_path

    LOGGER.info(f"Cloning {url} branch {branch} into {repo_path}")
    subprocess.run(
        ["git", "clone", "--branch", branch, "--depth", "1", clone_url, str(repo_path)],
        check=True,
        capture_output=True,
    )
    return repo_path


def _install_deps(repo_path: Path) -> None:
    """Install repository dependencies using uv.

    Args:
        repo_path: Path to the cloned repository.

    Raises:
        subprocess.CalledProcessError: If uv sync fails.
    """
    LOGGER.info(f"Installing dependencies in {repo_path}")
    subprocess.run(
        ["uv", "sync", "--no-install-workspace"],
        cwd=str(repo_path),
        check=True,
        capture_output=True,
    )


def _parse_pytest_collect_output(stdout: str) -> list[str]:
    """Parse pytest ``--collect-only -q`` output to extract test node IDs.

    Filters out non-test lines such as WARNING/ERROR messages from
    plugins that may contain ``::`` separators.

    Args:
        stdout: Raw stdout from ``pytest --collect-only -q``.

    Returns:
        List of pytest node IDs.
    """
    node_ids: list[str] = []
    for line in stdout.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith(("WARNING", "ERROR", "HINT")):
            continue
        if "::" not in stripped:
            continue
        path_part = stripped.split("::")[0]
        if " " in path_part:
            continue
        node_ids.append(stripped)
    return node_ids


def _collect_pytest_tests(repo_path: Path, tests_dir: str = "tests") -> list[str]:
    """Collect automated test node IDs via ``pytest --collect-only``.

    Args:
        repo_path: Path to the cloned repository.
        tests_dir: Subdirectory containing tests.

    Returns:
        List of pytest node IDs.
    """
    env = os.environ.copy()
    env["OPENSHIFT_VIRTUALIZATION_TEST_IMAGES_ARCH"] = "amd64"

    tests_path = repo_path / tests_dir
    result = subprocess.run(
        ["uv", "run", "pytest", "--collect-only", "-q", str(tests_path)],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(repo_path),
    )
    if result.returncode != 0:
        LOGGER.warning(f"pytest collection exited with code {result.returncode}")
        if result.stderr:
            LOGGER.warning(f"pytest stderr (truncated): {result.stderr[:500]}")

    node_ids = _parse_pytest_collect_output(stdout=result.stdout)
    LOGGER.info(f"Collected {len(node_ids)} automated tests via pytest")
    return node_ids


def _collect_gating_tests(repo_path: Path, tests_dir: str = "tests") -> set[str]:
    """Collect gating-marked test node IDs via ``pytest --collect-only -m gating``.

    Args:
        repo_path: Path to the cloned repository.
        tests_dir: Subdirectory containing tests.

    Returns:
        Set of gating test node IDs.
    """
    env = os.environ.copy()
    env["OPENSHIFT_VIRTUALIZATION_TEST_IMAGES_ARCH"] = "amd64"

    tests_path = repo_path / tests_dir
    result = subprocess.run(
        ["uv", "run", "pytest", "--collect-only", "-q", str(tests_path), "-m", "gating"],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(repo_path),
    )
    if result.returncode != 0:
        LOGGER.warning(f"pytest gating collection exited with code {result.returncode}")

    gating_ids = set(_parse_pytest_collect_output(stdout=result.stdout))
    LOGGER.info(f"Collected {len(gating_ids)} gating-marked tests")
    return gating_ids


def _get_team_from_node_id(node_id: str) -> str:
    """Extract the team name from a pytest node ID.

    The team is the first directory component after ``tests/``.

    Args:
        node_id: Pytest-style node ID.

    Returns:
        Team name, or empty string if not determinable.
    """
    parts = node_id.split("/")
    if len(parts) >= 2 and parts[0] == "tests":
        return parts[1]
    return ""


def _extract_string_from_node(node: ast.expr) -> str | None:
    """Extract a string value from an AST node.

    Handles constants, f-strings with variable references, and
    joined string fragments.

    Args:
        node: AST expression node.

    Returns:
        Extracted string if determinable, None otherwise.
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        parts: list[str] = []
        for value in node.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                parts.append(value.value)
            elif isinstance(value, ast.FormattedValue) and isinstance(value.value, ast.Name):
                parts.append(value.value.id)
            else:
                parts.append("...")
        return "".join(parts)
    return None


def _is_quarantine_xfail(decorator: ast.expr) -> tuple[bool, str]:
    """Check if a decorator is a quarantine xfail marker.

    Args:
        decorator: AST decorator node.

    Returns:
        Tuple of (is_quarantine, reason_string).
    """
    if not isinstance(decorator, ast.Call):
        return False, ""

    func = decorator.func
    if not (
        isinstance(func, ast.Attribute)
        and func.attr == "xfail"
        and isinstance(func.value, ast.Attribute)
        and func.value.attr == "mark"
    ):
        return False, ""

    reason = ""
    has_run_false = False

    for keyword in decorator.keywords:
        if keyword.arg == "reason":
            reason = _extract_string_from_node(node=keyword.value) or ""
        if keyword.arg == "run" and isinstance(keyword.value, ast.Constant) and keyword.value.value is False:
            has_run_false = True

    if has_run_false and "quarantined" in reason.lower():
        return True, reason
    return False, ""


def _is_jira_run_false(decorator: ast.expr) -> tuple[bool, str]:
    """Check if a decorator is a jira marker with run=False.

    Args:
        decorator: AST decorator node.

    Returns:
        Tuple of (is_jira_quarantine, jira_id).
    """
    if not isinstance(decorator, ast.Call):
        return False, ""

    func = decorator.func
    if not (
        isinstance(func, ast.Attribute)
        and func.attr == "jira"
        and isinstance(func.value, ast.Attribute)
        and func.value.attr == "mark"
    ):
        return False, ""

    has_run_false = False
    jira_id = ""

    for keyword in decorator.keywords:
        if keyword.arg == "run" and isinstance(keyword.value, ast.Constant) and keyword.value.value is False:
            has_run_false = True

    if decorator.args and isinstance(decorator.args[0], ast.Constant) and isinstance(decorator.args[0].value, str):
        jira_id = decorator.args[0].value

    if has_run_false and jira_id:
        return True, jira_id
    return False, ""


def _check_decorators(decorators: list[ast.expr]) -> tuple[bool, str, str | None]:
    """Check a list of decorators for quarantine markers.

    Args:
        decorators: List of AST decorator nodes.

    Returns:
        Tuple of (is_quarantined, reason, jira_id).
    """
    for decorator in decorators:
        is_xfail, reason = _is_quarantine_xfail(decorator=decorator)
        if is_xfail:
            jira_match = _CNV_JIRA_PATTERN.search(string=reason)
            return True, reason, jira_match.group() if jira_match else None

        is_jira, jira_id = _is_jira_run_false(decorator=decorator)
        if is_jira:
            return True, f"Jira {jira_id} (product bug, run=False)", jira_id

    return False, "", None


def _scan_quarantined_tests(tests_path: Path, repo_path: Path) -> list[TestInfo]:
    """Scan test files for quarantined tests using AST analysis.

    Detects two quarantine patterns:
    - ``@pytest.mark.xfail(reason=f"{QUARANTINED}: ...", run=False)``
    - ``@pytest.mark.jira("CNV-XXXXX", run=False)``

    Args:
        tests_path: Root directory to scan for test files.
        repo_path: Root of the cloned repository, used to compute
            relative paths for node IDs.

    Returns:
        List of TestInfo entries for quarantined tests.
    """
    quarantined: list[TestInfo] = []

    for test_file in tests_path.rglob("test_*.py"):
        try:
            source = test_file.read_text(encoding="utf-8")
            tree = ast.parse(source=source, filename=str(test_file))
        except (SyntaxError, UnicodeDecodeError):
            LOGGER.warning(f"Could not parse {test_file}, skipping quarantine scan")
            continue

        rel_path = str(test_file.relative_to(repo_path))

        for top_node in ast.iter_child_nodes(tree):
            if isinstance(top_node, ast.ClassDef):
                class_quarantined, class_reason, class_jira = _check_decorators(
                    decorators=top_node.decorator_list,
                )

                test_methods = [
                    item.name
                    for item in top_node.body
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and item.name.startswith("test_")
                ]

                if class_quarantined:
                    for method_name in test_methods:
                        node_id = f"{rel_path}::{top_node.name}::{method_name}"
                        quarantined.append(TestInfo(
                            node_id=node_id,
                            team=_get_team_from_node_id(node_id=node_id),
                            is_quarantined=True,
                            quarantine_reason=class_reason,
                            quarantine_jira=class_jira,
                        ))
                else:
                    for item in top_node.body:
                        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name.startswith("test_"):
                            is_q, reason, jira = _check_decorators(decorators=item.decorator_list)
                            if is_q:
                                node_id = f"{rel_path}::{top_node.name}::{item.name}"
                                quarantined.append(TestInfo(
                                    node_id=node_id,
                                    team=_get_team_from_node_id(node_id=node_id),
                                    is_quarantined=True,
                                    quarantine_reason=reason,
                                    quarantine_jira=jira,
                                ))

            if (
                isinstance(top_node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and top_node.name.startswith("test_")
            ):
                is_q, reason, jira = _check_decorators(decorators=top_node.decorator_list)
                if is_q:
                    node_id = f"{rel_path}::{top_node.name}"
                    quarantined.append(TestInfo(
                        node_id=node_id,
                        team=_get_team_from_node_id(node_id=node_id),
                        is_quarantined=True,
                        quarantine_reason=reason,
                        quarantine_jira=jira,
                    ))

    LOGGER.info(f"Found {len(quarantined)} quarantined tests")
    return quarantined


def _scan_manual_tests(tests_path: Path, repo_path: Path) -> list[TestInfo]:
    """Scan test files for manual (STD placeholder) tests.

    Identifies tests disabled via ``__test__ = False`` at three levels:
    module-level (disables all tests in the file), class-level (disables
    all methods in the class), and function-level (``func.__test__ = False``
    after the function definition).

    Args:
        tests_path: Root directory to scan for test files.
        repo_path: Root of the cloned repository, used to compute
            relative paths for node IDs.

    Returns:
        List of TestInfo entries for manual/placeholder tests.
    """
    manual_tests: list[TestInfo] = []

    for test_file in tests_path.rglob("test_*.py"):
        try:
            source = test_file.read_text(encoding="utf-8")
            tree = ast.parse(source=source, filename=str(test_file))
        except (SyntaxError, UnicodeDecodeError):
            LOGGER.warning(f"Could not parse {test_file}, skipping manual scan")
            continue

        rel_path = str(test_file.relative_to(repo_path))

        # Check for module-level __test__ = False
        module_disabled = False
        # Set of function names disabled via func.__test__ = False
        disabled_functions: set[str] = set()

        for node in ast.iter_child_nodes(tree):
            # Pattern 1: Module-level __test__ = False
            if _is_test_false_assign(node):
                module_disabled = True
                break

        if module_disabled:
            # All test functions and methods in this file are manual
            for node in ast.iter_child_nodes(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if node.name.startswith("test_"):
                        node_id = f"{rel_path}::{node.name}"
                        manual_tests.append(_manual_test_info(node_id=node_id))
                elif isinstance(node, ast.ClassDef):
                    for item in node.body:
                        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name.startswith("test_"):
                            node_id = f"{rel_path}::{node.name}::{item.name}"
                            manual_tests.append(_manual_test_info(node_id=node_id))
            continue

        # Pattern 2: func.__test__ = False (attribute assignment)
        # Walk only module-level nodes (and their non-class children like if blocks)
        # to avoid collecting class-body method.__test__ = False
        for top_node in ast.iter_child_nodes(tree):
            if isinstance(top_node, ast.ClassDef):
                continue
            for node in ast.walk(top_node):
                name = _is_attr_test_false(node)
                if name:
                    disabled_functions.add(name)

        # Process class-level and function-level __test__ = False
        for node in ast.iter_child_nodes(tree):
            # Pattern 3: Class-level __test__ = False
            if isinstance(node, ast.ClassDef):
                has_test_false = False
                class_disabled_funcs: set[str] = set()

                for item in node.body:
                    # Class body: __test__ = False
                    # Class-wide disable supersedes per-method disables,
                    # so break immediately — no need to collect class_disabled_funcs.
                    if _is_test_false_assign(item):
                        has_test_false = True
                        break

                    # Class body: method.__test__ = False
                    name = _is_attr_test_false(item)
                    if name:
                        class_disabled_funcs.add(name)

                if has_test_false:
                    # All test methods in this class are manual
                    for item in node.body:
                        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name.startswith("test_"):
                            node_id = f"{rel_path}::{node.name}::{item.name}"
                            manual_tests.append(_manual_test_info(node_id=node_id))
                elif class_disabled_funcs:
                    # Individual methods disabled
                    for item in node.body:
                        if (
                            isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
                            and item.name.startswith("test_")
                            and item.name in class_disabled_funcs
                        ):
                            node_id = f"{rel_path}::{node.name}::{item.name}"
                            manual_tests.append(_manual_test_info(node_id=node_id))

            # Function-level: func.__test__ = False (already collected above)
            elif (
                isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name.startswith("test_")
                and node.name in disabled_functions
            ):
                node_id = f"{rel_path}::{node.name}"
                manual_tests.append(_manual_test_info(node_id=node_id))

    LOGGER.info(f"Found {len(manual_tests)} manual/STD placeholder tests")
    return manual_tests


def _discover_teams(tests_path: Path) -> list[str]:
    """Auto-discover teams from tests/ subdirectories.

    Args:
        tests_path: Path to the tests directory.

    Returns:
        Sorted list of team directory names.
    """
    teams = sorted([
        entry.name
        for entry in tests_path.iterdir()
        if entry.is_dir() and not entry.name.startswith(("_", "."))
    ])
    LOGGER.info(f"Discovered {len(teams)} teams: {', '.join(teams)}")
    return teams


class PytestCollector:
    """Pytest-based test collector.

    Clones a repository, installs dependencies, and collects tests
    via ``pytest --collect-only``. Also scans for quarantined and
    manual tests via AST analysis.
    """

    def __init__(self, git_token: str | None = None, workdir: Path | None = None) -> None:
        self.git_token = git_token
        self.workdir = workdir or Path(tempfile.mkdtemp(prefix="coverage-reports-"))

    def collect(
        self,
        repo_url: str,
        branch: str,
        tests_dir: str = "tests",
        exclude_teams: list[str] | None = None,
    ) -> list[TestInfo]:
        """Collect all tests from a repository branch.

        Clones the repo, installs dependencies, and runs pytest collection.
        Also scans for quarantined and manual tests via AST.

        Args:
            repo_url: Git clone URL.
            branch: Branch name to checkout.
            tests_dir: Subdirectory containing tests.
            exclude_teams: Teams to exclude from results.

        Returns:
            Combined list of TestInfo for all discovered tests.
        """
        repo_path = clone_repo(
            url=repo_url,
            branch=branch,
            target_dir=self.workdir,
            git_token=self.git_token,
        )

        _install_deps(repo_path=repo_path)

        tests_path = repo_path / tests_dir
        exclude_set = set(exclude_teams) if exclude_teams else set()

        automated_ids = _collect_pytest_tests(repo_path=repo_path, tests_dir=tests_dir)
        gating_ids = _collect_gating_tests(repo_path=repo_path, tests_dir=tests_dir)
        quarantined_tests = _scan_quarantined_tests(tests_path=tests_path, repo_path=repo_path)
        manual_tests = _scan_manual_tests(tests_path=tests_path, repo_path=repo_path)

        quarantined_node_ids = {test.node_id for test in quarantined_tests}
        manual_node_ids = {test.node_id for test in manual_tests}

        all_tests: list[TestInfo] = []

        for node_id in automated_ids:
            team = _get_team_from_node_id(node_id=node_id)
            if team in exclude_set:
                continue
            if node_id in quarantined_node_ids:
                continue

            all_tests.append(TestInfo(
                node_id=node_id,
                team=team,
                is_gating=node_id in gating_ids,
            ))

        for test_info in manual_tests:
            if test_info.team in exclude_set:
                continue
            if test_info.node_id in quarantined_node_ids:
                continue
            all_tests.append(test_info)

        for test_info in quarantined_tests:
            if test_info.team in exclude_set:
                continue
            all_tests.append(test_info)

        LOGGER.info(
            f"Total collected: {len(all_tests)} "
            f"(automated: {len(automated_ids)}, "
            f"manual: {len(manual_tests)}, "
            f"quarantined: {len(quarantined_tests)})"
        )
        return all_tests
