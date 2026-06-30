"""LangChain tools = the agent's action surface.

Thin wrappers over os_ops (the shared implementation). Deliberately narrow:
knowledge lookups over the KG + read-only live OS calls + ONE gated dispatch
tool. There is no bash, no filesystem, no arbitrary HTTP — on a kiosk that
drives a robot the agent can only do what these tools allow.

`run_mode` is the only tool that changes the world: confirm=False is always
preview-only, confirm=True dispatches. Whether the agent may self-confirm is set
by the system prompt (autonomous vs. approval-required).
"""
from __future__ import annotations

from typing import Union

from langchain_core.tools import tool

import os_ops


@tool
def list_runnables() -> str:
    """List every app and mode that can be run on the A2S2-OS, with a one-line
    summary of each. Use this first to see what is available."""
    return os_ops.list_runnables()


@tool
def describe_mode(mode_id: str, app_id: str = "") -> str:
    """Describe one mode in detail: what it does, the script/command it runs, and
    its full parameter spec (types, choices, defaults, ranges). Use before
    running a mode so you fill parameters correctly. app_id is optional and only
    needed to disambiguate a mode id shared by two apps."""
    return os_ops.describe_mode(mode_id, app_id)


@tool
def search_knowledge(query: str) -> str:
    """Search the knowledge graph (apps, modes, parameters, curated protocols)
    for a keyword and return matching runnables. Use to map a user's words to a
    concrete mode, e.g. 'training data', 'tube', 'self-learning'."""
    return os_ops.search_knowledge(query)


@tool
def os_health() -> str:
    """Check the live A2S2-OS: whether it is reachable, which app is active, and
    whether that app is ready to run (e.g. API key present, hardware found)."""
    return os_ops.os_health()


@tool
def list_sessions() -> str:
    """List recent saved runs from the active app (newest first): label, model,
    outcome, time. Use to answer 'what was run recently' or 'did the last run
    succeed'."""
    return os_ops.list_sessions()


@tool
def job_status(job_id: str) -> str:
    """Get the status of a running or finished job: status, outcome, and the last
    few log lines. Use to follow up after run_mode dispatches a job."""
    return os_ops.job_status(job_id)


@tool
def run_mode(mode_id: str, task: Union[str, dict] = "", params_json: Union[str, dict] = "{}",
             app_id: str = "", confirm: Union[bool, str] = False) -> str:
    """Run a mode on the A2S2-OS. SAFETY: this can move real hardware. Call
    describe_mode first. run_mode with confirm=false ONLY returns a preview and
    never dispatches; confirm=true actually runs it.

    Args:
        mode_id: the mode to run (e.g. 'task', 'random', 'learn').
        task: the task string, required only for modes where needs_task is true.
        params_json: a JSON object string of parameter overrides, e.g.
            '{"episodes": 5, "model": "haiku"}'. Omit to use defaults.
        app_id: the app the mode belongs to; if it is not the active app the
            agent will switch to it first.
        confirm: must be true to actually dispatch. False returns a preview only.
    """
    return os_ops.run_mode(mode_id, task, params_json, app_id, confirm)


READ_TOOLS = [list_runnables, describe_mode, search_knowledge,
              os_health, list_sessions, job_status]
RUN_TOOLS = [run_mode]
ALL_TOOLS = READ_TOOLS + RUN_TOOLS
