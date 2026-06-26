"""Configuration loader for coverage reports.

Loads and validates YAML config + environment variables.
All configuration comes from external sources — zero hardcoded values.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

LOGGER = logging.getLogger(__name__)


@dataclass
class VersionConfig:
    """Branch-to-version mapping for a test repository.

    Attributes:
        branch: Git branch name to clone (e.g., ``cnv-4.22``).
        version: Y-stream version label (e.g., ``4.22``).
    """

    branch: str
    version: str


@dataclass
class RepoConfig:
    """Configuration for a test repository to collect from.

    Attributes:
        name: Repository display name.
        url: Git clone URL.
        collector: Collector type (e.g., ``pytest``).
        versions: List of branch-to-version mappings.
        exclude_teams: Teams to exclude from reports for this repo.
    """

    name: str
    url: str
    collector: str
    versions: list[VersionConfig]
    exclude_teams: list[str] = field(default_factory=list)


@dataclass
class RPConfig:
    """ReportPortal query settings.

    Attributes:
        base_url: RP instance base URL (from ``RP_URL`` env var).
        project: RP project name (from ``RP_PROJECT`` env var).
        token: RP API token (from ``RP_TOKEN`` env var).
        since_days: Only fetch launches from the last N days.
        max_bundles: Limit failure analysis to N most recent bundles.
        stale_days: Flag tests older than N days as stale.
        max_launches: Max launches to process (0 = all).
        arch: Architectures to report on.
        full: Whether to include per-test details.
    """

    base_url: str
    project: str
    token: str
    since_days: int = 14
    max_bundles: int = 5
    stale_days: int = 30
    max_launches: int = 0
    arch: list[str] = field(default_factory=lambda: ["amd64"])
    full: bool = True


@dataclass
class ServerConfig:
    """HTTP server settings.

    Attributes:
        port: Port number to listen on.
        output_dir: Directory path for generated reports.
    """

    port: int = 8080
    output_dir: str = "/data/reports"


@dataclass
class AppConfig:
    """Top-level application configuration.

    Attributes:
        repos: List of test repositories to process.
        rp: ReportPortal settings.
        team_mapping: RP TEAM attribute → display team name.
        team_strip_suffixes: Suffixes stripped during RP TEAM normalization.
        team_aliases: Directory team name → target team name aliases.
        server: HTTP server settings.
    """

    repos: list[RepoConfig]
    rp: RPConfig
    team_mapping: dict[str, str]
    team_strip_suffixes: list[str]
    team_aliases: dict[str, str]
    server: ServerConfig


class ConfigError(Exception):
    """Raised when configuration is invalid or incomplete."""


def _parse_repo(raw: dict[str, Any]) -> RepoConfig:
    """Parse a single repo entry from config YAML.

    Args:
        raw: Raw dict from YAML.

    Returns:
        Validated RepoConfig.

    Raises:
        ConfigError: If required fields are missing.
    """
    for required_field in ("name", "url", "collector", "versions"):
        if required_field not in raw:
            raise ConfigError(f"Repo config missing required field: {required_field}")

    versions = [
        VersionConfig(branch=ver["branch"], version=str(ver["version"]))
        for ver in raw["versions"]
    ]

    return RepoConfig(
        name=raw["name"],
        url=raw["url"],
        collector=raw["collector"],
        versions=versions,
        exclude_teams=raw.get("exclude_teams", []),
    )


def _parse_rp(raw: dict[str, Any]) -> RPConfig:
    """Parse RP settings from config YAML + env vars.

    Args:
        raw: Raw dict from YAML ``rp`` section.

    Returns:
        Validated RPConfig with secrets from env vars.

    Raises:
        ConfigError: If required env vars are missing.
    """
    base_url = os.environ.get("RP_URL", "")
    project = os.environ.get("RP_PROJECT", "")
    token = os.environ.get("RP_TOKEN", "")

    if not base_url:
        raise ConfigError("RP_URL environment variable is required")
    if not project:
        raise ConfigError("RP_PROJECT environment variable is required")
    if not token:
        raise ConfigError("RP_TOKEN environment variable is required")

    arch_raw = raw.get("arch", ["amd64"])
    arch = arch_raw if isinstance(arch_raw, list) else [arch_raw]

    return RPConfig(
        base_url=base_url,
        project=project,
        token=token,
        since_days=raw.get("since_days", 14),
        max_bundles=raw.get("max_bundles", 5),
        stale_days=raw.get("stale_days", 30),
        max_launches=raw.get("max_launches", 0),
        arch=arch,
        full=raw.get("full", True),
    )


def _parse_server(raw: dict[str, Any] | None) -> ServerConfig:
    """Parse server settings from config YAML.

    Args:
        raw: Raw dict from YAML ``server`` section, or None for defaults.

    Returns:
        ServerConfig with specified or default values.
    """
    if not raw:
        return ServerConfig()

    return ServerConfig(
        port=raw.get("port", 8080),
        output_dir=raw.get("output_dir", "/data/reports"),
    )


def load_config(config_path: Path, require_rp: bool = True) -> AppConfig:
    """Load and validate configuration from a YAML file + env vars.

    Args:
        config_path: Path to the YAML config file.
        require_rp: Whether to require RP env vars (False for dry-run).

    Returns:
        Fully validated AppConfig.

    Raises:
        ConfigError: If config is invalid or required fields are missing.
        FileNotFoundError: If config file does not exist.
    """
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    with open(config_path, encoding="utf-8") as config_file:
        raw = yaml.safe_load(config_file)

    if not raw:
        raise ConfigError("Config file is empty")

    if "repos" not in raw or not raw["repos"]:
        raise ConfigError("Config must define at least one repo in 'repos'")

    repos = [_parse_repo(raw=repo_raw) for repo_raw in raw["repos"]]

    if require_rp:
        rp_config = _parse_rp(raw=raw.get("rp", {}))
    else:
        rp_config = RPConfig(
            base_url=os.environ.get("RP_URL", ""),
            project=os.environ.get("RP_PROJECT", ""),
            token=os.environ.get("RP_TOKEN", ""),
            **{
                key: value
                for key, value in {
                    "since_days": raw.get("rp", {}).get("since_days"),
                    "max_bundles": raw.get("rp", {}).get("max_bundles"),
                    "stale_days": raw.get("rp", {}).get("stale_days"),
                    "max_launches": raw.get("rp", {}).get("max_launches"),
                    "full": raw.get("rp", {}).get("full"),
                }.items()
                if value is not None
            },
        )

    team_mapping = raw.get("team_mapping", {})
    team_strip_suffixes = raw.get("team_strip_suffixes", [])
    team_aliases = raw.get("team_aliases", {})
    server = _parse_server(raw=raw.get("server"))

    LOGGER.info(
        f"Loaded config: {len(repos)} repos, "
        f"{len(team_mapping)} team mappings, "
        f"{len(team_strip_suffixes)} strip suffixes, "
        f"{len(team_aliases)} team aliases"
    )

    return AppConfig(
        repos=repos,
        rp=rp_config,
        team_mapping=team_mapping,
        team_strip_suffixes=team_strip_suffixes,
        team_aliases=team_aliases,
        server=server,
    )
