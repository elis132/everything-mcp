"""Tests for everything_mcp.config auto-detection logic."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from everything_mcp.config import (
    EverythingConfig,
    _detect_instance,
    _is_everything_es,
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
            patch("everything_mcp.config._detect_instance", return_value=""),
            patch("everything_mcp.config._test_connection", return_value=(True, "Everything v1.4")),
        ):
            config = EverythingConfig.auto_detect()
            assert config.is_valid
            assert config.es_path == r"C:\es.exe"

    def test_auto_detect_with_1_5a(self):
        """Auto-detects 1.5a instance."""
        with (
            patch("everything_mcp.config._find_es_exe", return_value=r"C:\es.exe"),
            patch("everything_mcp.config._detect_instance", return_value="1.5a"),
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
            patch("everything_mcp.config._detect_instance", return_value=""),
            patch("everything_mcp.config._test_connection", return_value=(True, "OK")),
        ):
            config = EverythingConfig.auto_detect()
            assert config.instance == "custom"

    def test_auto_detect_connection_fail(self):
        """When Everything isn't running, config records the error."""
        with (
            patch("everything_mcp.config._find_es_exe", return_value=r"C:\es.exe"),
            patch("everything_mcp.config._detect_instance", return_value=""),
            patch("everything_mcp.config._test_connection", return_value=(False, "IPC not found")),
        ):
            config = EverythingConfig.auto_detect()
            assert not config.is_valid
            assert "IPC not found" in config.errors[0]


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


# ── _detect_instance ──────────────────────────────────────────────────────


class TestDetectInstance:
    def test_default_instance_works(self):
        mock_result = MagicMock()
        mock_result.stdout = "1.4.1.1024\n"
        mock_result.returncode = 0

        with patch("subprocess.run", return_value=mock_result):
            assert _detect_instance(r"C:\es.exe") == ""

    def test_1_5a_instance(self):
        """Default fails, 1.5a succeeds."""
        default_fail = MagicMock()
        default_fail.returncode = 1
        default_fail.stdout = ""

        alpha_ok = MagicMock()
        alpha_ok.returncode = 0
        alpha_ok.stdout = "1.5.0.1355a\n"

        with patch("subprocess.run", side_effect=[default_fail, alpha_ok]):
            assert _detect_instance(r"C:\es.exe") == "1.5a"

    def test_neither_instance(self):
        """Both default and 1.5a fail."""
        fail = MagicMock()
        fail.returncode = 1
        fail.stdout = ""

        with patch("subprocess.run", return_value=fail):
            assert _detect_instance(r"C:\es.exe") == ""


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
