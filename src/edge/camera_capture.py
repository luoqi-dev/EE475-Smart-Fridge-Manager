#!/usr/bin/env python3

from __future__ import annotations

import threading
import time
from collections import deque
from pathlib import Path
from typing import Optional

import cv2
import numpy as np


# Camera and motion constants
DEBUG = False
CAMERA_INDEX = 0
FRAME_WIDTH = 640
FRAME_HEIGHT = 480
DETECTION_FPS = 10.0
RECORDING_FPS = 30.0
BUFFER_SIZE = 10
STARTUP_IGNORE_SECONDS = 1.0
INACTIVITY_TIMEOUT_SECONDS = 1.0
DELTA_THRESH = 25
MOTION_PIXEL_THRESHOLD = 20000
GAUSSIAN_KERNEL = (5, 5)
JPEG_QUALITY = 90
FOURCC = "MJPG"


class CameraCaptureManager:
    def __init__(self, sessions_dir: Path) -> None:
        self._sessions_dir = sessions_dir
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._active_session_id: Optional[str] = None

    def camera_start(self, session_id: str) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                raise RuntimeError("camera capture already running")

            self._sessions_dir.mkdir(parents=True, exist_ok=True)
            (self._sessions_dir / session_id).mkdir(parents=True, exist_ok=True)
            self._stop_event.clear()
            self._active_session_id = session_id
            self._thread = threading.Thread(
                target=self._capture_loop,
                args=(session_id,),
                name=f"camera-{session_id}",
                daemon=True,
            )
            self._thread.start()

    def camera_stop(self) -> None:
        thread: Optional[threading.Thread]
        session_id: Optional[str]
        with self._lock:
            thread = self._thread
            session_id = self._active_session_id
            self._stop_event.set()

        if thread is not None:
            thread.join()

        with self._lock:
            self._thread = None
            self._active_session_id = None

        if session_id is not None:
            print(f"[CAM] stopped session {session_id}", flush=True)

    def _capture_loop(self, session_id: str) -> None:
        cap: Optional[cv2.VideoCapture] = None
        ring_buffer: deque[np.ndarray] = deque(maxlen=BUFFER_SIZE)
        prev_blurred: Optional[np.ndarray] = None
        recording = False
        frame_index = 0
        last_motion_time = 0.0
        session_start_time = time.monotonic()
        frames_dir = self._sessions_dir / session_id / "frames"

        try:
            cap = cv2.VideoCapture(CAMERA_INDEX, cv2.CAP_V4L2)
            if not cap.isOpened():
                print(f"[CAM] failed to open camera for session {session_id}", flush=True)
                return

            cap.set(cv2.CAP_PROP_FRAME_WIDTH, FRAME_WIDTH)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, FRAME_HEIGHT)
            cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*FOURCC))

            print(f"[CAM] start session {session_id}", flush=True)

            while not self._stop_event.is_set():
                loop_started = time.monotonic()
                target_interval = 1.0 / (RECORDING_FPS if recording else DETECTION_FPS)

                ok, frame = cap.read()
                if not ok:
                    if DEBUG:
                        print("[CAM] frame read failed", flush=True)
                    time.sleep(0.05)
                    continue

                ring_buffer.append(frame.copy())
                motion_detected, prev_blurred = self._detect_motion(frame, prev_blurred)
                now = time.monotonic()
                ignore_motion = (now - session_start_time) < STARTUP_IGNORE_SECONDS
                motion_for_transition = motion_detected and (not ignore_motion)

                if recording:
                    self._write_frame(frames_dir, frame_index, frame)
                    frame_index += 1

                    if motion_for_transition:
                        last_motion_time = now
                    elif now - last_motion_time >= INACTIVITY_TIMEOUT_SECONDS:
                        recording = False
                        print("[CAM] inactivity -> back to detect", flush=True)
                else:
                    if motion_for_transition:
                        frames_dir.mkdir(parents=True, exist_ok=True)
                        for buffered_frame in ring_buffer:
                            self._write_frame(frames_dir, frame_index, buffered_frame)
                            frame_index += 1
                        recording = True
                        last_motion_time = now
                        print("[CAM] motion triggered -> recording", flush=True)

                if DEBUG:
                    mode = "record" if recording else "detect"
                    print(
                        f"[CAM] mode={mode} frame_index={frame_index} "
                        f"motion={motion_detected} ignore_motion={ignore_motion}",
                        flush=True,
                    )

                elapsed = time.monotonic() - loop_started
                remaining = target_interval - elapsed
                if remaining > 0:
                    self._stop_event.wait(remaining)
        finally:
            ring_buffer.clear()
            if cap is not None:
                cap.release()

    def _detect_motion(
        self,
        frame: np.ndarray,
        prev_blurred: Optional[np.ndarray],
    ) -> tuple[bool, np.ndarray]:
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        blurred = cv2.GaussianBlur(gray, GAUSSIAN_KERNEL, 0)

        if prev_blurred is None:
            return False, blurred

        frame_delta = cv2.absdiff(prev_blurred, blurred)
        _, thresholded = cv2.threshold(frame_delta, DELTA_THRESH, 255, cv2.THRESH_BINARY)
        changed_pixels = cv2.countNonZero(thresholded)
        return changed_pixels > MOTION_PIXEL_THRESHOLD, blurred

    def _write_frame(self, frames_dir: Path, frame_index: int, frame: np.ndarray) -> None:
        output_path = frames_dir / f"{frame_index:03d}.jpg"
        cv2.imwrite(str(output_path), frame, [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_QUALITY])
