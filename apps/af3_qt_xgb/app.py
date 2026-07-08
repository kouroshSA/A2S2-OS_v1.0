#!/usr/bin/env python3
"""
A2S2-OS app: AF3-QuantumTunneling-XGB.

A FOURTH science app hosted by the A2S2-OS shell (see README -> "Adding an app"),
beside the Twin Robot World, ppiGPLM and ppiBTEP. It wraps the AF3 ->
quantum-tunneling -> XGBoost PPI pipeline as three runnable modes:

  1. summary  (the default "task") — AF3_alphafold_interaction_analyzer.py:
       walk an AlphaFold3 output folder -> per-interaction confidence CSV.
  2. qtfeatures — add_quantum_features_to_af3_qutipwindow.py:
       add ~35 quantum-tunneling feature columns per interaction (sliding-window
       qubit simulation; NumPy / CuPy / QuTiP backends).
  3. classify — af2-3_pair_classifier_w_shap_c.py:
       cross-validated XGBoost classifier (+ optional SHAP) over PRS/RRS-labeled
       QT feature tables, scoring an unlabeled prediction set.

The OS contract this implements:  MANIFEST + task_cmd(task, model) plus
modes()/build_cmd(mode, task, params) for the three-step gallery, and optional
health()/list_sessions()/classify_line(). Modeled on apps/ppi_btep/app.py.

The pipeline consumes AF3 output folders and QT feature CSVs — there is no
bundled demo dataset, so a run needs data the user points it at (health() and
each mode's hint say so).
"""

import json
import os
import re
import sys
import time
import uuid
from pathlib import Path

# ---------------------------------------------------------------- discovery
# Find the AF3-QuantumTunneling-XGB repo by the scripts it must contain, so any
# clone/rename works. Resolution order: env AF3XGB_DIR -> config.json
# "af3xgb_dir" -> auto-discover beside/under A2S2-OS (by content) -> default
# sibling named "AF3-QuantumTunneling-XGB".
_HERE = Path(__file__).resolve()
_OS_ROOT = _HERE.parents[2]          # apps/af3_qt_xgb/app.py -> A2S2-OS
_MODELS_DIR = _HERE.parents[3]       # the directory that holds the projects
AF3XGB_REPO_URL = "https://github.com/kouroshSA/AF3-QuantumTunneling-XGB"
AF3XGB_DIR_NAME = "AF3-QuantumTunneling-XGB"

STEP1_SCRIPT = "AF3_alphafold_interaction_analyzer.py"
STEP2_SCRIPT = "add_quantum_features_to_af3_qutipwindow.py"
STEP2_SIMULATOR = "compute_qtunneling_qutip_simulation_v4.py"
STEP3_SCRIPT = "af2-3_pair_classifier_w_shap_c.py"
# Two distinctive files that mark an AF3-QuantumTunneling-XGB checkout.
_MARKERS = (STEP3_SCRIPT, STEP2_SIMULATOR)


def _looks_like_af3xgb(d: Path) -> bool:
    try:
        return d.is_dir() and all((d / m).exists() for m in _MARKERS)
    except OSError:
        return False


def _find_af3xgb_dir():
    """Return (path, source). `source` explains how it was found (for health)."""
    def _abs(p):
        p = Path(p).expanduser()
        return p if p.is_absolute() else (_OS_ROOT / p)

    env = os.environ.get("AF3XGB_DIR")
    if env:
        return _abs(env).resolve(), "env AF3XGB_DIR"

    cfg = _OS_ROOT / "config.json"
    if cfg.is_file():
        try:
            pd = (json.loads(cfg.read_text()) or {}).get("af3xgb_dir")
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
        cand = root / AF3XGB_DIR_NAME
        if _looks_like_af3xgb(cand):
            return cand.resolve(), f"clone: {cand.name}"
        try:
            entries = sorted(p for p in root.iterdir() if p.is_dir())
        except OSError:
            continue
        for sib in entries:
            if _looks_like_af3xgb(sib):
                return sib.resolve(), f"found in: {sib.name}"

    return (_MODELS_DIR / AF3XGB_DIR_NAME).resolve(), "default (not found)"


AF3XGB_DIR, AF3XGB_SOURCE = _find_af3xgb_dir()
RUNS_ROOT = AF3XGB_DIR / "a2s2_runs"           # one subdir per OS-launched run


# ---------------------------------------------------------------- env python
def _af3_python():
    """Locate the `af3xgb` conda env's python (xgboost + gemmi + sklearn live
    there). Override with AF3XGB_PYTHON. Otherwise derive the conda base from the
    running interpreter / env vars and look for <base>/envs/af3xgb/bin/python,
    then common home install dirs. Falls back to "python" on PATH. (When the
    MANIFEST declares conda_env="af3xgb" and conda is available, the OS runs via
    `conda run -n af3xgb` and this is just the fallback.)"""
    override = os.environ.get("AF3XGB_PYTHON")
    if override:
        return override

    candidates = []
    p = Path(sys.executable).resolve()
    for anc in p.parents:
        if anc.name == "envs":
            candidates.append(anc / "af3xgb" / "bin" / "python")
            break
    else:
        candidates.append(p.parent.parent / "envs" / "af3xgb" / "bin" / "python")
    for var in ("CONDA_PREFIX_1", "CONDA_ROOT", "CONDA_EXE"):
        v = os.environ.get(var)
        if v:
            base = Path(v)
            base = base.parents[1] if base.name == "conda" else base
            candidates.append(base / "envs" / "af3xgb" / "bin" / "python")
    try:
        home = Path.home()
    except (RuntimeError, OSError):
        home = _MODELS_DIR
    for d in ("anaconda3", "miniconda3", "miniforge3", "mambaforge"):
        candidates.append(home / d / "envs" / "af3xgb" / "bin" / "python")

    for c in candidates:
        try:
            if c and Path(c).is_file():
                return str(c)
        except OSError:
            continue
    return "python"


# ---------------------------------------------------------------- helpers
def _resolve(path):
    """Resolve a user-supplied path: absolute as-is; relative tried against cwd,
    the AF3 repo, then the OS root. Returns the first that exists, else the
    cwd-relative Path (so the script reports a clear 'not found')."""
    s = (path or "").strip()
    if not s:
        return None
    if os.path.isabs(s):
        return Path(s)
    for base in (Path.cwd(), AF3XGB_DIR, _OS_ROOT):
        cand = base / s
        if cand.exists():
            return cand.resolve()
    return (Path.cwd() / s)


def _new_run_dir():
    run_id = time.strftime("%Y-%m-%d_%H-%M-%S") + "_run_" + uuid.uuid4().hex[:8]
    run_dir = RUNS_ROOT / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_id, run_dir


def _write_meta(run_dir, run_id, mode_id, task, extra=None):
    meta = {
        "session_id": run_id,
        "task_label": f"{mode_id}: {task or '(no input)'}",
        "raw_task": (task or "").strip(),
        "mode": mode_id,
        "model": "XGBoost" if mode_id == "classify" else mode_id,
        "interface": "AF3-QT-XGB",
        "started_at_iso": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    if extra:
        meta.update(extra)
    try:
        (run_dir / "metadata.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    except OSError:
        pass


def _pget(params, key, default):
    """Read a param, tolerating blanks/strings from the generic form."""
    v = (params or {}).get(key, default)
    if v is None or (isinstance(v, str) and not v.strip()):
        return default
    return v


def _pint(params, key, default):
    try:
        return str(int(str(_pget(params, key, default)).strip()))
    except (ValueError, TypeError):
        return str(default)


def _pbool(params, key):
    v = (params or {}).get(key, False)
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "yes", "on")
    return bool(v)


# ---------------------------------------------------------------- run command
def build_cmd(mode, task, params):
    """(argv, cwd, env) for one pipeline step. cwd is a fresh per-run directory
    under a2s2_runs/ so outputs stay isolated; the scripts are referenced by
    absolute path so cwd can be the run dir. Returns argv the OS wraps in
    `conda run -n af3xgb` (argv[0] is a bare `python` when the env is present)."""
    mode = mode or "task"
    params = params or {}
    task = (task or "").strip()
    run_id, run_dir = _new_run_dir()
    py = _af3_python()
    env = {**os.environ, "PYTHONUNBUFFERED": "1"}

    # ---- Step 3: XGBoost PPI classifier -------------------------------------
    if mode == "classify":
        pred = _resolve(task)
        out_dir = run_dir / "classify"
        argv = [py, str(AF3XGB_DIR / STEP3_SCRIPT)]
        if pred:
            argv += ["--pred_csv", str(pred)]
            # Best-effort: find PRS/RRS QT feature tables beside the pred CSV.
            d = pred.parent if pred.parent.exists() else AF3XGB_DIR
            prs = next((p for p in sorted(d.glob("*PRS*.csv"))), None)
            rrs = next((p for p in sorted(d.glob("*RRS*.csv"))), None)
            if prs:
                argv += ["--prs_csv", str(prs)]
            if rrs:
                argv += ["--rrs_csv", str(rrs)]
        argv += ["--output_dir", str(out_dir),
                 "--feature_mode", str(_pget(params, "feature_mode", "intersection"))]
        if _pbool(params, "shap"):
            argv += ["--shap"]
        _write_meta(run_dir, run_id, mode, task,
                    {"output_dir": str(out_dir), "pred_csv": str(pred) if pred else None})
        return argv, str(run_dir), env

    # ---- Step 2: quantum-tunneling features ---------------------------------
    if mode == "qtfeatures":
        folder = _resolve(task)
        # The AF3 summary CSV that step 1 emits for this folder.
        af3_csv = None
        if folder and folder.is_dir():
            hit = next((p for p in sorted(folder.glob("AF3_interaction_summary_*.csv"))), None)
            af3_csv = hit or (folder / f"AF3_interaction_summary_{folder.name}.csv")
        out_csv = run_dir / "qt_features.csv"
        argv = [py, str(AF3XGB_DIR / STEP2_SCRIPT),
                "--input-folder", str(folder) if folder else task,
                "--af3-csv", str(af3_csv) if af3_csv else "",
                "--output-csv", str(out_csv),
                "--scripts-dir", str(AF3XGB_DIR),
                "--simulator", str(AF3XGB_DIR / STEP2_SIMULATOR),
                "--qutip-dimension", str(_pget(params, "dimension", "2D")),
                "--window-size", _pint(params, "window_size", 10),
                "--max-pairs", _pint(params, "max_pairs", 10),
                "--backend", str(_pget(params, "backend", "numpy")),
                "--delta-zz", str(_pget(params, "delta_zz", 0.8))]
        if _pbool(params, "use_charges"):
            argv += ["--use-charges"]
        _write_meta(run_dir, run_id, mode, task,
                    {"output_csv": str(out_csv), "af3_csv": str(af3_csv) if af3_csv else None})
        return argv, str(run_dir), env

    # ---- Step 1 (default "task"/"summary"): AF3 interaction summary ----------
    folder = _resolve(task)
    argv = [py, str(AF3XGB_DIR / STEP1_SCRIPT), str(folder) if folder else task]
    _write_meta(run_dir, run_id, "summary", task, {"af3_folder": str(folder) if folder else task})
    return argv, str(run_dir), env


def task_cmd(task, model):
    """Legacy single-task entry (home Ask box) — runs step 1 (the AF3 summary)."""
    return build_cmd("task", task, {})


# ---------------------------------------------------------------- modes
MODES = [
    {
        "id": "task", "name": "AF3 interaction summary",
        "tagline": "Walk an AF3 output folder -> per-interaction confidence CSV",
        "desc": "Step 1 of the pipeline. Point it at an AlphaFold3 multimer "
                "output folder; it emits one row per interaction with ipTM, pTM, "
                "pLDDT and chain info (AF3_interaction_summary_<folder>.csv), the "
                "table every later step reads.",
        "accent": "#4C8DFF", "gallery": False, "needs_task": True,
        "task_hint": "path to an AlphaFold3 output folder",
        "params": [],
    },
    {
        "id": "qtfeatures", "name": "Quantum-tunneling features",
        "tagline": "Add ~35 QT feature columns via a sliding-window qubit sim",
        "desc": "Step 2. For each interaction in the AF3 summary, run the "
                "charge-aware sliding-window qubit simulation and merge the "
                "resulting QT_* columns. Give the same AF3 output folder as step "
                "1 (its AF3_interaction_summary_*.csv is picked up automatically).",
        "accent": "#46C26A", "gallery": True, "needs_task": True,
        "task_hint": "path to the AF3 output folder (with its summary CSV)",
        "params": [
            {"id": "window_size", "label": "Window size", "type": "int",
             "default": 10, "min": 2, "max": 30},
            {"id": "max_pairs", "label": "Max pairs", "type": "int",
             "default": 10, "min": 1, "max": 50},
            {"id": "dimension", "label": "Hamiltonian", "type": "choice",
             "choices": ["1D", "2D", "3D"], "default": "3D"},
            {"id": "backend", "label": "Backend", "type": "choice",
             "choices": ["numpy", "cupy", "qutip"], "default": "numpy"},
            {"id": "use_charges", "label": "Charge-aware", "type": "bool", "default": True},
        ],
    },
    {
        "id": "classify", "name": "XGBoost PPI classifier",
        "tagline": "Cross-validated XGBoost (+SHAP) over QT feature tables",
        "desc": "Step 3. Train a calibrated XGBoost classifier on labeled PRS "
                "(interacting) and RRS (random) QT feature tables and score an "
                "unlabeled prediction set. Give the prediction CSV as the task; "
                "place its *PRS*.csv and *RRS*.csv training tables in the same "
                "folder and they're wired in automatically.",
        "accent": "#F2B23E", "gallery": True, "needs_task": True,
        "task_hint": "path to the prediction QT-feature CSV",
        "params": [
            {"id": "feature_mode", "label": "Features", "type": "choice",
             "choices": ["intersection", "union"], "default": "intersection"},
            {"id": "shap", "label": "SHAP attributions", "type": "bool", "default": False},
        ],
    },
]


def modes():
    return MODES


# ---------------------------------------------------------------- log -> state
_OK_HINTS = re.compile(r"(saved|wrote|written|summary\.md|model\.joblib|done|complete|auroc|f1)", re.I)
_ERR_HINTS = re.compile(r"(error|traceback|exception|not found|failed|no such|out of memory)", re.I)
_PLAN_HINTS = re.compile(r"(loading|reading|parsing|window|interface|simulat|training|fold|shap)", re.I)
_RUN_RE = re.compile(r"a2s2_runs[/\\]([0-9A-Za-z_\-]+_run_[0-9A-Za-z]+)")


def classify_line(line):
    """Map one stdout line to {k, session_dir} (keys optional)."""
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
    """Saved AF3-QT-XGB runs, newest first (cap 80), in the shell's row shape."""
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
        # A run produced output if it wrote any CSV / classify dir under the run.
        try:
            produced = any(p.suffix == ".csv" for p in d.rglob("*") if p.is_file())
        except OSError:
            produced = False
        rec["outcome"] = "success" if produced else "pending"
        try:
            rec["files"] = sorted(p.name for p in d.iterdir() if p.is_file())[:6]
        except OSError:
            rec["files"] = []
        if not rec["time"]:
            rec["time"] = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(d.stat().st_mtime))
        out.append(rec)
    return out


# ---------------------------------------------------------------- health
def health():
    repo_found = AF3XGB_DIR.is_dir() and (AF3XGB_DIR / STEP3_SCRIPT).is_file()
    py = _af3_python()
    py_ok = (py != "python") and Path(py).is_file()
    return {
        # "ready" means the tooling is installed. Runs still need input data
        # (AF3 output folders / QT feature CSVs) — there is no bundled sample.
        "ready": bool(repo_found and py_ok),
        "repo": str(AF3XGB_DIR),
        "llm_dir": str(AF3XGB_DIR),
        "llm_dir_found": repo_found,
        "twin_source": AF3XGB_SOURCE,
        "twin_repo_url": AF3XGB_REPO_URL,
        "af3_python": py,
        "af3_python_found": py_ok,
        "env_hint": (None if py_ok else
                     "Create the af3xgb conda env: conda create -n af3xgb python=3.11 "
                     "&& conda run -n af3xgb pip install numpy pandas scikit-learn "
                     "xgboost matplotlib joblib gemmi (shap/cupy/qutip optional)."),
        "data_hint": "No bundled sample — point a mode at an AF3 output folder "
                     "or QT feature CSVs.",
        # local, classical ML: no API key needed.
        "api_key_present": True,
    }


# ---------------------------------------------------------------- manifest
MANIFEST = {
    "id": "af3_qt_xgb",
    "name": "AF3-QT-XGB",
    "tagline": "AlphaFold3 -> quantum-tunneling features -> XGBoost PPI classifier",
    "summary": "A three-step pipeline: build an AF3 interaction summary, add "
               "charge-aware quantum-tunneling features from a sliding-window "
               "qubit simulation, then classify PPIs with a SHAP-explained "
               "XGBoost model.",
    "accent": "#4C8DFF",
    "models": ["XGBoost"],
    # Runs under `conda run -n af3xgb` (xgboost + gemmi + sklearn). _af3_python()
    # is the fallback when conda is unavailable.
    "conda_env": "af3xgb",
    "vocabulary": "structural & computational biology (AlphaFold3 multimers, "
                  "interface contacts, quantum tunneling, qubit simulation, "
                  "XGBoost, SHAP, PRS/RRS, protein-protein interactions)",
    # Per-app wording for the shell's single-task "Ask" surface (step 1).
    "ask_title": "AF3 interaction summary",
    "ask_blurb": "Point at an AlphaFold3 output folder to build the "
                 "per-interaction confidence table — step 1 of the pipeline. "
                 "Then use Run modes for quantum-tunneling features and the "
                 "XGBoost classifier.",
    "ask_placeholder": "path to an AlphaFold3 output folder",
    "ask_button": "Build summary",
}
