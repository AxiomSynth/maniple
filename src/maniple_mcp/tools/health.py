"""
Health check tool.

Provides a lightweight health check for verifying maniple is responsive.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.session import ServerSession

if TYPE_CHECKING:
    from ..server import AppContext


def register_tools(mcp: FastMCP) -> None:
    """Register health check tool on the MCP server."""

    @mcp.tool()
    async def health(
        ctx: Context[ServerSession, "AppContext"],
    ) -> dict:
        """
        Check maniple server health.

        Returns server status, uptime, registry stats, and backend info.
        Useful for verifying maniple is responsive after restart.
        """
        app_ctx = ctx.request_context.lifespan_context
        registry = app_ctx.registry
        backend = app_ctx.terminal_backend

        managed = len(registry._sessions)
        recovered = len(registry._recovered_sessions)

        return {
            "status": "ok",
            "backend": backend.backend_id if backend else "none",
            "sessions": {
                "managed": managed,
                "recovered": recovered,
                "total": managed + recovered,
            },
        }
