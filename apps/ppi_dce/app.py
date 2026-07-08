#!/usr/bin/env python3
"""
A2S2-OS app: ppiDCE (Dual Cross-Encoder protein-pair interaction classifier).

A sibling of ppiBTEP and ppiGPLM on the OS app-registry seam (README "Adding an
app"). ppiDCE scores protein-protein interactions with a dual cross-encoder over
ESM-1b embeddings: a pair of sequences goes in, an interaction probability comes
out. Batch inference is driven by `inference_ppiDCE.py` in the model repo.

The OS contract:  MANIFEST + task_cmd(task, model) -> (argv, cwd, env), plus
health()/library()/list_sessions()/classify_line(). Modeled directly on
apps/ppi_btep/app.py — the two apps are near-identical (a seq1,seq2 CSV + an
ESM-1b checkpoint), differing only in the checkpoint, the head architecture, and
the inference script's CLI (ppiDCE has no --num_layers).
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
_OS_ROOT = _HERE.parents[2]          # apps/ppi_dce/app.py -> A2S2-OS
_MODELS_DIR = _HERE.parents[3]       # the directory that holds the projects
PPIDCE_REPO_URL = "https://github.com/kouroshSA/ppiDCE"
PPIDCE_DIR_NAME = "ppiDCE"
INFER_SCRIPT = "inference_ppiDCE.py"
# Files that uniquely mark a ppiDCE checkout.
_MARKERS = (INFER_SCRIPT, "train_ppiDCE.py")


def _looks_like_ppidce(d: Path) -> bool:
    try:
        return d.is_dir() and all((d / m).exists() for m in _MARKERS)
    except OSError:
        return False


def _find_ppidce_dir():
    """Return (path, source). `source` explains how it was found (for health).
    Resolution: env PPIDCE_DIR -> config.json "ppidce_dir" -> auto-discover
    beside/under A2S2-OS (by content) -> default sibling named "ppiDCE"."""
    def _abs(p):
        p = Path(p).expanduser()
        return p if p.is_absolute() else (_OS_ROOT / p)

    env = os.environ.get("PPIDCE_DIR")
    if env:
        return _abs(env).resolve(), "env PPIDCE_DIR"

    cfg = _OS_ROOT / "config.json"
    if cfg.is_file():
        try:
            pd = (json.loads(cfg.read_text()) or {}).get("ppidce_dir")
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
        cand = root / PPIDCE_DIR_NAME
        if _looks_like_ppidce(cand):
            return cand.resolve(), f"clone: {cand.name}"
        try:
            entries = sorted(p for p in root.iterdir() if p.is_dir())
        except OSError:
            continue
        for sib in entries:
            if _looks_like_ppidce(sib):
                return sib.resolve(), f"found in: {sib.name}"

    return (_MODELS_DIR / PPIDCE_DIR_NAME).resolve(), "default (not found)"


PPIDCE_DIR, PPIDCE_SOURCE = _find_ppidce_dir()
RUNS_ROOT = PPIDCE_DIR / "a2s2_runs"           # one subdir per OS-launched run
SAMPLE_CSV = PPIDCE_DIR / "MED4_PRS_100.csv"   # shipped MED4 positive-reference pairs
CKPT_REL = "checkpoints/ppiDCE_epoch8.pth"
MODEL_CONFIG = "facebook/esm1b_t33_650M_UR50S"


# ---------------------------------------------------------------- env python
def _ppidce_python():
    """Locate the `ppiDCE` conda env's python (torch + transformers/ESM-1b live
    there). Override with PPIDCE_PYTHON. Falls back to "python" on PATH (the OS
    then runs via `conda run -n ppiDCE`)."""
    override = os.environ.get("PPIDCE_PYTHON")
    if override:
        return override

    candidates = []
    p = Path(sys.executable).resolve()
    for anc in p.parents:
        if anc.name == "envs":
            candidates.append(anc / "ppiDCE" / "bin" / "python")
            break
    else:
        candidates.append(p.parent.parent / "envs" / "ppiDCE" / "bin" / "python")
    for var in ("CONDA_PREFIX_1", "CONDA_ROOT", "CONDA_EXE"):
        v = os.environ.get(var)
        if v:
            base = Path(v)
            base = base.parents[1] if base.name == "conda" else base
            candidates.append(base / "envs" / "ppiDCE" / "bin" / "python")
    try:
        home = Path.home()
    except (RuntimeError, OSError):
        home = _MODELS_DIR
    for d in ("anaconda3", "miniconda3", "miniforge3", "mambaforge"):
        candidates.append(home / d / "envs" / "ppiDCE" / "bin" / "python")

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
    (input_path, n_pairs, label).

    inference_ppiDCE.py reads the CSV with pandas' default header row and takes
    the two sequence columns positionally, so the shipped MED4_PRS_100.csv is
    headerless-style (row 0 is consumed as the header). To yield N scored pairs
    for demo:N we therefore keep the first N+1 raw lines.

      * demo / demo:N / demo:all -> first N data pairs of the shipped MED4
        positive-reference sample (N defaults to 5; 'all' uses the whole file).
      * an existing file path     -> used directly.
      * anything else             -> not a valid pair spec; fall back to demo:5.
    """
    task = (task or "").strip()
    m = _DEMO_RE.match(task) if task else None

    # explicit file path first (absolute, or relative to repo / OS root / cwd)
    if task and not m:
        for base in (Path.cwd(), PPIDCE_DIR, _OS_ROOT):
            cand = (base / task) if not os.path.isabs(task) else Path(task)
            if cand.is_file():
                try:
                    n = max(0, sum(1 for ln in cand.open("r", encoding="utf-8") if ln.strip()) - 1)
                except OSError:
                    n = 0
                return cand.resolve(), n, f"file: {cand.name}"
            if os.path.isabs(task):
                break
        # not a file -> demo default below (free text isn't a valid pair spec)

    n = 5
    if m and m.group(2):
        n = None if m.group(2).lower() == "all" else int(m.group(2))
    dst = run_dir / "input.csv"
    if SAMPLE_CSV.is_file():
        try:
            lines = [ln.rstrip("\n") for ln in SAMPLE_CSV.open("r", encoding="utf-8") if ln.strip()]
        except OSError:
            lines = []
        if not lines:
            dst.write_text("", encoding="utf-8")
            return dst, 0, "demo (sample empty)"
        # row 0 is treated as the header; keep N+1 lines to score N pairs.
        kept = lines if n is None else lines[: n + 1]
        dst.write_text("\n".join(kept) + "\n", encoding="utf-8")
        n_pairs = max(0, len(kept) - 1)
        label = f"MED4 positive-reference sample ({'all' if n is None else n_pairs} pairs)"
        return dst, n_pairs, label
    dst.write_text("", encoding="utf-8")
    return dst, 0, "demo (sample file missing)"


def task_cmd(task, model):
    """Build the command to run one ppiDCE inference job. Returns (argv, cwd,
    env). Runs under the `ppiDCE` env's python with cwd = the ppiDCE repo.
    Outputs + metadata.json land in a fresh a2s2_runs/<run_id>/."""
    run_id = time.strftime("%Y-%m-%d_%H-%M-%S") + "_run_" + uuid.uuid4().hex[:8]
    run_dir = RUNS_ROOT / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    input_path, n_pairs, label = _prep_input(task, run_dir)
    out_csv = run_dir / "predictions.csv"

    meta = {
        "session_id": run_id,
        "task_label": label,
        "raw_task": (task or "").strip(),
        "model": model or "ppiDCE",
        "checkpoint": CKPT_REL,
        "interface": "ppiDCE",
        "n_pairs": n_pairs,
        "input_file": str(input_path),
        "started_at_iso": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "output_file": str(out_csv),
    }
    try:
        (run_dir / "metadata.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    except OSError:
        pass

    py = _ppidce_python()
    argv = [
        py, INFER_SCRIPT,
        "--model_path", CKPT_REL,
        "--model_config", MODEL_CONFIG,
        "--input_file", str(input_path),
        "--output_file", str(out_csv),
        "--batch_size", "4",
        "--max_length", "1024",
        "--device", os.environ.get("PPIDCE_DEVICE", "cuda"),
    ]
    env = {**os.environ, "PYTHONUNBUFFERED": "1"}
    return argv, str(PPIDCE_DIR), env


# ---------------------------------------------------------------- log -> state
_OK_HINTS = re.compile(r"(saved|prediction|probability|wrote|done|interact)", re.I)
_ERR_HINTS = re.compile(r"(error|traceback|exception|not found|failed|cuda out of memory)", re.I)
_PLAN_HINTS = re.compile(r"(loading|checkpoint|tokeniz|encoding|device|embedding|esm|model_config)", re.I)
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
    """Saved ppiDCE runs, newest first (cap 80), in the shell's row shape."""
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
     "desc": "Score the first 5 MED4 positive-reference protein pairs shipped with ppiDCE."},
    {"color": "blue", "title": "Sample: 20 MED4 pairs", "task": "demo:20",
     "desc": "Score the first 20 MED4 positive-reference protein pairs."},
    {"color": "amber", "title": "Full MED4 positive set", "task": "demo:all",
     "desc": "Score the entire shipped MED4 positive-reference pair set (slower)."},
    {"color": "green", "title": "Custom pairs file", "task": "MED4_PRS_100.csv",
     "desc": "Run inference on a seq1,seq2 CSV by path (relative to the ppiDCE repo or absolute)."},
]


def library():
    return LIBRARY


# ---------------------------------------------------------------- health
def health():
    repo_found = PPIDCE_DIR.is_dir() and (PPIDCE_DIR / INFER_SCRIPT).is_file()
    ckpt_ok = (PPIDCE_DIR / CKPT_REL).is_file()
    py = _ppidce_python()
    py_ok = (py != "python") and Path(py).is_file()
    return {
        # checkpoint (913 MB) ships from Hugging Face; without it the model can't run.
        "ready": bool(repo_found and ckpt_ok and py_ok),
        "repo": str(PPIDCE_DIR),
        "llm_dir": str(PPIDCE_DIR),
        "llm_dir_found": repo_found,
        "twin_source": PPIDCE_SOURCE,
        "twin_repo_url": PPIDCE_REPO_URL,
        "infer_script": INFER_SCRIPT,
        "ppidce_python": py,
        "ppidce_python_found": py_ok,
        "checkpoint_found": ckpt_ok,
        "checkpoint_hint": (None if ckpt_ok else
                            "Fetch checkpoints/ppiDCE_epoch8.pth from Hugging Face "
                            "(kouroshSA/ppiDCE) — see its README."),
        "sample_found": SAMPLE_CSV.is_file(),
        # local model: no API key needed.
        "api_key_present": True,
    }


# ---------------------------------------------------------------- manifest
MANIFEST = {
    "id": "ppi_dce",
    "name": "ppiDCE",
    "tagline": "ppiDCE — dual cross-encoder protein-pair interaction classifier",
    "summary": "Score protein-protein interactions with a dual cross-encoder over "
               "ESM-1b embeddings — paste a seq1,seq2 CSV or run the MED4 sample.",
    "accent": "#3E9AC1",
    "models": ["ppiDCE"],
    # Run inference via `conda run -n ppiDCE` (torch + transformers). _ppidce_python()
    # is the fallback when conda is unavailable.
    "conda_env": "ppiDCE",
    "vocabulary": "molecular biology (proteins, protein pairs, interactions, MED4 "
                  "interactome, ESM-1b, dual cross-encoder, interaction probability)",
    # Per-app wording for the shell's single-task "Ask" surface.
    "ask_title": "Score protein pairs",
    "ask_blurb": "Score protein-protein interactions with ppiDCE (a dual "
                 "cross-encoder over ESM-1b). Type “demo” (or demo:N / "
                 "demo:all) for the shipped MED4 sample, give a seq1,seq2 CSV "
                 "path, or paste one.",
    "ask_placeholder": "e.g. demo:5  ·  demo:all  ·  a seq1,seq2 .csv path",
    "ask_button": "Run ppiDCE",
}
