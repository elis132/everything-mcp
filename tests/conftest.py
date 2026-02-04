"""Shared test fixtures for everything-mcp tests."""

from __future__ import annotations

import pytest

from everything_mcp.backend import EverythingBackend, SearchResult
from everything_mcp.config import EverythingConfig


@pytest.fixture
def valid_config() -> EverythingConfig:
    """A valid EverythingConfig (as if auto-detection succeeded)."""
    return EverythingConfig(
        es_path=r"C:\Program Files\Everything\es.exe",
        instance="",
        timeout=30,
        max_results_cap=1000,
        version_info="Everything v1.4.1.1024",
    )


@pytest.fixture
def config_15a() -> EverythingConfig:
    """Config for Everything 1.5 alpha."""
    return EverythingConfig(
        es_path=r"C:\Program Files\Everything 1.5a\es.exe",
        instance="1.5a",
        timeout=30,
        max_results_cap=1000,
        version_info="Everything v1.5.0.1355a",
    )


@pytest.fixture
def invalid_config() -> EverythingConfig:
    """An invalid config (es.exe not found)."""
    return EverythingConfig(
        errors=["es.exe not found"],
    )


@pytest.fixture
def backend(valid_config: EverythingConfig) -> EverythingBackend:
    """Backend with a valid config."""
    return EverythingBackend(valid_config)


@pytest.fixture
def sample_results() -> list[SearchResult]:
    """A set of sample SearchResult objects for testing formatters."""
    return [
        SearchResult(
            path=r"C:\Projects\app\main.py",
            name="main.py",
            size=2048,
            date_modified="2026-01-15 10:30:00",
            date_created="2025-12-01 09:00:00",
            extension="py",
        ),
        SearchResult(
            path=r"C:\Projects\app\utils.py",
            name="utils.py",
            size=512,
            date_modified="2026-01-14 14:20:00",
            date_created="2025-12-01 09:00:00",
            extension="py",
        ),
        SearchResult(
            path=r"C:\Projects\app\src",
            name="src",
            is_dir=True,
            date_modified="2026-01-15 10:30:00",
        ),
    ]
