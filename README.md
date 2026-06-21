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

---

## Layout

A2S2-OS is its **own project**, a sibling of the twin under `Models/`:

```
Models/
├── A2S2-OS/                     # ← this project (the OS)
│   ├── index.html               # the console (shell) — one self-contained file
│   ├── server.py                # the OS bridge: serves the shell, runs jobs, app registry
│   ├── apps/
│   │   └── bench_console/
│   │       └── app.py           # app #1 — everything xArm-specific lives here
│   └── README.md
└── MuJoCo_xArm_dev_HP/         # the twin repo (github: xarm6-digital-twin-v5)
    └── xarm6_rail_digital_twin_llm_v5/xarm_lab_twin/
        ├── scripts/run_task.py
        └── recordings/<session>/
```

`server.py` (the OS) is **app-agnostic** — it serves the shell and runs the
machinery every app needs (a threaded job runner, live logs, an app registry).
Everything specific to the xArm twin lives in `apps/bench_console/app.py`:
where the twin is, the command that runs a task, how saved runs are read, and
how a log line maps to on-screen bench state. The bench app **auto-discovers**
the twin as a sibling project (no hard-coded repo folder name; override with
`BENCH_TWIN_DIR`).

---

## Run

On the lab machine, with the twin's conda env active:

```bash
conda activate xarm6sim
python server.py            # serves http://localhost:8770
```

Launching from inside `xarm6sim` matters: the bench app runs `run_task.py` with
the **same** Python interpreter that started the server, so the env carries
through. Open <http://localhost:8770>.

- Twin reachable → **live mode**, badge reads **Connected to bench**.
- Twin missing / `server.py` not running → **demo bench**: an amber banner and
  *simulated* runs so the console is feelable alone. Demo mode never presents a
  simulated run as a real one. (Open `index.html` directly in a browser to get
  the demo with no server at all.)

Config knobs (env): `A2S2_OS_PORT` (default `8770`), `A2S2_OS_APP` (active app,
default `bench_console`), `LAB_OS_PYTHON` (interpreter for tasks),
`BENCH_TWIN_DIR` (override twin discovery). To change the task CLI, edit the
one-line `task_cmd()` in `apps/bench_console/app.py`.

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
| GET | `/api/health` | `{ready, repo, llm_dir, llm_dir_found, api_key_present, apps, active_app}` |
| GET | `/api/apps` | installed apps (the extension seam) |
| GET | `/api/library` | active app's curated protocol list |
| GET | `/api/sessions` | active app's saved runs, newest first (cap 80) |
| POST | `/api/run` | `{task, model}` → `{job_id}` |
| GET | `/api/job/<id>` | `{status, log[], outcome, arm, rail, session_dir}` |

`status`: `queued → running → done|error`. `outcome`: `success|error` from the
task's exit code.

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

Headless smoke shape (works against any stub command):
`GET /api/health` → `POST /api/run` → poll `GET /api/job/<id>` until `done` →
`GET /api/sessions` shows the new run.

---

## Adding an app

The OS hosts more than the bench. To add an app (e.g. an OT-2 liquid handler, a
microscope), create `apps/<your_app>/app.py` exposing this surface — the OS
discovers it automatically and `/api/apps` lists it:

```python
MANIFEST = {"id": "my_app", "name": "My App", "tagline": "...", "models": [...]}

def health():       ...   # {ready, ...}            drives the live/demo badge
def library():      ...   # [ {color,title,task,desc}, ... ]   curated protocols
def list_sessions():...   # [ {label,model,outcome,time,files}, ... ]  newest first
def task_cmd(task, model):  ...   # -> (argv, cwd, env)   how one task runs
def classify_line(line):    ...   # -> {k, arm, rail, session_dir}   log line -> state
```

Only `task_cmd` is required; the OS fills in safe defaults for the rest. Select
it with `A2S2_OS_APP=my_app`. The job runner, live logs, and the whole endpoint
contract are provided by the OS — an app never re-implements them. A shell-side
app launcher (pick an app, then enter it) is the next step once a second app
lands; today the shell drives the single active app directly.

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

A2S2-OS is licensed under the **PolyForm Noncommercial License 1.0.0** — see
[`LICENSE.md`](LICENSE.md). Noncommercial use (including by educational and
public-research institutions) is free. **Any commercial use of this software or
its derivatives requires a separate commercial license from both Kourosh
Salehi-Ashtiani and New York University Abu Dhabi** — see
[`COMMERCIAL.md`](COMMERCIAL.md).

Copyright © 2026 Kourosh Salehi-Ashtiani and New York University Abu Dhabi.

---

## Roadmap (out of scope for v1)

- **Live arm feed in Watch** — the camera pane is a labelled placeholder; wire
  the existing WebXR / nginx-FFmpeg HLS stream in later.
- **Kiosk lockdown** — shortcut suppression, crash-restart, screen-lock.
- **Auth'd "advanced" exit** to a real desktop for maintainers.
- **App launcher + multi-app** — a second app (2nd xArm6, Lite 6, OT-2,
  microscope) turns the home into an OS-level launcher. The app registry here is
  the seam to grow.
- **Provenance / Algae Senate hook** — "Ask" is the natural place to route a
  co-scientist query later.
