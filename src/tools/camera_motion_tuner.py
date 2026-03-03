#!/usr/bin/env python3
"""
Standalone Raspberry Pi camera motion sensitivity tuner.

Usage:
  source venv/bin/activate
  python src/tools/camera_motion_tuner.py

Controls:
  - Press 'q' to quit
  - Press ESC to quit

Notes:
  - This tool requires a desktop/display session because it uses cv2.imshow().
"""

from __future__ import annotations

import time
from collections import deque
from typing import Optional

import cv2
import numpy as np


# Camera and motion constants
CAMERA_INDEX = 0
FRAME_WIDTH = 640
FRAME_HEIGHT = 480
DETECTION_FPS = 10.0
RECORDING_FPS = 30.0
BUFFER_SIZE = 10
WARMUP_SECONDS = 0.5
INACTIVITY_TIMEOUT_SECONDS = 1.0
DELTA_THRESH = 25
MOTION_PIXEL_THRESHOLD = 10000
GAUSSIAN_KERNEL = (5, 5)
FOURCC = "MJPG"
WINDOW_NAME = "Camera Motion Tuner"
SAVE_FRAMES = False

# Overlay constants
TEXT_ORIGIN_X = 10
TEXT_ORIGIN_Y = 24
TEXT_LINE_HEIGHT = 24
TEXT_FONT = cv2.FONT_HERSHEY_SIMPLEX
TEXT_SCALE = 0.65
TEXT_THICKNESS = 2
TEXT_COLOR = (0, 255, 0)
TEXT_SHADOW_COLOR = (0, 0, 0)


MODE_DETECT = "DETECT"
MODE_RECORD = "RECORD"


def configure_camera() -> cv2.VideoCapture:
    cap = cv2.VideoCapture(CAMERA_INDEX, cv2.CAP_V4L2)
    if not cap.isOpened():
        raise RuntimeError(f"Failed to open camera index {CAMERA_INDEX}")

    cap.set(cv2.CAP_PROP_FRAME_WIDTH, FRAME_WIDTH)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, FRAME_HEIGHT)
    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*FOURCC))
    return cap


def detect_motion(
    frame: np.ndarray,
    prev_blurred: Optional[np.ndarray],
) -> tuple[bool, int, np.ndarray]:
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(gray, GAUSSIAN_KERNEL, 0)

    if prev_blurred is None:
        return False, 0, blurred

    frame_delta = cv2.absdiff(prev_blurred, blurred)
    _, thresholded = cv2.threshold(frame_delta, DELTA_THRESH, 255, cv2.THRESH_BINARY)
    changed_pixels = int(cv2.countNonZero(thresholded))
    motion_detected = changed_pixels > MOTION_PIXEL_THRESHOLD
    return motion_detected, changed_pixels, blurred


def draw_text_block(frame: np.ndarray, lines: list[str]) -> None:
    y = TEXT_ORIGIN_Y
    for line in lines:
        cv2.putText(
            frame,
            line,
            (TEXT_ORIGIN_X + 1, y + 1),
            TEXT_FONT,
            TEXT_SCALE,
            TEXT_SHADOW_COLOR,
            TEXT_THICKNESS + 1,
            cv2.LINE_AA,
        )
        cv2.putText(
            frame,
            line,
            (TEXT_ORIGIN_X, y),
            TEXT_FONT,
            TEXT_SCALE,
            TEXT_COLOR,
            TEXT_THICKNESS,
            cv2.LINE_AA,
        )
        y += TEXT_LINE_HEIGHT


def main() -> int:
    cap = configure_camera()

    ring_buffer: deque[np.ndarray] = deque(maxlen=BUFFER_SIZE)
    prev_blurred: Optional[np.ndarray] = None
    mode = MODE_DETECT
    start_time = time.monotonic()
    last_motion_time = start_time
    last_loop_time = start_time
    fps_estimate = 0.0

    try:
        while True:
            loop_started = time.monotonic()
            target_fps = RECORDING_FPS if mode == MODE_RECORD else DETECTION_FPS
            target_interval = 1.0 / target_fps

            ok, frame = cap.read()
            if not ok:
                print("Camera read failed.")
                time.sleep(0.05)
                continue

            ring_buffer.append(frame.copy())
            motion_detected, changed_pixels, prev_blurred = detect_motion(frame, prev_blurred)

            now = time.monotonic()
            if now - start_time < WARMUP_SECONDS:
                motion_detected = False

            if mode == MODE_DETECT:
                if motion_detected:
                    mode = MODE_RECORD
                    last_motion_time = now
                    print("[CAM] motion triggered -> recording", flush=True)
            else:
                if motion_detected:
                    last_motion_time = now
                elif now - last_motion_time >= INACTIVITY_TIMEOUT_SECONDS:
                    mode = MODE_DETECT
                    print("[CAM] inactivity -> back to detect", flush=True)

            if SAVE_FRAMES:
                pass

            delta_t = now - last_loop_time
            if delta_t > 0:
                fps_estimate = 1.0 / delta_t
            last_loop_time = now

            overlay_lines = [
                f"MODE: {mode}",
                f"capture_target_fps: {target_fps:.1f}",
                f"capture_measured_fps: {fps_estimate:.1f}",
                f"changed_pixels: {changed_pixels}",
                f"threshold: {MOTION_PIXEL_THRESHOLD}",
                f"motion_detected: {motion_detected}",
                f"warmup_active: {now - start_time < WARMUP_SECONDS}",
                f"buffer_size: {len(ring_buffer)}/{BUFFER_SIZE}",
            ]
            draw_text_block(frame, overlay_lines)
            cv2.imshow(WINDOW_NAME, frame)

            key = cv2.waitKey(1) & 0xFF
            if key in (27, ord("q")):
                break

            elapsed = time.monotonic() - loop_started
            remaining = target_interval - elapsed
            if remaining > 0:
                time.sleep(remaining)
    finally:
        cap.release()
        cv2.destroyAllWindows()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
