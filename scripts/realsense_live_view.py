#!/usr/bin/env python3
"""Live Intel RealSense view via pyrealsense2 — RGB + aligned depth, side by side.

Shows the color stream (with a center-pixel depth readout) next to a colorized
depth map, updating live. Press 'q' (or close the window) to quit.

Run in an env with pyrealsense2 + opencv, e.g.:
  ~/anaconda3/envs/ufactory_vision/bin/python scripts/realsense_live_view.py
Only one process can open the RealSense at a time.
"""
import numpy as np
import cv2
import pyrealsense2 as rs

W, H, FPS = 1280, 720, 30
DEPTH_MIN_MM, DEPTH_MAX_MM = 100.0, 1000.0   # colorize this range (0.10-1.00 m)
WIN = "Intel RealSense D435i - RGB + Depth (pyrealsense2)   [q to quit]"


def main():
    pipe = rs.pipeline()
    cfg = rs.config()
    cfg.enable_stream(rs.stream.color, W, H, rs.format.bgr8, FPS)
    cfg.enable_stream(rs.stream.depth, W, H, rs.format.z16, FPS)
    profile = pipe.start(cfg)
    align = rs.align(rs.stream.color)
    depth_scale = profile.get_device().first_depth_sensor().get_depth_scale()

    cv2.namedWindow(WIN, cv2.WINDOW_NORMAL)
    cv2.resizeWindow(WIN, 1600, 720)
    try:
        while True:
            frames = align.process(pipe.wait_for_frames())
            cframe = frames.get_color_frame()
            dframe = frames.get_depth_frame()
            if not cframe or not dframe:
                continue
            color = np.asanyarray(cframe.get_data())
            depth = np.asanyarray(dframe.get_data())

            # colorized depth mapped across a fixed 0.10-1.00 m window so the
            # full multicolor range spans that band (near=blue ... far=red).
            d = np.clip(depth.astype(np.float32), DEPTH_MIN_MM, DEPTH_MAX_MM)
            norm = ((d - DEPTH_MIN_MM) / (DEPTH_MAX_MM - DEPTH_MIN_MM) * 255.0).astype(np.uint8)
            cmap = getattr(cv2, "COLORMAP_TURBO", cv2.COLORMAP_JET)
            depth_vis = cv2.applyColorMap(norm, cmap)
            depth_vis[depth == 0] = (0, 0, 0)   # no-data stays black

            # center-pixel depth readout on the color image
            h, w = depth.shape
            cd = depth[h // 2, w // 2] * depth_scale
            cv2.circle(color, (w // 2, h // 2), 5, (0, 255, 0), -1)
            cv2.putText(color, f"center: {cd:.3f} m", (12, 34),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 255, 0), 2, cv2.LINE_AA)
            cv2.putText(color, "RGB", (12, h - 16), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2, cv2.LINE_AA)
            cv2.putText(depth_vis, "DEPTH  0.10-1.00 m", (12, h - 16), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2, cv2.LINE_AA)

            combo = np.hstack([color, depth_vis])
            cv2.imshow(WIN, combo)
            if cv2.waitKey(1) & 0xFF == ord("q"):
                break
            if cv2.getWindowProperty(WIN, cv2.WND_PROP_VISIBLE) < 1:
                break
    finally:
        pipe.stop()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
