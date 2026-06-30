"""A2S2-Agent CLI — talk to the agent from a terminal.

    python cli.py                       # interactive REPL
    python cli.py "what can I run?"     # one-shot
    python cli.py --read-only ...       # disable the run tool (safe Q&A)
    python cli.py --show-tools ...      # print tool calls as they happen

Provider/model come from the environment (see config.py); default is Claude
Haiku with the key in A2S2-Agent/.env.
"""
from __future__ import annotations

import argparse
import sys

from config import CONFIG
from agent import A2S2Agent
import memory


def _print_tool_trace(messages, already: int) -> int:
    """Print any tool calls / tool results added since index `already`."""
    for m in messages[already:]:
        mtype = getattr(m, "type", "")
        tool_calls = getattr(m, "tool_calls", None)
        if tool_calls:
            for tc in tool_calls:
                print(f"  \033[2m↳ tool {tc.get('name')}({tc.get('args')})\033[0m")
        elif mtype == "tool":
            content = str(getattr(m, "content", "")).replace("\n", "\n    ")
            print(f"  \033[2m  = {content[:500]}\033[0m")
    return len(messages)


def main():
    ap = argparse.ArgumentParser(description="A2S2-Agent CLI")
    ap.add_argument("message", nargs="*", help="one-shot message (omit for REPL)")
    ap.add_argument("--read-only", action="store_true", help="disable run_mode")
    ap.add_argument("--show-tools", action="store_true", help="print tool activity")
    ap.add_argument("--session", default="", help="persist/continue a named conversation")
    ap.add_argument("--reset", action="store_true", help="clear the session before this turn")
    args = ap.parse_args()

    if args.session and args.reset:
        memory.reset_history(args.session)

    print(f"[A2S2-Agent] {CONFIG.describe()}  confirm_runs={CONFIG.confirm_runs}")
    try:
        agent = A2S2Agent(read_only=args.read_only)
    except Exception as e:
        print(f"[A2S2-Agent] failed to start: {e}")
        sys.exit(1)

    def run_turn(text, history):
        # history is always a list of {role, content} text turns (portable +
        # persistable). Tool internals are shown live but not kept in history.
        if args.show_tools:
            msgs = agent.ask_verbose(text, history)
            _print_tool_trace(msgs, len(history) + 1)
            reply = msgs[-1].content
        else:
            reply = agent.ask(text, history)
        print(f"\nAgent: {reply}\n")
        history = history + [{"role": "user", "content": text},
                             {"role": "assistant", "content": reply}]
        if args.session:
            memory.save_history(args.session, history)
        return history

    start_history = memory.load_history(args.session) if args.session else []

    # one-shot
    if args.message:
        run_turn(" ".join(args.message), start_history)
        return

    # REPL
    print("Type a request, or 'exit' to quit.\n")
    if start_history:
        print(f"(resuming session '{args.session}' with {len(start_history)//2} prior turn(s))\n")
    history: list = start_history
    while True:
        try:
            text = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not text:
            continue
        if text.lower() in ("exit", "quit", "q"):
            break
        try:
            history = run_turn(text, history)
        except Exception as e:
            print(f"[error] {e}\n")


if __name__ == "__main__":
    main()
