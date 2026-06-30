#!/usr/bin/env python3
"""
A2S2-OS app: ppiBTEP (Siamese protein-protein interaction classifier).

A THIRD app hosted by the A2S2-OS shell, beside the xArm6 Bench Console and
ppiGPLM (see README -> "Adding an app"). It owns everything specific to ppiBTEP:
where the model repo lives, which Python runs it (the `esm` conda env: torch +
CUDA), how a free-text "task" becomes an input prompts CSV, and how the
inference script's stdout maps to run-log state.

ppiBTEP is a Siamese / twin-branch classifier (ESM-1b architecture, trained from
scratch). Each protein is encoded independently; the two [CLS] embeddings are
concatenated and a linear head emits a binary interaction prediction. Batch
inference is driven by `inference_ppiBTPE_2GPU.py` in the model repo.

The OS contract this implements:  MANIFEST + task_cmd(task, model) -> (argv,
cwd, env), plus optional health()/library()/list_sessions()/classify_line().
Modeled on apps/ppi_gplm/app.py.
"""

import json
import os
import re
import sys
import time
import uuid
from pathlib import Path

# ---------------------------------------------------------------- discovery
# Find the ppiBTEP repo by the files it must contain, so any clone/rename works.
# Resolution order: env PPIBTEP_DIR -> config.json "ppibtep_dir" -> auto-discover
# beside/under A2S2-OS (by content) -> default sibling named "ppiBTEP".
_HERE = Path(__file__).resolve()
_OS_ROOT = _HERE.parents[2]          # apps/ppi_btep/app.py -> A2S2-OS
_MODELS_DIR = _HERE.parents[3]       # the directory that holds the projects
PPIBTEP_REPO_URL = "https://github.com/kouroshSA/ppiBTEP"
PPIBTEP_DIR_NAME = "ppiBTEP"
INFER_SCRIPT = "inference_ppiBTPE_2GPU.py"
# Files that uniquely mark a ppiBTEP checkout.
_MARKERS = (INFER_SCRIPT, "train_ppiBTPE3b.py")


def _looks_like_ppibtep(d: Path) -> bool:
    try:
        return d.is_dir() and all((d / m).exists() for m in _MARKERS)
    except OSError:
        return False


def _find_ppibtep_dir():
    """Return (path, source). `source` explains how it was found (for health)."""
    def _abs(p):
        p = Path(p).expanduser()
        return p if p.is_absolute() else (_OS_ROOT / p)

    env = os.environ.get("PPIBTEP_DIR")
    if env:
        return _abs(env).resolve(), "env PPIBTEP_DIR"

    cfg = _OS_ROOT / "config.json"
    if cfg.is_file():
        try:
            pd = (json.loads(cfg.read_text()) or {}).get("ppibtep_dir")
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
        cand = root / PPIBTEP_DIR_NAME
        if _looks_like_ppibtep(cand):
            return cand.resolve(), f"clone: {cand.name}"
        try:
            entries = sorted(p for p in root.iterdir() if p.is_dir())
        except OSError:
            continue
        for sib in entries:
            if _looks_like_ppibtep(sib):
                return sib.resolve(), f"found in: {sib.name}"

    return (_MODELS_DIR / PPIBTEP_DIR_NAME).resolve(), "default (not found)"


PPIBTEP_DIR, PPIBTEP_SOURCE = _find_ppibtep_dir()
RUNS_ROOT = PPIBTEP_DIR / "a2s2_runs"          # one subdir per OS-launched run
SAMPLE_CSV = PPIBTEP_DIR / "MED4-PPIs-low-confidence_ppiTEPM_prompts.csv"
# Released MED4 checkpoint (12-layer). Fetched from Hugging Face; may be absent
# until the user downloads it (health() flags this).
CKPT_REL = "checkpoints/ppiBTPE_epoch_4.pth"
MODEL_CONFIG = "facebook/esm1b_t33_650M_UR50S"
NUM_LAYERS = "12"


# ---------------------------------------------------------------- esm env python
def _esm_python():
    """Locate the `esm` conda env's python (ppiBTEP needs torch+CUDA there).

    Override with PPIBTEP_PYTHON. Otherwise derive the conda base from the
    running interpreter / env vars and look for <base>/envs/esm/bin/python, then
    common home install dirs. Falls back to "python" on PATH. (When the MANIFEST
    declares conda_env="esm" and conda is available, the OS runs via `conda run
    -n esm` and this is just the fallback.)
    """
    override = os.environ.get("PPIBTEP_PYTHON")
    if override:
        return override

    candidates = []
    p = Path(sys.executable).resolve()
    for anc in p.parents:
        if anc.name == "envs":
            candidates.append(anc / "esm" / "bin" / "python")
            break
    else:
        candidates.append(p.parent.parent / "envs" / "esm" / "bin" / "python")
    for var in ("CONDA_PREFIX_1", "CONDA_ROOT", "CONDA_EXE"):
        v = os.environ.get(var)
        if v:
            base = Path(v)
            base = base.parents[1] if base.name == "conda" else base
            candidates.append(base / "envs" / "esm" / "bin" / "python")
    try:
        home = Path.home()
    except (RuntimeError, OSError):
        home = _MODELS_DIR
    for d in ("anaconda3", "miniconda3", "miniforge3", "mambaforge"):
        candidates.append(home / d / "envs" / "esm" / "bin" / "python")

    for c in candidates:
        try:
            if c and Path(c).is_file():
                return str(c)
        except OSError:
            continue
    return "python"


# ---------------------------------------------------------------- run command
_DEMO_RE = re.compile(r"^\s*(demo|sample|example|test)\s*(?::\s*(\d+|all))?\s*$", re.I)


def _prep_input(task, run_dir):
    """Turn the free-text `task` into an input CSV (seq1,seq2). Returns
    (input_path, n_pairs, label). Modes mirror ppiGPLM:

      * demo / demo:N / demo:all -> header + first N rows of the shipped MED4
        sample (N defaults to 5; 'all' uses the whole file).
      * an existing file path     -> used directly.
      * anything else             -> not a valid pair spec; fall back to demo:5.
    """
    task = (task or "").strip()
    m = _DEMO_RE.match(task) if task else None

    # explicit file path first (absolute, or relative to repo / OS root / cwd)
    if task and not m:
        for base in (Path.cwd(), PPIBTEP_DIR, _OS_ROOT):
            cand = (base / task) if not os.path.isabs(task) else Path(task)
            if cand.is_file():
                try:
                    n = max(0, sum(1 for ln in cand.open("r", encoding="utf-8") if ln.strip()) - 1)
                except OSError:
                    n = 0
                return cand.resolve(), n, f"file: {cand.name}"
            if os.path.isabs(task):
                break
        # not a file -> treat as demo default below (a free-text "task" isn't a
        # valid protein-pair spec for this model)

    n = 5
    if m and m.group(2):
        n = None if m.group(2).lower() == "all" else int(m.group(2))
    dst = run_dir / "input.csv"
    if SAMPLE_CSV.is_file():
        rows = []
        with SAMPLE_CSV.open("r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    rows.append(line.rstrip("\n"))
        if not rows:
            dst.write_text("seq1,seq2\n", encoding="utf-8")
            return dst, 0, "demo (sample empty)"
        header, data = rows[0], rows[1:]
        if n is not None:
            data = data[:n]
        dst.write_text("\n".join([header] + data) + "\n", encoding="utf-8")
        label = f"MED4 low-confidence sample ({'all' if n is None else len(data)} pairs)"
        return dst, len(data), label
    dst.write_text("seq1,seq2\n", encoding="utf-8")
    return dst, 0, "demo (sample file missing)"


def task_cmd(task, model):
    """Build the command to run one ppiBTEP inference job. Returns (argv, cwd,
    env). Runs under the `esm` env's python with cwd = the ppiBTEP repo. Outputs
    + metadata.json land in a fresh a2s2_runs/<run_id>/ for list_sessions."""
    run_id = time.strftime("%Y-%m-%d_%H-%M-%S") + "_run_" + uuid.uuid4().hex[:8]
    run_dir = RUNS_ROOT / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    input_path, n_pairs, label = _prep_input(task, run_dir)
    out_csv = run_dir / "predictions.csv"

    meta = {
        "session_id": run_id,
        "task_label": label,
        "raw_task": (task or "").strip(),
        "model": model or "ppiBTEP",
        "checkpoint": CKPT_REL,
        "interface": "ppiBTEP",
        "n_pairs": n_pairs,
        "input_file": str(input_path),
        "started_at_iso": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "output_file": str(out_csv),
    }
    try:
        (run_dir / "metadata.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    except OSError:
        pass

    py = _esm_python()
    argv = [
        py, INFER_SCRIPT,
        "--model_path", CKPT_REL,
        "--model_config", MODEL_CONFIG,
        "--num_layers", NUM_LAYERS,
        "--input_file", str(input_path),
        "--output_file", str(out_csv),
        "--batch_size", "4",
        "--max_length", "1024",
        "--device", os.environ.get("PPIBTEP_DEVICE", "cuda"),
    ]
    env = {**os.environ, "PYTHONUNBUFFERED": "1"}
    return argv, str(PPIBTEP_DIR), env


# ---------------------------------------------------------------- log -> state
_OK_HINTS = re.compile(r"(saved|prediction|probability|friends|enemies|wrote)", re.I)
_ERR_HINTS = re.compile(r"(error|traceback|exception|not found|failed|cuda out of memory)", re.I)
_PLAN_HINTS = re.compile(r"(loading|checkpoint|tokeniz|encoding|device|model_config)", re.I)
_RUN_RE = re.compile(r"a2s2_runs[/\\]([0-9A-Za-z_\-]+_run_[0-9A-Za-z]+)")


def classify_line(line):
    """Map one inference stdout line to {k, session_dir} (keys optional)."""
    out = {}
    low = line.lower()
    if "futurewarning" in low or "weights_only" in low or "torch.load" in low:
        out["k"] = ""
    elif _ERR_HINTS.search(low):
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
    """Saved ppiBTEP runs, newest first (cap 80), in the shell's row shape."""
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
                rec["model"] = m.get("model") or m.get("checkpoint")
                rec["time"] = m.get("started_at_iso")
            except (ValueError, OSError):
                pass
        rec["outcome"] = "success" if (d / "predictions.csv").is_file() else "pending"
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
    {"color": "green", "title": "Sample: 5 MED4 pairs", "task": "demo:5",
     "desc": "Classify the first 5 low-confidence MED4 protein pairs shipped with ppiBTEP."},
    {"color": "blue", "title": "Sample: 20 MED4 pairs", "task": "demo:20",
     "desc": "Classify the first 20 low-confidence MED4 protein pairs."},
    {"color": "amber", "title": "Full MED4 low-confidence set", "task": "demo:all",
     "desc": "Score the entire shipped MED4 low-confidence pair set (slower)."},
    {"color": "green", "title": "Custom pairs file", "task": "MED4-PPIs-low-confidence_ppiTEPM_prompts.csv",
     "desc": "Run inference on a seq1,seq2 CSV by path (relative to the ppiBTEP repo or absolute)."},
]


def library():
    return LIBRARY


# ---------------------------------------------------------------- health
def health():
    repo_found = PPIBTEP_DIR.is_dir() and (PPIBTEP_DIR / INFER_SCRIPT).is_file()
    ckpt_ok = (PPIBTEP_DIR / CKPT_REL).is_file()
    py = _esm_python()
    py_ok = (py != "python") and Path(py).is_file()
    return {
        # checkpoint ships from Hugging Face; without it the model can't run.
        "ready": bool(repo_found and ckpt_ok and py_ok),
        "repo": str(PPIBTEP_DIR),
        "llm_dir": str(PPIBTEP_DIR),
        "llm_dir_found": repo_found,
        "twin_source": PPIBTEP_SOURCE,
        "twin_repo_url": PPIBTEP_REPO_URL,
        "infer_script": INFER_SCRIPT,
        "esm_python": py,
        "esm_python_found": py_ok,
        "checkpoint_found": ckpt_ok,
        "checkpoint_hint": (None if ckpt_ok else
                            "Fetch checkpoints/ppiBTPE_epoch_4.pth from Hugging Face "
                            "(repo kouroshSA/ppiBTEP) — see its README."),
        "sample_found": SAMPLE_CSV.is_file(),
        # local model: no API key needed.
        "api_key_present": True,
    }


# ---------------------------------------------------------------- manifest
MANIFEST = {
    "id": "ppi_btep",
    "name": "ppiBTEP",
    "tagline": "ppiBTEP — Siamese protein-pair interaction classifier",
    "summary": "Score protein-protein interactions with a Siamese ESM-1b-architecture "
               "model — paste a seq1,seq2 CSV or run the MED4 sample.",
    "accent": "#9A6BFF",
    "models": ["ppiBTEP"],
    # Run inference via `conda run -n esm` (torch + CUDA). _esm_python() is the
    # fallback when conda is unavailable.
    "conda_env": "esm",
    "vocabulary": "molecular biology (proteins, protein pairs, interactions, MED4 "
                  "interactome, Siamese encoder, interaction probability)",
}
