"""A2S2-Agent persistent chat server (stdio JSON protocol).

Unlike cli.py (a fresh, amnesiac one-shot per message), this stays alive for a
whole conversation: the model and knowledge graph are loaded ONCE and kept warm,
so follow-up turns are fast and the agent can carry context and ask clarifying
questions. The OS (server.py) spawns one of these per chat session, pipes user
messages in on stdin, and streams events back out on stdout.

Protocol — one JSON object per line.
  stdin  (OS -> agent):
    {"type": "user", "text": "..."}     a user turn
    {"type": "ping"}                     liveness probe -> {"type":"pong"}
    {"type": "stop"}                     shut down
  stdout (agent -> OS), flushed per line:
    {"type": "ready", "provider":..., "model":..., "turns": N}
    {"type": "turn_start"}
    {"type": "token", "text": "..."}     incremental assistant text
    {"type": "tool_start", "name":..., "args": {...}}
    {"type": "tool_end", "name":..., "result": "..."}
    {"type": "final", "text": "..."}     end of turn (full assistant reply)
    {"type": "error", "text": "..."}

Anything the underlying libraries print (warnings, etc.) goes to stderr and is
drained separately by the OS, so this stdout stream stays clean JSONL.
"""
from __future__ import annotations

import json
import os
import sys

# Keep library chatter off stdout: route warnings to stderr.
import warnings
warnings.simplefilter("ignore")

from config import CONFIG
from agent import A2S2Agent
import memory


def emit(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def _text_of(content) -> str:
    """A message chunk's content may be a plain string or a list of typed
    blocks (Anthropic). Return just the text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for b in content:
            if isinstance(b, dict) and b.get("type") in (None, "text"):
                parts.append(b.get("text", ""))
        return "".join(parts)
    return ""


def stream_turn(agent: A2S2Agent, history: list, user_text: str) -> str:
    """Run one turn, emitting events as they happen. Returns the assistant reply
    (the visible text) so the caller can persist it to history."""
    msgs = list(history) + [{"role": "user", "content": user_text}]
    emit({"type": "turn_start"})

    reply_parts: list[str] = []
    seen_tool_calls: set = set()
    last_ai_text = ""

    for mode, payload in agent.graph.stream(
        {"messages": msgs}, stream_mode=["updates", "messages"]
    ):
        if mode == "messages":
            chunk, meta = payload
            if meta.get("langgraph_node") != "model":
                continue                      # skip tool-message echoes
            txt = _text_of(getattr(chunk, "content", ""))
            if txt:
                reply_parts.append(txt)
                emit({"type": "token", "text": txt})
            continue

        # mode == "updates": one dict of {node: {"messages":[...]}}
        for node, upd in (payload or {}).items():
            for m in upd.get("messages", []) or []:
                mtype = getattr(m, "type", "")
                tool_calls = getattr(m, "tool_calls", None)
                if tool_calls:
                    for tc in tool_calls:
                        tid = tc.get("id") or f"{tc.get('name')}:{tc.get('args')}"
                        if tid in seen_tool_calls:
                            continue
                        seen_tool_calls.add(tid)
                        emit({"type": "tool_start",
                              "name": tc.get("name"),
                              "args": tc.get("args") or {}})
                elif mtype == "tool":
                    emit({"type": "tool_end",
                          "name": getattr(m, "name", ""),
                          "result": str(getattr(m, "content", ""))[:4000]})
                elif mtype == "ai":
                    t = _text_of(getattr(m, "content", ""))
                    if t:
                        last_ai_text = t

    reply = "".join(reply_parts).strip() or last_ai_text.strip()
    emit({"type": "final", "text": reply})
    return reply


def main():
    session = os.environ.get("A2S2_AGENT_SESSION", "console")
    read_only = os.environ.get("A2S2_AGENT_READ_ONLY", "0") not in ("0", "", "false", "no")
    if os.environ.get("A2S2_AGENT_RESET") in ("1", "true", "yes"):
        memory.reset_history(session)

    try:
        agent = A2S2Agent(read_only=read_only)
    except Exception as e:
        emit({"type": "error", "text": f"failed to start agent: {e}"})
        return

    history = memory.load_history(session)
    emit({"type": "ready", "provider": CONFIG.provider, "model": CONFIG.model,
          "turns": len(history) // 2, "read_only": read_only})

    for raw in sys.stdin:
        raw = raw.strip()
        if not raw:
            continue
        try:
            cmd = json.loads(raw)
        except json.JSONDecodeError:
            continue
        ctype = cmd.get("type")
        if ctype == "stop":
            break
        if ctype == "ping":
            emit({"type": "pong"})
            continue
        if ctype != "user":
            continue
        text = (cmd.get("text") or "").strip()
        if not text:
            continue
        try:
            reply = stream_turn(agent, history, text)
            history = history + [{"role": "user", "content": text},
                                 {"role": "assistant", "content": reply}]
            memory.save_history(session, history)
        except Exception as e:
            emit({"type": "error", "text": f"{type(e).__name__}: {e}"})


if __name__ == "__main__":
    main()
