"""Shared OS-operation logic — the single implementation behind both the
LangChain tools (tools.py, used by the agent) and the MCP server (mcp_server.py,
used by Claude Desktop/Code and any other MCP client).

Each function returns a human/LLM-readable string. Keeping the logic here (and
out of any framework decorator) means the two protocols stay in lockstep: add a
capability once, expose it twice.
"""
from __future__ import annotations

import json

from config import CONFIG
from kg import KNOWLEDGE
from os_client import OSClient

_os = OSClient()


# --------------------------------------------------------------- knowledge (KG)
def list_runnables() -> str:
    return KNOWLEDGE.overview()


def describe_mode(mode_id: str, app_id: str = "") -> str:
    return KNOWLEDGE.describe_mode(mode_id, app_id or None)


def search_knowledge(query: str) -> str:
    return KNOWLEDGE.search(query)


# --------------------------------------------------------------- live OS (read)
def os_health() -> str:
    if not _os.reachable():
        return (f"A2S2-OS is NOT reachable at {CONFIG.os_url}. "
                "Knowledge-graph answers still work, but nothing can be run.")
    h = _os.health()
    return json.dumps({
        "reachable": True,
        "active_app": h.get("active_app"),
        "ready": h.get("ready"),
        "api_key_present": h.get("api_key_present"),
        "apps": [a.get("id") for a in h.get("apps", [])],
    }, indent=2)


def list_sessions() -> str:
    if not _os.reachable():
        return f"A2S2-OS not reachable at {CONFIG.os_url}."
    rows = _os.sessions()
    if not rows:
        return "No saved runs yet."
    out = []
    for r in rows[:25]:
        out.append(f"- {r.get('label')}  [{r.get('model') or '-'}]  "
                   f"outcome={r.get('outcome') or '-'}  {r.get('time') or ''}")
    return "\n".join(out)


def job_status(job_id: str) -> str:
    if not _os.reachable():
        return f"A2S2-OS not reachable at {CONFIG.os_url}."
    j = _os.job(job_id)
    tail = [ln.get("t", "") for ln in (j.get("log") or [])][-8:]
    return json.dumps({
        "status": j.get("status"),
        "outcome": j.get("outcome"),
        "arm": j.get("arm"),
        "session_dir": j.get("session_dir"),
        "log_tail": tail,
    }, indent=2)


# --------------------------------------------------------------- coercion
def coerce_params(params_json) -> tuple[dict, str | None]:
    """Accept a dict, a JSON string, or empty — weak models send any of them."""
    if params_json in (None, "", "{}"):
        return {}, None
    if isinstance(params_json, dict):
        return params_json, None
    if isinstance(params_json, str):
        try:
            val = json.loads(params_json)
        except json.JSONDecodeError as e:
            return {}, f"params_json is not valid JSON: {e}"
        if not isinstance(val, dict):
            return {}, "params_json must be a JSON object, e.g. '{\"episodes\": 5}'."
        return val, None
    return {}, f"params_json must be a JSON object or string, got {type(params_json).__name__}."


def coerce_bool(val) -> bool:
    if isinstance(val, bool):
        return val
    if isinstance(val, str):
        return val.strip().lower() in ("true", "1", "yes", "y")
    return bool(val)


# --------------------------------------------------------------- live OS (run)
def run_mode(mode_id: str, task="", params_json="{}", app_id: str = "",
             confirm=False) -> str:
    params, err = coerce_params(params_json)
    if err:
        return err
    confirm = coerce_bool(confirm)
    if not isinstance(task, str):
        task = ""

    hit = KNOWLEDGE.find_mode(mode_id, app_id or None)
    target_app = (hit[0].get("id") if hit else app_id) or "?"
    needs_task = bool(hit and hit[1].get("needs_task"))

    preview = (f"PREVIEW — would run:\n"
               f"  app:    {target_app}\n"
               f"  mode:   {mode_id}\n"
               f"  task:   {task or '(none)'}\n"
               f"  params: {params or '(defaults)'}\n"
               f"  base command (defaults shown; params above override): "
               f"{hit[1].get('command_template') if hit else '(unknown)'}")

    if needs_task and not task:
        return f"Mode '{mode_id}' requires a task string. {preview}"

    # confirm=False is ALWAYS preview-only (deterministic; no double-dispatch).
    if not confirm:
        return preview + "\n\nNot dispatched (preview only). Call again with confirm=true to dispatch."

    if not _os.reachable():
        return f"A2S2-OS not reachable at {CONFIG.os_url}; cannot dispatch.\n{preview}"

    # Target the app explicitly (via /api/run's `app` field) so dispatching does
    # NOT change the OS's active app — no flip away from whatever the user is on.
    try:
        res = _os.run(mode_id, task, params, app=(target_app if hit else None))
    except Exception as e:
        return f"Dispatch failed: {e}\n{preview}"
    job_id = res.get("job_id")
    return (f"Dispatched. job_id={job_id}. Use job_status('{job_id}') to follow "
            f"progress.\n{preview}")
