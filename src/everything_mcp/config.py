"""Configuration for the independent local file index; no external search app."""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass, field
from pathlib import Path


def _default_database() -> Path:
    base = Path(
        os.environ.get("LOCALAPPDATA")
        or os.environ.get("XDG_CACHE_HOME")
        or (Path.home() / ".cache")
    )
    return base / "everything-mcp" / "index.sqlite3"


@dataclass
class EverythingConfig:
    """Roots are explicit directory scopes; defaults cover only the current user home."""

    roots: list[Path] = field(default_factory=lambda: [Path.home()])
    db_path: Path = field(default_factory=_default_database)
    timeout: float = 10.0
    max_results_cap: int = 1000
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.roots = list(dict.fromkeys(Path(p).expanduser().resolve() for p in self.roots))
        self.db_path = Path(self.db_path).expanduser().resolve()
        if not self.roots:
            self.errors.append("At least one indexing root is required")
        for root in self.roots:
            if not root.is_dir():
                self.errors.append(f"Indexing root is not a directory: {root}")
        if not math.isfinite(self.timeout) or self.timeout <= 0:
            self.errors.append("Query timeout must be a positive finite number")
        if self.max_results_cap < 1:
            self.errors.append("Maximum result cap must be positive")

    @property
    def is_valid(self) -> bool:
        return not self.errors

    @classmethod
    def from_env(cls) -> EverythingConfig:
        """EVERYTHING_MCP_ROOTS is a JSON array; EVERYTHING_MCP_DB is a database path."""
        errors: list[str] = []
        roots = [Path.home()]
        raw = os.environ.get("EVERYTHING_MCP_ROOTS")
        if raw is not None:
            try:
                decoded = json.loads(raw)
                if (
                    not isinstance(decoded, list)
                    or not decoded
                    or any(not isinstance(p, str) or not p.strip() for p in decoded)
                ):
                    raise ValueError("expected a nonempty JSON array of directory paths")
                roots = [Path(p) for p in decoded]
            except (ValueError, TypeError) as exc:
                errors.append(f"Invalid EVERYTHING_MCP_ROOTS: {exc}")
        db = Path(os.environ.get("EVERYTHING_MCP_DB") or _default_database())
        try:
            timeout = float(os.environ.get("EVERYTHING_MCP_TIMEOUT", "10"))
        except ValueError:
            timeout = 10.0
            errors.append("Invalid EVERYTHING_MCP_TIMEOUT: expected positive seconds")
        try:
            cap = int(os.environ.get("EVERYTHING_MCP_MAX_RESULTS", "1000"))
        except ValueError:
            cap = 1000
            errors.append("Invalid EVERYTHING_MCP_MAX_RESULTS: expected a positive integer")
        return cls(roots=roots, db_path=db, timeout=timeout, max_results_cap=cap, errors=errors)

    @classmethod
    def auto_detect(cls) -> EverythingConfig:
        """Compatibility alias for environment configuration; probes no installations."""
        return cls.from_env()
