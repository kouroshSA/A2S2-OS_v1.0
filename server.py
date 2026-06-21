#!/usr/bin/env python3
"""
A2S2-OS — the Lab OS shell bridge (zero extra dependencies, stdlib only).

This is the OS layer: it serves the kiosk shell (index.html) and runs the
machinery every app needs — a threaded job runner, live job logs, an app
registry — while staying ignorant of any one app's domain. Each app under
apps/<id>/app.py owns its own specifics (what command runs a task, how saved
runs are read, how a log line maps to on-screen state).

App #1 is the xArm6 "Bench Console" (apps/bench_console). More apps drop in
beside it without touching this file (see README → "Adding an app").

Endpoint contract (stable; consumed by index.html):
  GET  /                 -> the shell HTML
  GET  /index.html       -> the shell HTML
  GET  /api/health       -> {ready, repo, llm_dir, ...} for the active app + OS info
  GET  /api/apps         -> [{id, name, ...}]  installed apps (the extension seam)
  GET  /api/library      -> active app's curated protocol list
  GET  /api/sessions     -> active app's saved runs, newest first
  POST /api/run          -> {task, model} -> {job_id}
  GET  /api/job/<id>     -> {status, log[], outcome, arm, rail, session_dir}

Run on the lab machine that has the `xarm6sim` conda env active:
    conda activate xarm6sim
    python server.py                 # then open http://localhost:8770

Kiosk (boot-to-console):
    chromium --kiosk --app=http://localhost:8770
"""

import importlib.util
import json
import os
import subprocess
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# ---------------------------------------------------------------- config
HERE = Path(__file__).resolve().parent
APPS_DIR = HERE / "apps"
PORT = int(os.environ.get("A2S2_OS_PORT", os.environ.get("LAB_OS_PORT", "8770")))
# Which app the shell drives. Defaults to the first installed app; with one
# app that is the Bench Console.
DEFAULT_APP = os.environ.get("A2S2_OS_APP", "bench_console")


# ---------------------------------------------------------------- app registry
class App:
    """A thin, defensive wrapper over an app module.

    Apps only have to implement what they need; the OS fills in safe defaults so
    a minimal app (just a MANIFEST + task_cmd) still works.
    """

    def __init__(self, module, app_id):
        self.mod = module
        self.manifest = dict(getattr(module, "MANIFEST", {}) or {})
        self.id = self.manifest.get("id") or app_id
        self.manifest.setdefault("id", self.id)
        self.manifest.setdefault("name", self.id)

    def health(self):
        fn = getattr(self.mod, "health", None)
        return fn() if fn else {"ready": True}

    def library(self):
        fn = getattr(self.mod, "library", None)
        return fn() if fn else []

    def list_sessions(self):
        fn = getattr(self.mod, "list_sessions", None)
        return fn() if fn else []

    def task_cmd(self, task, model):
        """Return (argv, cwd, env). cwd/env may be None."""
        out = self.mod.task_cmd(task, model)
        if isinstance(out, tuple):
            argv = list(out[0])
            cwd = out[1] if len(out) > 1 else None
            env = out[2] if len(out) > 2 else None
            return argv, cwd, env
        return list(out), None, None

    def classify_line(self, line):
        fn = getattr(self.mod, "classify_line", None)
        return fn(line) if fn else {}


def load_apps():
    """Import every apps/<id>/app.py into the registry."""
    registry = {}
    if not APPS_DIR.is_dir():
        return registry
    for d in sorted(APPS_DIR.iterdir()):
        f = d / "app.py"
        if not f.is_file():
            continue
        try:
            spec = importlib.util.spec_from_file_location(f"a2s2_app_{d.name}", f)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            app = App(mod, d.name)
            registry[app.id] = app
        except Exception as e:  # one broken app must not sink the OS
            print(f"  ! failed to load app '{d.name}': {e}")
    return registry


APPS = load_apps()


def active_app():
    if DEFAULT_APP in APPS:
        return APPS[DEFAULT_APP]
    return next(iter(APPS.values())) if APPS else None


# ---------------------------------------------------------------- job store
JOBS = {}          # id -> dict(status, log[], outcome, model, task, arm, rail, session_dir)
JOBS_LOCK = threading.Lock()


def _run_job(job_id, app):
    with JOBS_LOCK:
        JOBS[job_id]["status"] = "running"
        task = JOBS[job_id]["task"]
        model = JOBS[job_id]["model"]

    try:
        argv, cwd, env = app.task_cmd(task, model)
    except Exception as e:
        with JOBS_LOCK:
            j = JOBS[job_id]
            j["status"] = "error"; j["outcome"] = "error"
            j["log"].append({"k": "err", "t": f"Could not build run command: {e}"})
        return

    try:
        proc = subprocess.Popen(
            argv, cwd=cwd,
            # No stdin: the twin's run_task.py ends with an interactive
            # "Keep this recording? [Y/n]" prompt. With stdin detached the
            # prompt reads EOF and keeps the recording — without this the child
            # would inherit the server's terminal and hang every run.
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1, env=env,
        )
    except (FileNotFoundError, OSError) as e:
        with JOBS_LOCK:
            j = JOBS[job_id]
            j["status"] = "error"; j["outcome"] = "error"
            j["log"].append({"k": "err", "t": f"Could not start task: {e}"})
        return

    for raw in iter(proc.stdout.readline, ""):
        line = raw.rstrip("\n")
        if not line:
            continue
        try:
            info = app.classify_line(line)
        except Exception:
            info = {}
        with JOBS_LOCK:
            j = JOBS[job_id]
            j["log"].append({"k": info.get("k", ""), "t": line})
            if info.get("arm") is not None:
                j["arm"] = info["arm"]
            if info.get("rail") is not None:
                j["rail"] = info["rail"]
            if info.get("session_dir"):
                j["session_dir"] = info["session_dir"]
            if len(j["log"]) > 400:
                j["log"] = j["log"][-400:]
    proc.stdout.close()
    rc = proc.wait()
    with JOBS_LOCK:
        j = JOBS[job_id]
        j["arm"] = "idle"
        j["status"] = "done" if rc == 0 else "error"
        j["outcome"] = "success" if rc == 0 else "error"


def start_job(task, model, app):
    job_id = uuid.uuid4().hex[:12]
    with JOBS_LOCK:
        JOBS[job_id] = {"status": "queued", "log": [], "outcome": None,
                        "model": model, "task": task, "arm": "idle",
                        "rail": 0, "session_dir": None}
    threading.Thread(target=_run_job, args=(job_id, app), daemon=True).start()
    return job_id


# ---------------------------------------------------------------- composed responses
def health_payload():
    app = active_app()
    h = dict(app.health()) if app else {"ready": False}
    h["os"] = "A2S2-OS"
    h["apps"] = [a.manifest for a in APPS.values()]
    h["active_app"] = app.id if app else None
    return h


# ---------------------------------------------------------------- http
class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):  # quiet
        pass

    def do_GET(self):
        p = self.path.split("?")[0]
        if p in ("/", "/index.html"):
            f = HERE / "index.html"
            return self._send(200, f.read_bytes(), "text/html; charset=utf-8")
        if p == "/api/health":
            return self._send(200, health_payload())
        if p == "/api/apps":
            return self._send(200, [a.manifest for a in APPS.values()])
        if p == "/api/library":
            app = active_app()
            return self._send(200, app.library() if app else [])
        if p == "/api/sessions":
            app = active_app()
            return self._send(200, app.list_sessions() if app else [])
        if p.startswith("/api/job/"):
            jid = p.rsplit("/", 1)[-1]
            with JOBS_LOCK:
                j = dict(JOBS[jid]) if jid in JOBS else None
            if not j:
                return self._send(404, {"error": "no such job"})
            return self._send(200, {"status": j["status"], "log": j["log"],
                                    "outcome": j["outcome"], "arm": j.get("arm"),
                                    "rail": j.get("rail"),
                                    "session_dir": j.get("session_dir")})
        return self._send(404, {"error": "not found"})

    def do_POST(self):
        p = self.path.split("?")[0]
        if p == "/api/run":
            n = int(self.headers.get("Content-Length", "0") or 0)
            try:
                body = json.loads(self.rfile.read(n) or b"{}")
            except Exception:
                return self._send(400, {"error": "bad json"})
            task = (body.get("task") or "").strip()
            model = body.get("model") or "haiku"
            if not task:
                return self._send(400, {"error": "empty task"})
            app = active_app()
            if not app:
                return self._send(409, {"error": "no app installed"})
            h = app.health()
            if not h.get("ready", True):
                return self._send(409, {"error": "app not ready", "health": h})
            return self._send(200, {"job_id": start_job(task, model, app)})
        return self._send(404, {"error": "not found"})


def main():
    app = active_app()
    print("\n  A2S2-OS — Lab OS bench console")
    print(f"  os root     : {HERE}")
    if not APPS:
        print("  apps        : (none found under apps/)")
    else:
        for a in APPS.values():
            mark = " *active" if app and a.id == app.id else ""
            print(f"  app         : {a.id} — {a.manifest.get('name')}{mark}")
    if app:
        h = app.health()
        if "llm_dir" in h:
            print(f"  twin project: {h['llm_dir']}  "
                  f"{'[found]' if h.get('llm_dir_found') else '[MISSING — demo mode in browser]'}")
            if h.get("twin_source"):
                print(f"  found via   : {h['twin_source']}")
            if not h.get("llm_dir_found") and h.get("twin_repo_url"):
                print(f"  get the twin : git clone {h['twin_repo_url']}")
                print("                 (then place it beside A2S2-OS, or set twin_dir in config.json)")
        if "api_key_present" in h:
            print(f"  api key     : {'present' if h['api_key_present'] else 'not set'}")
    print(f"\n  open http://localhost:{PORT}\n")
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
