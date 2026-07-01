"""Filesystem operations for the agent — the read/write half of its new
"do real work" surface (the code-running half is in execops.py).

Everything is resolved against a WORKSPACE root (default <OS root>/workspace,
override with A2S2_AGENT_WORKSPACE) so plain relative paths like "data.csv"
land somewhere predictable. Absolute paths are allowed through as-is — there is
deliberately no jail yet (safeguards come later); the workspace is a convenient
default, not a boundary.

Each function returns a short human/LLM-readable string.
"""
from __future__ import annotations

import os
from pathlib import Path

HERE = Path(__file__).resolve().parent
OS_ROOT = HERE.parent                       # A2S2-Agent -> OS root

# The default working directory for relative paths. Created on first use.
WORKSPACE = Path(
    os.environ.get("A2S2_AGENT_WORKSPACE", str(OS_ROOT / "workspace"))
).expanduser()

MAX_READ_BYTES = 200_000                    # cap read_file output


def workspace_dir() -> Path:
    WORKSPACE.mkdir(parents=True, exist_ok=True)
    return WORKSPACE


def resolve(path: str) -> Path:
    """Absolute paths pass through; relative paths resolve under WORKSPACE."""
    p = Path(os.path.expanduser(str(path or "").strip() or "."))
    if not p.is_absolute():
        p = workspace_dir() / p
    return p


def _rel(p: Path) -> str:
    """Show a workspace-relative path when possible, else absolute."""
    try:
        return str(p.relative_to(WORKSPACE))
    except ValueError:
        return str(p)


def read_file(path: str, max_bytes: int = MAX_READ_BYTES) -> str:
    """Return the text contents of a file (truncated if very large)."""
    p = resolve(path)
    if not p.exists():
        return f"No such file: {p}"
    if p.is_dir():
        return f"{p} is a directory — use list_dir."
    try:
        data = p.read_bytes()
    except OSError as e:
        return f"Could not read {p}: {e}"
    truncated = len(data) > max_bytes
    text = data[:max_bytes].decode("utf-8", errors="replace")
    head = f"# {_rel(p)}  ({len(data)} bytes"
    head += ", truncated)\n" if truncated else ")\n"
    return head + text


def list_dir(path: str = ".") -> str:
    """List a directory: names, whether each is a dir, and file sizes."""
    p = resolve(path)
    if not p.exists():
        return f"No such directory: {p}"
    if not p.is_dir():
        return f"{p} is a file ({p.stat().st_size} bytes), not a directory."
    rows = []
    try:
        for entry in sorted(p.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower())):
            if entry.is_dir():
                rows.append(f"  {entry.name}/")
            else:
                try:
                    sz = entry.stat().st_size
                except OSError:
                    sz = "?"
                rows.append(f"  {entry.name}  ({sz} bytes)")
    except OSError as e:
        return f"Could not list {p}: {e}"
    header = f"{_rel(p) or '.'}  ({len(rows)} item{'s' if len(rows) != 1 else ''})"
    return header + "\n" + ("\n".join(rows) if rows else "  (empty)")


def write_file(path: str, content: str) -> str:
    """Create or overwrite a file with the given text (parent dirs are made)."""
    p = resolve(path)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        text = content if isinstance(content, str) else str(content)
        p.write_text(text, encoding="utf-8")
    except OSError as e:
        return f"Could not write {p}: {e}"
    return f"Wrote {len(text.encode('utf-8'))} bytes to {_rel(p)}."


def edit_file(path: str, find: str, replace: str, count: int = 0) -> str:
    """Replace occurrences of `find` with `replace` in a text file. count=0
    replaces all; a positive count replaces at most that many."""
    p = resolve(path)
    if not p.exists():
        return f"No such file: {p}"
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return f"Could not read {p}: {e}"
    n = text.count(find)
    if n == 0:
        return f"`find` text not present in {_rel(p)} — nothing changed."
    try:
        cnt = count if (isinstance(count, int) and count > 0) else -1
        new = text.replace(find, replace, cnt if cnt > 0 else n)
    except Exception as e:
        return f"Replace failed: {e}"
    done = n if cnt < 0 else min(cnt, n)
    try:
        p.write_text(new, encoding="utf-8")
    except OSError as e:
        return f"Could not write {p}: {e}"
    return f"Replaced {done} occurrence{'s' if done != 1 else ''} in {_rel(p)}."
