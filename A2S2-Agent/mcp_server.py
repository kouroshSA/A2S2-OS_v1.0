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

import execops
import fileops
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
def stop_job(job_id: str) -> str:
    """Stop a running job by job_id (one started via run_mode, or a long-running
    mode like VR teleop)."""
    return os_ops.stop_job(job_id)


@mcp.tool()
def run_mode(mode_id: str, task: Union[str, dict] = "", params_json: Union[str, dict] = "{}",
             app_id: str = "", confirm: Union[bool, str] = False) -> str:
    """Run a mode on the A2S2-OS. SAFETY: can move real hardware. confirm=false
    returns a preview only and never dispatches; confirm=true actually runs it.
    params_json is a JSON object of overrides, e.g. '{"episodes": 5}'."""
    return os_ops.run_mode(mode_id, task, params_json, app_id, confirm)


# ---- files & code (same capabilities as the agent's LangChain tools) ----
@mcp.tool()
def read_file(path: str) -> str:
    """Read a text file (relative paths resolve under the agent workspace)."""
    return fileops.read_file(path)


@mcp.tool()
def list_dir(path: str = ".") -> str:
    """List a directory's entries with sizes."""
    return fileops.list_dir(path)


@mcp.tool()
def write_file(path: str, content: str) -> str:
    """Create or overwrite a text file (parent folders are made)."""
    return fileops.write_file(path, content)


@mcp.tool()
def edit_file(path: str, find: str, replace: str, count: int = 0) -> str:
    """Replace exact text `find` with `replace` in a file (count=0 = all)."""
    return fileops.edit_file(path, find, replace, count)


@mcp.tool()
def run_python(code: str, env: str = "", cwd: str = "", timeout: int = 120) -> str:
    """Run a Python snippet and return stdout+stderr. `env` optionally names a
    conda env (gpt/esm/xarm6sim/base) to run under."""
    return execops.run_python(code, env=env, cwd=cwd, timeout=timeout)


@mcp.tool()
def run_shell(command: str, env: str = "", cwd: str = "", timeout: int = 120) -> str:
    """Run a shell command (bash) and return stdout+stderr."""
    return execops.run_shell(command, env=env, cwd=cwd, timeout=timeout)


@mcp.tool()
def open_terminal(command: str = "", env: str = "", cwd: str = "") -> str:
    """Open a new graphical terminal window (optionally running a command, then
    staying open at a shell). Needs a display."""
    return execops.open_terminal(command, env=env, cwd=cwd)


if __name__ == "__main__":
    import os
    import sys

    # Default transport is stdio (what Claude Desktop/Code launch). Pass --http
    # (or set A2S2_MCP_HTTP=1) to serve over Streamable HTTP instead — useful for
    # MCP clients that sandbox local processes and can only reach a network URL
    # (e.g. Claude Science). The endpoint is then http://<host>:<port>/mcp.
    if "--http" in sys.argv or os.environ.get("A2S2_MCP_HTTP"):
        mcp.settings.host = os.environ.get("A2S2_MCP_HOST", "127.0.0.1")
        mcp.settings.port = int(os.environ.get("A2S2_MCP_PORT", "8971"))
        mcp.run(transport="streamable-http")
    else:
        mcp.run(transport="stdio")
