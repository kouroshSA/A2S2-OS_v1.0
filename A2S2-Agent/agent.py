"""A2S2-Agent — the agent graph.

Wires the provider-agnostic model + the narrow tool set into a LangChain
`create_agent` ReAct loop. The system prompt is grounded with a live snapshot of
the knowledge graph so the agent starts every conversation already knowing what
the OS can run.

Embed it:
    from agent import A2S2Agent
    agent = A2S2Agent()
    print(agent.ask("what can I run, and what does the learn mode do?"))
"""
from __future__ import annotations

from langchain.agents import create_agent

from config import CONFIG, Config
from kg import KNOWLEDGE
from providers import make_model
from tools import ALL_TOOLS, READ_TOOLS

SYSTEM_PROMPT = """You are A2S2-Agent, the conversational operator for A2S2-OS — a
kiosk "Lab OS" that drives an xArm6 robot digital twin and other lab apps. You
are one way to run things: instead of tapping a tile, a user can ask you in plain
words and you find the right app/mode, fill its parameters, and run it.

How to work:
- Speak in bench-biology / plain-lab terms to the user; never leak robotics/ROS
  jargon unless the user uses it first.
- Use the knowledge tools (list_runnables, describe_mode, search_knowledge) to
  map a request to a concrete mode. Do not invent modes or parameters — only use
  what the tools report.
- Before running anything, call describe_mode to get the exact parameters.
{run_policy}
- run_mode with confirm=false NEVER dispatches — it only returns a preview. Only
  confirm=true actually runs, and running can move real hardware.
- After dispatching, use job_status to report progress when asked.
- If the OS is not reachable, you can still answer questions from the knowledge
  graph; just say runs are unavailable.
- Be concise. Prefer a short answer plus, when useful, the exact mode + params.

Here is the current catalog of what the OS can run (from the knowledge graph):

{catalog}
"""

# Inserted into {run_policy} depending on the autonomy setting.
RUN_POLICY_CONFIRM = (
    "- You must NOT run anything without explicit human approval. Call run_mode "
    "with confirm=false to produce a preview, show it to the user, and only call "
    "run_mode again with confirm=true after they clearly say yes."
)
RUN_POLICY_AUTONOMOUS = (
    "- You are authorized to run things directly. Once you have chosen the mode "
    "and parameters, call run_mode with confirm=true to dispatch it; you do not "
    "need to ask the user first. (A single confirm=true call is enough — do not "
    "repeat it.)"
)


class A2S2Agent:
    def __init__(self, cfg: Config = CONFIG, read_only: bool = False):
        self.cfg = cfg
        self.model = make_model(cfg)
        tools = READ_TOOLS if read_only else ALL_TOOLS
        catalog = KNOWLEDGE.overview()
        run_policy = RUN_POLICY_CONFIRM if cfg.confirm_runs else RUN_POLICY_AUTONOMOUS
        self.graph = create_agent(
            self.model,
            tools,
            system_prompt=SYSTEM_PROMPT.format(catalog=catalog, run_policy=run_policy),
        )

    def ask(self, message: str, history: list | None = None) -> str:
        """One turn. Returns the agent's final text. Pass/keep `history` (a list
        of messages) to maintain a conversation."""
        msgs = list(history or [])
        msgs.append({"role": "user", "content": message})
        result = self.graph.invoke({"messages": msgs})
        return result["messages"][-1].content

    def ask_verbose(self, message: str, history: list | None = None):
        """Like ask() but returns the full updated message list (for REPLs that
        want to show tool calls and keep history)."""
        msgs = list(history or [])
        msgs.append({"role": "user", "content": message})
        result = self.graph.invoke({"messages": msgs})
        return result["messages"]
