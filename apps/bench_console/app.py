#!/usr/bin/env python3
"""
A2S2-OS app: Bench Console (xArm6 digital twin).

This is ONE app hosted by the A2S2-OS shell. It owns everything specific to the
xArm6 rail digital twin: where the twin project lives, the command used to run a
task, how saved runs (recordings/) are read back, and how a run's stdout lines
map to bench state (arm motion, rail position, the saved session).

The OS (server.py) is app-agnostic: it serves the shell, runs jobs in threads,
and routes /api/health|library|sessions|run|job to whatever app is active. To
add a second app (e.g. an OT-2 liquid handler, a microscope), drop another
folder under apps/ exposing the same surface defined at the bottom of this file
(MANIFEST + the functions in APP). Nothing here needs to change.

User-facing strings speak bench biology only — no robotics/ROS nouns leak out.
"""

import json
import os
import re
import sys
import time
from pathlib import Path

# ---------------------------------------------------------------- discovery
# The OS lives in its own project (e.g. Models/A2S2-OS) and sits *on top of* the
# twin: a clone of the xArm6 digital-twin repo. We identify the twin by the
# well-known LLM-project path it contains, NOT by its folder name -- so any
# clone works (the default `git clone` name, a renamed copy, anything).
TWIN_REPO_URL = "https://github.com/kouroshSA/xarm6-digital-twin-v5"
TWIN_REPO_NAME = "xarm6-digital-twin-v5"   # the folder name `git clone` creates
_HERE = Path(__file__).resolve()
_OS_ROOT = _HERE.parents[2]          # apps/bench_console/app.py -> A2S2-OS
_MODELS_DIR = _HERE.parents[3]       # the directory that holds both projects
_TWIN_REL = Path("xarm6_rail_digital_twin_llm_v5") / "xarm_lab_twin"


def _scan_for_twin(roots):
    """Scan candidate roots for a twin clone. Returns (llm_dir, source) or None.

    Identification is by content, not folder name: any child directory that
    contains <child>/xarm6_rail_digital_twin_llm_v5/xarm_lab_twin is a twin
    clone. A child literally named `xarm6-digital-twin-v5` is preferred when
    several match, so the canonical clone wins over an unrelated leftover.
    """
    seen = set()
    for root in roots:
        try:
            root = root.resolve()
        except OSError:
            continue
        if root in seen:
            continue
        seen.add(root)
        try:
            entries = sorted(p for p in root.iterdir() if p.is_dir())
        except OSError:
            continue
        # prefer the canonical clone name
        for sib in entries:
            if sib.name == TWIN_REPO_NAME and (sib / _TWIN_REL).is_dir():
                return (sib / _TWIN_REL).resolve(), f"clone: {sib.name}"
        # otherwise any folder that holds the twin path (name-independent)
        for sib in entries:
            if (sib / _TWIN_REL).is_dir():
                return (sib / _TWIN_REL).resolve(), f"found in: {sib.name}"
    return None


def _find_llm_dir():
    """Locate <twin>/xarm6_rail_digital_twin_llm_v5/xarm_lab_twin.

    Returns (path, source) where `source` explains how it was found, so the
    server banner and /api/health can show it. Resolution order, first hit wins:

      1. env BENCH_TWIN_DIR            -- explicit, wins over everything
      2. config.json -> "twin_dir"     -- persistent, set once per install
      3. clone auto-discovery          -- any clone of the twin repo found beside
                                          A2S2-OS, inside it, or under the home
                                          dir (identified by content, not name)
      4. default fallback              -- the canonical clone name as a sibling;
                                          if absent the browser stays in demo mode.

    A relative path in BENCH_TWIN_DIR or config.json is resolved against the OS
    root, so "../xarm6-digital-twin-v5/..." works regardless of the launch cwd.
    """
    def _abs(p):
        p = Path(p).expanduser()
        return p if p.is_absolute() else (_OS_ROOT / p)

    # 1) explicit env override
    env = os.environ.get("BENCH_TWIN_DIR")
    if env:
        return _abs(env).resolve(), "env BENCH_TWIN_DIR"

    # 2) persistent config file at the OS root: {"twin_dir": "<path>"}
    cfg = _OS_ROOT / "config.json"
    if cfg.is_file():
        try:
            td = (json.loads(cfg.read_text()) or {}).get("twin_dir")
            if td:
                return _abs(td).resolve(), "config.json"
        except (ValueError, OSError):
            pass

    # 3) auto-discover a clone of the twin repo (by content, any folder name)
    try:
        home = Path.home()
    except (RuntimeError, OSError):
        home = _MODELS_DIR
    hit = _scan_for_twin([_MODELS_DIR, _OS_ROOT, home])
    if hit:
        return hit

    # 4) last resort -- canonical clone name as a sibling (not found => demo mode)
    return (_MODELS_DIR / TWIN_REPO_NAME / _TWIN_REL).resolve(), "default (not found)"


LLM_DIR, TWIN_SOURCE = _find_llm_dir()
TWIN_REPO = LLM_DIR.parents[1]       # the twin repo root (parent of the llm project)

# Second recordings root: the "basic" interactive project ships alongside the
# LLM one and writes the same session format.
BASIC_RECORDINGS = (
    TWIN_REPO / "xarm6_rail_sim_interactive_basic_v5" / "xarm_lab_twin" / "recordings"
)


# ---------------------------------------------------------------- modes
# The twin can be driven in several distinct ways ("modes of operation"). Each
# maps to one of the twin's scripts with a fixed set of flags. The OS shell
# renders one card + parameter form per mode from these descriptors, so adding
# or retuning a mode is a data edit here -- the server and shell stay generic.
#
# Field contract (read by server.py and index.html):
#   id          stable key; build_cmd() switches on it
#   name        card title (bench-friendly, no robotics nouns)
#   tagline     one-line subtitle on the card
#   desc        paragraph shown on the runner screen
#   accent      hex colour for the card swatch
#   gallery     True  -> shown in the "Lab modes" gallery
#               False -> reachable only via the dedicated "Ask"/"Protocol" tiles
#   needs_task  True  -> a free-text task is required before running
#   task_hint   placeholder for the task box (when needs_task)
#   long_running True -> runs until stopped (no natural end); shell shows Stop
#   params      list of {id,label,type,default,...}; type in
#               {choice(+choices), int(+min/max[,optional]), bool}
#
# All non-interactive flags below (--save-all, --auto-accept-variants,
# --no-annotate) are REQUIRED: the OS launches children with stdin detached, so
# any script that would otherwise prompt must be told not to.
MODES = [
    {
        "id": "task", "name": "Ask the bench",
        "tagline": "One task, planned and run",
        "desc": "Say a single task in plain words; the bench plans it and runs "
                "it once, saving the result.",
        "accent": "#F2B23E", "gallery": False, "needs_task": True,
        "task_hint": "e.g. Put the green cube in the green bin",
        "params": [
            {"id": "model", "label": "Planner", "type": "choice",
             "choices": ["haiku", "sonnet"], "default": "haiku"},
        ],
    },
    {
        "id": "learn", "name": "Self-learning loop",
        "tagline": "Repeat one task and improve",
        "desc": "The bench attempts a single task many times, learning as it "
                "goes — plans that worked are reused, and what failed becomes a "
                "constraint for the next attempt. Ends with an improvement "
                "summary across the run.",
        "accent": "#46C26A", "gallery": True, "needs_task": True,
        "task_hint": "e.g. Put the red cube in the red bin",
        "params": [
            {"id": "max_episodes", "label": "Attempts", "type": "int",
             "default": 10, "min": 2, "max": 50},
            {"id": "stringency", "label": "Grading", "type": "choice",
             "choices": ["loose", "normal", "strict"], "default": "loose"},
            {"id": "model", "label": "Planner", "type": "choice",
             "choices": ["haiku", "sonnet"], "default": "haiku"},
        ],
    },
    {
        "id": "autoplay", "name": "Auto-play batch",
        "tagline": "The bench invents and runs varied tasks",
        "desc": "The bench writes a batch of diverse tasks from the objects on "
                "the bench (cubes, bins, tubes, racks) and runs them "
                "back-to-back. Each is saved as its own result, and a review of "
                "the whole batch is written at the end.",
        "accent": "#4C8DFF", "gallery": True, "needs_task": False,
        "params": [
            {"id": "episodes", "label": "Tasks", "type": "int",
             "default": 5, "min": 1, "max": 50},
            {"id": "model", "label": "Planner", "type": "choice",
             "choices": ["haiku", "sonnet"], "default": "haiku"},
        ],
    },
    {
        "id": "random", "name": "Random play",
        "tagline": "Generate training data — no planner",
        "desc": "The arm samples random reachable poses and moves through them "
                "with no language model in the loop — a fast way to generate "
                "trajectory data for training. Each run is saved in the standard "
                "result format.",
        "accent": "#E5484D", "gallery": True, "needs_task": False,
        "params": [
            {"id": "episodes", "label": "Runs", "type": "int",
             "default": 10, "min": 1, "max": 200},
            {"id": "moves_per_episode", "label": "Moves each", "type": "int",
             "default": 8, "min": 1, "max": 40},
            {"id": "seed", "label": "Seed", "type": "int",
             "default": "", "min": 0, "max": 999999, "optional": True},
        ],
    },
    {
        "id": "augment", "name": "Augment a task",
        "tagline": "Many variations of one task",
        "desc": "Runs one task across several cycles — rewording it and nudging "
                "object positions a little each time — to build a small, varied "
                "dataset around a single task.",
        "accent": "#F2B23E", "gallery": True, "needs_task": True,
        "task_hint": "e.g. Put the red cube in the red bin",
        "params": [
            {"id": "cycles", "label": "Cycles", "type": "int",
             "default": 4, "min": 2, "max": 30},
            {"id": "pos_jitter_mm", "label": "Position jitter (mm)",
             "type": "int", "default": 20, "min": 0, "max": 100},
            {"id": "rot_jitter_deg", "label": "Rotation jitter (deg)",
             "type": "int", "default": 45, "min": 0, "max": 180},
            {"id": "model", "label": "Planner", "type": "choice",
             "choices": ["haiku", "sonnet"], "default": "haiku"},
        ],
    },
    {
        "id": "vr", "name": "VR teleoperation",
        "tagline": "Hand-guide the bench from a headset",
        "desc": "Starts the VR teleoperation server so you can drive the bench "
                "by hand from a headset and record demonstrations. Open the "
                "address shown in the run log on the headset; press Stop here "
                "when you are finished.",
        "accent": "#4C8DFF", "gallery": True, "needs_task": False,
        "long_running": True,
        "params": [
            {"id": "view", "label": "View", "type": "choice",
             "choices": ["mono", "stereo"], "default": "mono"},
        ],
    },
]


def modes():
    return MODES


# ---------------------------------------------------------------- run command
def _py():
    """The interpreter the twin's scripts run under. Defaults to the very Python
    running the OS server so a `conda activate xarm6sim` before launch carries
    through; the OS may still wrap this in `conda run -n xarm6sim`."""
    return os.environ.get("LAB_OS_PYTHON") or sys.executable or "python"


def build_cmd(mode, task, params):
    """Build the exact command + working dir for one run of `mode`.

    This is the single place that knows the twin's CLI for every mode. Returns
    (argv, cwd, env). `params` is the shell's parameter dict for the mode (keys
    match the `params[].id` in MODES); missing keys fall back to the descriptor
    defaults. Edit here if the twin's flags ever change.
    """
    py = _py()
    p = params or {}

    def i(key, default):           # int param, tolerant of strings/blanks
        try:
            return int(float(str(p.get(key, default)).strip()))
        except (TypeError, ValueError):
            return int(default)

    def f(key, default):           # float param
        try:
            return float(str(p.get(key, default)).strip())
        except (TypeError, ValueError):
            return float(default)

    def s(key, default):           # string/choice param
        v = p.get(key, default)
        return str(v) if v is not None else default

    if mode in (None, "", "task"):
        argv = [py, "scripts/run_task.py", task, "--model", s("model", "haiku")]
    elif mode == "learn":
        argv = [py, "scripts/run_task.py", task,
                "--model", s("model", "haiku"), "--loop",
                "--max-episodes", str(i("max_episodes", 10)),
                "--stringency", s("stringency", "loose")]
    elif mode == "autoplay":
        argv = [py, "scripts/auto_play.py",
                "--episodes", str(i("episodes", 5)),
                "--model", s("model", "haiku"), "--save-all"]
    elif mode == "random":
        argv = [py, "scripts/random_play.py",
                "--episodes", str(i("episodes", 10)),
                "--moves-per-episode", str(i("moves_per_episode", 8)),
                "--save-all"]
        if str(p.get("seed", "")).strip() != "":
            argv += ["--seed", str(i("seed", 0))]
    elif mode == "augment":
        argv = [py, "scripts/run_task_augmented.py", task,
                "--model", s("model", "haiku"),
                "--cycles", str(i("cycles", 4)),
                "--pos-jitter-mm", str(f("pos_jitter_mm", 20.0)),
                "--rot-jitter-deg", str(f("rot_jitter_deg", 45.0)),
                "--auto-accept-variants", "--no-annotate"]
    elif mode == "vr":
        argv = [py, "scripts/run_vr.py", "--mode", s("view", "mono")]
    else:
        raise ValueError(f"unknown mode: {mode!r}")
    return argv, str(LLM_DIR), {**os.environ}


def task_cmd(task, model):
    """Back-compat shim: the single-task path. New code uses build_cmd()."""
    return build_cmd("task", task, {"model": model})


# ---------------------------------------------------------------- log -> state
# The twin's scripts print a planned sequence of primitives and an execution
# trace; the multi-episode modes (auto-play, random play, augment, loop) add
# their own [AutoPlay]/[RandomPlay]/[Augment]/Episode markers and a final
# RUN SUMMARY. We turn each line into a colour tag for the run log and sniff
# bench state out of it.
_ARM_HINTS = re.compile(r"(pick|grasp|place|push|move|wave|rail|gripper)", re.I)
_OK_HINTS = re.compile(
    r"(✓|\bok\b|complete|\[lessons\]|\bsaved\b|improving|success=true)", re.I)
_ERR_HINTS = re.compile(
    r"(error|traceback|exception|\bfault\b|\bfail\b|success=false)", re.I)
_PLAN_HINTS = re.compile(
    r"(\[autoplay\]|\[randomplay\]|\[augment\]|\[task\]|\[llmbrain\]"
    r"|episode\s+\d+|cycle\s+\d+|run id|generating|plan|planned sequence"
    r"|locate|grasp|dispatch|physical outcome|outcome check|run summary"
    r"|trend|workspace)", re.I)
# A saved run is referenced either as a path  recordings/<ts>_session_<id>  or,
# by the batch scripts, as  Saved: <ts>_session_<id>_ep1.
_SESS_RE = re.compile(
    r"(?:recordings[/\\]|saved:\s*)([0-9A-Za-z_\-]+_session_[0-9A-Za-z_]+)",
    re.I)
# Rail target shows up inside printed param dicts: {'position_mm': 0, ...}
_RAIL_RE = re.compile(r"position_mm['\"]?\s*[:=]\s*(-?\d+(?:\.\d+)?)", re.I)


def classify_line(line):
    """Map one raw stdout line to {k, arm, rail, session_dir} (any key optional).

    k is the run-log colour tag ('plan' | 'ok' | 'err' | ''). arm/rail drive the
    signature bench strip. session_dir captures the saved run path so the OS can
    surface it. Only the keys it can determine are returned.
    """
    out = {}
    low = line.lower()
    if _OK_HINTS.search(line):
        out["k"] = "ok"
    elif _ERR_HINTS.search(low):
        out["k"] = "err"
    elif _PLAN_HINTS.search(low):
        out["k"] = "plan"
    else:
        out["k"] = ""

    arm = _ARM_HINTS.search(low)
    if arm:
        out["arm"] = arm.group(1).lower()

    rail = _RAIL_RE.search(line)
    if rail:
        try:
            out["rail"] = float(rail.group(1))
        except ValueError:
            pass

    sess = _SESS_RE.search(line)
    if sess:
        out["session_dir"] = sess.group(1)

    return out


# ---------------------------------------------------------------- sessions
def _recording_roots():
    return [LLM_DIR / "recordings", BASIC_RECORDINGS]


def list_sessions():
    """Parse saved runs newest-first from both recordings/ roots, cap at 80.

    Maps the twin's real metadata.json schema onto the shell's row shape:
        task_label -> label    llm_model -> model
        outcome -> outcome      started_at_iso/ended_at_iso -> time
    Falls back to dir mtime when a run has no metadata yet.
    """
    # Collect candidate dirs first, sort by name (timestamp prefix => chrono),
    # and only THEN read metadata for the newest 80. The twin can accumulate
    # thousands of recordings; reading every metadata.json each poll would be
    # needlessly heavy.
    candidates = []
    for root in _recording_roots():
        if not root.is_dir():
            continue
        try:
            for d in root.iterdir():
                if d.is_dir():
                    candidates.append(d)
        except OSError:
            continue
    candidates.sort(key=lambda p: p.name, reverse=True)

    out = []
    for d in candidates[:80]:
        rec = {"label": d.name, "model": None, "outcome": None, "time": None, "files": []}
        meta = d / "metadata.json"
        if meta.is_file():
            try:
                m = json.loads(meta.read_text())
                rec["label"] = (
                    m.get("task_label") or m.get("task") or m.get("label") or d.name
                )
                rec["model"] = _short_model(
                    m.get("llm_model") or m.get("model")
                )
                rec["outcome"] = (m.get("outcome") or m.get("result") or "").lower() or None
                rec["time"] = (
                    m.get("ended_at_iso")
                    or m.get("started_at_iso")
                    or m.get("timestamp")
                    or m.get("time")
                )
            except (ValueError, OSError):
                pass
        try:
            rec["files"] = sorted(p.name for p in d.iterdir() if p.is_file())[:6]
        except OSError:
            rec["files"] = []
        if not rec["time"]:
            rec["time"] = time.strftime(
                "%Y-%m-%dT%H:%M:%S", time.localtime(d.stat().st_mtime)
            )
        out.append(rec)
    return out[:80]


# Map the twin's full model ids back to the short names the shell shows.
_MODEL_SHORT = {
    "claude-haiku-4-5-20251001": "haiku",
    "claude-sonnet-4-6": "sonnet",
    "claude-opus-4-7": "opus",
}


def _short_model(full):
    if not full:
        return None
    return _MODEL_SHORT.get(full, full)


# ---------------------------------------------------------------- library
# Curated protocols, built from the scene's own objects/primitives (cube->bin,
# tube->rack, push, wave). Overrides the shell's built-in default via /api/library.
LIBRARY = [
    {"color": "red", "title": "Sort red cube", "task": "Put the red cube in the red bin",
     "desc": "Pick the red cube and place it in the matching bin."},
    {"color": "green", "title": "Sort green cube", "task": "Put the green cube in the green bin",
     "desc": "Pick the green cube and place it in the matching bin."},
    {"color": "blue", "title": "Sort blue cube", "task": "Put the blue cube in the blue bin",
     "desc": "Pick the blue cube and place it in the matching bin."},
    {"color": "amber", "title": "Rack a tube", "task": "Place a Falcon tube into the first rack slot",
     "desc": "Move a Falcon tube into an open slot on the rack."},
    {"color": "blue", "title": "Clear the bench", "task": "Push the blue cube off the front edge of the bench",
     "desc": "Demonstration push primitive — clears an object off the bench."},
    {"color": "green", "title": "Wave", "task": "Wave goodbye",
     "desc": "A quick liveness check — the arm waves."},
]


def library():
    return LIBRARY


# ---------------------------------------------------------------- health
def health():
    found = LLM_DIR.is_dir()
    return {
        "ready": found,
        "repo": str(TWIN_REPO),
        "llm_dir": str(LLM_DIR),
        "llm_dir_found": found,
        "twin_source": TWIN_SOURCE,
        "twin_repo_url": TWIN_REPO_URL,
        "api_key_present": bool(os.environ.get("ANTHROPIC_API_KEY"))
        or (LLM_DIR / ".env").is_file(),
    }


# ---------------------------------------------------------------- manifest
# How the OS identifies and presents this app. `models` lets the OS validate the
# model chips; `vocabulary` documents the bench-only language contract.
MANIFEST = {
    "id": "bench_console",
    "name": "Twin Robot World",
    "tagline": "xArm6 digital twin — a robot world you drive in plain words",
    "summary": "Drive the xArm6 lab bench in plain words — cubes, bins, tubes, racks.",
    "accent": "#F2B23E",
    "models": ["haiku", "sonnet"],
    # The twin (run_task.py + MuJoCo) runs in the `xarm6sim` env. Declaring it
    # lets the OS launch tasks via `conda run -n xarm6sim` even if the server
    # itself was started from a different env. task_cmd's sys.executable default
    # remains the fallback when conda isn't found.
    "conda_env": "xarm6sim",
    "vocabulary": "bench biology (plates, samples, tubes, cubes, bins, racks, protocols, results)",
    # This app has a live physical scene (arm + rail + objects). The shell shows
    # its bench status strip and camera view only for apps that declare a scene;
    # compute-only apps (ppiGPLM, ppiBTEP) render a clean, scene-free console.
    "scene": "bench",
}
