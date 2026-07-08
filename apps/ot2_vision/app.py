#!/usr/bin/env python3
"""
A2S2-OS app: OT-2 Vision (Vision-Language-to-Protocol for the Opentrons OT-2).

A FIFTH app hosted by the A2S2-OS shell (see README -> "Adding an app"). It wraps
the ot2-vision CLI: describe what you want in plain words and it generates an
Opentrons OT-2 protocol with the Claude API — from a built-in mock deck (demo,
no hardware) or from a live camera view of the real deck (vision).

Modes:
  1. demo   (the default "task") — `ot2_vision.cli demo <instruction>`:
       generate a protocol from a built-in mock scene. No camera / OT-2 needed;
       only an Anthropic API key. This is the reliable, hardware-free path.
  2. vision — `ot2_vision.cli vision-run <instruction> --no-execute`:
       capture the deck from a webcam, analyze it with Claude Vision, then
       generate the protocol. Needs a camera at the machine.
  3. analyze — `ot2_vision.cli analyze`:
       Claude-Vision scene analysis of the deck (labware + slots). Needs a camera.

None of these move hardware (execution is never enabled here). The OS contract:
MANIFEST + task_cmd(task, model) plus modes()/build_cmd(mode, task, params) and
optional health()/list_sessions()/classify_line(). Modeled on apps/ppi_btep and
apps/af3_qt_xgb.
"""

import json
import os
import re
import sys
import time
import uuid
from pathlib import Path

# ---------------------------------------------------------------- discovery
_HERE = Path(__file__).resolve()
_OS_ROOT = _HERE.parents[2]          # apps/ot2_vision/app.py -> A2S2-OS
_MODELS_DIR = _HERE.parents[3]       # the directory that holds the projects
OT2VISION_REPO_URL = "https://github.com/kouroshSA/ot2-vision"
OT2VISION_DIR_NAME = "ot2-vision"
# Files that uniquely mark an ot2-vision checkout.
_MARKERS = (Path("ot2_vision") / "cli.py", Path("ot2_vision") / "pipeline.py")

# Claude model choices offered as "Brain" — mapped to concrete model ids that
# ot2-vision reads from ANTHROPIC_MODEL_CODEGEN.
MODELS = {
    "sonnet": "claude-sonnet-4-6",
    "haiku": "claude-haiku-4-5-20251001",
}
DEFAULT_MODEL = "sonnet"


def _looks_like_ot2vision(d: Path) -> bool:
    try:
        return d.is_dir() and all((d / m).exists() for m in _MARKERS)
    except OSError:
        return False


def _find_ot2vision_dir():
    """Return (path, source). `source` explains how it was found (for health)."""
    def _abs(p):
        p = Path(p).expanduser()
        return p if p.is_absolute() else (_OS_ROOT / p)

    env = os.environ.get("OT2VISION_DIR")
    if env:
        return _abs(env).resolve(), "env OT2VISION_DIR"

    cfg = _OS_ROOT / "config.json"
    if cfg.is_file():
        try:
            pd = (json.loads(cfg.read_text()) or {}).get("ot2vision_dir")
            if pd:
                return _abs(pd).resolve(), "config.json"
        except (ValueError, OSError):
            pass

    try:
        home = Path.home()
    except (RuntimeError, OSError):
        home = _MODELS_DIR
    seen = set()
    for root in (_MODELS_DIR, _OS_ROOT, home):
        try:
            root = root.resolve()
        except OSError:
            continue
        if root in seen:
            continue
        seen.add(root)
        cand = root / OT2VISION_DIR_NAME
        if _looks_like_ot2vision(cand):
            return cand.resolve(), f"clone: {cand.name}"
        try:
            entries = sorted(p for p in root.iterdir() if p.is_dir())
        except OSError:
            continue
        for sib in entries:
            if _looks_like_ot2vision(sib):
                return sib.resolve(), f"found in: {sib.name}"

    return (_MODELS_DIR / OT2VISION_DIR_NAME).resolve(), "default (not found)"


OT2VISION_DIR, OT2VISION_SOURCE = _find_ot2vision_dir()
RUNS_ROOT = OT2VISION_DIR / "a2s2_runs"        # one subdir per OS-launched run


# ---------------------------------------------------------------- env python
def _ot2_python():
    """Locate the `ot2vision` conda env's python (anthropic + opencv + the
    installed package live there). Override with OT2VISION_PYTHON. Falls back to
    "python" on PATH (the OS then runs via `conda run -n ot2vision`)."""
    override = os.environ.get("OT2VISION_PYTHON")
    if override:
        return override

    candidates = []
    p = Path(sys.executable).resolve()
    for anc in p.parents:
        if anc.name == "envs":
            candidates.append(anc / "ot2vision" / "bin" / "python")
            break
    else:
        candidates.append(p.parent.parent / "envs" / "ot2vision" / "bin" / "python")
    for var in ("CONDA_PREFIX_1", "CONDA_ROOT", "CONDA_EXE"):
        v = os.environ.get(var)
        if v:
            base = Path(v)
            base = base.parents[1] if base.name == "conda" else base
            candidates.append(base / "envs" / "ot2vision" / "bin" / "python")
    try:
        home = Path.home()
    except (RuntimeError, OSError):
        home = _MODELS_DIR
    for d in ("anaconda3", "miniconda3", "miniforge3", "mambaforge"):
        candidates.append(home / d / "envs" / "ot2vision" / "bin" / "python")

    for c in candidates:
        try:
            if c and Path(c).is_file():
                return str(c)
        except OSError:
            continue
    return "python"


# ---------------------------------------------------------------- api key
def _anthropic_key():
    """The Anthropic key ot2-vision needs. Prefer the process env; otherwise read
    it from the agent's .env (the OS already keeps one there), so the app works
    without a separate ot2-vision/.env."""
    k = os.environ.get("ANTHROPIC_API_KEY")
    if k:
        return k
    for envf in (OT2VISION_DIR / ".env", _OS_ROOT / "A2S2-Agent" / ".env"):
        try:
            if envf.is_file():
                for line in envf.read_text().splitlines():
                    line = line.strip()
                    if line.startswith("ANTHROPIC_API_KEY"):
                        _, _, val = line.partition("=")
                        val = val.strip().strip('"').strip("'")
                        if val:
                            return val
        except OSError:
            continue
    return ""


# ---------------------------------------------------------------- helpers
def _pget(params, key, default):
    v = (params or {}).get(key, default)
    if v is None or (isinstance(v, str) and not v.strip()):
        return default
    return v


def _pint(params, key, default):
    try:
        return str(int(str(_pget(params, key, default)).strip()))
    except (ValueError, TypeError):
        return str(default)


def _new_run_dir():
    run_id = time.strftime("%Y-%m-%d_%H-%M-%S") + "_run_" + uuid.uuid4().hex[:8]
    run_dir = RUNS_ROOT / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_id, run_dir


def _write_meta(run_dir, run_id, mode_id, task, model, extra=None):
    meta = {
        "session_id": run_id,
        "task_label": (task or f"{mode_id} (no instruction)"),
        "raw_task": (task or "").strip(),
        "mode": mode_id,
        "model": model,
        "interface": "OT-2 Vision",
        "started_at_iso": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    if extra:
        meta.update(extra)
    try:
        (run_dir / "metadata.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    except OSError:
        pass


def _base_env(model):
    brain = model if model in MODELS else DEFAULT_MODEL
    env = {**os.environ, "PYTHONUNBUFFERED": "1"}
    key = _anthropic_key()
    if key:
        env["ANTHROPIC_API_KEY"] = key
    env["ANTHROPIC_MODEL_CODEGEN"] = MODELS[brain]     # protocol generation
    return env, brain


# ---------------------------------------------------------------- run command
def build_cmd(mode, task, params):
    """(argv, cwd, env) for one ot2-vision run. cwd is the repo (so `-m
    ot2_vision.cli` and relative outputs resolve); argv[0] is a bare-ish python
    the OS wraps in `conda run -n ot2vision`. Outputs land in a per-run dir."""
    mode = mode or "task"
    params = params or {}
    task = (task or "").strip()
    model = _pget(params, "model", DEFAULT_MODEL)
    run_id, run_dir = _new_run_dir()
    py = _ot2_python()
    env, brain = _base_env(model)
    out = run_dir / "protocol.py"

    if mode == "run":                   # RealSense + YOLO -> ground -> protocol
        argv = [py, "-m", "ot2_vision.cli", "run", task, "--no-execute",
                "--output", str(out)]
        _write_meta(run_dir, run_id, mode, task, brain, {"output": str(out)})
        return argv, str(OT2VISION_DIR), env

    if mode == "vision":                # live camera -> Claude Vision -> protocol
        argv = [py, "-m", "ot2_vision.cli", "vision-run", task, "--no-execute",
                "--device", _pint(params, "device", 0), "--output", str(out)]
        _write_meta(run_dir, run_id, mode, task, brain, {"output": str(out)})
        return argv, str(OT2VISION_DIR), env

    if mode == "analyze":               # Claude-Vision deck analysis (no protocol)
        argv = [py, "-m", "ot2_vision.cli", "analyze",
                "--device", _pint(params, "device", 0)]
        _write_meta(run_dir, run_id, mode, task or "analyze deck", brain)
        return argv, str(OT2VISION_DIR), env

    # default: demo (mock scene, no hardware)
    argv = [py, "-m", "ot2_vision.cli", "demo", task, "--output", str(out)]
    _write_meta(run_dir, run_id, "demo", task, brain, {"output": str(out)})
    return argv, str(OT2VISION_DIR), env


def task_cmd(task, model):
    """Legacy single-task entry (home Ask box) — runs the hardware-free demo."""
    return build_cmd("task", task, {"model": model})


# ---------------------------------------------------------------- modes
MODES = [
    {
        "id": "task", "name": "Describe a protocol",
        "tagline": "Plain words -> an OT-2 protocol (mock deck, no hardware)",
        "desc": "Say what you want the OT-2 to do; the Claude API generates a "
                "validated Opentrons protocol from a built-in mock deck (two "
                "96-well plates + a 300 uL tip rack). No camera or robot needed.",
        "accent": "#F2B23E", "gallery": False, "needs_task": True,
        "task_hint": "e.g. Transfer 100 uL from the plate in slot 1 to slot 3",
        "params": [
            {"id": "model", "label": "Brain", "type": "choice",
             "choices": list(MODELS), "default": DEFAULT_MODEL},
        ],
    },
    {
        "id": "vision", "name": "Vision run",
        "tagline": "See the real deck, then write the protocol",
        "desc": "Capture the OT-2 deck from a webcam, analyze the labware and "
                "slots with Claude Vision, then generate the protocol for your "
                "instruction. Generation only — never executes on the robot. "
                "Needs a camera at the machine.",
        "accent": "#4C8DFF", "gallery": True, "needs_task": True,
        "task_hint": "e.g. Add 50 uL of buffer to every well in column 1",
        "params": [
            {"id": "model", "label": "Brain", "type": "choice",
             "choices": list(MODELS), "default": DEFAULT_MODEL},
            {"id": "device", "label": "Camera device", "type": "int",
             "default": 0, "min": 0, "max": 15},
        ],
    },
    {
        "id": "run", "name": "Deck run (RealSense + YOLO)",
        "tagline": "Live RealSense capture -> YOLO detect -> ground -> protocol",
        "desc": "The full depth pipeline: capture the deck with the Intel "
                "RealSense (RGB + aligned depth), detect labware with YOLOv8, "
                "ground each detection to its deck slot, then generate the "
                "protocol for your instruction. Generation only — never executes "
                "on the robot. Needs the RealSense camera, the YOLOv8 weights, "
                "and a one-time deck calibration (ArUco markers).",
        "accent": "#E5484D", "gallery": True, "needs_task": True,
        "task_hint": "e.g. Transfer 50 uL from the plate in slot 5 to column 1",
        "params": [
            {"id": "model", "label": "Brain", "type": "choice",
             "choices": list(MODELS), "default": DEFAULT_MODEL},
        ],
    },
    {
        "id": "analyze", "name": "Analyze the deck",
        "tagline": "Claude Vision: what labware is on the deck?",
        "desc": "Capture the deck and use Claude Vision to identify the labware "
                "in each slot (type, load name, confidence). No instruction "
                "needed. Needs a camera at the machine.",
        "accent": "#46C26A", "gallery": True, "needs_task": False,
        "params": [
            {"id": "device", "label": "Camera device", "type": "int",
             "default": 0, "min": 0, "max": 15},
        ],
    },
]


def modes():
    return MODES


# ---------------------------------------------------------------- log -> state
_OK_HINTS = re.compile(r"(saved to|validation:.*(ok|valid|passed)|generated protocol|succeeded|connected)", re.I)
_ERR_HINTS = re.compile(r"(error|traceback|exception|failed|not found|connection failed|invalid)", re.I)
_PLAN_HINTS = re.compile(r"(instruction:|scene description|generating protocol|analyzing|capturing|scene analysis|labware)", re.I)
_RUN_RE = re.compile(r"a2s2_runs[/\\]([0-9A-Za-z_\-]+_run_[0-9A-Za-z]+)")


def classify_line(line):
    out = {}
    low = line.lower()
    if _ERR_HINTS.search(low):
        out["k"] = "err"
    elif _OK_HINTS.search(low):
        out["k"] = "ok"
    elif _PLAN_HINTS.search(low):
        out["k"] = "plan"
    else:
        out["k"] = ""
    run = _RUN_RE.search(line)
    if run:
        out["session_dir"] = run.group(0)
    return out


# ---------------------------------------------------------------- sessions
def list_sessions():
    """Saved OT-2 Vision runs, newest first (cap 80), in the shell's row shape."""
    if not RUNS_ROOT.is_dir():
        return []
    try:
        dirs = [d for d in RUNS_ROOT.iterdir() if d.is_dir()]
    except OSError:
        return []
    dirs.sort(key=lambda p: p.name, reverse=True)

    out = []
    for d in dirs[:80]:
        rec = {"label": d.name, "model": None, "outcome": None, "time": None, "files": []}
        meta = d / "metadata.json"
        if meta.is_file():
            try:
                m = json.loads(meta.read_text())
                rec["label"] = m.get("task_label") or d.name
                rec["model"] = m.get("model") or m.get("mode")
                rec["time"] = m.get("started_at_iso")
            except (ValueError, OSError):
                pass
        rec["outcome"] = "success" if (d / "protocol.py").is_file() else "pending"
        try:
            rec["files"] = sorted(p.name for p in d.iterdir() if p.is_file())[:6]
        except OSError:
            rec["files"] = []
        if not rec["time"]:
            rec["time"] = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(d.stat().st_mtime))
        out.append(rec)
    return out


# ---------------------------------------------------------------- library
LIBRARY = [
    {"color": "amber", "title": "Serial dilution",
     "task": "Do a 1:2 serial dilution across row A of the plate in slot 1 using the tip rack in slot 10.",
     "desc": "Generate a serial-dilution protocol on the mock deck (no hardware)."},
    {"color": "green", "title": "Plate-to-plate transfer",
     "task": "Transfer 100 uL from each well of the plate in slot 1 to the matching well of the plate in slot 3.",
     "desc": "One-to-one 96-well transfer between the two mock plates."},
    {"color": "blue", "title": "Add buffer to a column",
     "task": "Add 50 uL of buffer to every well in column 1 of the plate in slot 3.",
     "desc": "Single-column dispense from the mock tip rack."},
    {"color": "amber", "title": "Pool a column",
     "task": "Pool 20 uL from each well of column 1 of the plate in slot 1 into well A1 of the plate in slot 3.",
     "desc": "Consolidation (many-to-one) on the mock deck."},
]


def library():
    return LIBRARY


# ---------------------------------------------------------------- health
def health():
    repo_found = OT2VISION_DIR.is_dir() and all((OT2VISION_DIR / m).exists() for m in _MARKERS)
    py = _ot2_python()
    py_ok = (py != "python") and Path(py).is_file()
    key_ok = bool(_anthropic_key())
    # Extra prerequisites for the "run" mode (RealSense + YOLO + calibration).
    yolo_ok = (OT2VISION_DIR / "models" / "yolov8m.pt").is_file()
    calib_ok = (OT2VISION_DIR / "calibration" / "camera_to_deck.json").is_file()
    return {
        # demo (the default) needs only the repo, its env, and an Anthropic key.
        "ready": bool(repo_found and py_ok and key_ok),
        "repo": str(OT2VISION_DIR),
        "llm_dir": str(OT2VISION_DIR),
        "llm_dir_found": repo_found,
        "twin_source": OT2VISION_SOURCE,
        "twin_repo_url": OT2VISION_REPO_URL,
        "ot2_python": py,
        "ot2_python_found": py_ok,
        "api_key_present": key_ok,
        "api_key_hint": (None if key_ok else
                         "Set ANTHROPIC_API_KEY (env or ot2-vision/.env) — the "
                         "demo/vision modes call the Claude API."),
        "camera_hint": "The vision & analyze modes need a webcam at the machine; "
                       "the demo mode runs with no hardware.",
        # "run" mode extras (RealSense pipeline):
        "yolo_weights_found": yolo_ok,
        "calibration_found": calib_ok,
        "run_hint": (None if (yolo_ok and calib_ok) else
                     "The 'run' mode also needs models/yolov8m.pt"
                     + ("" if yolo_ok else " (missing)")
                     + " and a deck calibration at calibration/camera_to_deck.json"
                     + ("" if calib_ok else " (missing — run `ot2-vision calibrate` "
                        "with 4 ArUco DICT_4X4_50 markers, IDs 0-3, on the deck)")
                     + "."),
    }


# ---------------------------------------------------------------- manifest
MANIFEST = {
    "id": "ot2_vision",
    "name": "OT-2 Vision",
    "tagline": "Vision-Language-to-Protocol for the Opentrons OT-2",
    "summary": "Describe a liquid-handling task in plain words and get a "
               "validated Opentrons OT-2 protocol — from a built-in mock deck "
               "(no hardware) or from a live camera view of the real deck.",
    "accent": "#4C8DFF",
    "models": list(MODELS),
    # Has a live camera (RealSense / webcam) — the console shows its MJPEG feed
    # (served by camera_server.py) in the Watch view.
    "camera": True,
    # Runs under `conda run -n ot2vision` (anthropic + opencv + the installed
    # package). _ot2_python() is the fallback when conda is unavailable.
    "conda_env": "ot2vision",
    "vocabulary": "lab automation (Opentrons OT-2, deck slots, labware, tip "
                  "racks, 96-well plates, pipettes, aspirate/dispense, protocols)",
    # Per-app wording for the shell's single-task "Ask" surface (demo mode).
    "ask_title": "Describe a protocol",
    "ask_blurb": "Say what you want the OT-2 to do in plain words — it generates "
                 "a validated Opentrons protocol from a mock deck, no hardware "
                 "needed. Use Run modes for the live-camera vision pipeline.",
    "ask_placeholder": "e.g. Transfer 100 uL from the plate in slot 1 to slot 3",
    "ask_button": "Generate protocol",
}
