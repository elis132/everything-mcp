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
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

if sys.platform == "win32":
    import msvcrt
else:
    import fcntl

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

# Everything.ini index switches, with Everything's defaults (only file size
# and date modified are indexed out of the box).  Properties that are not
# indexed are read from disk per file when a query filters on them.
INDEX_DEFAULTS: dict[str, bool] = {
    "index_size": True,
    "index_date_modified": True,
    "index_date_created": False,
    "index_date_accessed": False,
    "index_attributes": False,
}

# Startup probes.  A healthy Everything answers in ~50 ms; a busy one blocks
# every probe, so keep the total well under MCP client startup timeouts.
_PROBE_TIMEOUT = 4
_TIMED_OUT = "Connection timed out (Everything may be busy with a slow query)"

# Machine-wide lock file.  Every everything-mcp process holds it while one of
# its es.exe calls is inside Everything, so sessions never queue a call behind
# another session's slow query (the queued call is what freezes Everything).
_MACHINE_LOCK = os.path.join(tempfile.gettempdir(), "everything-mcp.lock")
_DETECT_LOCK_WAIT = 2.0  # seconds detection waits for another session's query

BUSY_OTHER_SESSION = (
    "Everything is busy with a query from another everything-mcp session. Wait and "
    "retry; if this persists, ask the user to restart Everything."
)


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
    indexed: dict[str, bool] = field(default_factory=lambda: dict(INDEX_DEFAULTS))

    @property
    def is_valid(self) -> bool:
        """True when es.exe was found and Everything is responding."""
        return bool(self.es_path) and len(self.errors) == 0

    @classmethod
    def auto_detect(cls) -> EverythingConfig:
        """Auto-detect Everything installation and return a ready config.

        Detection order:
          1. ``EVERYTHING_ES_PATH`` / ``EVERYTHING_INSTANCE`` env vars
          2. ``es.exe`` on ``PATH`` (identified via ``-h``, which works
             without Everything, then ``-get-everything-version``)
          3. Common installation directories
          4. Windows Registry
          5. Instance auto-detection (default → 1.5a)
          6. Connectivity test
          7. Index settings from Everything's ini

        Steps 2-6 hold the machine-wide lock: if another session's query is
        inside Everything, probing would queue behind it and freeze
        Everything, so detection reports busy instead (retried later).

        ``EVERYTHING_MAX_RESULTS_CAP`` optionally lowers the hard cap on
        results per search (default 1000), useful for limiting token usage.
        """
        config = cls()

        env_path = os.environ.get("EVERYTHING_ES_PATH", "").strip()
        env_instance = os.environ.get("EVERYTHING_INSTANCE", "").strip()
        env_max_results_cap = os.environ.get("EVERYTHING_MAX_RESULTS_CAP", "").strip()

        if env_max_results_cap:
            try:
                cap = int(env_max_results_cap)
                if cap > 0:
                    config.max_results_cap = cap
                else:
                    logger.warning(
                        "EVERYTHING_MAX_RESULTS_CAP='%s' must be positive, ignoring",
                        env_max_results_cap,
                    )
            except ValueError:
                logger.warning(
                    "EVERYTHING_MAX_RESULTS_CAP='%s' is not a valid integer, ignoring",
                    env_max_results_cap,
                )

        if env_instance:
            config.instance = env_instance
            logger.info("Using instance from EVERYTHING_INSTANCE=%s", env_instance)

        lock_fd = _wait_machine_lock(timeout=_DETECT_LOCK_WAIT)
        if lock_fd is None:
            config.errors.append(BUSY_OTHER_SESSION)
            return config
        try:
            config._detect(env_path, env_instance)
        finally:
            _release_machine_lock(lock_fd)
        return config

    def _detect(self, env_path: str, env_instance: str) -> None:
        """Steps 2-7 of :meth:`auto_detect` (caller holds the machine lock)."""
        config = self
        config.es_path = _find_es_exe(env_path)

        if not config.es_path:
            config.errors.append(
                "es.exe not found. Install from https://github.com/voidtools/es/releases "
                "or set the EVERYTHING_ES_PATH environment variable. "
                "Everything (https://www.voidtools.com/) must be installed and running."
            )
            return

        logger.info("Found es.exe: %s", config.es_path)

        busy = False
        if not config.instance:
            config.instance, busy = _detect_instance(config.es_path)
            if config.instance:
                logger.info("Auto-detected instance: %s", config.instance)

        if busy:  # one unanswered probe is enough; a second would queue too
            ok, info = False, _TIMED_OUT
        else:
            ok, info = _test_connection(config.es_path, config.instance)

        # A wrong EVERYTHING_INSTANCE is a common misconfiguration: most
        # Everything installs (including 1.5) run on the *default* instance,
        # where passing -instance breaks the IPC lookup.  If the explicit
        # instance doesn't respond, fall back to auto-detection instead of
        # failing outright.  A timeout is different: that instance exists and
        # is busy, and switching would query some other Everything.
        if not ok and env_instance and info != _TIMED_OUT:
            detected, detected_busy = _detect_instance(config.es_path)
            retry_ok, retry_info = (
                (False, _TIMED_OUT) if detected_busy else _test_connection(config.es_path, detected)
            )
            if retry_ok:
                warning = (
                    f"EVERYTHING_INSTANCE='{env_instance}' does not respond; "
                    f"using the {detected or 'default'} instance instead. "
                    "Remove EVERYTHING_INSTANCE unless you configured a named "
                    "instance in Everything (Tools > Options > General)."
                )
                config.warnings.append(warning)
                logger.warning(warning)
                config.instance = detected
                ok, info = retry_ok, retry_info

        config.indexed = _read_index_settings(config.instance, config.es_path)

        if ok:
            config.version_info = info
            logger.info("Everything connection OK: %s", info)
        elif info == _TIMED_OUT:
            config.errors.append(
                "Everything did not answer: it is probably busy with a slow query. "
                "The connection is retried on later tool calls (at most every 10 s); "
                "if Everything stays busy, ask the user to restart it."
            )
        else:
            if env_instance:
                hint = (
                    f"You set EVERYTHING_INSTANCE='{env_instance}' - try removing it. "
                    "It is only needed when Everything runs under a named instance "
                    "(Tools > Options > General), which most installs do not."
                )
            else:
                hint = (
                    "If Everything runs under a named instance, "
                    "set EVERYTHING_INSTANCE to its name (e.g. 1.5a)."
                )
            config.errors.append(
                f"Cannot connect to Everything: {info}. "
                f"Ensure Everything is running (check your system tray). {hint}"
            )


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
    """Verify that *path* is voidtools Everything's es.exe.

    ``-h`` comes first because it answers without Everything: a busy or hung
    Everything must not get a valid es.exe rejected (#18).  (``-version``
    prints only the ES version number, so it cannot identify the binary.)
    """
    try:
        result = subprocess.run(
            [path, "-h"],
            capture_output=True,
            text=True,
            timeout=_PROBE_TIMEOUT,
            creationflags=_CREATE_NO_WINDOW,
        )
        if "everything" in result.stdout.lower():
            return True
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass

    try:
        result = subprocess.run(
            [path, "-get-everything-version"],
            capture_output=True,
            text=True,
            timeout=_PROBE_TIMEOUT,
            creationflags=_CREATE_NO_WINDOW,
        )
        output = result.stdout.strip()
        return bool(output) and any(c.isdigit() for c in output)
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return False


def _find_via_registry() -> str:
    """Look up es.exe in Everything's install path from the Windows Registry."""
    for install_dir in _registry_install_dirs():
        candidate = install_dir / "es.exe"
        if candidate.is_file() and _is_everything_es(str(candidate)):
            return str(candidate)
    return ""


def _registry_install_dirs() -> list[Path]:
    """Everything's install directories from the Windows Registry.

    The 1.4 installer writes ``InstallLocation``; ``InstallPath`` is kept
    as a fallback for older or third-party installs.
    """
    if sys.platform != "win32":
        return []
    try:
        import winreg
    except ImportError:
        return []

    dirs: list[Path] = []
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        for subkey in (
            r"SOFTWARE\voidtools\Everything",
            r"SOFTWARE\WOW6432Node\voidtools\Everything",
        ):
            try:
                key = winreg.OpenKey(hive, subkey)
            except OSError:
                continue
            with key:
                for value in ("InstallLocation", "InstallPath"):
                    try:
                        dirs.append(Path(winreg.QueryValueEx(key, value)[0]))
                    except OSError:
                        continue
    return dirs


def _detect_instance(es_path: str) -> tuple[str, bool]:
    """Detect which Everything instance is running (default vs 1.5a).

    Returns ``(instance, busy)``.  A probe that times out means that
    instance exists but is busy: stop there, since every further probe
    would queue behind the same slow query.
    """
    for instance in ("", "1.5a"):
        args = [es_path, *(["-instance", instance] if instance else []), "-get-everything-version"]
        try:
            result = subprocess.run(
                args,
                capture_output=True,
                text=True,
                timeout=_PROBE_TIMEOUT,
                creationflags=_CREATE_NO_WINDOW,
            )
        except subprocess.TimeoutExpired:
            return instance, True
        except (FileNotFoundError, OSError):
            continue
        if result.returncode == 0 and result.stdout.strip():
            return instance, False
    return "", False


def _test_connection(es_path: str, instance: str) -> tuple[bool, str]:
    """Verify Everything is running and responsive."""
    base = [es_path]
    if instance:
        base.extend(["-instance", instance])

    try:
        result = subprocess.run(
            [*base, "-get-everything-version"],
            capture_output=True,
            text=True,
            timeout=_PROBE_TIMEOUT,
            creationflags=_CREATE_NO_WINDOW,
        )
        if result.returncode == 0 and result.stdout.strip():
            return True, f"Everything v{result.stdout.strip()}"
        err = result.stderr.strip() or result.stdout.strip() or "Unknown error"
        return False, err
    except subprocess.TimeoutExpired:
        return False, _TIMED_OUT
    except FileNotFoundError:
        return False, f"es.exe not found at {es_path}"
    except OSError as exc:
        return False, str(exc)


def _read_index_settings(instance: str, es_path: str) -> dict[str, bool]:
    """Read the index_* switches from Everything's ini, or return the defaults.

    Settings live in %APPDATA%\\Everything unless the ini next to
    Everything.exe says ``app_data=0`` (portable installs).  Everything.exe is
    looked for next to es.exe, in the registry InstallPath and in Program
    Files.  Named instances use ``Everything-<instance>.ini``.
    """
    name = f"Everything-{instance}.ini" if instance else "Everything.ini"
    exe_dirs = [Path(es_path).parent, *_registry_install_dirs()]
    for env in ("ProgramFiles", "ProgramFiles(x86)"):
        program_files = os.environ.get(env)
        if not program_files:
            continue
        exe_dirs.append(Path(program_files) / "Everything")
        if instance:
            exe_dirs.append(Path(program_files) / f"Everything {instance}")

    candidates = [d / name for d in exe_dirs if _read_ini(d / name).get("app_data") == "0"]
    appdata = os.environ.get("APPDATA")
    if appdata:
        candidates.append(Path(appdata) / "Everything" / name)

    for ini in candidates:
        values = _read_ini(ini)
        if any(key in values for key in INDEX_DEFAULTS):
            logger.info("Index settings from %s", ini)
            return {
                key: values[key] == "1" if key in values else default
                for key, default in INDEX_DEFAULTS.items()
            }
    return dict(INDEX_DEFAULTS)


def _read_ini(ini: Path) -> dict[str, str]:
    """Parse ``key=value`` lines of an Everything ini file ({} if unreadable)."""
    try:
        text = ini.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return {}
    pairs = (line.partition("=") for line in text.splitlines())
    return {k.strip(): v.strip() for k, sep, v in pairs if sep}


# ── Machine-wide lock ─────────────────────────────────────────────────────


def _try_machine_lock() -> int | None:
    """Lock ``_MACHINE_LOCK``: its fd, None if another holder, -1 if unusable."""
    try:
        fd = os.open(_MACHINE_LOCK, os.O_RDWR | os.O_CREAT, 0o600)
    except OSError:
        return -1  # no writable temp dir: run without cross-session locking
    try:
        if sys.platform == "win32":
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        return None
    return fd


def _release_machine_lock(fd: int) -> None:
    if fd < 0:
        return
    try:
        if sys.platform == "win32":
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    except OSError:
        pass
    finally:
        os.close(fd)  # also drops a flock; the OS drops both if we crash


def _wait_machine_lock(timeout: float) -> int | None:
    """Blocking variant for startup: the lock fd, or None if still held."""
    deadline = time.monotonic() + timeout
    while (fd := _try_machine_lock()) is None:
        if time.monotonic() >= deadline:
            return None
        time.sleep(0.05)
    return fd
