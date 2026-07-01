"""LangChain tools = the agent's action surface.

Thin wrappers over the shared implementations. Two groups:

  READ_TOOLS  — knowledge lookups over the KG, read-only live OS calls, and
                read-only filesystem inspection. Safe; always available.
  RUN_TOOLS   — the things that change the world: dispatch a bench mode
                (run_mode, gated by confirm), write/edit files, and run
                Python/shell code. Only loaded when the agent is not read-only.

`run_mode` is the only tool whose confirm gate matters for hardware: confirm=False
is always preview-only, confirm=True dispatches. Whether the agent may
self-confirm is set by the system prompt (autonomous vs. approval-required). The
file/code tools are NOT hardware and are not gated by that policy — on this
kiosk they let the agent do real data work (edit a CSV, sample a FASTA, ...).
Safeguards (a sandbox / path jail) are deliberately deferred.
"""
from __future__ import annotations

from typing import Union

from langchain_core.tools import tool

import execops
import fileops
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


# --------------------------------------------------------------- filesystem (read)
@tool
def read_file(path: str) -> str:
    """Read a text file and return its contents (truncated if very large).
    Relative paths resolve under the agent workspace; absolute paths work too.
    Use before edit_file so you know what you are changing."""
    return fileops.read_file(path)


@tool
def list_dir(path: str = ".") -> str:
    """List a directory's entries with sizes. Relative paths resolve under the
    workspace. Use to find data files before reading or processing them."""
    return fileops.list_dir(path)


# --------------------------------------------------------------- filesystem (write)
@tool
def write_file(path: str, content: str) -> str:
    """Create or overwrite a text file with `content` (parent folders are made).
    Relative paths resolve under the workspace. Use for new scripts, outputs,
    or small data files."""
    return fileops.write_file(path, content)


@tool
def edit_file(path: str, find: str, replace: str, count: int = 0) -> str:
    """Replace occurrences of the exact text `find` with `replace` in a file.
    count=0 replaces all; a positive count limits how many. Read the file first
    so `find` matches exactly."""
    return fileops.edit_file(path, find, replace, count)


# --------------------------------------------------------------- code execution
@tool
def run_python(code: str, env: str = "", timeout: int = 120) -> str:
    """Run a Python snippet and return its stdout+stderr. This is the main way to
    do data work — e.g. add a column to a CSV, subsample a FASTA, compute stats.
    Files you write land in the workspace by default. `env` optionally names a
    conda env to run under (e.g. 'gpt', 'esm', 'xarm6sim', 'base') when you need
    libraries like pandas, biopython or torch; leave empty for the agent's env."""
    return execops.run_python(code, env=env, timeout=timeout)


@tool
def run_shell(command: str, env: str = "", cwd: str = "", timeout: int = 120) -> str:
    """Run a shell command (bash) and return its stdout+stderr. Use for quick
    file ops (head, wc, grep, cp), package checks, or invoking tools. `env`
    optionally names a conda env to run under; `cwd` sets the working directory
    (defaults to the workspace)."""
    return execops.run_shell(command, env=env, cwd=cwd, timeout=timeout)


READ_TOOLS = [list_runnables, describe_mode, search_knowledge,
              os_health, list_sessions, job_status,
              read_file, list_dir]
RUN_TOOLS = [run_mode, write_file, edit_file, run_python, run_shell]
ALL_TOOLS = READ_TOOLS + RUN_TOOLS
