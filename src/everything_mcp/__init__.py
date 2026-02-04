"""
Everything MCP — The definitive MCP server for voidtools Everything.

Lightning-fast file search for AI agents.
"""

__version__ = "1.0.2"


def main() -> None:
    """Entry point for the ``everything-mcp`` command."""
    from everything_mcp.server import mcp

    mcp.run()
