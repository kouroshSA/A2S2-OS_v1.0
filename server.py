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
  GET  /api/health       -> {ready, repo, llm_dir, modes[], ...} active app + OS
  GET  /api/apps         -> [{id, name, ...}]  installed apps (the extension seam)
  GET  /api/modes        -> active app's modes of operation (descriptors)
  GET  /api/library      -> active app's curated protocol list
  GET  /api/sessions     -> active app's saved runs, newest first
  POST /api/run          -> {mode, task, params} -> {job_id}
                            (legacy {task, model} still accepted -> task mode)
  GET  /api/job/<id>     -> {status, log[], outcome, mode, arm, rail, session_dir}
  POST /api/job/<id>/stop-> stop a running job (long-running modes, e.g. VR)

Run on the lab machine that has the `xarm6sim` conda env active:
    conda activate xarm6sim
    python server.py                 # then open http://localhost:8770

Kiosk (boot-to-console):
    chromium --kiosk --app=http://localhost:8770
"""

import importlib.util
import json
import os
import queue
import shutil
import signal
import subprocess
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

# ---------------------------------------------------------------- config
HERE = Path(__file__).resolve().parent
APPS_DIR = HERE / "apps"
PORT = int(os.environ.get("A2S2_OS_PORT", os.environ.get("LAB_OS_PORT", "8770")))
# Tell children that call back into us where WE actually are. The AI agent's
# run_mode dispatches to A2S2_OS_URL; without this it would fall back to a
# hardcoded default port and hit the wrong server (or none) when we bind a
# non-default port. An explicit A2S2_OS_URL in the environment still wins.
os.environ.setdefault("A2S2_OS_URL", f"http://localhost:{PORT}")
# Which app the shell drives. Defaults to the first installed app; with one
# app that is the Bench Console.
DEFAULT_APP = os.environ.get("A2S2_OS_APP", "bench_console")


# ---------------------------------------------------------------- conda envs
# Each app may declare a `conda_env` in its MANIFEST. When set (and conda is
# present), the OS runs that app's tasks via `conda run -n <env> ...` so the
# right interpreter + its full activated environment (PATH, LD_LIBRARY_PATH,
# CUDA libs, ...) are used regardless of which env launched the server. This
# decouples each app from the server's own env (e.g. the server runs in
# `xarm6sim` while ppiGPLM needs `gpt`). If conda can't be found, we fall back
# to whatever interpreter the app's task_cmd chose, so nothing hard-breaks.
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


def wrap_for_env(argv, conda_env):
    """Prefix argv with `conda run -n <env>` when an env is declared and conda
    is available. --no-capture-output keeps the child's stdout streaming live
    (plain `conda run` buffers until exit, which would stall the run log).

    If argv[0] is a python interpreter, it's replaced with a bare `python` so
    conda runs the *target env's* interpreter rather than whatever python the
    app picked (often the server's own). Non-python entry points are left as-is.
    The original argv is the fallback used when conda can't be found."""
    if not conda_env or not CONDA or not argv:
        return argv
    inner = list(argv)
    if os.path.basename(str(inner[0])).lower().startswith("python"):
        inner[0] = "python"
    return [CONDA, "run", "--no-capture-output", "-n", conda_env, *inner]


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
        # Optional conda env this app's tasks should run in (see wrap_for_env).
        self.conda_env = self.manifest.get("conda_env")

    def health(self):
        fn = getattr(self.mod, "health", None)
        return fn() if fn else {"ready": True}

    def library(self):
        fn = getattr(self.mod, "library", None)
        return fn() if fn else []

    def list_sessions(self):
        fn = getattr(self.mod, "list_sessions", None)
        return fn() if fn else []

    def modes(self):
        """The app's modes of operation (descriptors the shell renders). Apps
        that don't declare any get a single implicit task mode so the classic
        {task, model} path still works."""
        fn = getattr(self.mod, "modes", None)
        if fn:
            return list(fn() or [])
        return [{
            "id": "task", "name": "Run a task", "gallery": False,
            "needs_task": True,
            "params": [{"id": "model", "label": "Model", "type": "choice",
                        "choices": self.manifest.get("models", ["haiku"]),
                        "default": "haiku"}],
        }]

    def mode(self, mode_id):
        """Look up a single mode descriptor by id (None if unknown)."""
        for m in self.modes():
            if m.get("id") == (mode_id or "task"):
                return m
        return None

    def build_cmd(self, mode, task, params):
        """Return (argv, cwd, env) for one run of `mode` with `params`.

        Prefers the app's build_cmd(mode, task, params); falls back to the
        legacy task_cmd(task, model) for apps that only know one mode. argv is
        wrapped with `conda run -n <env>` when this app declares a conda_env and
        conda is available (the app's own interpreter choice is the fallback)."""
        fn = getattr(self.mod, "build_cmd", None)
        if fn:
            out = fn(mode, task, params or {})
        else:
            model = (params or {}).get("model", "haiku")
            out = self.mod.task_cmd(task, model)
        if isinstance(out, tuple):
            argv = list(out[0])
            cwd = out[1] if len(out) > 1 else None
            env = out[2] if len(out) > 2 else None
        else:
            argv, cwd, env = list(out), None, None
        return wrap_for_env(argv, self.conda_env), cwd, env

    def chat_cmd(self, params):
        """Return (argv, cwd, env) for a persistent chat session, or None if this
        app has no chat mode. argv is wrapped with `conda run -n <env>` like
        build_cmd, so the long-lived agent runs in the app's own env."""
        fn = getattr(self.mod, "chat_cmd", None)
        if not fn:
            return None
        out = fn(params or {})
        if not out:
            return None
        if isinstance(out, tuple):
            argv = list(out[0])
            cwd = out[1] if len(out) > 1 else None
            env = out[2] if len(out) > 2 else None
        else:
            argv, cwd, env = list(out), None, None
        return wrap_for_env(argv, self.conda_env), cwd, env

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


def _initial_active():
    """The app the shell drives at startup: env/default if installed, else the
    first app found, else None."""
    if DEFAULT_APP in APPS:
        return DEFAULT_APP
    return next(iter(APPS), None)


# The active app is switchable at runtime (POST /api/active) so the shell can
# offer an in-UI app switcher. Guarded because the HTTP server is threaded.
ACTIVE_APP_ID = _initial_active()
ACTIVE_LOCK = threading.Lock()


def active_app():
    with ACTIVE_LOCK:
        aid = ACTIVE_APP_ID
    if aid and aid in APPS:
        return APPS[aid]
    return next(iter(APPS.values())) if APPS else None


def set_active_app(app_id):
    """Switch the active app. Returns True if `app_id` is installed."""
    global ACTIVE_APP_ID
    if app_id not in APPS:
        return False
    with ACTIVE_LOCK:
        ACTIVE_APP_ID = app_id
    return True


# ---------------------------------------------------------------- job store
# A job runs one mode of operation. Some modes finish on their own (a task, a
# fixed batch); others run until stopped (VR teleop). We keep the child Popen so
# a long-running job can be terminated from the UI (POST /api/job/<id>/stop).
JOBS = {}          # id -> dict(status, log[], outcome, mode, params, task, ...)
JOBS_LOCK = threading.Lock()


def _run_job(job_id, app):
    with JOBS_LOCK:
        j = JOBS[job_id]
        j["status"] = "running"
        task = j["task"]; mode = j["mode"]; params = j["params"]

    try:
        argv, cwd, env = app.build_cmd(mode, task, params)
    except Exception as e:
        with JOBS_LOCK:
            j = JOBS[job_id]
            j["status"] = "error"; j["outcome"] = "error"
            j["log"].append({"k": "err", "t": f"Could not build run command: {e}"})
        return

    try:
        proc = subprocess.Popen(
            argv, cwd=cwd,
            # No stdin: the twin's scripts end with an interactive
            # "Keep this recording? [Y/n]" prompt (and the batch modes are run
            # with their own non-interactive flags). With stdin detached the
            # prompt reads EOF and keeps the recording — without this the child
            # would inherit the server's terminal and hang every run.
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1, env=env,
            # Own process group so stop_job() can signal the whole tree —
            # `conda run` spawns a grandchild that a plain terminate() misses.
            start_new_session=True,
        )
    except (FileNotFoundError, OSError) as e:
        with JOBS_LOCK:
            j = JOBS[job_id]
            j["status"] = "error"; j["outcome"] = "error"
            j["log"].append({"k": "err", "t": f"Could not start task: {e}"})
        return

    with JOBS_LOCK:
        JOBS[job_id]["proc"] = proc

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
        if j.get("stopping"):
            # User asked to stop — a non-zero rc here is expected, not a fault.
            j["status"] = "stopped"; j["outcome"] = "stopped"
        else:
            j["status"] = "done" if rc == 0 else "error"
            j["outcome"] = "success" if rc == 0 else "error"


def start_job(mode, task, params, app):
    job_id = uuid.uuid4().hex[:12]
    model = (params or {}).get("model")
    with JOBS_LOCK:
        JOBS[job_id] = {"status": "queued", "log": [], "outcome": None,
                        "mode": mode, "params": params or {}, "task": task,
                        "model": model, "arm": "idle", "rail": 0,
                        "session_dir": None, "proc": None, "stopping": False}
    threading.Thread(target=_run_job, args=(job_id, app), daemon=True).start()
    return job_id


def stop_job(job_id):
    """Signal a running job's whole process group to terminate. Returns True if
    the job exists. Idempotent — repeated calls escalate nothing here; the
    child's own SIGTERM handling (or eventual exit) ends the run."""
    with JOBS_LOCK:
        j = JOBS.get(job_id)
        if not j:
            return False
        j["stopping"] = True
        proc = j.get("proc")
        j["log"].append({"k": "", "t": "— stop requested —"})
    if proc and proc.poll() is None:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError):
            try:
                proc.terminate()
            except OSError:
                pass
    return True


# ---------------------------------------------------------------- chat sessions
# A chat is a long-lived agent subprocess (see A2S2-Agent/chat_server.py) the OS
# keeps warm for a whole conversation. Its stdout is a stream of JSON events; a
# reader thread parses them into a per-chat queue that the SSE endpoint drains to
# the browser. User turns are written to the child's stdin. This is separate from
# the job store: jobs are one-shot runs, a chat persists across many turns.
CHATS = {}          # chat_id -> dict(proc, q, app_id, alive, sse, idle_since)
CHATS_LOCK = threading.Lock()
CHAT_REAP_GRACE_S = 60      # stop a chat with no SSE client attached this long


def _chat_reader(chat_id, proc):
    """Pump the agent's stdout JSONL into the chat's event queue."""
    q = CHATS[chat_id]["q"]
    for raw in iter(proc.stdout.readline, ""):
        line = raw.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue                       # ignore any non-JSON chatter
        q.put(ev)
    try:
        proc.stdout.close()
    except OSError:
        pass
    with CHATS_LOCK:
        c = CHATS.get(chat_id)
        if c:
            c["alive"] = False
    q.put({"type": "closed"})


def _drain(stream):
    """Consume a pipe we don't forward (child stderr) so it can't fill and block."""
    try:
        for _ in iter(stream.readline, ""):
            pass
    except (OSError, ValueError):
        pass


def start_chat(app, params):
    """Spawn a persistent chat agent for `app`. Returns chat_id, or None if the
    app has no chat mode / failed to start."""
    try:
        cmd = app.chat_cmd(params)
    except Exception:
        return None
    if not cmd:
        return None
    argv, cwd, env = cmd
    try:
        proc = subprocess.Popen(
            argv, cwd=cwd, env=env,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, bufsize=1,
            start_new_session=True,        # own group so stop can signal the tree
        )
    except (FileNotFoundError, OSError):
        return None
    chat_id = uuid.uuid4().hex[:12]
    with CHATS_LOCK:
        CHATS[chat_id] = {"proc": proc, "q": queue.Queue(),
                          "app_id": app.id, "alive": True,
                          "sse": 0, "idle_since": time.monotonic()}
    threading.Thread(target=_chat_reader, args=(chat_id, proc), daemon=True).start()
    threading.Thread(target=_drain, args=(proc.stderr,), daemon=True).start()
    return chat_id


def chat_send(chat_id, text):
    """Deliver a user turn to a live chat. Returns True if accepted."""
    with CHATS_LOCK:
        c = CHATS.get(chat_id)
    if not c or not c.get("alive"):
        return False
    proc = c["proc"]
    try:
        proc.stdin.write(json.dumps({"type": "user", "text": text}) + "\n")
        proc.stdin.flush()
        return True
    except (OSError, ValueError):
        return False


def stop_chat(chat_id):
    """End a chat and terminate its agent. Idempotent."""
    with CHATS_LOCK:
        c = CHATS.pop(chat_id, None)
    if not c:
        return False
    proc = c["proc"]
    try:
        proc.stdin.write(json.dumps({"type": "stop"}) + "\n")
        proc.stdin.flush()
    except (OSError, ValueError):
        pass
    if proc.poll() is None:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError):
            try:
                proc.terminate()
            except OSError:
                pass
    return True


def _reap_chats():
    """Kiosk hygiene: a persistent chat lives as long as a browser is attached
    via SSE. When a client goes away (tab closed, reload) without calling
    /api/chat/stop, its agent would otherwise linger. This stops any chat that
    has had no SSE client for CHAT_REAP_GRACE_S (EventSource reconnects within
    seconds, so a real gap means the client is gone)."""
    while True:
        time.sleep(15)
        now = time.monotonic()
        dead = []
        with CHATS_LOCK:
            for cid, c in CHATS.items():
                idle = c.get("idle_since")
                if c.get("sse", 0) <= 0 and idle and (now - idle) > CHAT_REAP_GRACE_S:
                    dead.append(cid)
        for cid in dead:
            stop_chat(cid)


# ---------------------------------------------------------------- composed responses
def health_payload():
    app = active_app()
    h = dict(app.health()) if app else {"ready": False}
    h["os"] = "A2S2-OS"
    h["apps"] = [a.manifest for a in APPS.values()]
    h["active_app"] = app.id if app else None
    # Ship the active app's modes here too, so the shell can re-skin and rebuild
    # its mode gallery in one round-trip on boot and on app switch.
    h["modes"] = app.modes() if app else []
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

    def _sse_chat(self, chat_id):
        """Stream a chat's events to the browser as Server-Sent Events. Stays
        open for the whole conversation (idle between turns); ends when the agent
        process exits ('closed') or the client disconnects."""
        with CHATS_LOCK:
            c = CHATS.get(chat_id)
            if c:
                c["sse"] = c.get("sse", 0) + 1
                c["idle_since"] = None
        if not c:
            return self._send(404, {"error": "no such chat"})
        q = c["q"]
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")   # don't let proxies buffer
        self.end_headers()
        try:
            while True:
                try:
                    ev = q.get(timeout=15)
                except queue.Empty:
                    with CHATS_LOCK:
                        still = CHATS.get(chat_id)
                    if not still or not still.get("alive"):
                        break                 # dead chat: don't hang forever
                    self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()
                    continue
                self.wfile.write(f"data: {json.dumps(ev)}\n\n".encode())
                self.wfile.flush()
                if ev.get("type") == "closed":
                    break
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass                              # client went away
        finally:
            with CHATS_LOCK:
                cc = CHATS.get(chat_id)
                if cc:
                    cc["sse"] = max(0, cc.get("sse", 1) - 1)
                    if cc["sse"] == 0:
                        cc["idle_since"] = time.monotonic()

    def do_GET(self):
        p = self.path.split("?")[0]
        if p in ("/", "/index.html"):
            f = HERE / "index.html"
            return self._send(200, f.read_bytes(), "text/html; charset=utf-8")
        if p == "/api/health":
            return self._send(200, health_payload())
        if p == "/api/apps":
            return self._send(200, [a.manifest for a in APPS.values()])
        if p == "/api/modes":
            app = active_app()
            return self._send(200, app.modes() if app else [])
        if p == "/api/library":
            app = active_app()
            return self._send(200, app.library() if app else [])
        if p == "/api/sessions":
            app = active_app()
            return self._send(200, app.list_sessions() if app else [])
        if p == "/api/chat/stream":
            qs = parse_qs(urlparse(self.path).query)
            cid = (qs.get("chat_id", [""])[0]).strip()
            return self._sse_chat(cid)
        if p.startswith("/api/job/"):
            jid = p.rsplit("/", 1)[-1]
            with JOBS_LOCK:
                j = dict(JOBS[jid]) if jid in JOBS else None
            if not j:
                return self._send(404, {"error": "no such job"})
            return self._send(200, {"status": j["status"], "log": j["log"],
                                    "outcome": j["outcome"], "arm": j.get("arm"),
                                    "rail": j.get("rail"), "mode": j.get("mode"),
                                    "session_dir": j.get("session_dir")})
        return self._send(404, {"error": "not found"})

    def do_POST(self):
        p = self.path.split("?")[0]
        if p == "/api/active":
            n = int(self.headers.get("Content-Length", "0") or 0)
            try:
                body = json.loads(self.rfile.read(n) or b"{}")
            except Exception:
                return self._send(400, {"error": "bad json"})
            aid = (body.get("app") or "").strip()
            if set_active_app(aid):
                # Hand back the full health payload so the shell can re-skin in
                # one round-trip (active_app, ready, manifests).
                return self._send(200, health_payload())
            return self._send(400, {"error": "unknown app",
                                    "apps": [a.id for a in APPS.values()]})
        if p == "/api/run":
            n = int(self.headers.get("Content-Length", "0") or 0)
            try:
                body = json.loads(self.rfile.read(n) or b"{}")
            except Exception:
                return self._send(400, {"error": "bad json"})
            # Optional `app` targets a specific app for THIS run without
            # changing the global active app (so an agent can dispatch into
            # another app without flipping what the shell is showing). Absent ->
            # the active app, as before.
            req_app = (body.get("app") or "").strip()
            if req_app:
                app = APPS.get(req_app)
                if not app:
                    return self._send(400, {"error": "unknown app", "app": req_app,
                                            "apps": [a.id for a in APPS.values()]})
            else:
                app = active_app()
            if not app:
                return self._send(409, {"error": "no app installed"})
            mode = (body.get("mode") or "task").strip()
            task = (body.get("task") or "").strip()
            params = dict(body.get("params") or {})
            # Back-compat: classic clients post {task, model} with no mode/params.
            if "model" in body and "model" not in params:
                params["model"] = body["model"]
            desc = app.mode(mode)
            if desc is None:
                return self._send(400, {"error": "unknown mode", "mode": mode,
                                        "modes": [m.get("id") for m in app.modes()]})
            if desc.get("needs_task") and not task:
                return self._send(400, {"error": "empty task"})
            h = app.health()
            if not h.get("ready", True):
                return self._send(409, {"error": "app not ready", "health": h})
            return self._send(200, {"job_id": start_job(mode, task, params, app)})
        if p == "/api/chat/start":
            n = int(self.headers.get("Content-Length", "0") or 0)
            try:
                body = json.loads(self.rfile.read(n) or b"{}")
            except Exception:
                return self._send(400, {"error": "bad json"})
            req_app = (body.get("app") or "").strip()
            app = APPS.get(req_app) if req_app else active_app()
            if not app:
                return self._send(400, {"error": "unknown app", "app": req_app,
                                        "apps": [a.id for a in APPS.values()]})
            h = app.health()
            if not h.get("ready", True):
                return self._send(409, {"error": "app not ready", "health": h})
            chat_id = start_chat(app, dict(body.get("params") or {}))
            if not chat_id:
                return self._send(400, {"error": "app has no chat mode", "app": app.id})
            return self._send(200, {"chat_id": chat_id, "app": app.id})
        if p == "/api/chat/send":
            n = int(self.headers.get("Content-Length", "0") or 0)
            try:
                body = json.loads(self.rfile.read(n) or b"{}")
            except Exception:
                return self._send(400, {"error": "bad json"})
            cid = (body.get("chat_id") or "").strip()
            text = (body.get("text") or "").strip()
            if not cid or not text:
                return self._send(400, {"error": "chat_id and text required"})
            if chat_send(cid, text):
                return self._send(200, {"ok": True})
            return self._send(404, {"error": "no such chat or it has ended"})
        if p == "/api/chat/stop":
            n = int(self.headers.get("Content-Length", "0") or 0)
            try:
                body = json.loads(self.rfile.read(n) or b"{}")
            except Exception:
                return self._send(400, {"error": "bad json"})
            cid = (body.get("chat_id") or "").strip()
            if stop_chat(cid):
                return self._send(200, {"stopped": cid})
            return self._send(404, {"error": "no such chat"})
        if p.startswith("/api/job/") and p.endswith("/stop"):
            jid = p[len("/api/job/"):-len("/stop")]
            if stop_job(jid):
                return self._send(200, {"stopped": jid})
            return self._send(404, {"error": "no such job"})
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
            if a.conda_env:
                env_note = (f" [env: {a.conda_env} via conda run]" if CONDA
                            else f" [env: {a.conda_env} — conda NOT found, using app default]")
            else:
                env_note = ""
            print(f"  app         : {a.id} — {a.manifest.get('name')}{mark}{env_note}")
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
    threading.Thread(target=_reap_chats, daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
