"""Fixtures use isolated, real independent indexes and never discover installed apps."""

import pytest

from everything_mcp.backend import EverythingBackend, SearchResult
from everything_mcp.config import EverythingConfig


@pytest.fixture
def valid_config(tmp_path):
    root = tmp_path / "files"
    root.mkdir()
    (root / "main.py").write_text("print('hello')\n", encoding="utf-8")
    (root / "notes.txt").write_text("notes", encoding="utf-8")
    (root / "photo.jpg").write_bytes(b"image")
    (root / "src").mkdir()
    return EverythingConfig(roots=[root], db_path=tmp_path / "cache" / "index.sqlite3")


@pytest.fixture
async def backend(valid_config):
    instance = EverythingBackend(valid_config)
    await instance.start()
    await instance.wait_ready(15)
    try:
        yield instance
    finally:
        await instance.close()


@pytest.fixture
def sample_results():
    return [
        SearchResult(
            path=r"C:\Projects\app\main.py",
            name="main.py",
            size=2048,
            date_modified="2026-01-15 10:30:00",
            extension="py",
        ),
        SearchResult(
            path=r"C:\Projects\app\utils.py",
            name="utils.py",
            size=512,
            date_modified="2026-01-14 14:20:00",
            extension="py",
        ),
        SearchResult(path=r"C:\Projects\app\src", name="src", is_dir=True),
    ]
