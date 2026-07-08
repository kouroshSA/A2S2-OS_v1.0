#!/usr/bin/env python3
"""Live GGCNN grasp preview for uFactory vision — CAMERA ONLY, no robot.

Runs uFactory's GGCNN model continuously on the RealSense feed and shows, side by
side, the RGB view and the grasp-quality heatmap with the predicted grasp marked
(the same visualization the real grasp demo uses, minus the arm). Watch it pick
grasp points on whatever is under the camera. Press 'q' in the window to quit.

Run in the ufactory_vision env (torch + pyrealsense2):
  conda run -n ufactory_vision python scripts/live_ggcnn_preview.py
"""
import os, sys, time, math
import numpy as np, cv2
from queue import Queue

DEMO = "/home/ksa/Models/ufactory_vision/ggcnn_grasping_demo"
sys.path.append(DEMO)
from camera.rs_camera import RealSenseCamera
from camera.utils import get_combined_img
from grasp.ggcnn_torch import TorchGGCNN

WIN = "uFactory Vision - GGCNN grasp preview (no arm)  [press q to quit]"


def draw_grasp(img, center, ang, width):
    """Overlay the predicted grasp on the RGB crop: an amber opening line between
    two green gripper plates (perpendicular to it), at the grasp angle/width."""
    cx, cy = int(center[0]), int(center[1])
    w = float(width)
    if not np.isfinite(w):
        return
    w = max(12.0, min(220.0, w))
    dx, dy = math.cos(ang), math.sin(ang)          # opening direction (between fingers)
    px, py = -dy, dx                               # plate direction (perpendicular)
    half = w / 2.0
    plate = max(16.0, w * 0.4)
    f1 = (cx + dx * half, cy + dy * half)
    f2 = (cx - dx * half, cy - dy * half)
    cv2.line(img, (int(f1[0]), int(f1[1])), (int(f2[0]), int(f2[1])), (0, 200, 255), 2, cv2.LINE_AA)
    for f in (f1, f2):
        a = (int(f[0] + px * plate / 2), int(f[1] + py * plate / 2))
        b = (int(f[0] - px * plate / 2), int(f[1] - py * plate / 2))
        cv2.line(img, a, b, (0, 255, 0), 3, cv2.LINE_AA)
    cv2.circle(img, (cx, cy), 4, (0, 255, 0), -1, cv2.LINE_AA)


def main():
    cam = RealSenseCamera(width=640, height=480)
    ci, _ = cam.get_intrinsics(align=True)
    K = np.array([[ci.fx, 0, ci.ppx], [0, ci.fy, ci.ppy], [0, 0, 1]])
    cfg = {"MODEL_FILE": os.path.join(DEMO, "models", "ggcnn_epoch_23_cornell"),
           "OPEN_LOOP_HEIGHT": 340, "GGCNN_IN_THREAD": False, "DEPTH_CAM_K": K}
    ggcnn = TorchGGCNN(cfg, Queue(1), Queue(1))
    time.sleep(1)
    cv2.namedWindow(WIN, cv2.WINDOW_NORMAL)
    cv2.resizeWindow(WIN, 1280, 480)
    try:
        while True:
            color, depth = cam.get_images(align=True)
            imh, imw = depth.shape
            cs = min(imh, imw); yi = (imh - cs) // 2; xi = (imw - cs) // 2
            color_crop = color[yi:yi + cs, xi:xi + cs, :].copy()
            grasp_img, result = ggcnn.get_grasp_img(depth, K, 0.4)   # robot_z dummy
            if result is not None:                      # overlay the grasp on the RGB
                center = (ggcnn.prev_mp[1], ggcnn.prev_mp[0])   # (x, y) in crop pixels
                draw_grasp(color_crop, center, result[3], result[4])
                cv2.putText(color_crop, "grasp  %.0f px  @ %.0f deg" % (result[4], math.degrees(result[3])),
                            (10, cs - 14), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2, cv2.LINE_AA)
            combined = get_combined_img(color_crop, grasp_img)
            cv2.imshow(WIN, combined)
            if cv2.waitKey(1) & 0xFF == ord("q"):
                break
            # window closed by the user (X button)
            if cv2.getWindowProperty(WIN, cv2.WND_PROP_VISIBLE) < 1:
                break
    finally:
        cam.stop()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
