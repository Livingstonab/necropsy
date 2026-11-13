"""The MCP server: a stdio adapter over the shared analysis pipeline (R-11, E8).

Three tools — analyze_repo, get_exhibit, list_graveyard — and no business logic.
The server, the web app and the CLI all go through the same `AnalysisService`,
the same scorer and the same SQLite file.
"""

from necropsy.mcp.server import NecropsyTools, build_server, main, serve

__all__ = ["NecropsyTools", "build_server", "main", "serve"]
