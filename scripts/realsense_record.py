#!/usr/bin/env python3
"""Record a fixed-length RealSense RGB+Depth clip and save it as .webm.

Captures the same side-by-side view as realsense_live_view.py (RGB with a
center-depth readout | colorized depth over 0.10-1.00 m) for DURATION seconds,
then encodes to WebM (VP9, no audio). Real capture fps is measured and used for
the output so playback matches real time.

Usage: python scripts/realsense_record.py OUT.webm [DURATION_SECONDS]
Needs pyrealsense2 + opencv + ffmpeg. Only one process can open the camera.
"""
import os
import sys
import time
import tempfile
import subprocess
import numpy as np
import cv2
import pyrealsense2 as rs

OUT = sys.argv[1]
DUR = float(sys.argv[2]) if len(sys.argv) > 2 else 10.0
W, H, FPS = 1280, 720, 30
DMIN, DMAX = 100.0, 1000.0

pipe = rs.pipeline()
cfg = rs.config()
cfg.enable_stream(rs.stream.color, W, H, rs.format.bgr8, FPS)
cfg.enable_stream(rs.stream.depth, W, H, rs.format.z16, FPS)
prof = pipe.start(cfg)
align = rs.align(rs.stream.color)
scale = prof.get_device().first_depth_sensor().get_depth_scale()
cmap = getattr(cv2, "COLORMAP_TURBO", cv2.COLORMAP_JET)

cw = W * 2
tmp = tempfile.mktemp(suffix=".avi")
vw = cv2.VideoWriter(tmp, cv2.VideoWriter_fourcc(*"MJPG"), FPS, (cw, H))

for _ in range(15):                 # warm up auto-exposure
    align.process(pipe.wait_for_frames())

t0 = time.time()
n = 0
while time.time() - t0 < DUR:
    fr = align.process(pipe.wait_for_frames())
    c, dd = fr.get_color_frame(), fr.get_depth_frame()
    if not c or not dd:
        continue
    color = np.asanyarray(c.get_data())
    depth = np.asanyarray(dd.get_data())
    d = np.clip(depth.astype(np.float32), DMIN, DMAX)
    norm = ((d - DMIN) / (DMAX - DMIN) * 255.0).astype(np.uint8)
    dv = cv2.applyColorMap(norm, cmap)
    dv[depth == 0] = (0, 0, 0)
    h, w = depth.shape
    cd = depth[h // 2, w // 2] * scale
    cv2.circle(color, (w // 2, h // 2), 5, (0, 255, 0), -1)
    cv2.putText(color, f"center: {cd:.3f} m", (12, 34), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 255, 0), 2, cv2.LINE_AA)
    cv2.putText(color, "RGB", (12, h - 16), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2, cv2.LINE_AA)
    cv2.putText(dv, "DEPTH  0.10-1.00 m", (12, h - 16), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2, cv2.LINE_AA)
    vw.write(np.hstack([color, dv]))
    n += 1

vw.release()
pipe.stop()
el = time.time() - t0
fps_true = (n / el) if el > 0 else FPS

subprocess.run(
    ["ffmpeg", "-y", "-loglevel", "error", "-r", f"{fps_true:.3f}", "-i", tmp,
     "-c:v", "libvpx-vp9", "-b:v", "0", "-crf", "32", "-deadline", "good",
     "-cpu-used", "4", "-row-mt", "1", "-pix_fmt", "yuv420p", "-an", OUT],
    check=True)
os.remove(tmp)
print(f"captured {n} frames in {el:.1f}s ({fps_true:.1f} fps) -> {OUT}")
