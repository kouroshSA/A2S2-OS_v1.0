# A2S2-OS — Bench Console

A runnable kiosk **Lab OS** shell: one full-screen console a bench biologist
walks up to and drives the **xArm6 digital twin** from — tap a tile, ask in
plain words — without ever seeing a terminal, a node, a topic, or a URDF.

This is the **shell** layer (the role a desktop environment plays), sitting on
top of the existing control plane (the MuJoCo twin + `run_task.py`). It is a
single appliance app — like a lab analyzer or a Bambu printer screen — not a
Linux distro, not a desktop environment.

> The interface speaks **bench biology** (plates, samples, tubes, cubes, bins,
> racks, protocols, results) and never leaks robotics/ROS nouns. Internal code
> comments use the real terms; user-facing strings do not.

**New here? See [`quick_start.md`](quick_start.md)** for a few-minute setup
(demo or live).

---

## Layout

A2S2-OS is its **own project**, a sibling of the twin under `Models/`:

```
Models/
├── A2S2-OS/                     # ← this project (the OS)
│   ├── index.html               # the console (shell) — one self-contained file
│   ├── server.py                # the OS bridge: serves the shell, runs jobs, app registry
│   ├── apps/
│   │   ├── bench_console/
│   │   │   └── app.py           # app #1 — everything xArm-specific lives here
│   │   └── ppi_gplm/
│   │       └── app.py           # app #2 — ppiGPLM protein-pair classifier
│   └── README.md
└── xarm6-digital-twin-v5/      # the twin: a clone of the repo below (any folder name)
    └── xarm6_rail_digital_twin_llm_v5/xarm_lab_twin/
        ├── scripts/run_task.py
        └── recordings/<session>/
```

The twin is a clone of **<https://github.com/kouroshSA/xarm6-digital-twin-v5>**.
`git clone` names the folder `xarm6-digital-twin-v5`, but the OS identifies the
twin by the project path it contains, **not** by folder name — so a renamed
clone works just as well.

`server.py` (the OS) is **app-agnostic** — it serves the shell and runs the
machinery every app needs (a threaded job runner, live logs, an app registry).
Everything specific to the xArm twin lives in `apps/bench_console/app.py`:
where the twin is, the command that runs a task, how saved runs are read, and
how a log line maps to on-screen bench state. The bench app **auto-discovers**
the twin clone (see [Pointing it at the twin](#pointing-it-at-the-twin)).

---

## Run

On the lab machine:

```bash
python server.py            # serves http://localhost:8770
```

You **no longer need to `conda activate`** first. Each app declares the conda
env its tasks need (`conda_env` in its MANIFEST — see
[Apps & environments](#apps--environments)), and the OS launches tasks with
`conda run -n <env>` so the right interpreter and its fully activated
environment (PATH, `LD_LIBRARY_PATH`, CUDA libs) are used no matter which env
started the server. If `conda` can't be found, the OS falls back to the app's
own interpreter choice — in that case launch from the app's env (e.g.
`conda activate xarm6sim`) as before. Open <http://localhost:8770>.

- Twin reachable → **live mode**, badge reads **Connected to bench**.
- Twin missing / `server.py` not running → **demo bench**: an amber banner and
  *simulated* runs so the console is feelable alone. Demo mode never presents a
  simulated run as a real one. (Open `index.html` directly in a browser to get
  the demo with no server at all.)

Config knobs (env): `A2S2_OS_PORT` (default `8770`), `A2S2_OS_APP` (active app
at startup, default `bench_console` — switchable later from the UI),
`LAB_OS_PYTHON` (bench-app interpreter override), `BENCH_TWIN_DIR` (override
twin discovery). ppiGPLM app: `PPIGPLM_DIR` (override repo discovery),
`PPIGPLM_PYTHON` (interpreter fallback when conda is absent), `PPIGPLM_DEVICE`
(`cuda`/`cpu`; default auto-detect). To change a task's CLI, edit the one-line
`task_cmd()` in that app's `app.py`.

---

## Apps & environments

The OS hosts multiple apps and the shell can switch between them at runtime:

- **Bench Console** (`bench_console`) — the xArm6 digital twin (env `xarm6sim`).
- **ppiGPLM** (`ppi_gplm`) — a GPT-2-small protein language model that scores
  protein-protein interactions (env `gpt`). Pick a sample protocol or paste a
  structured prompt; runs land under **My samples & results**.

**Switching apps.** A dropdown in the top bar lists every installed app; choosing
one re-skins the console and reloads that app's library and saved runs. The
startup default is `A2S2_OS_APP` (or the first app found). Under the hood the
shell calls `POST /api/active {"app": "<id>"}`.

**Per-app conda env — the `conda_env` manifest field.** An app declares the
conda env its tasks run in:

```python
MANIFEST = {"id": "ppi_gplm", "name": "ppiGPLM", "conda_env": "gpt", ...}
```

When `conda_env` is set **and** conda is found, the OS runs that app's tasks as:

```
conda run --no-capture-output -n <conda_env> python <script> ...
```

so the correct interpreter and its full activated environment are used
regardless of which env launched `server.py`. Notes:

- `--no-capture-output` keeps the run log **streaming live** (plain `conda run`
  buffers until the process exits).
- The wrapper substitutes a bare `python` for the interpreter the app's
  `task_cmd()` returned, so conda runs the *target env's* python (not the
  server's). The app's own interpreter remains the fallback when conda is absent.
- Omit `conda_env` for apps that need no special env — tasks then run with the
  interpreter `task_cmd()` chose (e.g. `sys.executable`).
- Each `conda run` adds ~1–2 s of activation overhead per task launch.

---

## Modes of operation

A single task is only one way to drive the twin. The Bench Console surfaces the
twin's **major modes of operation** as a gallery behind the **Lab modes** home
tile — each is a card with a small parameter form and a **Run** button. The home
tiles **Ask the bench** and **Run a protocol** remain quick paths to the plain
single-task mode.

| Mode | Twin script | What it does |
|---|---|---|
| **Ask the bench** (`task`) | `run_task.py` | Plan and run one task once. |
| **Self-learning loop** (`learn`) | `run_task.py --loop` | Attempt one task many times, reusing what worked and turning failures into constraints; ends with an improvement summary. |
| **Auto-play batch** (`autoplay`) | `auto_play.py` | The bench invents a batch of diverse tasks and runs them back-to-back; a review is written at the end. |
| **Random play** (`random`) | `random_play.py` | Sample random reachable poses with no planner — fast trajectory-data generation. |
| **Augment a task** (`augment`) | `run_task_augmented.py` | Run one task across cycles, rewording it and jittering object poses to build a small varied dataset. |
| **VR teleoperation** (`vr`) | `run_vr.py` | Start the VR teleop server; drive the bench by hand from a headset and record demos. **Long-running** — press **Stop** in **Watch** to end it. |

Each mode maps to its script + flags in **one place**: the `MODES` list and the
`build_cmd(mode, task, params)` function in `apps/bench_console/app.py`. The
server and shell are generic — they render whatever modes the active app
declares (via `/api/modes`) and post `{mode, task, params}` to `/api/run`. The
batch modes are launched with their non-interactive flags (`--save-all`,
`--auto-accept-variants`, `--no-annotate`) because the OS detaches child stdin.
Long-running modes are terminated by `POST /api/job/<id>/stop`, which signals the
child's whole process group (so the `conda run` grandchild dies too).

---

## Pointing it at the twin

On startup the bench app resolves where the xArm6 twin lives and prints it:

```
  twin project: /…/MuJoCo_xArm_dev_HP/xarm6_rail_digital_twin_llm_v5/xarm_lab_twin  [found]
  found via   : sibling: MuJoCo_xArm_dev_HP
```

`/api/health` reports the same (`llm_dir`, `llm_dir_found`, `twin_source`).
Resolution order — first hit wins:

1. **`BENCH_TWIN_DIR`** env var — explicit, overrides everything.
   `BENCH_TWIN_DIR=/path/to/.../xarm_lab_twin python server.py`
2. **`config.json`** next to `server.py`, key `twin_dir` — set it once per
   install. Copy `config.example.json` → `config.json` and edit the path.
   (`config.json` is git-ignored, so your local path is never committed.)
3. **Clone auto-discovery** — looks for a clone of the twin repo (any folder
   name) in the folder that *contains* `A2S2-OS`, inside `A2S2-OS` itself, and
   under your home directory. A clone is recognized by the project path it holds
   (`xarm6_rail_digital_twin_llm_v5/xarm_lab_twin`), not its name; a folder named
   `xarm6-digital-twin-v5` wins if several match. This is why it works with no
   setup when the twin sits beside `A2S2-OS`.
4. **Default fallback** — the canonical clone name `xarm6-digital-twin-v5` as a
   sibling; if absent, the browser simply stays in demo mode (the startup banner
   then prints the `git clone` command for the repo).

So: **clone the twin beside `A2S2-OS` and it finds it automatically, whatever
the folder is named.** Install `A2S2-OS` somewhere unrelated and set `twin_dir`
in `config.json` once (or export `BENCH_TWIN_DIR`). Paths may be absolute or
relative to the `A2S2-OS` folder, and `~` is expanded.

---

## Running on macOS (standalone shell)

The **live** server (`server.py` driving the twin) belongs on the WSL/Linux
machine that runs the simulation — MuJoCo, `run_task.py`, and the `xarm6sim`
conda env all live there. But the **standalone shell** (the *demo bench* — the
full console with simulated runs) runs on macOS with **no code changes**:

```bash
# Easiest: double-click  open-demo-macos.command  in Finder.
# Or, equivalently, from a terminal in this folder:
open index.html
```

`index.html` is a single self-contained file (inline CSS + vanilla JS), so any
modern browser renders it; opening it directly puts the console in demo mode
(amber "Demo bench" banner, simulated runs, never presented as real).

**Apple Silicon (ARM) vs Intel:** both run it identically. Nothing in the shell
is compiled or architecture-specific — it is HTML/CSS/JS in a browser, and
`server.py` (if you run it) is pure-stdlib Python 3, which CPython provides on
both architectures. The only place CPU architecture would matter is the *twin
itself* (MuJoCo), which is not part of this repo and is not needed for the
standalone shell.

Optional — serve it locally on the Mac instead of `file://` (still demo, since
the twin isn't present), e.g. to preview kiosk mode:

```bash
python3 server.py          # needs Python 3 (preinstalled on macOS / `brew install python`)
# then open http://localhost:8770
```

macOS kiosk (full-screen, no browser chrome) with Chrome:

```bash
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
  --kiosk --app=http://localhost:8770       # or --app=file://$PWD/index.html for pure demo
```

To actually drive the real arm from a Mac you would need to port the twin
(MuJoCo + `run_task.py` + its env) onto that Mac; the OS bridge here is already
cross-platform and would serve it unchanged.

---

## Endpoints

| Method | Path | Returns |
|---|---|---|
| GET | `/`, `/index.html` | the shell HTML |
| GET | `/api/health` | `{ready, repo, llm_dir, llm_dir_found, api_key_present, apps, active_app, modes}` |
| GET | `/api/apps` | installed apps (the extension seam) |
| GET | `/api/modes` | active app's modes of operation (descriptors) |
| GET | `/api/library` | active app's curated protocol list |
| GET | `/api/sessions` | active app's saved runs, newest first (cap 80) |
| POST | `/api/run` | `{mode, task, params}` → `{job_id}` (legacy `{task, model}` still accepted → task mode). Optional `app` targets a specific app for this run **without** changing the active app; absent → the active app. |
| POST | `/api/active` | `{app}` → health payload; switches the active app |
| GET | `/api/job/<id>` | `{status, log[], outcome, mode, arm, rail, session_dir}` |
| POST | `/api/job/<id>/stop` | stop a running job (the long-running modes, e.g. VR) |

`status`: `queued → running → done|error|stopped`. `outcome`: `success|error`
from the task's exit code, or `stopped` when the user ends a long-running mode.

---

## Acceptance / smoke test

Live (env `xarm6sim` active, `.env` key present, on a machine with a display):

1. `python server.py` prints the twin path and `[found]`.
2. `GET /api/health` → `ready: true`, `llm_dir_found: true`.
3. Open the page → badge reads **Connected to bench** (no amber banner).
4. **Run a protocol → Sort green cube** → the view switches to **Watch**, the
   run log streams plan + dispatch lines, the bench strip shows the carriage
   move and green-cube highlight, the arm returns to `idle`.
5. A new run appears under **My samples & results** with its model, outcome and
   saved files (`metadata.json`, `trajectory.h5`, …).
6. Repeat via **Ask the bench** with free text ("Put the red cube in the red bin").
7. **Lab modes → Random play** (Runs 2, Moves 4) → the run log streams
   `[RandomPlay]` lines and two sessions land under **My samples & results**.
   Start **VR teleoperation** → the log shows the headset address and a **Stop**
   button appears in **Watch**; pressing it ends the run (`status: stopped`).

Headless smoke shape (works against any stub command):
`GET /api/health` → `POST /api/run` → poll `GET /api/job/<id>` until `done` →
`GET /api/sessions` shows the new run.

---

## Adding an app

The OS hosts more than the bench. To add an app (e.g. an OT-2 liquid handler, a
microscope), create `apps/<your_app>/app.py` exposing this surface — the OS
discovers it automatically and `/api/apps` lists it:

```python
MANIFEST = {"id": "my_app", "name": "My App", "tagline": "...",
            "models": [...], "conda_env": "my_env"}   # conda_env is optional

def health():       ...   # {ready, ...}            drives the live/demo badge
def library():      ...   # [ {color,title,task,desc}, ... ]   curated protocols
def list_sessions():...   # [ {label,model,outcome,time,files}, ... ]  newest first
def task_cmd(task, model):  ...   # -> (argv, cwd, env)   how one task runs
def classify_line(line):    ...   # -> {k, arm, rail, session_dir}   log line -> state

# optional — expose multiple modes of operation (see Bench Console):
def modes():        ...   # [ {id,name,gallery,needs_task,params,...}, ... ]
def build_cmd(mode, task, params):  ...   # -> (argv, cwd, env)  per-mode command
```

Only `task_cmd` is required; the OS fills in safe defaults for the rest. An app
that declares `modes()` + `build_cmd()` gets the **Lab modes** gallery and a
generated parameter form per mode for free; an app that defines only `task_cmd`
runs as a single implicit task mode. Set
`conda_env` in the MANIFEST to have the OS run the app's tasks via
`conda run -n <env>` (see [Apps & environments](#apps--environments)); omit it
to run with whatever interpreter `task_cmd()` returns. The app starts active if
`A2S2_OS_APP=my_app`, and is selectable any time from the top-bar **app
switcher**. The job runner, live logs, and the whole endpoint contract are
provided by the OS — an app never re-implements them.

---

## Notes / deviations from the original spec

The shell `index.html` is the tested feel prototype, used verbatim. Three
deliberate, verified changes were made while wiring it to the **real** repo:

- **OS is a standalone sibling project**, not a `lab_os_shell/` folder inside
  the twin repo — so it can host non-xArm apps later. The bench app discovers
  the twin as a sibling instead of as its parent.
- **`run_task.py` is spawned with `stdin=DEVNULL`.** The twin's task script ends
  with an interactive `Keep this recording? [Y/n]` prompt. Detaching stdin makes
  that prompt read EOF and keep the recording; without it the child would
  inherit the server's terminal and **hang every run**.
- **`/api/sessions` maps the twin's real `metadata.json` schema** onto the
  shell's rows: `task_label → label`, `llm_model → model` (shortened back to
  `haiku`/`sonnet`/`opus`), `started/ended_at_iso → time`. Runs with no metadata
  yet fall back to the directory name and mtime.

---

## Kiosk / boot-to-console

Stock machine, auto-launch the page full screen:

```bash
chromium --kiosk --app=http://localhost:8770
#   --kiosk    full screen, no chrome
#   --app=URL  no tabs/address bar
```

Autostart `server.py` + this command on boot (a systemd user service or the
desktop autostart entry) and the console comes up on its own.

---

## License

A2S2-OS is licensed under the **PolyForm Noncommercial License 1.0.0, plus
Additional Terms** — see [`LICENSE.md`](LICENSE.md). Noncommercial use (including
by educational and public-research institutions) is free. Two obligations apply
to **all** users:

- **Cite this repository.** Any use of the software or a derivative — in
  research, software, products, or publications — must credit and cite this
  repo, using [`CITATION.cff`](CITATION.cff) for scholarly work. Applies to
  commercial and noncommercial use alike.
- **Modified versions carry the license.** Every copy, fork, or derivative —
  commercial or not — must include a complete copy of `LICENSE.md` and keep all
  `Required Notice:` lines.

**Any commercial use of this software or its derivatives also requires a separate
commercial license from both Kourosh Salehi-Ashtiani and New York University Abu
Dhabi** — see [`COMMERCIAL.md`](COMMERCIAL.md).

Copyright © 2026 Kourosh Salehi-Ashtiani and New York University Abu Dhabi.

---

## Roadmap (out of scope for v1)

- **Live arm feed in Watch** — the camera pane is a labelled placeholder; wire
  the existing WebXR / nginx-FFmpeg HLS stream in later.
- **Kiosk lockdown** — shortcut suppression, crash-restart, screen-lock.
- **Auth'd "advanced" exit** to a real desktop for maintainers.
- **App launcher + multi-app** — *shipped:* a top-bar app switcher and a second
  app (ppiGPLM) now run on the registry seam. Next: more apps (2nd xArm6, Lite 6,
  OT-2, microscope) and a richer home-screen launcher.
- **Provenance / Algae Senate hook** — "Ask" is the natural place to route a
  co-scientist query later.
