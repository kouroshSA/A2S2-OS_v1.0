"""Conversation memory across one-shot agent invocations.

The OS runs the assistant as a fresh subprocess per message, so without help each
turn is amnesiac. This module persists a trimmed conversation (plain user/
assistant text turns) to a per-session JSON file, so a kiosk conversation —
"what can I run?" then "ok do the third one with 5 episodes" — carries context.

Tool-call internals are intentionally NOT persisted: only the conversational
gist is replayed, which is provider-portable (Claude or local) and keeps the
context small. History is trimmed to the last MAX_MESSAGES.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
SESS_DIR = HERE / "sessions"
MAX_MESSAGES = 20            # ~10 turns


def _safe(session_id: str) -> str:
    s = re.sub(r"[^A-Za-z0-9_.-]", "_", (session_id or "default").strip())
    return s or "default"


def _path(session_id: str) -> Path:
    return SESS_DIR / f"{_safe(session_id)}.json"


def load_history(session_id: str) -> list[dict]:
    p = _path(session_id)
    if not p.is_file():
        return []
    try:
        data = json.loads(p.read_text())
        if isinstance(data, list):
            # keep only well-formed text turns
            return [m for m in data
                    if isinstance(m, dict) and m.get("role") in ("user", "assistant")
                    and isinstance(m.get("content"), str)]
    except Exception:
        return []
    return []


def save_history(session_id: str, messages: list[dict]) -> None:
    SESS_DIR.mkdir(exist_ok=True)
    trimmed = messages[-MAX_MESSAGES:]
    _path(session_id).write_text(json.dumps(trimmed, indent=2))


def append_turn(session_id: str, user: str, assistant: str) -> list[dict]:
    """Append one user+assistant exchange and persist. Returns the new history."""
    hist = load_history(session_id)
    hist.append({"role": "user", "content": user})
    hist.append({"role": "assistant", "content": assistant})
    save_history(session_id, hist)
    return hist


def reset_history(session_id: str) -> None:
    p = _path(session_id)
    if p.is_file():
        p.unlink()
