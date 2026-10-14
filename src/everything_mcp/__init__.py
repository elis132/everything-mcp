"""
Everything MCP - Independent local file search for agents.

Lightning-fast file search for AI agents.
"""

__version__ = "2.0.0"


def main() -> None:
    """Entry point for the ``everything-mcp`` command."""
    from everything_mcp.server import mcp

    mcp.run()
