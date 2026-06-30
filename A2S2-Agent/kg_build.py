"""Auto-seed the A2S2-Agent knowledge graph from the OS app modules.

The KG is what lets the agent *know about the scripts the OS runs* — not just
fire blindly. It is rebuilt by re-reading each `apps/<id>/app.py`, so when new
apps, modes, or params are added to the OS, a plain `python kg_build.py` brings
the agent up to date. No hand-maintained list.

For each installed app it records:
  - the MANIFEST (id, name, conda_env, models, vocabulary)
  - every mode descriptor (name, desc, needs_task, full param spec)
  - the curated protocol library
  - the actual script + command template each mode runs (via build_cmd),
    best-effort — so the agent can explain "this maps to run_task.py --loop".

Output: kg.json next to this file.
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import re
import shutil
import sys
from pathlib import Path

# Per-invocation run dir some apps create in task_cmd (e.g. ppiGPLM/ppiBTEP).
# We must not leave these behind when merely *resolving* the command for the KG.
_RUNDIR_RE = re.compile(r"(.*/a2s2_runs/[0-9A-Za-z_\-]+_run_[0-9A-Za-z]+)")

HERE = Path(__file__).resolve().parent
OS_ROOT = HERE.parent                     # A2S2-Agent lives inside the OS repo
APPS_DIR = OS_ROOT / "apps"
KG_PATH = HERE / "kg.json"

# The agent should know about the science/bench apps, not itself. Excluded apps
# are not seeded into the catalog the agent reasons over.
EXCLUDE_APPS = {"assistant"}

# Static facts about the OS bridge itself (endpoints the agent talks to).
OS_ENDPOINTS = [
    {"method": "GET", "path": "/api/health", "returns": "OS + active-app readiness"},
    {"method": "GET", "path": "/api/apps", "returns": "installed apps"},
    {"method": "GET", "path": "/api/modes", "returns": "active app's modes"},
    {"method": "GET", "path": "/api/library", "returns": "active app's curated protocols"},
    {"method": "GET", "path": "/api/sessions", "returns": "saved runs (newest first)"},
    {"method": "POST", "path": "/api/run", "returns": "{job_id}; body {mode,task,params,app?} (app targets a specific app without flipping the active one)"},
    {"method": "GET", "path": "/api/job/<id>", "returns": "{status,log,outcome,arm,rail,...}"},
    {"method": "POST", "path": "/api/job/<id>/stop", "returns": "stop a long-running job"},
    {"method": "POST", "path": "/api/active", "returns": "switch the active app"},
]


def _discover_app_dirs() -> list[Path]:
    if not APPS_DIR.is_dir():
        return []
    out = []
    for d in sorted(APPS_DIR.iterdir()):
        if d.name in EXCLUDE_APPS:
            continue
        if d.is_dir() and (d / "app.py").is_file():
            out.append(d)
    return out


def _import_app(app_dir: Path):
    """Import apps/<id>/app.py under a unique module name, stdout suppressed."""
    app_id = app_dir.name
    mod_name = f"a2s2app_{app_id}"
    spec = importlib.util.spec_from_file_location(mod_name, app_dir / "app.py")
    module = importlib.util.module_from_spec(spec)
    # Let the app resolve sibling imports and the OS root if it needs to.
    added = [p for p in (str(app_dir), str(OS_ROOT)) if p not in sys.path]
    sys.path[:0] = added
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            spec.loader.exec_module(module)
    finally:
        for p in added:
            with contextlib.suppress(ValueError):
                sys.path.remove(p)
    return module


def _clean_cmd(argv) -> str:
    """Render an argv list as a readable template (interpreter -> 'python')."""
    if not argv:
        return ""
    parts = list(argv)
    parts[0] = "python"  # collapse the absolute/conda interpreter
    out = []
    for p in parts:
        p = str(p)
        # collapse volatile per-run dirs to a stable placeholder
        m = _RUNDIR_RE.search(p)
        if m:
            p = p.replace(m.group(1), "<run_dir>")
        # show only the script's repo-relative tail, not absolute paths
        elif p.endswith(".py") and "/" in p:
            p = ".../" + "/".join(p.rsplit("/", 2)[-2:])
        out.append(p)
    return " ".join(out)


def _cleanup_run_artifacts(argv):
    """Remove any a2s2_runs/<id> dir an app's task_cmd created as a side effect
    of being called purely to resolve its command for the KG."""
    seen = set()
    for a in argv or []:
        m = _RUNDIR_RE.search(str(a))
        if not m or m.group(1) in seen:
            continue
        seen.add(m.group(1))
        with contextlib.suppress(OSError):
            d = Path(m.group(1))
            if d.is_dir():
                shutil.rmtree(d, ignore_errors=True)
            # also drop the a2s2_runs/ parent if our cleanup left it empty
            parent = d.parent
            if parent.name == "a2s2_runs" and parent.is_dir() and not any(parent.iterdir()):
                parent.rmdir()


def _script_of(argv) -> str | None:
    for a in argv or []:
        a = str(a)
        if a.endswith(".py"):
            return a.rsplit("/", 1)[-1]
    return None


def _mode_command(module, mode: dict) -> dict:
    """Best-effort: resolve the script + command template a mode runs."""
    info = {"script": None, "command_template": None}
    mode_id = mode.get("id")
    task = "<task>" if mode.get("needs_task") else ""
    build = getattr(module, "build_cmd", None)
    task_cmd = getattr(module, "task_cmd", None)
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            if callable(build):
                argv, _cwd, _env = build(mode_id, task, {})
            elif callable(task_cmd):
                models = mode.get("params", [{}])
                default_model = next((p.get("default") for p in mode.get("params", [])
                                      if p.get("id") == "model"), None)
                argv, _cwd, _env = task_cmd(task, default_model)
            else:
                return info
        info["script"] = _script_of(argv)
        info["command_template"] = _clean_cmd(argv)
        _cleanup_run_artifacts(argv)   # don't leave a run dir behind
    except Exception as e:
        info["command_template"] = f"(unresolved: {type(e).__name__})"
    return info


def _safe_call(module, name, default):
    fn = getattr(module, name, None)
    if not callable(fn):
        return default
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return fn()
    except Exception:
        return default


def build_kg() -> dict:
    apps_out = []
    for app_dir in _discover_app_dirs():
        try:
            module = _import_app(app_dir)
        except Exception as e:
            apps_out.append({"id": app_dir.name, "error": f"import failed: {e}"})
            continue

        manifest = dict(getattr(module, "MANIFEST", {}) or {})
        modes = _safe_call(module, "modes", [])
        library = _safe_call(module, "library", [])

        # OS contract: an app with only task_cmd() and no modes() runs as a
        # single implicit "task" mode. Synthesize it so the KG still describes
        # how to run the app.
        if not modes and callable(getattr(module, "task_cmd", None)):
            models = manifest.get("models") or []
            params = []
            if models:
                params.append({"id": "model", "label": "Model", "type": "choice",
                               "choices": models, "default": models[0]})
            modes = [{
                "id": "task", "name": "Run a task",
                "tagline": "Single implicit task mode (no modes() declared)",
                "desc": f"{manifest.get('name', app_dir.name)} exposes one task "
                        "mode; submit a task string and it runs once.",
                "needs_task": True, "params": params, "_implicit": True,
            }]

        mode_records = []
        for m in modes:
            rec = {
                "id": m.get("id"),
                "name": m.get("name"),
                "tagline": m.get("tagline"),
                "desc": m.get("desc"),
                "needs_task": bool(m.get("needs_task")),
                "long_running": bool(m.get("long_running")),
                "gallery": bool(m.get("gallery")),
                "task_hint": m.get("task_hint"),
                "params": m.get("params", []),
            }
            rec.update(_mode_command(module, m))
            mode_records.append(rec)

        apps_out.append({
            "id": manifest.get("id", app_dir.name),
            "name": manifest.get("name"),
            "tagline": manifest.get("tagline"),
            "conda_env": manifest.get("conda_env"),
            "models": manifest.get("models"),
            "vocabulary": manifest.get("vocabulary"),
            "modes": mode_records,
            "library": library,
        })

    return {
        "os": {
            "name": "A2S2-OS",
            "root": str(OS_ROOT),
            "server": "server.py",
            "endpoints": OS_ENDPOINTS,
        },
        "apps": apps_out,
    }


def is_stale() -> bool:
    """True if kg.json is missing or older than the app sources — i.e. an app
    was added, removed, or edited since the KG was last built. The apps/ dir's
    own mtime changes when a sub-app is added/removed; each app.py's mtime
    changes on edit, so both new apps and edits are caught."""
    if not KG_PATH.is_file():
        return True
    try:
        kg_m = KG_PATH.stat().st_mtime
        if APPS_DIR.stat().st_mtime > kg_m:          # app added/removed
            return True
        for d in _discover_app_dirs():
            if (d / "app.py").stat().st_mtime > kg_m:  # app edited
                return True
    except OSError:
        return True
    return False


def ensure_fresh() -> bool:
    """Rebuild kg.json if stale. Returns True if it rebuilt. This is what lets
    the agent self-seed: any consumer that reads the KG calls this first, so a
    newly added OS app shows up without anyone running kg_build by hand."""
    if not is_stale():
        return False
    kg = build_kg()
    KG_PATH.write_text(json.dumps(kg, indent=2))
    return True


def main():
    kg = build_kg()
    KG_PATH.write_text(json.dumps(kg, indent=2))
    n_apps = len(kg["apps"])
    n_modes = sum(len(a.get("modes", [])) for a in kg["apps"])
    print(f"[kg_build] wrote {KG_PATH}")
    print(f"[kg_build] {n_apps} app(s), {n_modes} mode(s):")
    for a in kg["apps"]:
        modes = ", ".join(m.get("id") for m in a.get("modes", [])) or "(none)"
        print(f"  - {a['id']}: {modes}")


if __name__ == "__main__":
    main()
