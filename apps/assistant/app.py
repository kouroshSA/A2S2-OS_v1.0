"""A2S2-OS app: AI agent (the conversational operator).

This is the in-UI "AI agent" option — one way to run things alongside the
tiles. A user types a request in plain words; the OS runs A2S2-Agent on it
(a LangGraph agent that knows the OS catalog via its knowledge graph) and
streams the reply into the run log.

The agent itself lives in `<OS root>/A2S2-Agent` and runs in its own conda env
(`a2s2agent`). This module is just the OS-contract adapter: a MANIFEST, a single
`ask` mode, and a `build_cmd` that launches the agent's one-shot CLI with the
right provider/model and confirmation policy.

Two ways to use the `ask` mode (the "Action" parameter):
  - preview  : the agent answers questions and PREVIEWS runs, but never
               dispatches hardware. Safe default.
  - run      : the agent may dispatch the mode it chooses (autonomous). It calls
               back into this same OS via /api/run, so the actual work appears as
               its own job (and briefly switches the active app to that app).

Nothing here imports the agent's heavy deps — it only builds the command.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
OS_ROOT = HERE.parents[1]                       # apps/assistant -> OS root
AGENT_DIR = Path(os.environ.get("A2S2_AGENT_DIR", OS_ROOT / "A2S2-Agent"))

# A "brain" choice maps to a concrete (provider, model). Keep the labels short
# for the UI chips; the mapping is the single source of truth.
BRAINS = {
    "haiku":      ("anthropic", "claude-haiku-4-5-20251001"),
    "sonnet":     ("anthropic", "claude-sonnet-4-6"),
    "llama3.2":   ("ollama",    "llama3.2"),
    "qwen-coder": ("ollama",    "qwen2.5-coder:14b"),
}
DEFAULT_BRAIN = "haiku"

MODES = [
    {
        # id "task" so the shell's home "Ask the bench" box (which posts
        # mode:"task") drives the agent directly. gallery:True also gives it a
        # full-control card under "Lab modes" with the Action/Brain/Conversation
        # form below (the home box uses defaults: preview, haiku, continue).
        "id": "task", "name": "AI agent",
        "tagline": "Ask in plain words; it finds and runs the right thing",
        "desc": "Describe what you want in plain language. The agent maps it to "
                "the right app and mode, fills in the parameters, and either "
                "shows you a preview or runs it for you. It knows the current "
                "catalog of everything the bench can do.",
        "accent": "#7C5CFF", "gallery": True, "needs_task": True,
        "task_hint": "e.g. what can I run?  ·  make a little training data",
        "params": [
            {"id": "intent", "label": "Action", "type": "choice",
             "choices": ["preview", "run"], "default": "preview"},
            {"id": "brain", "label": "Brain", "type": "choice",
             "choices": list(BRAINS), "default": DEFAULT_BRAIN},
            {"id": "memory", "label": "Conversation", "type": "choice",
             "choices": ["continue", "new"], "default": "continue"},
        ],
    },
]


def modes():
    return MODES


def _agent_python():
    """Fallback interpreter when conda is unavailable. The OS normally wraps the
    command in `conda run -n a2s2agent` (see MANIFEST conda_env); this is only
    used if conda can't be found."""
    return os.environ.get("A2S2_AGENT_PYTHON") or sys.executable or "python"


def build_cmd(mode, task, params):
    """(argv, cwd, env) for one agent turn. The OS wraps argv in
    `conda run -n a2s2agent` because argv[0] is a bare `python`."""
    p = params or {}
    # `brain` comes from the Lab-modes form; `model` from the home Ask box's
    # haiku/sonnet chips. Accept either; fall back to the default.
    brain = p.get("brain") or p.get("model") or DEFAULT_BRAIN
    if brain not in BRAINS:
        brain = DEFAULT_BRAIN
    provider, model = BRAINS[brain]
    intent = p.get("intent", "preview")

    # intent -> confirmation policy. preview: confirm gate ON, so run_mode only
    # previews (it cannot get a human confirm inside a one-shot). run: gate OFF,
    # so the agent may dispatch the mode it chooses.
    confirm = "1" if intent != "run" else "0"

    # One rolling conversation for the console so multi-turn context carries
    # across these one-shot job invocations ("ok, do the third one with 5
    # episodes"). "new" wipes it before this turn.
    argv = ["python", "cli.py", task, "--show-tools", "--session", "console"]
    if p.get("memory") == "new":
        argv.append("--reset")
    if intent != "run":
        argv.append("--read-only")  # belt-and-suspenders: no run tool in preview

    env = {
        **os.environ,
        "A2S2_AGENT_PROVIDER": provider,
        "A2S2_AGENT_MODEL": model,
        "A2S2_AGENT_CONFIRM": confirm,
        # Let the agent call back into this running OS.
        "A2S2_OS_URL": os.environ.get("A2S2_OS_URL", "http://localhost:8770"),
    }
    return [_agent_python()] + argv[1:], str(AGENT_DIR), env


def health():
    """Ready if the agent can run at all: either a Claude key is available (for
    the default anthropic brain) or a local Ollama server is reachable."""
    key_present = bool(os.environ.get("ANTHROPIC_API_KEY")) or (AGENT_DIR / ".env").is_file()
    ollama_up = False
    try:
        base = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
        with urllib.request.urlopen(f"{base}/api/tags", timeout=0.5) as r:
            ollama_up = r.status == 200
    except Exception:
        ollama_up = False
    return {
        "ready": bool(key_present or ollama_up) and AGENT_DIR.is_dir(),
        "agent_dir": str(AGENT_DIR),
        "agent_dir_found": AGENT_DIR.is_dir(),
        "api_key_present": key_present,
        "ollama_reachable": ollama_up,
    }


def classify_line(line):
    """The agent emits plain text + tool traces; surface a dispatched job id if
    one appears, otherwise no special bench state."""
    if "job_id=" in line:
        try:
            jid = line.split("job_id=")[1].split(".")[0].strip()
            return {"k": "info", "session_dir": None, "job_id": jid}
        except Exception:
            return {}
    return {}


MANIFEST = {
    "id": "assistant",
    "name": "AI agent",
    "tagline": "Run things by asking in plain words",
    "conda_env": "a2s2agent",
    "models": list(BRAINS),
    "vocabulary": "Speaks plain lab language; maps requests to OS apps/modes.",
    # Per-app wording for the shell's single-task ("Ask") surface. The shell
    # falls back to its bench defaults when an app omits these.
    "ask_title": "Ask the agent",
    "ask_blurb": "Ask in plain words — “what can I run?” or “make a "
                 "little training data.” The agent finds the right tool, fills "
                 "in the details, and previews or runs it.",
    "ask_placeholder": "e.g. what can I run?  ·  make a little training data",
    "ask_button": "Ask the agent",
}
