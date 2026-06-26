"""Tests for config loading and validation."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from coverage_reports.config import (
    AppConfig,
    ConfigError,
    RPConfig,
    RepoConfig,
    ServerConfig,
    VersionConfig,
    load_config,
)


@pytest.fixture()
def minimal_config_yaml(tmp_path: Path) -> Path:
    """Create a minimal valid config YAML file."""
    config = {
        "repos": [
            {
                "name": "test-repo",
                "url": "https://github.com/example/test-repo",
                "collector": "pytest",
                "versions": [
                    {"branch": "main", "version": "1.0"},
                ],
            }
        ],
        "team_mapping": {"NETWORK": "Network"},
        "team_strip_suffixes": ["-OCS"],
        "server": {"port": 9090, "output_dir": "/tmp/reports"},
    }
    config_file = tmp_path / "config.yaml"
    config_file.write_text(data=yaml.dump(data=config), encoding="utf-8")
    return config_file


@pytest.fixture()
def full_config_yaml(tmp_path: Path) -> Path:
    """Create a full config YAML file with all sections."""
    config = {
        "repos": [
            {
                "name": "openshift-virtualization-tests",
                "url": "https://github.com/RedHatQE/openshift-virtualization-tests",
                "collector": "pytest",
                "versions": [
                    {"branch": "cnv-4.22", "version": "4.22"},
                    {"branch": "main", "version": "4.99"},
                ],
                "exclude_teams": ["chaos", "scale"],
            }
        ],
        "rp": {
            "since_days": 14,
            "max_bundles": 5,
            "stale_days": 30,
            "max_launches": 0,
            "arch": ["amd64", "s390x"],
            "full": True,
        },
        "team_mapping": {
            "VIRT-NODE": "Virt",
            "NETWORK": "Network",
        },
        "team_strip_suffixes": ["-OVN-OCS-S390X", "-OCS"],
        "server": {"port": 8080, "output_dir": "/data/reports"},
    }
    config_file = tmp_path / "config.yaml"
    config_file.write_text(data=yaml.dump(data=config), encoding="utf-8")
    return config_file


@pytest.fixture()
def rp_env_vars():
    """Set required RP environment variables."""
    env = {
        "RP_URL": "https://rp.example.com",
        "RP_PROJECT": "test-project",
        "RP_TOKEN": "test-token-123",
    }
    with patch.dict(os.environ, values=env):
        yield env


class TestLoadConfig:
    """Tests for load_config function."""

    def test_load_minimal_config(self, minimal_config_yaml: Path, rp_env_vars: dict) -> None:
        config = load_config(config_path=minimal_config_yaml)
        assert len(config.repos) == 1
        assert config.repos[0].name == "test-repo"
        assert config.repos[0].collector == "pytest"
        assert len(config.repos[0].versions) == 1
        assert config.repos[0].versions[0].branch == "main"
        assert config.repos[0].versions[0].version == "1.0"

    def test_load_full_config(self, full_config_yaml: Path, rp_env_vars: dict) -> None:
        config = load_config(config_path=full_config_yaml)
        assert len(config.repos) == 1
        assert config.repos[0].exclude_teams == ["chaos", "scale"]
        assert len(config.repos[0].versions) == 2
        assert config.rp.since_days == 14
        assert config.rp.max_bundles == 5
        assert config.rp.arch == ["amd64", "s390x"]
        assert config.team_mapping["VIRT-NODE"] == "Virt"
        assert "-OCS" in config.team_strip_suffixes

    def test_rp_env_vars_required(self, minimal_config_yaml: Path) -> None:
        with patch.dict(os.environ, values={}, clear=True):
            with pytest.raises(ConfigError, match="RP_URL"):
                load_config(config_path=minimal_config_yaml)

    def test_rp_env_vars_not_required_dry_run(self, minimal_config_yaml: Path) -> None:
        with patch.dict(os.environ, values={}, clear=True):
            config = load_config(config_path=minimal_config_yaml, require_rp=False)
            assert config.rp.base_url == ""

    def test_missing_config_file(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            load_config(config_path=tmp_path / "nonexistent.yaml")

    def test_empty_config_file(self, tmp_path: Path) -> None:
        empty_file = tmp_path / "empty.yaml"
        empty_file.write_text(data="", encoding="utf-8")
        with pytest.raises(ConfigError, match="empty"):
            load_config(config_path=empty_file)

    def test_missing_repos(self, tmp_path: Path) -> None:
        config_file = tmp_path / "config.yaml"
        config_file.write_text(data=yaml.dump(data={"rp": {}}), encoding="utf-8")
        with pytest.raises(ConfigError, match="repos"):
            load_config(config_path=config_file)

    def test_server_defaults(self, tmp_path: Path, rp_env_vars: dict) -> None:
        config = {
            "repos": [{
                "name": "r",
                "url": "https://example.com/r",
                "collector": "pytest",
                "versions": [{"branch": "main", "version": "1.0"}],
            }],
        }
        config_file = tmp_path / "config.yaml"
        config_file.write_text(data=yaml.dump(data=config), encoding="utf-8")
        app_config = load_config(config_path=config_file)
        assert app_config.server.port == 8080
        assert app_config.server.output_dir == "/data/reports"

    def test_version_coerced_to_string(self, tmp_path: Path, rp_env_vars: dict) -> None:
        config = {
            "repos": [{
                "name": "r",
                "url": "https://example.com/r",
                "collector": "pytest",
                "versions": [{"branch": "main", "version": 4.22}],
            }],
        }
        config_file = tmp_path / "config.yaml"
        config_file.write_text(data=yaml.dump(data=config), encoding="utf-8")
        app_config = load_config(config_path=config_file)
        assert app_config.repos[0].versions[0].version == "4.22"

    def test_repo_missing_required_field(self, tmp_path: Path, rp_env_vars: dict) -> None:
        config = {
            "repos": [{
                "name": "r",
                "url": "https://example.com/r",
                # missing collector and versions
            }],
        }
        config_file = tmp_path / "config.yaml"
        config_file.write_text(data=yaml.dump(data=config), encoding="utf-8")
        with pytest.raises(ConfigError, match="collector"):
            load_config(config_path=config_file)

    def test_team_aliases_loaded(self, tmp_path: Path, rp_env_vars: dict) -> None:
        config = {
            "repos": [{
                "name": "r",
                "url": "https://example.com/r",
                "collector": "pytest",
                "versions": [{"branch": "main", "version": "1.0"}],
            }],
            "team_aliases": {"observability": "install_upgrade_operators"},
        }
        config_file = tmp_path / "config.yaml"
        config_file.write_text(data=yaml.dump(data=config), encoding="utf-8")
        app_config = load_config(config_path=config_file)
        assert app_config.team_aliases == {"observability": "install_upgrade_operators"}

    def test_team_aliases_default_empty(self, minimal_config_yaml: Path, rp_env_vars: dict) -> None:
        config = load_config(config_path=minimal_config_yaml)
        assert config.team_aliases == {}
