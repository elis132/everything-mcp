import json
from pathlib import Path

import pytest

from everything_mcp.config import EverythingConfig


def test_default_is_only_user_home(monkeypatch):
    for name in (
        "EVERYTHING_MCP_ROOTS",
        "EVERYTHING_MCP_DB",
        "EVERYTHING_MCP_TIMEOUT",
        "EVERYTHING_MCP_MAX_RESULTS",
    ):
        monkeypatch.delenv(name, raising=False)
    config = EverythingConfig.from_env()
    assert config.is_valid
    assert config.roots == [Path.home().resolve()]
    assert config.db_path.name == "index.sqlite3"


def test_explicit_json_scopes_and_database(tmp_path, monkeypatch):
    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir()
    second.mkdir()
    monkeypatch.setenv("EVERYTHING_MCP_ROOTS", json.dumps([str(first), str(second), str(first)]))
    monkeypatch.setenv("EVERYTHING_MCP_DB", str(tmp_path / "custom.db"))
    monkeypatch.setenv("EVERYTHING_MCP_TIMEOUT", "2.5")
    monkeypatch.setenv("EVERYTHING_MCP_MAX_RESULTS", "23")
    config = EverythingConfig.from_env()
    assert config.is_valid and config.roots == [first.resolve(), second.resolve()]
    assert config.db_path == tmp_path / "custom.db"
    assert config.timeout == 2.5 and config.max_results_cap == 23


@pytest.mark.parametrize("raw", ["oops", '"D:/"', "[]", "[null]", '[""]', "[5]"])
def test_invalid_roots_fail_without_fallback_scan(raw, monkeypatch):
    monkeypatch.setenv("EVERYTHING_MCP_ROOTS", raw)
    config = EverythingConfig.from_env()
    assert not config.is_valid and "EVERYTHING_MCP_ROOTS" in config.errors[0]


def test_missing_root_is_error(tmp_path):
    config = EverythingConfig(roots=[tmp_path / "missing"], db_path=tmp_path / "db")
    assert not config.is_valid and "not a directory" in config.errors[0]


@pytest.mark.parametrize(
    "variable,value",
    [
        ("EVERYTHING_MCP_TIMEOUT", "nan"),
        ("EVERYTHING_MCP_TIMEOUT", "0"),
        ("EVERYTHING_MCP_TIMEOUT", "broken"),
        ("EVERYTHING_MCP_MAX_RESULTS", "-1"),
        ("EVERYTHING_MCP_MAX_RESULTS", "bad"),
    ],
)
def test_invalid_limits_rejected(variable, value, monkeypatch):
    monkeypatch.setenv(variable, value)
    assert not EverythingConfig.from_env().is_valid


def test_obsolete_external_environment_is_ignored(monkeypatch):
    monkeypatch.setenv("EVERYTHING_ES_PATH", "nonexistent.exe")
    monkeypatch.setenv("EVERYTHING_INSTANCE", "not-an-instance")
    assert EverythingConfig.from_env().is_valid
