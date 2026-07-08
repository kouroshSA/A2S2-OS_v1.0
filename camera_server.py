#!/usr/bin/env python3
"""A2S2-OS camera stream — a tiny MJPEG server for the console's live camera pane.

The OS bridge (server.py) runs in a plain-Python env with no camera libraries, so
this standalone process owns the camera and serves it over HTTP. The console
embeds <img src="http://<host>:CAMERA_STREAM_PORT/stream">.

Only one process can open an Intel RealSense at a time, so this opens the camera
**on demand** (first /stream client) and releases it a few seconds after the last
client disconnects — leaving the device free for realsense-viewer or a grasp run
when nobody is watching the console. If the camera can't be opened (missing or in
use by another process) it serves a labelled placeholder frame instead of failing.

Run in an env that has pyrealsense2 + opencv (e.g. `conda run -n ufactory_vision
python camera_server.py`). Env knobs: CAMERA_STREAM_PORT (default 8771),
CAMERA_STREAM_WIDTH/HEIGHT/FPS.
"""
from __future__ import annotations

import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import cv2
import numpy as np

PORT = int(os.environ.get("CAMERA_STREAM_PORT", "8771"))
WIDTH = int(os.environ.get("CAMERA_STREAM_WIDTH", "640"))
HEIGHT = int(os.environ.get("CAMERA_STREAM_HEIGHT", "480"))
FPS = int(os.environ.get("CAMERA_STREAM_FPS", "15"))
LINGER_S = 4.0                      # keep the camera open this long after last client


def _placeholder(msg: str) -> bytes:
    img = np.zeros((HEIGHT, WIDTH, 3), np.uint8)
    img[:] = (20, 22, 24)
    for i, line in enumerate(msg.split("\n")):
        cv2.putText(img, line, (24, 40 + i * 34), cv2.FONT_HERSHEY_SIMPLEX,
                    0.7, (126, 147, 140), 1, cv2.LINE_AA)
    ok, buf = cv2.imencode(".jpg", img)
    return buf.tobytes()


class Camera:
    """Shared RealSense capture with on-demand open/close and a latest-frame buffer."""

    def __init__(self):
        self._lock = threading.Lock()
        self._clients = 0
        self._latest = _placeholder("Starting camera...")
        self._run = False
        self._last_client_ts = 0.0
        self._thread = None

    def acquire(self):
        with self._lock:
            self._clients += 1
            if not self._run:
                self._run = True
                self._thread = threading.Thread(target=self._loop, daemon=True)
                self._thread.start()

    def release(self):
        with self._lock:
            self._clients = max(0, self._clients - 1)
            self._last_client_ts = time.monotonic()

    def latest(self) -> bytes:
        return self._latest

    def _loop(self):
        pipe = None
        try:
            import pyrealsense2 as rs
            try:
                pipe = rs.pipeline()
                cfg = rs.config()
                cfg.enable_stream(rs.stream.color, WIDTH, HEIGHT, rs.format.bgr8, FPS)
                pipe.start(cfg)
            except Exception as e:                       # busy / missing
                self._latest = _placeholder("Camera unavailable\n(in use or not connected)")
                pipe = None
            while True:
                with self._lock:
                    if self._clients == 0 and (time.monotonic() - self._last_client_ts) > LINGER_S:
                        self._run = False
                        break
                if pipe is not None:
                    try:
                        frames = pipe.wait_for_frames(1000)
                        color = np.asanyarray(frames.get_color_frame().get_data())
                        ok, buf = cv2.imencode(".jpg", color, [cv2.IMWRITE_JPEG_QUALITY, 80])
                        if ok:
                            self._latest = buf.tobytes()
                    except Exception:
                        time.sleep(0.1)
                else:
                    self._latest = _placeholder("Camera unavailable\n(in use or not connected)")
                    time.sleep(0.5)
        finally:
            if pipe is not None:
                try:
                    pipe.stop()
                except Exception:
                    pass
            with self._lock:
                self._run = False


CAM = Camera()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")

    def do_GET(self):
        if self.path.startswith("/stream"):
            return self._stream()
        if self.path.startswith("/snapshot"):
            CAM.acquire()
            try:
                time.sleep(0.3)
                frame = CAM.latest()
            finally:
                CAM.release()
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(frame)))
            self._cors()
            self.end_headers()
            self.wfile.write(frame)
            return
        # info
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self._cors()
        self.end_headers()
        self.wfile.write(b"A2S2-OS camera stream. GET /stream (MJPEG) or /snapshot (JPEG).")

    def _stream(self):
        CAM.acquire()
        self.send_response(200)
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
        self._cors()
        self.end_headers()
        try:
            period = 1.0 / max(1, FPS)
            while True:
                frame = CAM.latest()
                self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\n"
                                 b"Content-Length: " + str(len(frame)).encode() + b"\r\n\r\n")
                self.wfile.write(frame)
                self.wfile.write(b"\r\n")
                time.sleep(period)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            CAM.release()


def main():
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"  A2S2-OS camera stream on http://localhost:{PORT}/stream  ({WIDTH}x{HEIGHT}@{FPS})")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
