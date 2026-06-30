"""MCP server exposing the A2S2-OS operations as model-agnostic tools.

Same logic as the agent's LangChain tools (both call os_ops), but spoken over
the Model Context Protocol, so any MCP client — Claude Desktop, Claude Code, or
another agent — can list and run things on the OS without going through
A2S2-Agent at all. MCP is model-agnostic, so this is the "drive the OS from
anything" seam.

Run over stdio (what Claude Desktop/Code launch):

    python mcp_server.py

Register it with an MCP client by pointing at this file in the target conda env.
See README "MCP server" for the client config snippet.
"""
from __future__ import annotations

from typing import Union

from mcp.server.fastmcp import FastMCP

import os_ops

mcp = FastMCP("a2s2-os")


@mcp.tool()
def list_runnables() -> str:
    """List every app and mode that can be run on the A2S2-OS, with a one-line
    summary of each."""
    return os_ops.list_runnables()


@mcp.tool()
def describe_mode(mode_id: str, app_id: str = "") -> str:
    """Describe one mode in detail: what it does, the command it runs, and its
    full parameter spec. app_id is optional, only to disambiguate."""
    return os_ops.describe_mode(mode_id, app_id)


@mcp.tool()
def search_knowledge(query: str) -> str:
    """Search the knowledge graph (apps, modes, params, protocols) for a keyword
    and return matching runnables."""
    return os_ops.search_knowledge(query)


@mcp.tool()
def os_health() -> str:
    """Check the live A2S2-OS: reachable, which app is active, whether it is
    ready to run."""
    return os_ops.os_health()


@mcp.tool()
def list_sessions() -> str:
    """List recent saved runs from the active app (newest first)."""
    return os_ops.list_sessions()


@mcp.tool()
def job_status(job_id: str) -> str:
    """Get the status, outcome, and recent log of a job."""
    return os_ops.job_status(job_id)


@mcp.tool()
def run_mode(mode_id: str, task: Union[str, dict] = "", params_json: Union[str, dict] = "{}",
             app_id: str = "", confirm: Union[bool, str] = False) -> str:
    """Run a mode on the A2S2-OS. SAFETY: can move real hardware. confirm=false
    returns a preview only and never dispatches; confirm=true actually runs it.
    params_json is a JSON object of overrides, e.g. '{"episodes": 5}'."""
    return os_ops.run_mode(mode_id, task, params_json, app_id, confirm)


if __name__ == "__main__":
    mcp.run(transport="stdio")
