# A2S2-OS — Bench Console

**Claude Code build instruction · component spec**

> **Build target.** A runnable kiosk "Lab OS" shell that sits on top of
> `xarm6-digital-twin-v5` and lets a bench biologist with minimal computer
> skills drive the twin from one full-screen console — tap a tile, ask in plain
> words — without ever seeing a terminal, a node, a topic, or a URDF.
>
> This file is the design document for the build, in the same spirit as the
> existing `xarm6_rail_*_v5.md` specs. Follow it exactly; the verbatim feel
> prototype is in **Appendix A** and the reference bridge in **Appendix B**.

---

## 0. TL;DR for Claude Code

Create a new top-level folder `lab_os_shell/` containing three files:

```
xarm6-digital-twin-v5/
└── lab_os_shell/
    ├── index.html     # the console (shell) — Appendix A, use verbatim to start
    ├── server.py      # stdlib-only bridge to run_task.py + recordings/ — Appendix B
    └── README.md      # short run notes
```

Then make it run:

```bash
conda activate xarm6sim
python lab_os_shell/server.py        # serves http://localhost:8770
```

**Done when:** opening `http://localhost:8770` shows the console with the badge
reading **Connected to bench**; tapping a protocol runs `run_task.py` for real;
the finished run appears under *My samples & results*. Acceptance steps in §7.

Start from the Appendix files **verbatim** — they are tested and working. Only
then iterate (§6). Do not rewrite from scratch; do not redesign the look.

---

## 1. What this is — and what it is NOT

This is the **shell** layer of the lab OS: the GUI surface the human touches,
the role a desktop environment plays, sitting on top of the existing control
plane (the MuJoCo twin + `run_task.py`).

It is **NOT** a Linux distribution, **NOT** a custom desktop environment, and
**NOT** a reskin of GNOME. It is a single full-screen *appliance* app — like a
lab analyzer or a Bambu printer screen — that a stock machine boots straight
into. That choice is deliberate: ~1% of the maintenance burden of a distro,
near-zero bus factor, and a blast radius locked down for a machine that drives a
robot arm.

**The hard rule (do not violate):** the interface speaks **bench biology** —
plates, samples, tubes, cubes, bins, racks, protocols, results. It **never**
leaks robotics/ROS nouns. No "node", "topic", "URDF", "joint", "end-effector",
"MuJoCo", "terminal" anywhere a user can see. If a biologist sees the word
"node", the abstraction has failed. Internal code comments may use real terms;
**user-facing strings may not.**

---

## 2. Where it lives & how it attaches to the repo

The shell folder sits at the repo root. The bridge auto-discovers the LLM
project relative to itself:

```
REPO            = <repo root>                         # parent of lab_os_shell/
LLM_DIR         = REPO/xarm6_rail_digital_twin_llm_v5/xarm_lab_twin
TASK COMMAND    = python scripts/run_task.py "<task>" --model <model>   (cwd = LLM_DIR)
RECORDINGS      = LLM_DIR/recordings/<session>/        (+ the basic project's recordings/)
```

The bridge shells out to the **existing** `scripts/run_task.py` — it does not
reimplement planning or control. It reads the **existing** `recordings/*/metadata.json`
session format. Nothing in the twin needs to change.

---

## 3. Architecture (one diagram)

```
   Bench biologist
        │  taps a tile / types a task
        ▼
   index.html  ── the SHELL (kiosk, full screen) ─────────────┐
        │  fetch()                                             │  speaks bench
        ▼                                                      │  vocabulary only
   server.py  ── the BRIDGE (stdlib http, threaded jobs) ──────┘
        │  subprocess (cwd = LLM_DIR)
        ▼
   scripts/run_task.py  ── EXISTING control plane
        │
        ▼
   LLMBrain → primitives → SimXArmAPI → MuJoCo → Recorder
        │
        ▼
   recordings/<session>/  ── read back by /api/sessions
```

---

## 4. Component A — the shell (`index.html`)

A single self-contained HTML file (inline CSS + vanilla JS, no build step). Use
**Appendix A verbatim** as the starting point. Behaviour contract:

**Home = four tiles**, in bench vocabulary:

| Tile | User words | Wires to |
|---|---|---|
| Ask the bench | "say it in plain words" | `POST /api/run` → stream `/api/job/<id>` |
| Run a protocol | library of saved tasks | same run path, task string from a card |
| Watch | live camera + run log + queue | job log; camera pane = feed slot |
| My samples & results | saved runs | `GET /api/sessions` |

**Signature element — the bench layout strip.** A top-down SVG schematic of the
*actual scene*: the rail with the arm carriage, the three RGB cubes, three
matching bins, and two 6-tube Falcon racks. It carries live `arm` and `rail`
state during a run (carriage slides, target object highlights). This is the one
memorable element; keep everything else quiet. Do not replace it with a generic
progress bar.

**Demo-mode contract (important).** On load the shell calls `/api/health`. If
reachable and ready → **live mode** ("Connected to bench"). If not → **demo
bench**: a clearly-labelled amber banner, and Ask/protocols run a *simulated*
plan so the console is feelable with no server. Demo mode must **never** present
a simulated run as a real one. Keep this behaviour.

**Design tokens — do not drift to the generic AI look.** The identity is a lab
*instrument*, grounded in the scene's own RGB object-coding over anodised
charcoal-green. Hold these:

- **Palette:** bench `#0E1614`, panel `#15211E`, hairline `#243430`, text
  `#E8F0ED`, muted `#7E938C`. Functional accents = the scene's coding: red
  `#E5484D`, green `#46C26A`, blue `#4C8DFF`. One bold spend only: amber "go"
  `#F2B23E` for the primary action.
- **Type:** Space Grotesk (display/UI), Inter (body/labels), IBM Plex Mono
  (data, log, readouts — the "instrument" voice).
- **Spend boldness once** (the amber action + the live bench strip); everything
  else stays disciplined. No cream/serif, no acid-green-on-black, no broadsheet
  rules.

**Copy rules:** active voice, sentence case, name things by what the user
controls. Empty states are invitations ("No runs yet. Ask the bench to do
something…"), errors give direction in the interface's voice ("Could not reach
the bench. Is server.py running on the lab machine?").

---

## 5. Component B — the bridge (`server.py`)

Stdlib only — **no new pip dependencies** (`http.server`, `subprocess`,
`threading`, `json`, `pathlib`). Use **Appendix B verbatim** as the reference.
Endpoint contract:

| Method | Path | Body | Returns |
|---|---|---|---|
| GET | `/` , `/index.html` | — | the shell HTML |
| GET | `/api/health` | — | `{ready, repo, llm_dir, llm_dir_found, api_key_present}` |
| GET | `/api/library` | — | curated protocol list (overrides front-end default) |
| GET | `/api/sessions` | — | parsed `recordings/*/metadata.json`, newest first |
| POST | `/api/run` | `{task, model}` | `{job_id}` |
| GET | `/api/job/<id>` | — | `{status, log[], outcome, arm, session_dir}` |

**Job model.** `POST /api/run` spawns `run_task.py` in a daemon thread with
`cwd = LLM_DIR`, captures combined stdout/stderr line by line, tags each line
(`plan` / `ok` / `err`), sniffs arm state and the saved `recordings/<session>`
path from the output, and exposes it all via `/api/job/<id>`. `status` goes
`queued → running → done|error`; `outcome` is `success|error` from the exit code.

**Sessions.** `/api/sessions` scans both projects' `recordings/`, parses
`metadata.json` (`task`, `model`, `outcome`, `timestamp`), lists the saved files
per session, falls back to mtime if no timestamp. Cap at 80.

**Config knobs** (top of file): `LAB_OS_PORT` (default 8770), `LAB_OS_PYTHON`,
and a one-line `task_cmd()` to edit if the CLI ever changes. Default matches the
repo: `python scripts/run_task.py "<task>" --model <model>`.

---

## 6. Build steps (start small — this repo rewards it)

1. **Drop the files.** Create `lab_os_shell/` with Appendix A and B verbatim,
   plus a short `README.md`. Commit.
2. **Run & verify live mode** against the real twin (§7). Commit.
3. **Then** iterate one change at a time, committing between:
   - confirm the protocol library in `server.py` matches the scene's real
     objects/primitives (cube→bin, tube→rack, push, wave);
   - tune the bench-strip animation to the actual `arm`/`rail` values the log
     emits;
   - widen log tagging if `run_task.py` prints distinctive plan/dispatch lines.
4. Keep each step small; inspect a real recording (`python replay.py <session>`)
   when something looks off rather than re-reading code.

Do **not** big-bang a redesign. The look and the endpoint contract are fixed by
this spec.

---

## 7. Acceptance criteria (verification)

Run these against the **real** repo (env `xarm6sim` active, `.env` key present):

1. `python lab_os_shell/server.py` prints the repo + LLM-project paths and
   `[found]`.
2. `GET /api/health` → `ready: true`, `llm_dir_found: true`.
3. Open `http://localhost:8770` → badge reads **Connected to bench** (no amber
   banner).
4. Tap **Run a protocol → Sort green cube**. The view switches to **Watch**; the
   run log streams plan + dispatch lines; the bench strip shows the carriage
   move and the green cube highlight; the arm returns to `idle`.
5. A new session appears under **My samples & results** with `model`, `outcome:
   success`, and the saved files (`metadata.json`, `trajectory.h5`, …).
6. Repeat via **Ask the bench** with a free-text task ("Put the red cube in the
   red bin").

Smoke-test shape (also works against a stub `run_task.py` for CI):
`GET /api/health` → `POST /api/run` → poll `GET /api/job/<id>` until `done` →
`GET /api/sessions` shows the new run.

---

## 8. Kiosk / boot-to-console (the "walk up and it's there" experience)

No custom distro or DE. Stock machine, auto-launch the page full screen:

```bash
chromium --kiosk --app=http://localhost:8770
#   --kiosk        full screen, no chrome
#   --app=URL      no tabs/address bar
# Autostart server.py + this command on boot via a systemd user service or the
# desktop's autostart entry, and the bench console comes up on its own.
```

Optional hardening (see §9): disable shortcuts that escape the app, suppress the
screensaver, restart the browser if it crashes so it can't drop to a desktop.

---

## 9. Roadmap — explicitly OUT of scope for v1

Note these in the README; do not build them in the first pass:

- **Live arm feed in Watch.** The camera pane is a labelled placeholder. Wire
  the existing WebXR / nginx-FFmpeg HLS stream into it later.
- **Kiosk lockdown.** Shortcut suppression, crash-restart, screen-lock — real
  work, separate pass.
- **Auth'd "advanced" exit** to a real desktop for maintainers (every escape
  hatch chips at the safety the kiosk buys — add deliberately).
- **Multi-arm / queue across instruments** (2nd xArm6, Lite 6, OT-2) — the
  bridge's job model is the seam to grow here.
- **Provenance / Algae Senate hook** — "Ask" is the natural place to route a
  co-scientist query later.

---

## Appendix A — feel prototype: `index.html` (verbatim)

This is the tested console. Drop it in as-is; it is both the **feel prototype**
(open it directly → demo bench) and the production shell (served by `server.py`
→ live). Everything about the look and behaviour above is realised here.

```html
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no" />
<title>Lab OS — A2S2 Bench Console</title>
<link rel="preconnect" href="https://fonts.googleapis.com" />
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
<link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;600;700&family=Inter:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500;600&display=swap" rel="stylesheet" />
<style>
  :root{
    --bench:#0E1614;      /* anodized charcoal-green casing */
    --bench-2:#0A100F;
    --panel:#15211E;      /* raised panel */
    --panel-2:#1B2A26;
    --line:#243430;       /* hairline */
    --line-2:#2E423C;
    --text:#E8F0ED;
    --muted:#7E938C;
    --muted-2:#566962;
    --red:#E5484D;        /* scene object coding */
    --green:#46C26A;
    --blue:#4C8DFF;
    --go:#F2B23E;         /* the one bold spend: primary action */
    --go-ink:#1A1305;
    --ok:#46C26A; --warn:#F2B23E; --err:#E5484D;
    --r:14px;
    font-synthesis:none;
  }
  *{box-sizing:border-box;margin:0;padding:0}
  html,body{height:100%}
  body{
    background:
      radial-gradient(1200px 700px at 80% -10%, #16241f 0%, transparent 60%),
      var(--bench);
    color:var(--text);
    font-family:"Inter",system-ui,sans-serif;
    -webkit-font-smoothing:antialiased;
    overflow:hidden;
    user-select:none;
  }
  .app{height:100vh;display:flex;flex-direction:column}

  /* ---------- top bar ---------- */
  .topbar{
    display:flex;align-items:center;gap:18px;
    padding:14px 22px;border-bottom:1px solid var(--line);
    background:linear-gradient(var(--bench-2),transparent);
  }
  .brand{display:flex;align-items:center;gap:12px;cursor:pointer}
  .mark{width:30px;height:30px;border-radius:8px;background:
      conic-gradient(from 210deg, var(--green), var(--blue) 40%, var(--red) 75%, var(--green));
      box-shadow:inset 0 0 0 4px var(--bench);position:relative}
  .mark::after{content:"";position:absolute;inset:9px;border-radius:3px;background:var(--bench)}
  .brand h1{font-family:"Space Grotesk";font-size:15px;font-weight:600;letter-spacing:.04em}
  .brand small{display:block;font-size:11px;color:var(--muted);font-weight:500;letter-spacing:.12em;text-transform:uppercase}
  .top-spacer{flex:1}
  .conn{display:flex;align-items:center;gap:8px;font-family:"IBM Plex Mono";font-size:12px;
    padding:6px 11px;border:1px solid var(--line);border-radius:999px;color:var(--muted)}
  .dot{width:8px;height:8px;border-radius:50%;background:var(--muted-2)}
  .conn.live .dot{background:var(--ok);box-shadow:0 0 10px var(--ok)}
  .conn.demo .dot{background:var(--warn);box-shadow:0 0 10px var(--warn)}
  .clock{font-family:"IBM Plex Mono";font-size:13px;color:var(--muted);min-width:58px;text-align:right}

  /* ---------- bench status strip (signature) ---------- */
  .strip{padding:12px 22px;border-bottom:1px solid var(--line);background:var(--bench-2)}
  .strip-head{display:flex;align-items:center;justify-content:space-between;margin-bottom:8px}
  .strip-head .label{font-family:"IBM Plex Mono";font-size:11px;letter-spacing:.16em;color:var(--muted);text-transform:uppercase}
  .strip-head .state{font-family:"IBM Plex Mono";font-size:12px;color:var(--muted)}
  .strip-head .state b{color:var(--text);font-weight:600}
  svg.bench{width:100%;height:96px;display:block}

  /* ---------- body ---------- */
  .body{flex:1;overflow:hidden;display:flex}
  .home{flex:1;display:grid;grid-template-columns:1fr 1fr;grid-template-rows:1fr 1fr;gap:18px;padding:22px}
  .tile{
    position:relative;border:1px solid var(--line);border-radius:var(--r);
    background:linear-gradient(160deg,var(--panel),var(--bench));
    padding:26px 28px;cursor:pointer;overflow:hidden;
    display:flex;flex-direction:column;justify-content:space-between;
    transition:border-color .15s, transform .1s;
  }
  .tile:hover{border-color:var(--line-2)}
  .tile:active{transform:translateY(1px)}
  .tile .ico{width:46px;height:46px;border-radius:11px;display:grid;place-items:center;
    border:1px solid var(--line-2);background:var(--bench-2)}
  .tile h2{font-family:"Space Grotesk";font-size:24px;font-weight:600;margin-top:auto;letter-spacing:.01em}
  .tile p{color:var(--muted);font-size:14px;margin-top:6px;line-height:1.45;max-width:34ch}
  .tile .accent{position:absolute;right:-40px;top:-40px;width:140px;height:140px;border-radius:50%;
    opacity:.10;filter:blur(6px)}
  .tile.ask .accent{background:var(--go)}
  .tile.proto .accent{background:var(--green)}
  .tile.watch .accent{background:var(--blue)}
  .tile.samples .accent{background:var(--red)}
  .tile .badge{position:absolute;top:18px;right:18px;font-family:"IBM Plex Mono";font-size:12px;
    color:var(--muted);border:1px solid var(--line);border-radius:999px;padding:3px 9px}

  /* ---------- drilled view ---------- */
  .view{flex:1;display:flex;flex-direction:column;overflow:hidden}
  .view-head{display:flex;align-items:center;gap:14px;padding:18px 22px;border-bottom:1px solid var(--line)}
  .back{width:38px;height:38px;border-radius:10px;border:1px solid var(--line);background:var(--panel);
    color:var(--text);cursor:pointer;display:grid;place-items:center;font-size:18px}
  .back:hover{border-color:var(--line-2)}
  .view-head h2{font-family:"Space Grotesk";font-size:20px;font-weight:600}
  .view-head .sub{color:var(--muted);font-size:13px;margin-left:2px}
  .view-body{flex:1;overflow:auto;padding:22px}

  /* ask */
  .ask-wrap{max-width:760px;margin:0 auto}
  .ask-box{border:1px solid var(--line);border-radius:var(--r);background:var(--panel);padding:18px}
  textarea#task{width:100%;min-height:96px;resize:none;background:transparent;border:none;outline:none;
    color:var(--text);font-family:"Inter";font-size:18px;line-height:1.5}
  textarea#task::placeholder{color:var(--muted-2)}
  .ask-row{display:flex;align-items:center;justify-content:space-between;margin-top:14px;gap:12px}
  .model-pick{display:flex;gap:6px}
  .chip{font-family:"IBM Plex Mono";font-size:12px;color:var(--muted);border:1px solid var(--line);
    background:var(--bench-2);border-radius:999px;padding:6px 12px;cursor:pointer}
  .chip.on{color:var(--go);border-color:var(--go)}
  .go{font-family:"Space Grotesk";font-weight:600;font-size:15px;letter-spacing:.02em;
    background:var(--go);color:var(--go-ink);border:none;border-radius:11px;padding:12px 22px;cursor:pointer;
    display:flex;align-items:center;gap:9px}
  .go:disabled{opacity:.45;cursor:not-allowed}
  .go:hover:not(:disabled){filter:brightness(1.06)}
  .suggest{margin-top:22px}
  .suggest .label{font-family:"IBM Plex Mono";font-size:11px;letter-spacing:.16em;text-transform:uppercase;color:var(--muted);margin-bottom:10px}
  .sug-list{display:flex;flex-wrap:wrap;gap:9px}
  .sug{font-size:14px;color:var(--text);border:1px solid var(--line);background:var(--panel);
    border-radius:999px;padding:9px 15px;cursor:pointer}
  .sug:hover{border-color:var(--line-2)}

  /* protocol library */
  .lib{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:16px;max-width:980px;margin:0 auto}
  .card{border:1px solid var(--line);border-radius:var(--r);background:var(--panel);padding:18px;cursor:pointer;
    display:flex;flex-direction:column;gap:10px;transition:border-color .15s}
  .card:hover{border-color:var(--line-2)}
  .card .swatch{width:100%;height:6px;border-radius:99px;background:var(--line-2)}
  .card.red .swatch{background:var(--red)} .card.green .swatch{background:var(--green)}
  .card.blue .swatch{background:var(--blue)} .card.amber .swatch{background:var(--go)}
  .card h3{font-family:"Space Grotesk";font-size:17px;font-weight:600}
  .card p{color:var(--muted);font-size:13.5px;line-height:1.5}
  .card .run-tag{font-family:"IBM Plex Mono";font-size:12px;color:var(--muted);margin-top:auto}

  /* watch */
  .watch{display:grid;grid-template-columns:1.3fr 1fr;gap:18px;height:100%}
  .cam{border:1px solid var(--line);border-radius:var(--r);background:
      repeating-linear-gradient(45deg,#0c1311 0 12px,#0a100e 12px 24px);
      display:flex;flex-direction:column;overflow:hidden}
  .cam .cam-bar{display:flex;align-items:center;gap:8px;padding:10px 14px;border-bottom:1px solid var(--line);
    font-family:"IBM Plex Mono";font-size:12px;color:var(--muted);background:var(--bench-2)}
  .cam .cam-bar .rec{width:9px;height:9px;border-radius:50%;background:var(--red);box-shadow:0 0 8px var(--red)}
  .cam .cam-body{flex:1;display:grid;place-items:center;text-align:center;color:var(--muted-2);padding:24px}
  .cam .cam-body .big{font-family:"Space Grotesk";font-size:16px;color:var(--muted);margin-bottom:6px}
  .readout{border:1px solid var(--line);border-radius:var(--r);background:var(--bench-2);display:flex;flex-direction:column;overflow:hidden}
  .readout .r-bar{padding:10px 14px;border-bottom:1px solid var(--line);font-family:"IBM Plex Mono";
    font-size:12px;letter-spacing:.1em;text-transform:uppercase;color:var(--muted);display:flex;justify-content:space-between}
  .log{flex:1;overflow:auto;padding:12px 14px;font-family:"IBM Plex Mono";font-size:12.5px;line-height:1.65;color:#bcd}
  .log .l-plan{color:var(--go)} .log .l-ok{color:var(--ok)} .log .l-err{color:var(--err)} .log .l-dim{color:var(--muted-2)}
  .queue{margin-top:0}
  .q-item{display:flex;align-items:center;gap:10px;padding:10px 12px;border:1px solid var(--line);border-radius:10px;margin-bottom:8px;background:var(--panel)}
  .q-item .q-dot{width:9px;height:9px;border-radius:50%;flex:none}
  .q-running{background:var(--go);box-shadow:0 0 8px var(--go);animation:pulse 1.1s infinite}
  .q-done{background:var(--ok)} .q-err{background:var(--err)} .q-queued{background:var(--muted-2)}
  @keyframes pulse{50%{opacity:.35}}
  .q-item .q-task{flex:1;font-size:13.5px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .q-item .q-meta{font-family:"IBM Plex Mono";font-size:11px;color:var(--muted)}

  /* samples */
  .samples{max-width:900px;margin:0 auto;display:flex;flex-direction:column;gap:10px}
  .s-row{display:grid;grid-template-columns:14px 1fr auto auto;align-items:center;gap:14px;
    border:1px solid var(--line);border-radius:12px;background:var(--panel);padding:14px 16px;cursor:pointer}
  .s-row:hover{border-color:var(--line-2)}
  .s-row .s-out{width:10px;height:10px;border-radius:50%}
  .s-row h4{font-size:14.5px;font-weight:500}
  .s-row .s-files{font-family:"IBM Plex Mono";font-size:11px;color:var(--muted-2);margin-top:3px}
  .s-row .s-model{font-family:"IBM Plex Mono";font-size:12px;color:var(--muted);border:1px solid var(--line);padding:3px 9px;border-radius:999px}
  .s-row .s-time{font-family:"IBM Plex Mono";font-size:12px;color:var(--muted);min-width:120px;text-align:right}

  .empty{display:grid;place-items:center;height:100%;text-align:center;color:var(--muted)}
  .empty .e-card{max-width:380px}
  .empty h3{font-family:"Space Grotesk";font-size:18px;color:var(--text);margin-bottom:8px}
  .empty p{font-size:14px;line-height:1.6}
  .empty .go{margin:18px auto 0;display:inline-flex}

  .demo-banner{background:rgba(242,178,62,.10);border-bottom:1px solid rgba(242,178,62,.3);
    color:var(--go);font-family:"IBM Plex Mono";font-size:12px;padding:7px 22px;text-align:center;letter-spacing:.03em}

  ::-webkit-scrollbar{width:10px;height:10px}
  ::-webkit-scrollbar-thumb{background:var(--line-2);border-radius:99px;border:3px solid var(--bench)}
  *:focus-visible{outline:2px solid var(--go);outline-offset:2px}
  @media (prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important}}
</style>
</head>
<body>
<div class="app">
  <div id="demoBanner" class="demo-banner" style="display:none">
    Demo bench — simulated runs. Start <code>server.py</code> on the lab machine to drive the live digital twin.
  </div>

  <div class="topbar">
    <div class="brand" onclick="go('home')">
      <div class="mark"></div>
      <div><h1>Lab OS</h1><small>A2S2 Bench Console</small></div>
    </div>
    <div class="top-spacer"></div>
    <div id="conn" class="conn demo"><span class="dot"></span><span id="connText">Demo bench</span></div>
    <div id="clock" class="clock">--:--</div>
  </div>

  <!-- signature: live bench status strip -->
  <div class="strip">
    <div class="strip-head">
      <span class="label">Bench layout · live state</span>
      <span class="state">arm <b id="armState">idle</b> · rail <b id="railState">0 mm</b></span>
    </div>
    <svg class="bench" id="benchSvg" viewBox="0 0 760 96" preserveAspectRatio="xMidYMid meet"></svg>
  </div>

  <div class="body" id="body"></div>
</div>

<script>
/* ============================================================
   Lab OS bench console — single-file kiosk shell.
   Talks to server.py when present (live mode); otherwise runs
   a clearly-labelled demo bench so the console is feelable alone.
============================================================ */

const SCENE = {
  cubes: [ {id:'red_cube',color:'red'}, {id:'green_cube',color:'green'}, {id:'blue_cube',color:'blue'} ],
  bins:  [ {id:'red_bin',color:'red'}, {id:'green_bin',color:'green'}, {id:'blue_bin',color:'blue'} ],
  racks: 2, tubesPerRack: 6
};

const STATE = {
  mode:'demo',          // 'live' | 'demo'
  view:'home',
  model:'haiku',
  jobs:[],              // {id, task, status, log[], outcome, model, started}
  sessions:[],
  busy:false,
  arm:'idle', rail:0
};

/* curated protocol library — built from the scene's own objects */
const LIBRARY = [
  {color:'red',   title:'Sort red cube',   task:'Put the red cube in the red bin',     desc:'Pick the red cube and place it in the matching bin.'},
  {color:'green', title:'Sort green cube', task:'Put the green cube in the green bin',  desc:'Pick the green cube and place it in the matching bin.'},
  {color:'blue',  title:'Sort blue cube',  task:'Put the blue cube in the blue bin',    desc:'Pick the blue cube and place it in the matching bin.'},
  {color:'amber', title:'Rack a tube',     task:'Place a Falcon tube into the first rack slot', desc:'Move a Falcon tube into an open slot on the rack.'},
  {color:'blue',  title:'Clear the bench', task:'Push the blue cube off the front edge of the bench', desc:'Demonstration push primitive — clears an object off the bench.'},
  {color:'green', title:'Wave',            task:'Wave goodbye',                          desc:'A quick liveness check — the arm waves.'}
];

/* ---------------- networking (graceful) ---------------- */
async function api(path, opts={}, timeout=4000){
  const ctrl = new AbortController();
  const t = setTimeout(()=>ctrl.abort(), timeout);
  try{
    const r = await fetch(path, {...opts, signal:ctrl.signal});
    clearTimeout(t);
    if(!r.ok) throw new Error('http '+r.status);
    return await r.json();
  }catch(e){ clearTimeout(t); throw e; }
}

async function boot(){
  try{
    const h = await api('/api/health', {}, 2500);
    STATE.mode = h.ready ? 'live' : 'demo';
    STATE._health = h;
  }catch{ STATE.mode='demo'; }
  setConn();
  try{ const lib = await api('/api/library'); if(Array.isArray(lib)&&lib.length) LIBRARY.splice(0,LIBRARY.length,...lib);}catch{}
  await refreshSessions();
  drawBench();
  render();
  tickClock(); setInterval(tickClock, 10000);
  if(STATE.mode==='live') setInterval(refreshSessions, 8000);
}

function setConn(){
  const c = document.getElementById('conn');
  const live = STATE.mode==='live';
  c.className = 'conn '+(live?'live':'demo');
  document.getElementById('connText').textContent = live ? 'Connected to bench' : 'Demo bench';
  document.getElementById('demoBanner').style.display = live ? 'none':'block';
}

async function refreshSessions(){
  if(STATE.mode!=='live') return;
  try{ STATE.sessions = await api('/api/sessions'); if(STATE.view==='samples') render(); }catch{}
}

/* ---------------- clock ---------------- */
function tickClock(){
  const d=new Date();
  document.getElementById('clock').textContent =
    String(d.getHours()).padStart(2,'0')+':'+String(d.getMinutes()).padStart(2,'0');
}

/* ---------------- bench schematic (signature) ---------------- */
function drawBench(highlight){
  const svg=document.getElementById('benchSvg');
  const COL={red:'#E5484D',green:'#46C26A',blue:'#4C8DFF'};
  let s='';
  // bench surface
  s+=`<rect x="8" y="20" width="744" height="58" rx="8" fill="#101a17" stroke="#243430"/>`;
  // rail (left) with arm carriage
  s+=`<rect x="20" y="30" width="120" height="38" rx="6" fill="#0c1411" stroke="#2E423C"/>`;
  s+=`<text x="80" y="16" fill="#566962" font-size="9" font-family="IBM Plex Mono" text-anchor="middle">RAIL · ARM</text>`;
  const carriageX = 30 + (STATE.rail/700)*96;
  s+=`<rect x="${carriageX}" y="36" width="26" height="26" rx="5" fill="#1B2A26" stroke="${STATE.arm==='idle'?'#2E423C':'#F2B23E'}" stroke-width="${STATE.arm==='idle'?1:2}"/>`;
  s+=`<circle cx="${carriageX+13}" cy="49" r="5" fill="${STATE.arm==='idle'?'#2E423C':'#F2B23E'}"/>`;
  // cubes
  SCENE.cubes.forEach((c,i)=>{
    const x=200+i*60, hot=highlight&&highlight.includes(c.color);
    s+=`<rect x="${x}" y="40" width="22" height="22" rx="4" fill="${COL[c.color]}" opacity="${hot?1:.85}"${hot?` stroke="#fff" stroke-width="1.5"`:''}/>`;
  });
  s+=`<text x="231" y="16" fill="#566962" font-size="9" font-family="IBM Plex Mono" text-anchor="middle">CUBES</text>`;
  // bins
  SCENE.bins.forEach((b,i)=>{
    const x=400+i*54;
    s+=`<rect x="${x}" y="38" width="34" height="26" rx="4" fill="none" stroke="${COL[b.color]}" stroke-width="2" opacity=".9"/>`;
  });
  s+=`<text x="454" y="16" fill="#566962" font-size="9" font-family="IBM Plex Mono" text-anchor="middle">BINS</text>`;
  // racks (2 × 4x2 → show as 2 grids of 6 tubes)
  for(let r=0;r<2;r++){
    const ox=590+r*78;
    s+=`<rect x="${ox-4}" y="32" width="64" height="34" rx="5" fill="#0c1411" stroke="#2E423C"/>`;
    for(let k=0;k<6;k++){
      const cx=ox+6+(k%3)*18, cy=42+Math.floor(k/3)*14;
      s+=`<circle cx="${cx}" cy="${cy}" r="4.5" fill="#1B2A26" stroke="#3a554d"/>`;
    }
  }
  s+=`<text x="668" y="16" fill="#566962" font-size="9" font-family="IBM Plex Mono" text-anchor="middle">TUBE RACKS</text>`;
  svg.innerHTML=s;
  document.getElementById('armState').textContent=STATE.arm;
  document.getElementById('railState').textContent=Math.round(STATE.rail)+' mm';
}

/* ---------------- view router ---------------- */
function go(view){ STATE.view=view; render(); }

function render(){
  const body=document.getElementById('body');
  if(STATE.view==='home') return body.innerHTML=homeView();
  body.innerHTML = viewShell(STATE.view);
  if(STATE.view==='ask') wireAsk();
  if(STATE.view==='watch') renderWatch();
}

function homeView(){
  const running = STATE.jobs.filter(j=>j.status==='running').length;
  const done = STATE.mode==='live'? STATE.sessions.length : STATE.jobs.filter(j=>j.status==='done').length;
  return `<div class="home">
    <div class="tile ask" onclick="go('ask')"><div class="accent"></div>
      <div class="ico">${ICO.ask}</div>
      <div><h2>Ask the bench</h2><p>Say what you want in plain words — "put the green cube in the green bin." The bench plans it and runs it.</p></div>
    </div>
    <div class="tile proto" onclick="go('library')"><div class="accent"></div>
      <span class="badge">${LIBRARY.length} ready</span>
      <div class="ico">${ICO.proto}</div>
      <div><h2>Run a protocol</h2><p>Pick a saved task from the library and press go. No typing needed.</p></div>
    </div>
    <div class="tile watch" onclick="go('watch')"><div class="accent"></div>
      <span class="badge">${running?running+' running':'idle'}</span>
      <div class="ico">${ICO.watch}</div>
      <div><h2>Watch</h2><p>See the live bench camera, what's running now, and the queue.</p></div>
    </div>
    <div class="tile samples" onclick="go('samples')"><div class="accent"></div>
      <span class="badge">${done} run${done===1?'':'s'}</span>
      <div class="ico">${ICO.samples}</div>
      <div><h2>My samples &amp; results</h2><p>Every run is saved with its plates, tubes and trajectory. Browse and replay.</p></div>
    </div>
  </div>`;
}

const TITLES={ask:['Ask the bench','say it in plain words'],library:['Run a protocol','pick a saved task'],
  watch:['Watch','live bench &amp; queue'],samples:['My samples & results','saved runs']};
function viewShell(v){
  const [t,s]=TITLES[v];
  return `<div class="view">
    <div class="view-head">
      <button class="back" onclick="go('home')" aria-label="Back to home">‹</button>
      <h2>${t}</h2><span class="sub">${s}</span>
    </div>
    <div class="view-body" id="vbody">${{ask:askView,library:libView,watch:watchView,samples:samplesView}[v]()}</div>
  </div>`;
}

/* ---- ASK ---- */
function askView(){
  return `<div class="ask-wrap">
    <div class="ask-box">
      <textarea id="task" placeholder="e.g. Put the green cube in the green bin"></textarea>
      <div class="ask-row">
        <div class="model-pick">
          <span class="chip ${STATE.model==='haiku'?'on':''}" data-m="haiku">haiku · fast</span>
          <span class="chip ${STATE.model==='sonnet'?'on':''}" data-m="sonnet">sonnet · careful</span>
        </div>
        <button class="go" id="runBtn">${ICO.play}<span>Run on the bench</span></button>
      </div>
    </div>
    <div class="suggest">
      <div class="label">Try one</div>
      <div class="sug-list">${LIBRARY.slice(0,5).map(l=>`<span class="sug" data-task="${esc(l.task)}">${esc(l.task)}</span>`).join('')}</div>
    </div>
  </div>`;
}
function wireAsk(){
  document.querySelectorAll('.chip').forEach(c=>c.onclick=()=>{STATE.model=c.dataset.m;render();});
  document.querySelectorAll('.sug').forEach(s=>s.onclick=()=>{document.getElementById('task').value=s.dataset.task;});
  document.getElementById('runBtn').onclick=()=>{
    const t=document.getElementById('task').value.trim();
    if(t) submit(t);
  };
}

/* ---- LIBRARY ---- */
function libView(){
  return `<div class="lib">${LIBRARY.map(l=>`
    <div class="card ${l.color}" data-task="${esc(l.task)}">
      <div class="swatch"></div>
      <h3>${esc(l.title)}</h3>
      <p>${esc(l.desc)}</p>
      <div class="run-tag">${esc(l.task)}</div>
    </div>`).join('')}</div>`;
}
/* delegate clicks for cards/suggestions after render */
document.addEventListener('click',e=>{
  const card=e.target.closest('.card[data-task]');
  if(card) submit(card.dataset.task);
});

/* ---- WATCH ---- */
function watchView(){ return `<div id="watchMount" style="height:100%"></div>`; }
function renderWatch(){
  const mount=document.getElementById('watchMount'); if(!mount) return;
  const active=STATE.jobs.find(j=>j.status==='running')||STATE.jobs[STATE.jobs.length-1];
  const logHtml = active? active.log.map(fmtLog).join('') :
    `<span class="l-dim">Bench idle. Ask the bench to do something, or run a protocol.</span>`;
  mount.innerHTML=`<div class="watch">
    <div class="cam">
      <div class="cam-bar"><span class="rec"></span> BENCH CAMERA ${STATE.mode==='live'?'· live':'· demo'}</div>
      <div class="cam-body"><div>
        <div class="big">${active&&active.status==='running'?'Recording run…':'Camera ready'}</div>
        <div style="font-size:13px;max-width:34ch">The live arm feed plugs in here. Until then, watch the bench layout strip above for arm and rail state.</div>
      </div></div>
    </div>
    <div style="display:flex;flex-direction:column;gap:14px;min-height:0">
      <div class="readout" style="flex:1;min-height:0">
        <div class="r-bar"><span>Run log</span><span>${active?esc(active.model):''}</span></div>
        <div class="log" id="logBox">${logHtml}</div>
      </div>
      <div class="queue">
        <div class="r-bar" style="border:0;padding-left:0">Queue</div>
        ${STATE.jobs.length? STATE.jobs.slice(-5).reverse().map(qItem).join('') :
          '<div class="q-item"><span class="q-dot q-queued"></span><span class="q-task" style="color:var(--muted)">Nothing queued</span></div>'}
      </div>
    </div>
  </div>`;
  const lb=document.getElementById('logBox'); if(lb) lb.scrollTop=lb.scrollHeight;
}
function qItem(j){
  const cls={running:'q-running',done:'q-done',error:'q-err',queued:'q-queued'}[j.status]||'q-queued';
  return `<div class="q-item"><span class="q-dot ${cls}"></span>
    <span class="q-task">${esc(j.task)}</span>
    <span class="q-meta">${j.status==='running'?'running':j.outcome||j.status}</span></div>`;
}
function fmtLog(line){
  const cls=line.k==='plan'?'l-plan':line.k==='ok'?'l-ok':line.k==='err'?'l-err':line.k==='dim'?'l-dim':'';
  return `<div class="${cls}">${esc(line.t)}</div>`;
}

/* ---- SAMPLES ---- */
function samplesView(){
  const rows = STATE.mode==='live'? STATE.sessions :
    STATE.jobs.filter(j=>j.status!=='queued').map(j=>({label:j.task,model:j.model,outcome:j.outcome||(j.status==='error'?'error':'ok'),
      time:j.started,files:['metadata.json','commands.jsonl','trajectory.h5','llm_session.jsonl']})).reverse();
  if(!rows.length) return `<div class="empty"><div class="e-card">
      <h3>No runs yet</h3><p>When you run something on the bench, it's saved here with its plates, tubes and full trajectory.</p>
      <button class="go" onclick="go('ask')">${ICO.play}<span>Ask the bench</span></button></div></div>`;
  const COL={ok:'var(--ok)',success:'var(--ok)',error:'var(--err)',failed:'var(--err)'};
  return `<div class="samples">${rows.map(r=>`
    <div class="s-row">
      <span class="s-out" style="background:${COL[(r.outcome||'').toLowerCase()]||'var(--muted-2)'}"></span>
      <div><h4>${esc(r.label||'Untitled run')}</h4>
        <div class="s-files">${(r.files||['trajectory.h5']).join(' · ')}</div></div>
      <span class="s-model">${esc(r.model||'—')}</span>
      <span class="s-time">${esc(fmtTime(r.time))}</span>
    </div>`).join('')}</div>`;
}

/* ---------------- run a task (live or demo) ---------------- */
async function submit(task){
  if(STATE.busy) return;
  STATE.busy=true;
  const job={id:'j'+Date.now(), task, model:STATE.model, status:'running', log:[], started:new Date().toISOString(), outcome:null};
  STATE.jobs.push(job);
  go('watch');
  if(STATE.mode==='live') await runLive(job); else await runDemo(job);
  STATE.busy=false;
  STATE.arm='idle'; drawBench(); 
  if(STATE.view==='watch') renderWatch();
  refreshSessions();
}

async function runLive(job){
  try{
    const res=await api('/api/run',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({task:job.task,model:job.model})}, 6000);
    const id=res.job_id;
    // poll
    while(true){
      await sleep(900);
      let st; try{ st=await api('/api/job/'+id,{},5000);}catch{ break; }
      job.log = (st.log||[]).map(coerceLine);
      if(st.arm!=null){STATE.arm=st.arm;} if(st.rail!=null){STATE.rail=st.rail;}
      animateFromTask(job.task);
      if(STATE.view==='watch') renderWatch();
      if(st.status==='done'||st.status==='error'){
        job.status=st.status; job.outcome=st.outcome||(st.status==='error'?'error':'ok'); break;
      }
    }
  }catch(e){
    job.status='error'; job.outcome='error';
    job.log.push({k:'err',t:'Could not reach the bench. Is server.py running on the lab machine?'});
  }
}

/* demo: plausible plan/dispatch so the console is feelable without the sim */
async function runDemo(job){
  const color=(job.task.match(/red|green|blue/i)||['blue'])[0].toLowerCase();
  const steps=[
    {k:'dim',t:`$ run_task "${job.task}" --model ${job.model}`},
    {k:'',t:'Planning task…'},
    {k:'plan',t:`plan: locate ${color}_cube → grasp → carry → release over ${color}_bin`},
    {k:'',t:'Dispatching to bench…'},
    {k:'',t:`move_rail → ${color} zone`,_arm:'moving',_rail:color==='red'?120:color==='green'?180:240},
    {k:'',t:`pick_object(${color}_cube)`,_arm:`grasp ${color}`},
    {k:'',t:`place_object(${color}_bin)`,_arm:`place ${color}`},
    {k:'ok',t:'✓ task complete · outcome: success'},
    {k:'dim',t:'saved → recordings/'+ts()+'_session_'+Math.floor(Math.random()*900+100)+'/'}
  ];
  for(const st of steps){
    await sleep(620+Math.random()*420);
    job.log.push({k:st.k,t:st.t});
    if(st._arm){STATE.arm=st._arm;} if(st._rail!=null){STATE.rail=st._rail;}
    drawBench(color); if(STATE.view==='watch') renderWatch();
  }
  job.status='done'; job.outcome='success';
}

function animateFromTask(task){
  const color=(task.match(/red|green|blue/i)||[''])[0].toLowerCase();
  if(color) drawBench(color);
}

/* ---------------- helpers ---------------- */
function coerceLine(l){ if(typeof l==='string'){ 
  const k=/✓|success|complete/i.test(l)?'ok':/error|fail|traceback/i.test(l)?'err':/plan|locate|grasp/i.test(l)?'plan':'';
  return {k,t:l}; } return l; }
function fmtTime(iso){ if(!iso) return '—'; const d=new Date(iso); if(isNaN(d)) return String(iso);
  return d.toLocaleDateString(undefined,{month:'short',day:'numeric'})+' '+String(d.getHours()).padStart(2,'0')+':'+String(d.getMinutes()).padStart(2,'0'); }
function ts(){ const d=new Date(); return d.toISOString().slice(0,19).replace(/[-:T]/g,''); }
function esc(s){ return String(s).replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m])); }
function sleep(ms){ return new Promise(r=>setTimeout(r,ms)); }

const ICO={
  ask:`<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="#F2B23E" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M21 11.5a8.4 8.4 0 0 1-8.5 8.5 8.6 8.6 0 0 1-3.9-.9L3 21l1.9-5.6A8.5 8.5 0 1 1 21 11.5z"/></svg>`,
  proto:`<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="#46C26A" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="4" y="3" width="16" height="18" rx="2"/><path d="M8 8h8M8 12h8M8 16h5"/></svg>`,
  watch:`<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="#4C8DFF" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7-10-7-10-7z"/><circle cx="12" cy="12" r="3"/></svg>`,
  samples:`<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="#E5484D" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M9 3v7l-5 8a2 2 0 0 0 1.7 3h12.6a2 2 0 0 0 1.7-3l-5-8V3"/><path d="M7.5 3h9"/></svg>`,
  play:`<svg width="16" height="16" viewBox="0 0 24 24" fill="#1A1305"><path d="M8 5v14l11-7z"/></svg>`
};

boot();
</script>
</body>
</html>

```

---

## Appendix B — reference bridge: `server.py` (verbatim)

Tested, stdlib-only. Drop it in as-is.

```python
#!/usr/bin/env python3
"""
Lab OS — bench console server (zero extra dependencies, stdlib only).

Serves the kiosk shell (index.html) and bridges it to the xArm6 digital twin:
  - POST /api/run        -> spawns `python scripts/run_task.py "<task>" --model <m>`
  - GET  /api/job/<id>   -> live status + log for a running/finished task
  - GET  /api/sessions   -> parsed metadata for every saved run under recordings/
  - GET  /api/library    -> curated protocol list (overrides the front-end default)
  - GET  /api/health     -> repo found? api key present? -> drives live/demo badge

Run on the lab machine that has the `xarm6sim` conda env active:
    conda activate xarm6sim
    python lab_os_shell/server.py            # then open http://localhost:8770

Put it in kiosk mode (boot-to-console) with, e.g.:
    chromium --kiosk --app=http://localhost:8770
"""

import json, os, re, subprocess, threading, time, uuid, sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# ---------------------------------------------------------------- config
HERE = Path(__file__).resolve().parent
REPO = HERE.parent                                    # repo root (shell lives in repo/lab_os_shell)
LLM_DIR = REPO / "xarm6_rail_digital_twin_llm_v5" / "xarm_lab_twin"
PORT = int(os.environ.get("LAB_OS_PORT", "8770"))

# The exact command used to run a task. Edit here if your CLI differs.
def task_cmd(task: str, model: str):
    py = os.environ.get("LAB_OS_PYTHON", sys.executable or "python")
    return [py, "scripts/run_task.py", task, "--model", model]

LIBRARY = [
    {"color": "red",   "title": "Sort red cube",   "task": "Put the red cube in the red bin",
     "desc": "Pick the red cube and place it in the matching bin."},
    {"color": "green", "title": "Sort green cube", "task": "Put the green cube in the green bin",
     "desc": "Pick the green cube and place it in the matching bin."},
    {"color": "blue",  "title": "Sort blue cube",  "task": "Put the blue cube in the blue bin",
     "desc": "Pick the blue cube and place it in the matching bin."},
    {"color": "amber", "title": "Rack a tube",     "task": "Place a Falcon tube into the first rack slot",
     "desc": "Move a Falcon tube into an open slot on the rack."},
    {"color": "blue",  "title": "Clear the bench", "task": "Push the blue cube off the front edge of the bench",
     "desc": "Demonstration push primitive — clears an object off the bench."},
    {"color": "green", "title": "Wave",            "task": "Wave goodbye",
     "desc": "A quick liveness check — the arm waves."},
]

# ---------------------------------------------------------------- job store
JOBS = {}          # id -> dict(status, log[], outcome, model, task, session_dir)
JOBS_LOCK = threading.Lock()

ARM_HINTS = re.compile(r"(pick|grasp|place|push|move|wave|rail)", re.I)
OK_HINTS  = re.compile(r"(✓|success|complete|done)", re.I)
ERR_HINTS = re.compile(r"(error|fail|traceback|exception)", re.I)
SESS_RE   = re.compile(r"recordings[/\\]([0-9A-Za-z_\-]+session[0-9A-Za-z_\-]*)")

def _run_job(job_id, task, model):
    with JOBS_LOCK:
        j = JOBS[job_id]
        j["status"] = "running"
    cmd = task_cmd(task, model)
    try:
        proc = subprocess.Popen(
            cmd, cwd=str(LLM_DIR),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1, env={**os.environ},
        )
    except FileNotFoundError as e:
        with JOBS_LOCK:
            j = JOBS[job_id]; j["status"] = "error"; j["outcome"] = "error"
            j["log"].append({"k": "err", "t": f"Could not start task: {e}"})
        return

    for raw in iter(proc.stdout.readline, ""):
        line = raw.rstrip("\n")
        if not line:
            continue
        k = "ok" if OK_HINTS.search(line) else "err" if ERR_HINTS.search(line) else \
            "plan" if "plan" in line.lower() else ""
        with JOBS_LOCK:
            j = JOBS[job_id]
            j["log"].append({"k": k, "t": line})
            if ARM_HINTS.search(line):
                j["arm"] = ARM_HINTS.search(line).group(1).lower()
            m = SESS_RE.search(line)
            if m:
                j["session_dir"] = m.group(0)
            if len(j["log"]) > 400:
                j["log"] = j["log"][-400:]
    proc.stdout.close()
    rc = proc.wait()
    with JOBS_LOCK:
        j = JOBS[job_id]
        j["arm"] = "idle"
        j["status"] = "done" if rc == 0 else "error"
        j["outcome"] = "success" if rc == 0 else "error"

def start_job(task, model):
    job_id = uuid.uuid4().hex[:12]
    with JOBS_LOCK:
        JOBS[job_id] = {"status": "queued", "log": [], "outcome": None,
                        "model": model, "task": task, "arm": "idle", "session_dir": None}
    threading.Thread(target=_run_job, args=(job_id, task, model), daemon=True).start()
    return job_id

# ---------------------------------------------------------------- sessions
def list_sessions():
    out = []
    roots = [LLM_DIR / "recordings",
             REPO / "xarm6_rail_sim_interactive_basic_v5" / "xarm_lab_twin" / "recordings"]
    for root in roots:
        if not root.is_dir():
            continue
        for d in sorted(root.iterdir(), reverse=True):
            if not d.is_dir():
                continue
            meta = d / "metadata.json"
            rec = {"label": d.name, "model": None, "outcome": None,
                   "time": None, "files": []}
            if meta.is_file():
                try:
                    m = json.loads(meta.read_text())
                    rec["label"] = m.get("task") or m.get("label") or d.name
                    rec["model"] = m.get("model")
                    rec["outcome"] = (m.get("outcome") or m.get("result") or "").lower() or None
                    rec["time"] = m.get("timestamp") or m.get("time") or m.get("started")
                except Exception:
                    pass
            rec["files"] = sorted(p.name for p in d.iterdir() if p.is_file())[:6]
            if not rec["time"]:
                rec["time"] = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(d.stat().st_mtime))
            out.append(rec)
    return out[:80]

def health():
    return {
        "ready": LLM_DIR.is_dir(),
        "repo": str(REPO),
        "llm_dir": str(LLM_DIR),
        "llm_dir_found": LLM_DIR.is_dir(),
        "api_key_present": bool(os.environ.get("ANTHROPIC_API_KEY"))
                           or (LLM_DIR / ".env").is_file(),
    }

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

    def do_GET(self):
        p = self.path.split("?")[0]
        if p in ("/", "/index.html"):
            f = HERE / "index.html"
            return self._send(200, f.read_bytes(), "text/html; charset=utf-8")
        if p == "/api/health":
            return self._send(200, health())
        if p == "/api/library":
            return self._send(200, LIBRARY)
        if p == "/api/sessions":
            return self._send(200, list_sessions())
        if p.startswith("/api/job/"):
            jid = p.rsplit("/", 1)[-1]
            with JOBS_LOCK:
                j = JOBS.get(jid)
                j = dict(j) if j else None
            if not j:
                return self._send(404, {"error": "no such job"})
            return self._send(200, {"status": j["status"], "log": j["log"],
                                    "outcome": j["outcome"], "arm": j.get("arm"),
                                    "session_dir": j.get("session_dir")})
        return self._send(404, {"error": "not found"})

    def do_POST(self):
        p = self.path.split("?")[0]
        if p == "/api/run":
            n = int(self.headers.get("Content-Length", "0") or 0)
            try:
                body = json.loads(self.rfile.read(n) or b"{}")
            except Exception:
                return self._send(400, {"error": "bad json"})
            task = (body.get("task") or "").strip()
            model = body.get("model") or "haiku"
            if not task:
                return self._send(400, {"error": "empty task"})
            if not LLM_DIR.is_dir():
                return self._send(409, {"error": "llm project not found", "llm_dir": str(LLM_DIR)})
            return self._send(200, {"job_id": start_job(task, model)})
        return self._send(404, {"error": "not found"})


def main():
    h = health()
    print("\n  Lab OS — bench console")
    print(f"  repo        : {h['repo']}")
    print(f"  llm project : {h['llm_dir']}  {'[found]' if h['llm_dir_found'] else '[MISSING — demo mode in browser]'}")
    print(f"  api key      : {'present' if h['api_key_present'] else 'not set'}")
    print(f"\n  open http://localhost:{PORT}\n")
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()

```
