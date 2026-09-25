"""Tests for everything_mcp.config auto-detection logic."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from everything_mcp.config import (
    INDEX_DEFAULTS,
    EverythingConfig,
    _detect_instance,
    _is_everything_es,
    _read_index_settings,
    _test_connection,
)

# ── EverythingConfig ──────────────────────────────────────────────────────


class TestEverythingConfig:
    def test_default_is_invalid(self):
        config = EverythingConfig()
        assert not config.is_valid  # no es_path

    def test_valid_config(self, valid_config):
        assert valid_config.is_valid
        assert valid_config.es_path
        assert len(valid_config.errors) == 0

    def test_config_with_errors_is_invalid(self):
        config = EverythingConfig(
            es_path=r"C:\somewhere\es.exe",
            errors=["Cannot connect"],
        )
        assert not config.is_valid

    def test_auto_detect_no_es_exe(self):
        """When es.exe can't be found, config has errors."""
        with patch("everything_mcp.config._find_es_exe", return_value=""):
            config = EverythingConfig.auto_detect()
            assert not config.is_valid
            assert len(config.errors) > 0
            assert "es.exe" in config.errors[0]

    def test_auto_detect_success(self):
        """Happy path: es.exe found and connection OK."""
        with (
            patch("everything_mcp.config._find_es_exe", return_value=r"C:\es.exe"),
            patch("everything_mcp.config._detect_instance", return_value=("", False)),
            patch("everything_mcp.config._test_connection", return_value=(True, "Everything v1.4")),
        ):
            config = EverythingConfig.auto_detect()
            assert config.is_valid
            assert config.es_path == r"C:\es.exe"

    def test_auto_detect_with_1_5a(self):
        """Auto-detects 1.5a instance."""
        with (
            patch("everything_mcp.config._find_es_exe", return_value=r"C:\es.exe"),
            patch("everything_mcp.config._detect_instance", return_value=("1.5a", False)),
            patch("everything_mcp.config._test_connection", return_value=(True, "Everything v1.5")),
        ):
            config = EverythingConfig.auto_detect()
            assert config.is_valid
            assert config.instance == "1.5a"

    def test_auto_detect_env_instance(self):
        """EVERYTHING_INSTANCE env var is honoured."""
        with (
            patch.dict("os.environ", {"EVERYTHING_INSTANCE": "custom"}),
            patch("everything_mcp.config._find_es_exe", return_value=r"C:\es.exe"),
            patch("everything_mcp.config._detect_instance", return_value=("", False)),
            patch("everything_mcp.config._test_connection", return_value=(True, "OK")),
        ):
            config = EverythingConfig.auto_detect()
            assert config.instance == "custom"

    def test_auto_detect_env_max_results_cap(self):
        """EVERYTHING_MAX_RESULTS_CAP overrides the default cap."""
        with (
            patch.dict("os.environ", {"EVERYTHING_MAX_RESULTS_CAP": "200"}),
            patch("everything_mcp.config._find_es_exe", return_value=r"C:\es.exe"),
            patch("everything_mcp.config._detect_instance", return_value=("", False)),
            patch("everything_mcp.config._test_connection", return_value=(True, "OK")),
        ):
            config = EverythingConfig.auto_detect()
            assert config.max_results_cap == 200

    def test_auto_detect_env_max_results_cap_invalid_ignored(self):
        """A non-numeric EVERYTHING_MAX_RESULTS_CAP falls back to the default."""
        with (
            patch.dict("os.environ", {"EVERYTHING_MAX_RESULTS_CAP": "not-a-number"}),
            patch("everything_mcp.config._find_es_exe", return_value=r"C:\es.exe"),
            patch("everything_mcp.config._detect_instance", return_value=("", False)),
            patch("everything_mcp.config._test_connection", return_value=(True, "OK")),
        ):
            config = EverythingConfig.auto_detect()
            assert config.max_results_cap == 1000

    def test_auto_detect_env_max_results_cap_negative_ignored(self):
        """A non-positive EVERYTHING_MAX_RESULTS_CAP falls back to the default."""
        with (
            patch.dict("os.environ", {"EVERYTHING_MAX_RESULTS_CAP": "-5"}),
            patch("everything_mcp.config._find_es_exe", return_value=r"C:\es.exe"),
            patch("everything_mcp.config._detect_instance", return_value=("", False)),
            patch("everything_mcp.config._test_connection", return_value=(True, "OK")),
        ):
            config = EverythingConfig.auto_detect()
            assert config.max_results_cap == 1000

    def test_auto_detect_connection_fail(self):
        """When Everything isn't running, config records the error."""
        with (
            patch("everything_mcp.config._find_es_exe", return_value=r"C:\es.exe"),
            patch("everything_mcp.config._detect_instance", return_value=("", False)),
            patch("everything_mcp.config._test_connection", return_value=(False, "IPC not found")),
        ):
            config = EverythingConfig.auto_detect()
            assert not config.is_valid
            assert "IPC not found" in config.errors[0]

    def test_auto_detect_bad_env_instance_falls_back(self):
        """A wrong EVERYTHING_INSTANCE falls back to auto-detection with a warning."""

        def connection(es_path, instance):
            return (True, "OK") if instance == "" else (False, "Error 8: IPC not found")

        with (
            patch.dict("os.environ", {"EVERYTHING_INSTANCE": "1.5a"}),
            patch("everything_mcp.config._find_es_exe", return_value=r"C:\es.exe"),
            patch("everything_mcp.config._detect_instance", return_value=("", False)),
            patch("everything_mcp.config._test_connection", side_effect=connection),
        ):
            config = EverythingConfig.auto_detect()
            assert config.is_valid
            assert config.instance == ""
            assert config.warnings
            assert "EVERYTHING_INSTANCE" in config.warnings[0]

    def test_auto_detect_bad_env_instance_error_hint(self):
        """When nothing responds, the error suggests removing EVERYTHING_INSTANCE."""
        with (
            patch.dict("os.environ", {"EVERYTHING_INSTANCE": "1.5a"}),
            patch("everything_mcp.config._find_es_exe", return_value=r"C:\es.exe"),
            patch("everything_mcp.config._detect_instance", return_value=("", False)),
            patch("everything_mcp.config._test_connection", return_value=(False, "IPC not found")),
        ):
            config = EverythingConfig.auto_detect()
            assert not config.is_valid
            assert "try removing it" in config.errors[0]


# ── _is_everything_es ─────────────────────────────────────────────────────


class TestIsEverythingEs:
    def test_valid_version_output(self):
        mock_result = MagicMock()
        mock_result.stdout = "1.4.1.1024\n"
        mock_result.returncode = 0

        with patch("subprocess.run", return_value=mock_result):
            assert _is_everything_es(r"C:\es.exe") is True

    def test_not_everything(self):
        """Some other 'es' binary that doesn't support -get-everything-version."""
        with patch("subprocess.run", side_effect=FileNotFoundError):
            assert _is_everything_es(r"C:\not-es.exe") is False

    def test_timeout(self):
        import subprocess

        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="es", timeout=5)):
            # Should try fallback and also fail
            assert _is_everything_es(r"C:\slow.exe") is False

    def test_identified_while_everything_hangs(self, tmp_path, monkeypatch):
        """#18: a busy Everything must not get a valid EVERYTHING_ES_PATH rejected."""
        import subprocess

        es = tmp_path / "es.exe"
        es.write_bytes(b"")

        def fake_run(cmd, **kwargs):
            if cmd[1:] == ["-h"]:
                return MagicMock(
                    stdout="ES 1.1.0.38\nES is the command-line interface for searching "
                    "Everything from a command prompt.\n",
                    returncode=0,
                )
            raise subprocess.TimeoutExpired(cmd=cmd, timeout=5)  # Everything not answering

        monkeypatch.setenv("EVERYTHING_ES_PATH", str(es))
        with patch("subprocess.run", side_effect=fake_run):
            config = EverythingConfig.auto_detect()

        assert config.es_path == str(es)
        assert not any("es.exe not found" in e for e in config.errors)
        assert any("probably busy with a slow query" in e for e in config.errors)


# ── Detection vs a busy Everything (#18) ──────────────────────────────────


def _es_help_then_timeout(cmd, **kwargs):
    """es.exe answers -h; every call that needs Everything times out."""
    import subprocess

    if cmd[1:] == ["-h"]:
        return MagicMock(stdout="ES is the command-line interface for searching Everything\n")
    raise subprocess.TimeoutExpired(cmd=cmd, timeout=4)


class TestDetectionWhileBusy:
    def test_other_session_query_makes_detection_report_busy(self, monkeypatch):
        import everything_mcp.config as config_mod

        monkeypatch.setattr(config_mod, "_DETECT_LOCK_WAIT", 0.1)
        other = config_mod._try_machine_lock()
        try:
            with patch("subprocess.run") as run:
                config = EverythingConfig.auto_detect()
        finally:
            config_mod._release_machine_lock(other)
        run.assert_not_called()  # probing would queue behind the other query
        assert config.errors == [config_mod.BUSY_OTHER_SESSION]

    def test_busy_env_instance_is_kept(self, tmp_path, monkeypatch):
        es = tmp_path / "es.exe"
        es.write_bytes(b"")
        monkeypatch.setenv("EVERYTHING_ES_PATH", str(es))
        monkeypatch.setenv("EVERYTHING_INSTANCE", "1.5a")
        with patch("subprocess.run", side_effect=_es_help_then_timeout):
            config = EverythingConfig.auto_detect()
        assert config.instance == "1.5a"
        assert not config.warnings  # no switch to some other running Everything
        assert any("probably busy" in e for e in config.errors)

    def test_index_settings_follow_the_instance_actually_used(self, tmp_path, monkeypatch):
        import everything_mcp.config as config_mod

        es = tmp_path / "es.exe"
        es.write_bytes(b"")
        monkeypatch.setenv("EVERYTHING_ES_PATH", str(es))
        monkeypatch.setenv("EVERYTHING_INSTANCE", "wrong")
        seen = []
        monkeypatch.setattr(
            config_mod, "_read_index_settings", lambda inst, path: seen.append(inst) or {}
        )

        def fake_run(cmd, **kwargs):
            if cmd[1:] == ["-h"]:
                return MagicMock(stdout="Everything\n")
            if "wrong" in cmd:
                return MagicMock(returncode=1, stdout="", stderr="IPC window not found")
            if "1.5a" in cmd:
                return MagicMock(returncode=1, stdout="", stderr="")
            return MagicMock(returncode=0, stdout="1.4.1.1032\n", stderr="")

        with patch("subprocess.run", side_effect=fake_run):
            config = EverythingConfig.auto_detect()
        assert config.instance == ""
        assert seen == [""]


# ── _read_index_settings ──────────────────────────────────────────────────


class TestReadIndexSettings:
    def test_defaults_without_ini(self, tmp_path, monkeypatch):
        monkeypatch.setenv("APPDATA", str(tmp_path))
        settings = _read_index_settings("", str(tmp_path / "es.exe"))
        assert settings == INDEX_DEFAULTS

    def test_reads_appdata_ini(self, tmp_path, monkeypatch):
        monkeypatch.setenv("APPDATA", str(tmp_path))
        (tmp_path / "Everything").mkdir()
        (tmp_path / "Everything" / "Everything.ini").write_text(
            "﻿[Everything]\nindex_date_created=1\nindex_attributes=0\n", encoding="utf-8"
        )
        settings = _read_index_settings("", str(tmp_path / "es.exe"))
        assert settings["index_date_created"] is True
        assert settings["index_attributes"] is False
        assert settings["index_size"] is True  # missing key keeps Everything's default

    def test_portable_ini_next_to_es_wins(self, tmp_path, monkeypatch):
        monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
        (tmp_path / "Everything.ini").write_text("app_data=0\nindex_date_accessed=1\n")
        settings = _read_index_settings("", str(tmp_path / "es.exe"))
        assert settings["index_date_accessed"] is True

    def test_named_instance_ini(self, tmp_path, monkeypatch):
        monkeypatch.setenv("APPDATA", str(tmp_path))
        (tmp_path / "Everything").mkdir()
        (tmp_path / "Everything" / "Everything-1.5a.ini").write_text("index_date_created=1\n")
        assert _read_index_settings("1.5a", str(tmp_path / "es.exe"))["index_date_created"]
        assert not _read_index_settings("", str(tmp_path / "es.exe"))["index_date_created"]


# ── _detect_instance ──────────────────────────────────────────────────────


class TestDetectInstance:
    def test_default_instance_works(self):
        mock_result = MagicMock()
        mock_result.stdout = "1.4.1.1024\n"
        mock_result.returncode = 0

        with patch("subprocess.run", return_value=mock_result):
            assert _detect_instance(r"C:\es.exe") == ("", False)

    def test_1_5a_instance(self):
        """Default fails, 1.5a succeeds."""
        default_fail = MagicMock()
        default_fail.returncode = 1
        default_fail.stdout = ""

        alpha_ok = MagicMock()
        alpha_ok.returncode = 0
        alpha_ok.stdout = "1.5.0.1355a\n"

        with patch("subprocess.run", side_effect=[default_fail, alpha_ok]):
            assert _detect_instance(r"C:\es.exe") == ("1.5a", False)

    def test_neither_instance(self):
        """Both default and 1.5a fail."""
        fail = MagicMock()
        fail.returncode = 1
        fail.stdout = ""

        with patch("subprocess.run", return_value=fail):
            assert _detect_instance(r"C:\es.exe") == ("", False)

    def test_busy_default_instance_skips_1_5a_probe(self):
        """A timeout means the default instance exists but is busy (#18)."""
        import subprocess

        with patch(
            "subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="es", timeout=4)
        ) as run:
            assert _detect_instance(r"C:\es.exe") == ("", True)
        assert run.call_count == 1

    def test_busy_1_5a_instance_is_reported_as_busy(self):
        """Default instance absent (fails fast), 1.5a present but busy (times out)."""
        import subprocess

        absent = MagicMock(returncode=1, stdout="")
        busy = subprocess.TimeoutExpired(cmd="es", timeout=4)
        with patch("subprocess.run", side_effect=[absent, busy]):
            assert _detect_instance(r"C:\es.exe") == ("1.5a", True)


class TestRegistryInstallDirs:
    def test_reads_installer_install_location(self, monkeypatch):
        """Everything's 1.4 installer writes InstallLocation, not InstallPath."""
        import sys
        import types

        from everything_mcp.config import _registry_install_dirs

        values = {r"SOFTWARE\voidtools\Everything": {"InstallLocation": r"D:\Apps\Everything"}}

        class Key:
            def __init__(self, subkey):
                self.values = values[subkey]

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        def open_key(hive, subkey):
            if hive != "HKLM" or subkey not in values:
                raise FileNotFoundError(subkey)
            return Key(subkey)

        def query_value(key, name):
            if name not in key.values:
                raise FileNotFoundError(name)
            return key.values[name], 1

        fake = types.SimpleNamespace(
            HKEY_LOCAL_MACHINE="HKLM",
            HKEY_CURRENT_USER="HKCU",
            OpenKey=open_key,
            QueryValueEx=query_value,
        )
        monkeypatch.setitem(sys.modules, "winreg", fake)
        monkeypatch.setattr(sys, "platform", "win32")
        assert [str(d) for d in _registry_install_dirs()] == [str(Path(r"D:\Apps\Everything"))]


# ── _test_connection ──────────────────────────────────────────────────────


class TestTestConnection:
    def test_version_query_succeeds(self):
        mock_result = MagicMock()
        mock_result.stdout = "1.4.1.1024\n"
        mock_result.returncode = 0

        with patch("subprocess.run", return_value=mock_result):
            ok, info = _test_connection(r"C:\es.exe", "")
            assert ok is True
            assert "1.4.1.1024" in info

    def test_connection_timeout(self):
        import subprocess

        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="es", timeout=10)):
            ok, info = _test_connection(r"C:\es.exe", "")
            assert ok is False
            assert "timed out" in info.lower()
