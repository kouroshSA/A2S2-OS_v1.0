#!/usr/bin/env python3
"""
A2S2-OS app: ppiGPLM (protein-protein interaction classifier).

This is a SECOND app hosted by the A2S2-OS shell, dropped in beside the xArm6
"Bench Console" without touching server.py (see README -> "Adding an app"). It
owns everything specific to the ppiGPLM model: where the model project lives,
which Python runs it, how a free-text "task" becomes a batch of prompts, and how
the inference script's stdout maps to run-log state.

ppiGPLM is a GPT-2-small protein language model that frames binary PPI
prediction as next-token prediction: given a structured prompt encoding a
protein pair, it emits the softmax probability that the next token is "1"
(interacting) vs "0" (not). Batch inference is driven by
`sample_fasta3.3_softmax_error_handling*.py` in the model repo.

Cross-environment note: the OS server runs in the twin's `xarm6sim` conda env,
but ppiGPLM needs its own `gpt` env (torch + CUDA). So unlike the Bench Console
(which reuses `sys.executable`), this app resolves the `gpt` env's interpreter
explicitly and runs the inference script as a subprocess under it.

The OS contract this implements:  MANIFEST + task_cmd(task, model) -> (argv,
cwd, env), plus optional health()/library()/list_sessions()/classify_line().
"""

import glob
import json
import os
import re
import sys
import time
import uuid
from pathlib import Path

# ---------------------------------------------------------------- discovery
# Like the Bench Console finds the twin by content, we find the ppiGPLM repo by
# the files it must contain -- so any clone/rename works. Resolution order:
#   1) env PPIGPLM_DIR              -- explicit, wins over everything
#   2) config.json -> "ppigplm_dir" -- persistent, set once per install
#   3) auto-discover beside/under A2S2-OS (identified by content, not name)
#   4) default: sibling folder named "ppiGPLM"
_HERE = Path(__file__).resolve()
_OS_ROOT = _HERE.parents[2]          # apps/ppi_gplm/app.py -> A2S2-OS
_MODELS_DIR = _HERE.parents[3]       # the directory that holds both projects
PPIGPLM_REPO_URL = "https://github.com/kouroshSA/ppiGPLM"
PPIGPLM_DIR_NAME = "ppiGPLM"
# Files that uniquely mark a ppiGPLM checkout.
_MARKERS = ("model.py", "configurator.py", "data/MED4_char/meta.pkl")


def _looks_like_ppigplm(d: Path) -> bool:
    try:
        return d.is_dir() and all((d / m).exists() for m in _MARKERS)
    except OSError:
        return False


def _find_ppigplm_dir():
    """Return (path, source). `source` explains how it was found (for health)."""
    def _abs(p):
        p = Path(p).expanduser()
        return p if p.is_absolute() else (_OS_ROOT / p)

    env = os.environ.get("PPIGPLM_DIR")
    if env:
        return _abs(env).resolve(), "env PPIGPLM_DIR"

    cfg = _OS_ROOT / "config.json"
    if cfg.is_file():
        try:
            pd = (json.loads(cfg.read_text()) or {}).get("ppigplm_dir")
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
        # prefer the canonical folder name when it qualifies
        cand = root / PPIGPLM_DIR_NAME
        if _looks_like_ppigplm(cand):
            return cand.resolve(), f"clone: {cand.name}"
        try:
            entries = sorted(p for p in root.iterdir() if p.is_dir())
        except OSError:
            continue
        for sib in entries:
            if _looks_like_ppigplm(sib):
                return sib.resolve(), f"found in: {sib.name}"

    return (_MODELS_DIR / PPIGPLM_DIR_NAME).resolve(), "default (not found)"


PPIGPLM_DIR, PPIGPLM_SOURCE = _find_ppigplm_dir()
RUNS_ROOT = PPIGPLM_DIR / "a2s2_runs"          # one subdir per OS-launched run
SAMPLE_CSV = PPIGPLM_DIR / "MED4-PPIs-low-confidence_ppiGPLM_prompts.csv"


def _infer_script():
    """Newest `sample_fasta3.3_softmax_error_handling*.py` in the repo.

    The repo ships revisions (3e, 3f, ...). Pick the lexically-greatest match
    so we use the latest, falling back to the documented 3f name.
    """
    matches = sorted(glob.glob(str(PPIGPLM_DIR / "sample_fasta3.3_softmax_error_handling*.py")))
    if matches:
        return Path(matches[-1]).name
    return "sample_fasta3.3_softmax_error_handling3f.py"


INFER_SCRIPT = _infer_script()


# ---------------------------------------------------------------- gpt env python
def _gpt_python():
    """Locate the `gpt` conda env's python (ppiGPLM needs torch+CUDA there).

    Override with PPIGPLM_PYTHON. Otherwise derive the conda base from the
    running interpreter / env vars and look for <base>/envs/gpt/bin/python,
    then common home install dirs. Falls back to "python" on PATH.
    """
    override = os.environ.get("PPIGPLM_PYTHON")
    if override:
        return override

    candidates = []
    # (a) derive from the server's own interpreter: .../envs/<x>/bin/python
    p = Path(sys.executable).resolve()
    for anc in p.parents:
        if anc.name == "envs":
            candidates.append(anc / "gpt" / "bin" / "python")
            break
    else:
        # base-env layout: <base>/bin/python -> <base>/envs/gpt/bin/python
        candidates.append(p.parent.parent / "envs" / "gpt" / "bin" / "python")
    # (b) conda env vars
    for var in ("CONDA_PREFIX_1", "CONDA_ROOT", "CONDA_EXE"):
        v = os.environ.get(var)
        if v:
            base = Path(v)
            base = base.parents[1] if base.name == "conda" else base  # CONDA_EXE -> .../bin/conda
            candidates.append(base / "envs" / "gpt" / "bin" / "python")
    # (c) common home installs
    try:
        home = Path.home()
    except (RuntimeError, OSError):
        home = _MODELS_DIR
    for d in ("anaconda3", "miniconda3", "miniforge3", "mambaforge"):
        candidates.append(home / d / "envs" / "gpt" / "bin" / "python")

    for c in candidates:
        try:
            if c and Path(c).is_file():
                return str(c)
        except OSError:
            continue
    return "python"


# ---------------------------------------------------------------- run command
_DEMO_RE = re.compile(r"^\s*(demo|sample|example|test)\s*(?::\s*(\d+|all))?\s*$", re.I)


def _checkpoint_name(model):
    """Map a model chip to a checkpoint file. There is one released checkpoint
    (ckpt.pt); a chip named <m> selects ckpt_<m>.pt when that file exists."""
    if model:
        cand = PPIGPLM_DIR / "checkpoints" / f"ckpt_{model}.pt"
        if cand.is_file():
            return cand.name
    return "ckpt.pt"


def _prep_input(task, run_dir):
    """Turn the free-text `task` into an input-prompts file. Returns
    (input_path, n_prompts, label). Three modes:

      * demo / demo:N / demo:all -> first N lines of the shipped MED4 sample
        (N defaults to 5; 'all' uses the whole file).
      * an existing file path     -> used directly as the prompt file.
      * anything else             -> treated as one inline prompt, written verbatim.
    """
    task = (task or "").strip()

    m = _DEMO_RE.match(task) if task else None
    if not task or m:
        n = 5
        if m and m.group(2):
            n = None if m.group(2).lower() == "all" else int(m.group(2))
        dst = run_dir / "input.csv"
        if SAMPLE_CSV.is_file():
            lines = []
            with SAMPLE_CSV.open("r", encoding="utf-8") as f:
                for i, line in enumerate(f):
                    if n is not None and i >= n:
                        break
                    if line.strip():
                        lines.append(line.rstrip("\n"))
            dst.write_text("\n".join(lines) + "\n", encoding="utf-8")
            label = f"MED4 low-confidence sample ({'all' if n is None else len(lines)} pairs)"
            return dst, len(lines), label
        # sample missing -> empty file; health() will have flagged this
        dst.write_text("", encoding="utf-8")
        return dst, 0, "demo (sample file missing)"

    # explicit file path (absolute, or relative to the repo / OS root / cwd)
    for base in (Path.cwd(), PPIGPLM_DIR, _OS_ROOT):
        cand = (base / task) if not os.path.isabs(task) else Path(task)
        if cand.is_file():
            try:
                n = sum(1 for ln in cand.open("r", encoding="utf-8") if ln.strip())
            except OSError:
                n = 0
            return cand.resolve(), n, f"file: {cand.name}"
        if os.path.isabs(task):
            break

    # inline single prompt
    dst = run_dir / "input.csv"
    dst.write_text(task + "\n", encoding="utf-8")
    short = task if len(task) <= 60 else task[:57] + "..."
    return dst, 1, f"inline: {short}"


def task_cmd(task, model):
    """Build the exact command to run one ppiGPLM inference job.

    Returns (argv, cwd, env). The job runs under the `gpt` env's python, with
    cwd = the ppiGPLM repo (the inference script `exec`s configurator.py and
    reads data/MED4_char/meta.pkl by relative path). Outputs and a metadata.json
    land in a fresh a2s2_runs/<run_id>/ folder so list_sessions can surface them.
    """
    run_id = time.strftime("%Y-%m-%d_%H-%M-%S") + "_run_" + uuid.uuid4().hex[:8]
    run_dir = RUNS_ROOT / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    input_path, n_prompts, label = _prep_input(task, run_dir)
    ckpt = _checkpoint_name(model)

    # Record what we know up front; outcome is filled in by list_sessions from
    # whether the probabilities CSV exists once the run finishes.
    meta = {
        "session_id": run_id,
        "task_label": label,
        "raw_task": (task or "").strip(),
        "model": model,
        "checkpoint": ckpt,
        "interface": "ppiGPLM",
        "n_prompts": n_prompts,
        "input_file": str(input_path),
        "started_at_iso": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "output_prefix": "ppiGPLM",
    }
    try:
        (run_dir / "metadata.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    except OSError:
        pass

    py = _gpt_python()
    argv = [
        py, INFER_SCRIPT,
        "--input_file", str(input_path),
        "--output_dir", str(run_dir),
        "--output_prefix", "ppiGPLM",
        "--model_dir", "checkpoints",
        "--ckpt_name", ckpt,
    ]
    # Force unbuffered child stdout so the OS sees per-prompt lines live.
    env = {**os.environ, "PYTHONUNBUFFERED": "1"}
    return argv, str(PPIGPLM_DIR), env


# ---------------------------------------------------------------- log -> state
_OK_HINTS = re.compile(r"(saved to|probability\(|>sample_|number of parameters)", re.I)
_ERR_HINTS = re.compile(r"(error|traceback|exception|not found|failed|cuda out of memory)", re.I)
_PLAN_HINTS = re.compile(r"(overriding|falling back|meta file|loading|resume)", re.I)
_RUN_RE = re.compile(r"a2s2_runs[/\\]([0-9A-Za-z_\-]+_run_[0-9A-Za-z]+)")
# Optional: capture P(interact) so the UI could show a live "score" (rail slot).
_P1_RE = re.compile(r"probability\(next_token=1\)\s*=\s*([0-9.eE+-]+)", re.I)


def classify_line(line):
    """Map one inference stdout line to {k, rail, session_dir} (keys optional).

    k is the run-log colour tag. We surface the latest P(interact) in `rail`
    (0..1) purely so the shell's signature strip has something live to show for
    a non-robot app; session_dir captures the run folder for the results link.
    """
    out = {}
    low = line.lower()
    # The FutureWarning torch prints about weights_only is benign -- don't flag it red.
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

    p1 = _P1_RE.search(line)
    if p1:
        try:
            out["rail"] = float(p1.group(1))
        except ValueError:
            pass

    run = _RUN_RE.search(line)
    if run:
        out["session_dir"] = run.group(0)

    return out


# ---------------------------------------------------------------- sessions
def list_sessions():
    """Saved ppiGPLM runs, newest first (cap 80), in the shell's row shape."""
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
        # outcome inferred from whether the probabilities CSV was written
        rec["outcome"] = "success" if (d / "ppiGPLM_probabilities.csv").is_file() else "pending"
        try:
            rec["files"] = sorted(p.name for p in d.iterdir() if p.is_file())[:6]
        except OSError:
            rec["files"] = []
        if not rec["time"]:
            rec["time"] = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(d.stat().st_mtime))
        out.append(rec)
    return out


# ---------------------------------------------------------------- library
# Curated "protocols" -> task strings understood by task_cmd / _prep_input.
LIBRARY = [
    {"color": "green", "title": "Sample: 5 MED4 pairs",
     "task": "demo:5",
     "desc": "Classify the first 5 low-confidence MED4 protein pairs shipped with ppiGPLM."},
    {"color": "blue", "title": "Sample: 20 MED4 pairs",
     "task": "demo:20",
     "desc": "Classify the first 20 low-confidence MED4 protein pairs."},
    {"color": "amber", "title": "Full MED4 low-confidence set",
     "task": "demo:all",
     "desc": "Score the entire shipped MED4 low-confidence prompt set (slower — thousands of pairs)."},
    {"color": "green", "title": "Custom prompt file",
     "task": "MED4-PPIs-low-confidence_ppiGPLM_prompts.csv",
     "desc": "Run inference on a prompts file by path (relative to the ppiGPLM repo or absolute)."},
]


def library():
    return LIBRARY


# ---------------------------------------------------------------- health
def health():
    repo_found = PPIGPLM_DIR.is_dir() and (PPIGPLM_DIR / INFER_SCRIPT).is_file()
    ckpt_ok = (PPIGPLM_DIR / "checkpoints" / "ckpt.pt").is_file()
    meta_ok = (PPIGPLM_DIR / "data" / "MED4_char" / "meta.pkl").is_file()
    py = _gpt_python()
    py_ok = (py != "python") and Path(py).is_file()
    return {
        "ready": bool(repo_found and ckpt_ok and meta_ok and py_ok),
        "repo": str(PPIGPLM_DIR),
        # reuse the shell's twin-oriented keys so the existing UI/banner render:
        "llm_dir": str(PPIGPLM_DIR),
        "llm_dir_found": repo_found,
        "twin_source": PPIGPLM_SOURCE,
        "twin_repo_url": PPIGPLM_REPO_URL,
        "infer_script": INFER_SCRIPT,
        "gpt_python": py,
        "gpt_python_found": py_ok,
        "checkpoint_found": ckpt_ok,
        "meta_found": meta_ok,
        "sample_found": SAMPLE_CSV.is_file(),
        # ppiGPLM is a local model: no API key needed. Report present so the
        # shell's "api key" gate never blocks a run.
        "api_key_present": True,
    }


# ---------------------------------------------------------------- manifest
MANIFEST = {
    "id": "ppi_gplm",
    "name": "ppiGPLM",
    "tagline": "ppiGPLM — protein-pair interaction classifier",
    "summary": "Score protein-protein interactions with a GPT-2-small protein language model — paste a prompt or run the MED4 sample.",
    "accent": "#3EC1A6",
    "models": ["ppiGPLM"],
    # Run inference via `conda run -n gpt` (torch + CUDA live there). The OS
    # honours this; _gpt_python() below stays as the fallback when conda is
    # unavailable.
    "conda_env": "gpt",
    "vocabulary": "molecular biology (proteins, protein pairs, interactions, MED4 interactome, interaction probability)",
}
