#!/bin/bash
# A2S2-OS — open the standalone demo bench on macOS.
#
# Double-click this file in Finder (or run ./open-demo-macos.command in Terminal).
# It opens index.html directly in your default browser -> the DEMO bench:
# simulated runs, no server and no digital twin required. This is the
# "standalone shell" — the feel of the console with nothing to install.
#
# Works the same on Apple Silicon (M-series, ARM) and Intel Macs: it is just a
# browser opening a local HTML file, so the CPU architecture is irrelevant.
#
# For LIVE mode (driving the real xArm6 twin) you need the twin + server.py on
# the machine that runs the simulation — see README.md.

cd "$(dirname "$0")" || exit 1
open index.html
