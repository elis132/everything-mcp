import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_release_manifests_parse_and_share_project_version():
    project_version = re.search(
        r'^version\s*=\s*"([^"]+)"',
        (ROOT / "pyproject.toml").read_text(encoding="utf-8"),
        flags=re.MULTILINE,
    ).group(1)
    plugin = json.loads((ROOT / ".claude-plugin/plugin.json").read_text(encoding="utf-8"))
    marketplace = json.loads((ROOT / ".claude-plugin/marketplace.json").read_text(encoding="utf-8"))
    server = json.loads((ROOT / "server.json").read_text(encoding="utf-8"))

    assert plugin["version"] == project_version
    assert marketplace["plugins"][0]["version"] == project_version
    assert server["version"] == project_version
    assert server["packages"][0]["version"] == project_version
    assert project_version == "2.0.0"
