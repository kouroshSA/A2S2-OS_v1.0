#!/usr/bin/env python3
"""
A2S2-OS app: xArm Vision Grasp (uFactory ufactory_vision / GGCNN).

Wraps uFactory's official vision-grasping demo so the OS (and the agent) can
launch it. GGCNN (Generative Grasping CNN) reads the Intel RealSense D435 depth
image, predicts a top-down antipodal grasp, and drives a PHYSICAL xArm (5/6/7 or
850) or Lite6 to pick the object. The pretrained GGCNN weights ship in the repo.

Unlike the "Twin Robot World" app (the MuJoCo simulation), this moves a REAL arm.
It is a LONG-RUNNING grasp loop with a live camera window — start it with the
arm's IP, watch it grasp, and press Stop in Watch to end it.

The OS contract:  MANIFEST + task_cmd(task, model) plus modes()/build_cmd. There
are no saved run artifacts (it is a live loop), so list_sessions returns []. The
"task" IS the robot's IP address (the demo's one required argument).
"""

import os
import re
import sys
from pathlib import Path

# ---------------------------------------------------------------- discovery
_HERE = Path(__file__).resolve()
_OS_ROOT = _HERE.parents[2]          # apps/ufactory_vision/app.py -> A2S2-OS
_MODELS_DIR = _HERE.parents[3]       # the directory that holds the projects
UFV_REPO_URL = "https://github.com/xArm-Developer/ufactory_vision"
UFV_DIR_NAME = "ufactory_vision"

_DEMO_REL = Path("ggcnn_grasping_demo")
_EXAMPLE_REL = _DEMO_REL / "example" / "realsense_d435"
SCRIPT_XARM = "run_rs_d435_grasp.py"          # xArm 5/6/7, 850 + gripper G1/G2
SCRIPT_LITE6 = "run_rs_d435_grasp_lite6.py"   # Lite6 + vacuum gripper
_MODEL_REL = _DEMO_REL / "models" / "ggcnn_epoch_23_cornell"
# Files that uniquely mark a ufactory_vision checkout.
_MARKERS = (_EXAMPLE_REL / SCRIPT_XARM, _MODEL_REL)


def _looks_like_ufv(d: Path) -> bool:
    try:
        return d.is_dir() and all((d / m).exists() for m in _MARKERS)
    except OSError:
        return False


def _find_ufv_dir():
    """Return (path, source). Resolution: env UFACTORY_VISION_DIR ->
    config.json "ufactory_vision_dir" -> auto-discover beside/under A2S2-OS ->
    default sibling named "ufactory_vision"."""
    def _abs(p):
        p = Path(p).expanduser()
        return p if p.is_absolute() else (_OS_ROOT / p)

    env = os.environ.get("UFACTORY_VISION_DIR")
    if env:
        return _abs(env).resolve(), "env UFACTORY_VISION_DIR"

    cfg = _OS_ROOT / "config.json"
    if cfg.is_file():
        try:
            import json
            pd = (json.loads(cfg.read_text()) or {}).get("ufactory_vision_dir")
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
        cand = root / UFV_DIR_NAME
        if _looks_like_ufv(cand):
            return cand.resolve(), f"clone: {cand.name}"
        try:
            entries = sorted(p for p in root.iterdir() if p.is_dir())
        except OSError:
            continue
        for sib in entries:
            if _looks_like_ufv(sib):
                return sib.resolve(), f"found in: {sib.name}"

    return (_MODELS_DIR / UFV_DIR_NAME).resolve(), "default (not found)"


UFV_DIR, UFV_SOURCE = _find_ufv_dir()
EXAMPLE_DIR = UFV_DIR / _EXAMPLE_REL


# ---------------------------------------------------------------- env python
def _ufv_python():
    """Locate the `ufactory_vision` conda env's python (torch + xarm-python-sdk +
    pyrealsense2 live there). Override with UFACTORY_VISION_PYTHON. Falls back to
    "python" (the OS then runs via `conda run -n ufactory_vision`)."""
    override = os.environ.get("UFACTORY_VISION_PYTHON")
    if override:
        return override

    candidates = []
    p = Path(sys.executable).resolve()
    for anc in p.parents:
        if anc.name == "envs":
            candidates.append(anc / "ufactory_vision" / "bin" / "python")
            break
    else:
        candidates.append(p.parent.parent / "envs" / "ufactory_vision" / "bin" / "python")
    for var in ("CONDA_PREFIX_1", "CONDA_ROOT", "CONDA_EXE"):
        v = os.environ.get(var)
        if v:
            base = Path(v)
            base = base.parents[1] if base.name == "conda" else base
            candidates.append(base / "envs" / "ufactory_vision" / "bin" / "python")
    try:
        home = Path.home()
    except (RuntimeError, OSError):
        home = _MODELS_DIR
    for d in ("anaconda3", "miniconda3", "miniforge3", "mambaforge"):
        candidates.append(home / d / "envs" / "ufactory_vision" / "bin" / "python")

    for c in candidates:
        try:
            if c and Path(c).is_file():
                return str(c)
        except OSError:
            continue
    return "python"


# ---------------------------------------------------------------- run command
_IP_RE = re.compile(r"^\d{1,3}(\.\d{1,3}){3}$")


def _script_for(params):
    arm = str((params or {}).get("arm", "xarm")).strip().lower()
    return SCRIPT_LITE6 if arm in ("lite6", "lite 6", "lite") else SCRIPT_XARM


def build_cmd(mode, task, params):
    """(argv, cwd, env) for the grasp loop. `task` is the xArm's IP address (the
    demo's single required arg). Runs under the `ufactory_vision` env's python;
    argv[0] is a bare-ish python the OS wraps in `conda run -n ufactory_vision`.
    Long-running — the OS stops it by signalling the process group."""
    params = params or {}
    ip = (task or "").strip() or "ROBOT_IP"     # placeholder keeps kg_build happy
    script = EXAMPLE_DIR / _script_for(params)
    argv = [_ufv_python(), str(script), ip]
    env = {**os.environ, "PYTHONUNBUFFERED": "1"}
    return argv, str(EXAMPLE_DIR), env


def task_cmd(task, model):
    """Legacy single-task entry — same grasp loop (xArm variant)."""
    return build_cmd("grasp", task, {"arm": "xarm"})


# ---------------------------------------------------------------- modes
MODES = [
    {
        "id": "grasp", "name": "Vision grasp (GGCNN)",
        "tagline": "RealSense depth -> GGCNN -> grasp on a physical xArm",
        "desc": "Start the vision-grasping loop: the Intel RealSense D435 depth "
                "image feeds GGCNN, which predicts a top-down grasp, and the arm "
                "picks the object. This drives a REAL xArm — enter the arm's IP "
                "address. Runs until you press Stop in Watch. A camera window "
                "opens on the machine.",
        "accent": "#E5484D", "gallery": False, "needs_task": True,
        "long_running": True,
        "task_hint": "the xArm's IP address, e.g. 192.168.1.111",
        "params": [
            {"id": "arm", "label": "Arm", "type": "choice",
             "choices": ["xarm", "lite6"], "default": "xarm"},
        ],
    },
]


def modes():
    return MODES


# ---------------------------------------------------------------- log -> state
_OK_HINTS = re.compile(r"(connected|grasp|success|reached|picked|done)", re.I)
_ERR_HINTS = re.compile(r"(error|traceback|exception|failed|not connect|timeout|no device|refused)", re.I)
_PLAN_HINTS = re.compile(r"(ggcnn|depth|detect|moving|planning|camera|realsense|loading)", re.I)


def classify_line(line):
    low = (line or "").lower()
    if _ERR_HINTS.search(low):
        return {"k": "err"}
    if _OK_HINTS.search(low):
        return {"k": "ok"}
    if _PLAN_HINTS.search(low):
        return {"k": "plan"}
    return {"k": ""}


def list_sessions():
    # Live grasp loop — no saved run artifacts.
    return []


# ---------------------------------------------------------------- health
def health():
    repo_found = _looks_like_ufv(UFV_DIR)
    py = _ufv_python()
    py_ok = (py != "python") and Path(py).is_file()
    return {
        # "ready" means the code + env are installed. A real run also needs a
        # physical xArm reachable at the given IP, the RealSense D435, a gripper,
        # and a display for the camera window.
        "ready": bool(repo_found and py_ok),
        "repo": str(UFV_DIR),
        "llm_dir": str(UFV_DIR),
        "llm_dir_found": repo_found,
        "twin_source": UFV_SOURCE,
        "twin_repo_url": UFV_REPO_URL,
        "ufv_python": py,
        "ufv_python_found": py_ok,
        "model_found": (UFV_DIR / _MODEL_REL).is_file(),
        "hardware_hint": "Moves a REAL xArm. Needs the arm reachable at the IP "
                         "you provide, the RealSense D435, a UFACTORY gripper, "
                         "and a display for the live camera window.",
        # local model (GGCNN weights ship in the repo): no API key needed.
        "api_key_present": True,
    }


# ---------------------------------------------------------------- manifest
MANIFEST = {
    "id": "ufactory_vision",
    "name": "xArm Vision Grasp",
    "tagline": "GGCNN vision-guided grasping on a physical xArm (RealSense D435)",
    "summary": "uFactory's ufactory_vision demo: GGCNN reads the RealSense D435 "
               "depth image, predicts a top-down grasp, and a real xArm (or "
               "Lite6) picks the object. Long-running loop — start it with the "
               "arm's IP, stop it from Watch.",
    "accent": "#E5484D",
    "models": ["GGCNN"],
    # Runs under `conda run -n ufactory_vision` (torch + xarm-python-sdk +
    # pyrealsense2). _ufv_python() is the fallback when conda is unavailable.
    "conda_env": "ufactory_vision",
    "vocabulary": "robotics (xArm, Lite6, end effector/gripper, RealSense depth "
                  "camera, GGCNN grasp detection, top-down antipodal grasp, TCP)",
    # Per-app wording for the shell's single-task "Ask" surface.
    "ask_title": "Vision grasp",
    "ask_blurb": "Start GGCNN vision-guided grasping on a real xArm. Enter the "
                 "arm's IP address; the RealSense D435 drives the grasp. This "
                 "moves the physical arm and runs until you stop it.",
    "ask_placeholder": "the xArm's IP address, e.g. 192.168.1.111",
    "ask_button": "Start grasping",
}
