# A2S2-OS — Quick start

Get the bench console running in a few minutes. Two paths: **demo** (feel the
console, nothing to install) and **live** (drive the real xArm6 twin).

---

## A. Demo in 30 seconds (any computer)

The standalone shell needs no server, no Python, no twin.

- **macOS:** double-click `open-demo-macos.command` (or run `open index.html`).
- **Windows / Linux:** double-click `index.html`, or open it in any browser.

You get the **Demo bench** (amber banner, simulated runs). Works the same on
Apple Silicon and Intel — it's just a browser opening a local HTML file.

---

## B. Live — drive the real twin

### 1. Get the twin

Clone the xArm6 digital-twin repo and set up its environment as in its own
README:

```bash
git clone https://github.com/kouroshSA/xarm6-digital-twin-v5
# create the conda env it expects, and add your Anthropic API key to
# xarm6-digital-twin-v5/xarm6_rail_digital_twin_llm_v5/xarm_lab_twin/.env
```

This must be a machine that can run the simulation (MuJoCo + a display). On
Windows that means WSL/Ubuntu.

### 2. Get A2S2-OS, beside the twin

Place `A2S2-OS` next to the twin clone so it's found automatically:

```bash
git clone https://github.com/kouroshSA/A2S2-OS_v1.0 A2S2-OS
```

Resulting layout (the twin folder may be named anything — it's matched by
content, not name):

```
<parent>/
├── A2S2-OS/
└── xarm6-digital-twin-v5/
```

> Installing A2S2-OS somewhere *not* beside the twin? Either
> `cp config.example.json config.json` and set `twin_dir`, or run with
> `BENCH_TWIN_DIR=/path/to/.../xarm_lab_twin`. See README → *Pointing it at the twin*.

### 3. Run

```bash
conda activate xarm6sim          # the twin's env — important: tasks reuse this Python
cd A2S2-OS
python server.py                 # serves http://localhost:8770
```

The startup banner should print the twin path and `[found]`:

```
  twin project: /…/xarm6_rail_digital_twin_llm_v5/xarm_lab_twin  [found]
  found via   : clone: xarm6-digital-twin-v5
  api key     : present
```

If it instead says `[MISSING — demo mode in browser]`, the banner prints the
`git clone` command and how to point it at the twin.

### 4. Open it

Browse to <http://localhost:8770>. The badge should read **Connected to bench**
(no amber banner). Try **Run a protocol → Sort green cube**, or **Ask the
bench** with free text like *“Put the red cube in the red bin.”* Finished runs
appear under **My samples & results**.

### 5. (Optional) Kiosk

Boot-to-console, full screen:

```bash
# Linux
chromium --kiosk --app=http://localhost:8770
# macOS (Chrome)
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --kiosk --app=http://localhost:8770
```

Autostart `server.py` + the browser on login (systemd user service or desktop
autostart) and the console comes up on its own.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| Badge stays **Demo bench** with the server running | Twin not found — check the banner's `found via` line; set `twin_dir` in `config.json` or `BENCH_TWIN_DIR`. |
| `api key : not set` | Add `ANTHROPIC_API_KEY` to the twin's `.env` (or export it before launching). |
| A run hangs / nothing saved | Ensure you launched from the twin's conda env so `run_task.py` uses the right Python. |
| Port already in use | `A2S2_OS_PORT=8780 python server.py`. |

More detail — endpoints, app architecture, adding an app, license — is in
[`README.md`](README.md).
