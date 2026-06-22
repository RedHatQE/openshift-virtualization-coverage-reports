"""Base collector interface and shared data types.

Defines the TestInfo dataclass and CollectorProtocol for test collectors.
Each collector (pytest, Go/Ginkgo, etc.) implements the protocol to provide
a uniform collection interface.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable


@dataclass
class TestInfo:
    """Information about a single collected test.

    Attributes:
        node_id: Unique test identifier (e.g., pytest node ID).
        team: Team name derived from directory structure.
        is_manual: True if this is an STD placeholder (``__test__ = False``).
        is_quarantined: True if the test is quarantined.
        quarantine_reason: Human-readable quarantine reason.
        quarantine_jira: Jira ticket ID for quarantined tests.
        is_gating: True if marked with ``@pytest.mark.gating``.
        markers: List of pytest marker names on this test.
    """

    node_id: str
    team: str
    is_manual: bool = False
    is_quarantined: bool = False
    quarantine_reason: str = ""
    quarantine_jira: str | None = None
    is_gating: bool = False
    markers: list[str] = field(default_factory=list)


@runtime_checkable
class CollectorProtocol(Protocol):
    """Protocol for test collectors.

    Each collector implementation handles a specific test framework
    (pytest, Go/Ginkgo, etc.) and returns a uniform list of TestInfo.
    """

    def collect(self, repo_path: Path, tests_dir: str = "tests") -> list[TestInfo]:
        """Collect all tests from the given repository path.

        Args:
            repo_path: Root path of the cloned repository.
            tests_dir: Subdirectory containing tests (default: ``tests``).

        Returns:
            List of TestInfo for all discovered tests.
        """
        ...
