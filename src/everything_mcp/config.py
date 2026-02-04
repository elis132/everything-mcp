"""
Auto-detection and configuration for voidtools Everything.

Finds es.exe, detects Everything version/instance, and validates the setup.
Zero-config by default - discovers installation via PATH, common install
locations, and the Windows Registry.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

__all__ = ["EverythingConfig"]

logger = logging.getLogger("everything_mcp")

# ── Search locations for es.exe ───────────────────────────────────────────

ES_SEARCH_PATHS: list[str] = [
    os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WindowsApps"),
    r"C:\Program Files\Everything",
    r"C:\Program Files (x86)\Everything",
    r"C:\Program Files\Everything 1.5a",
    r"C:\Program Files (x86)\Everything 1.5a",
    os.path.expandvars(r"%LOCALAPPDATA%\Everything"),
    os.path.expandvars(r"%USERPROFILE%\Everything"),
    os.path.expandvars(r"%PROGRAMDATA%\Everything"),
    os.path.expandvars(r"%USERPROFILE%\scoop\shims"),
    os.path.expandvars(r"%USERPROFILE%\scoop\apps\everything\current"),
    os.path.expandvars(r"%PROGRAMDATA%\chocolatey\bin"),
]

# Suppress console window on Windows
_CREATE_NO_WINDOW: int = getattr(subprocess, "CREATE_NO_WINDOW", 0)


@dataclass
class EverythingConfig:
    """Configuration for communicating with Everything."""

    es_path: str = ""
    instance: str = ""
    timeout: int = 30
    max_results_cap: int = 1000
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    version_info: str = ""

    @property
    def is_valid(self) -> bool:
        """True when es.exe was found and Everything is responding."""
        return bool(self.es_path) and len(self.errors) == 0

    @classmethod
    def auto_detect(cls) -> EverythingConfig:
        """Auto-detect Everything installation and return a ready config.

        Detection order:
          1. ``EVERYTHING_ES_PATH`` / ``EVERYTHING_INSTANCE`` env vars
          2. ``es.exe`` on ``PATH`` (verified via ``-get-everything-version``)
          3. Common installation directories
          4. Windows Registry
          5. Instance auto-detection (default → 1.5a)
          6. Connectivity test
        """
        config = cls()

        env_path = os.environ.get("EVERYTHING_ES_PATH", "").strip()
        env_instance = os.environ.get("EVERYTHING_INSTANCE", "").strip()

        if env_instance:
            config.instance = env_instance
            logger.info("Using instance from EVERYTHING_INSTANCE=%s", env_instance)

        config.es_path = _find_es_exe(env_path)

        if not config.es_path:
            config.errors.append(
                "es.exe not found. Install from https://github.com/voidtools/es/releases "
                "or set the EVERYTHING_ES_PATH environment variable. "
                "Everything (https://www.voidtools.com/) must be installed and running."
            )
            return config

        logger.info("Found es.exe: %s", config.es_path)

        if not config.instance:
            config.instance = _detect_instance(config.es_path)
            if config.instance:
                logger.info("Auto-detected instance: %s", config.instance)

        ok, info = _test_connection(config.es_path, config.instance)
        if ok:
            config.version_info = info
            logger.info("Everything connection OK: %s", info)
        else:
            config.errors.append(
                f"Cannot connect to Everything: {info}. "
                "Ensure Everything is running (check your system tray). "
                "If you use Everything 1.5 alpha, try EVERYTHING_INSTANCE=1.5a"
            )

        return config


# ── Internal helpers ──────────────────────────────────────────────────────


def _find_es_exe(env_override: str = "") -> str:
    """Locate the es.exe executable."""
    if env_override:
        p = Path(env_override)
        if p.is_file() and p.name.lower() == "es.exe":
            if _is_everything_es(str(p)):
                return str(p)
        elif p.is_dir():
            candidate = p / "es.exe"
            if candidate.is_file() and _is_everything_es(str(candidate)):
                return str(candidate)
        logger.warning("EVERYTHING_ES_PATH='%s' not valid, continuing search", env_override)

    for name in ("es", "es.exe"):
        found = shutil.which(name)
        if found and _is_everything_es(found):
            return found

    for search_dir in ES_SEARCH_PATHS:
        candidate = Path(search_dir) / "es.exe"
        try:
            if candidate.is_file() and _is_everything_es(str(candidate)):
                return str(candidate)
        except OSError:
            continue

    return _find_via_registry()


def _is_everything_es(path: str) -> bool:
    """Verify that *path* is voidtools Everything's es.exe."""
    try:
        result = subprocess.run(
            [path, "-get-everything-version"],
            capture_output=True, text=True, timeout=5,
            creationflags=_CREATE_NO_WINDOW,
        )
        output = result.stdout.strip()
        return bool(output) and any(c.isdigit() for c in output)
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass

    try:
        result = subprocess.run(
            [path, "-version"],
            capture_output=True, text=True, timeout=5,
            creationflags=_CREATE_NO_WINDOW,
        )
        return "everything" in result.stdout.lower()
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return False


def _find_via_registry() -> str:
    """Look up the Everything install path from the Windows Registry."""
    if sys.platform != "win32":
        return ""
    try:
        import winreg
    except ImportError:
        return ""

    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        for subkey in (
            r"SOFTWARE\voidtools\Everything",
            r"SOFTWARE\WOW6432Node\voidtools\Everything",
        ):
            try:
                with winreg.OpenKey(hive, subkey) as key:
                    install_path, _ = winreg.QueryValueEx(key, "InstallPath")
                    candidate = Path(install_path) / "es.exe"
                    if candidate.is_file() and _is_everything_es(str(candidate)):
                        return str(candidate)
            except (FileNotFoundError, OSError):
                continue

    return ""


def _detect_instance(es_path: str) -> str:
    """Detect which Everything instance is running (default vs 1.5a)."""
    try:
        result = subprocess.run(
            [es_path, "-get-everything-version"],
            capture_output=True, text=True, timeout=5,
            creationflags=_CREATE_NO_WINDOW,
        )
        if result.returncode == 0 and result.stdout.strip():
            return ""
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass

    try:
        result = subprocess.run(
            [es_path, "-instance", "1.5a", "-get-everything-version"],
            capture_output=True, text=True, timeout=5,
            creationflags=_CREATE_NO_WINDOW,
        )
        if result.returncode == 0 and result.stdout.strip():
            return "1.5a"
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass

    return ""


def _test_connection(es_path: str, instance: str) -> tuple[bool, str]:
    """Verify Everything is running and responsive."""
    base = [es_path]
    if instance:
        base.extend(["-instance", instance])

    try:
        result = subprocess.run(
            [*base, "-get-everything-version"],
            capture_output=True, text=True, timeout=10,
            creationflags=_CREATE_NO_WINDOW,
        )
        if result.returncode == 0 and result.stdout.strip():
            return True, f"Everything v{result.stdout.strip()}"
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass

    try:
        result = subprocess.run(
            [*base, "-n", "1", "*.txt"],
            capture_output=True, text=True, timeout=10,
            creationflags=_CREATE_NO_WINDOW,
        )
        if result.returncode == 0:
            return True, "Everything connected (version unknown)"
        err = result.stderr.strip() or result.stdout.strip() or "Unknown error"
        return False, err
    except subprocess.TimeoutExpired:
        return False, "Connection timed out"
    except FileNotFoundError:
        return False, f"es.exe not found at {es_path}"
    except OSError as exc:
        return False, str(exc)
