"""Load and query the auto-seeded knowledge graph (kg.json).

Small, dependency-free read layer over the KG so both the agent tools and any
future MCP server can ask "what apps/modes exist, what does this mode do, what
params/script does it run" without re-importing the OS app modules.
"""
from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
KG_PATH = HERE / "kg.json"


class KG:
    def __init__(self, path: Path = KG_PATH):
        self.path = path
        self._data = None

    @property
    def data(self) -> dict:
        if self._data is None:
            if not self.path.is_file():
                raise FileNotFoundError(
                    f"{self.path} not found. Run `python kg_build.py` to seed it."
                )
            self._data = json.loads(self.path.read_text())
        return self._data

    def apps(self) -> list[dict]:
        return self.data.get("apps", [])

    def app(self, app_id: str) -> dict | None:
        for a in self.apps():
            if a.get("id") == app_id:
                return a
        return None

    def find_mode(self, mode_id: str, app_id: str | None = None) -> tuple[dict, dict] | None:
        """Return (app, mode) for a mode id, optionally scoped to an app."""
        for a in self.apps():
            if app_id and a.get("id") != app_id:
                continue
            for m in a.get("modes", []):
                if m.get("id") == mode_id:
                    return a, m
        return None

    def overview(self) -> str:
        """Compact human/LLM-readable catalog of everything runnable."""
        lines = []
        os_info = self.data.get("os", {})
        lines.append(f"A2S2-OS at {os_info.get('root', '?')}")
        for a in self.apps():
            env = f" [env: {a.get('conda_env')}]" if a.get("conda_env") else ""
            lines.append(f"\nApp '{a.get('id')}' — {a.get('name')}{env}")
            if a.get("tagline"):
                lines.append(f"  {a.get('tagline')}")
            for m in a.get("modes", []):
                task = " (needs a task string)" if m.get("needs_task") else ""
                lr = " [long-running]" if m.get("long_running") else ""
                lines.append(f"  • {m.get('id')}: {m.get('tagline') or m.get('name')}{task}{lr}")
        return "\n".join(lines)

    def describe_mode(self, mode_id: str, app_id: str | None = None) -> str:
        hit = self.find_mode(mode_id, app_id)
        if not hit:
            return f"No mode '{mode_id}'" + (f" in app '{app_id}'." if app_id else ".")
        a, m = hit
        out = [
            f"Mode '{m.get('id')}' ({m.get('name')}) in app '{a.get('id')}'",
            f"  {m.get('desc') or m.get('tagline') or ''}",
            f"  needs_task: {m.get('needs_task')}   long_running: {m.get('long_running')}",
            f"  runs: {m.get('command_template') or '(unknown)'}",
        ]
        params = m.get("params") or []
        if params:
            out.append("  parameters:")
            for p in params:
                bits = [f"type={p.get('type')}"]
                if "choices" in p:
                    bits.append(f"choices={p.get('choices')}")
                if "default" in p and p.get("default") != "":
                    bits.append(f"default={p.get('default')}")
                if "min" in p or "max" in p:
                    bits.append(f"range={p.get('min')}..{p.get('max')}")
                if p.get("optional"):
                    bits.append("optional")
                out.append(f"    - {p.get('id')} ({p.get('label')}): {', '.join(bits)}")
        else:
            out.append("  parameters: none")
        return "\n".join(out)

    def search(self, query: str) -> str:
        """Substring search across apps/modes/params/library; returns matches."""
        q = query.lower().strip()
        hits = []
        for a in self.apps():
            blob_app = json.dumps(a).lower()
            if q in blob_app:
                for m in a.get("modes", []):
                    if q in json.dumps(m).lower():
                        hits.append(f"{a.get('id')}/{m.get('id')}: {m.get('tagline') or m.get('name')}")
                for lib in a.get("library", []):
                    if q in json.dumps(lib).lower():
                        hits.append(f"{a.get('id')} protocol '{lib.get('title')}': {lib.get('task')}")
        if not hits:
            return f"No KG matches for {query!r}."
        return "\n".join(dict.fromkeys(hits))  # dedupe, keep order


KNOWLEDGE = KG()
