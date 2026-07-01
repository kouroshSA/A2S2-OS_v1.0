"""Code execution for the agent — run Python or a shell command and return
what it printed. This is how "add a column to that CSV" or "sample 10 records
from that FASTA" actually happen: the agent writes a small script and runs it.

Environment selection (per the OS design): by default code runs in the agent's
own env (a2s2agent), but any call can name another conda env — xarm6sim, gpt,
esm, base — and it is run via `conda run -n <env>`, so the agent can reach
whatever libraries a task needs (pandas, biopython, torch, ...).

No sandbox yet (safeguards come later): commands run with the invoking user's
privileges. Output is captured (stdout+stderr merged), truncated, and every run
is bounded by a timeout.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import fileops

MAX_OUTPUT = 20_000          # chars of captured output returned to the model
DEFAULT_TIMEOUT = 120        # seconds


def _find_conda():
    exe = os.environ.get("CONDA_EXE")
    if exe and Path(exe).is_file():
        return exe
    found = shutil.which("conda")
    if found:
        return found
    try:
        home = Path.home()
    except (RuntimeError, OSError):
        home = Path(".")
    for d in ("anaconda3", "miniconda3", "miniforge3", "mambaforge"):
        for sub in ("bin/conda", "condabin/conda"):
            c = home / d / sub
            if c.is_file():
                return str(c)
    return None


CONDA = _find_conda()


def _wrap_env(argv, env: str):
    """Prefix argv with `conda run -n <env>` when a non-default env is named and
    conda is available. Empty env -> run in the current (a2s2agent) env."""
    env = (env or "").strip()
    if not env or not CONDA:
        return argv
    return [CONDA, "run", "--no-capture-output", "-n", env, *argv]


def _truncate(s: str) -> str:
    if len(s) > MAX_OUTPUT:
        return s[:MAX_OUTPUT] + f"\n… (truncated, {len(s)} chars total)"
    return s


def _run(argv, cwd: Path, timeout: int) -> str:
    try:
        proc = subprocess.run(
            argv, cwd=str(cwd),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return f"[timed out after {timeout}s]"
    except (FileNotFoundError, OSError) as e:
        return f"[could not run: {e}]"
    out = _truncate(proc.stdout or "")
    status = "ok" if proc.returncode == 0 else f"exit {proc.returncode}"
    body = out if out.strip() else "(no output)"
    return f"[{status}]\n{body}"


def run_python(code: str, env: str = "", cwd: str = "", timeout: int = DEFAULT_TIMEOUT) -> str:
    """Write `code` to a temp .py file in the workspace and run it. Returns the
    merged stdout/stderr. `env` optionally names a conda env to run under."""
    ws = fileops.workspace_dir()
    tmpdir = ws / ".agent_tmp"
    tmpdir.mkdir(parents=True, exist_ok=True)
    # Stable-ish name without needing randomness (which is unavailable to the
    # workflow layer); the pid keeps concurrent runs from colliding.
    script = tmpdir / f"snippet_{os.getpid()}.py"
    script.write_text(code if isinstance(code, str) else str(code), encoding="utf-8")
    run_cwd = fileops.resolve(cwd) if cwd else ws
    argv = _wrap_env(["python", str(script)], env)
    return _run(argv, run_cwd, int(timeout) if timeout else DEFAULT_TIMEOUT)


def run_shell(command: str, env: str = "", cwd: str = "", timeout: int = DEFAULT_TIMEOUT) -> str:
    """Run a shell command (via bash -lc) and return merged stdout/stderr. `env`
    optionally names a conda env to run the command under."""
    ws = fileops.workspace_dir()
    run_cwd = fileops.resolve(cwd) if cwd else ws
    argv = _wrap_env(["bash", "-lc", str(command)], env)
    return _run(argv, run_cwd, int(timeout) if timeout else DEFAULT_TIMEOUT)
