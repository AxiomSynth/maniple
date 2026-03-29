"""
Prune recovered workers tool.

Provides prune_recovered_workers for marking stale recovered sessions closed.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.session import ServerSession

if TYPE_CHECKING:
    from ..server import AppContext


logger = logging.getLogger("maniple")


def register_tools(mcp: FastMCP) -> None:
    """Register prune_recovered_workers tool on the MCP server."""

    @mcp.tool()
    async def prune_recovered_workers(
        ctx: Context[ServerSession, "AppContext"],
    ) -> dict:
        """
        Prune stale worker sessions — both recovered and managed.

        Checks ALL sessions (not just recovered) for terminal pane liveness:
        - Recovered sessions (source=event_log): emits worker_closed events
        - Managed sessions (source=registry): removes from registry directly

        Common after crashes, manual tmux cleanup, or repeated /nexus:boot.

        Returns:
            Dict with:
                - pruned: total sessions pruned (recovered + managed)
                - pruned_recovered: recovered sessions pruned
                - pruned_managed: managed sessions with dead panes removed
                - emitted_closed: worker_closed events emitted (recovered only)
                - session_ids: list of all pruned session IDs
                - errors: list of non-fatal errors encountered
        """
        app_ctx = ctx.request_context.lifespan_context
        registry = app_ctx.registry
        backend = app_ctx.terminal_backend

        # Prune managed sessions with dead panes
        managed_pruned = await registry.prune_stale_managed_sessions(backend)
        if managed_pruned:
            logger.info("Pruned %d stale managed sessions: %s", len(managed_pruned), managed_pruned)

        # Prune recovered sessions with dead panes
        report = await registry.prune_stale_recovered_sessions(backend)
        if report.errors:
            logger.warning("prune_recovered_workers encountered errors: %s", report.errors)

        all_pruned = list(report.session_ids) + managed_pruned
        return {
            "pruned": report.pruned + len(managed_pruned),
            "pruned_recovered": report.pruned,
            "pruned_managed": len(managed_pruned),
            "emitted_closed": report.emitted_closed,
            "session_ids": all_pruned,
            "errors": list(report.errors),
        }

