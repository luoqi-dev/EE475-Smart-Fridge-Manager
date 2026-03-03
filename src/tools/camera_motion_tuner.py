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
  - GPIO backend order is: gpiozero -> RPi.GPIO.
  - On Raspberry Pi 5, prefer gpiozero/lgpio:
      sudo apt install python3-gpiozero python3-lgpio
  - If you use RPi.GPIO instead:
      sudo apt install python3-rpi.gpio
"""

from __future__ import annotations

import time
from collections import deque
from typing import Optional

import cv2
import numpy as np

try:
    from gpiozero import LED
except ImportError:
    LED = None  # type: ignore[assignment]

try:
    import RPi.GPIO as GPIO
except ImportError:
    GPIO = None  # type: ignore[assignment]


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

# GPIO LED constants
LED_PIN_BCM = 4
DETECT_BLINK_INTERVAL_SECONDS = 0.5
RECORD_BLINK_INTERVAL_SECONDS = 0.1

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


class LedController:
    def __init__(self) -> None:
        self.backend = "none"
        self._state = False
        self._gpio_led = None

    def setup(self) -> None:
        if LED is not None:
            try:
                self._gpio_led = LED(LED_PIN_BCM)
                self._gpio_led.off()
                self.backend = "gpiozero"
                print(f"[LED] backend=gpiozero pin=BCM{LED_PIN_BCM}", flush=True)
                return
            except Exception as exc:
                print(f"[LED] gpiozero init failed: {exc}", flush=True)

        if GPIO is not None:
            try:
                GPIO.setwarnings(False)
                GPIO.setmode(GPIO.BCM)
                GPIO.setup(LED_PIN_BCM, GPIO.OUT, initial=GPIO.LOW)
                self.backend = "RPi.GPIO"
                print(f"[LED] backend=RPi.GPIO pin=BCM{LED_PIN_BCM}", flush=True)
                return
            except RuntimeError as exc:
                raise SystemExit(
                    "RPi.GPIO failed to initialize. If you see 'cannot determine SOC peripheral "
                    "base address', install and use gpiozero/lgpio instead:\n"
                    "  sudo apt install python3-gpiozero python3-lgpio"
                ) from exc

        raise SystemExit(
            "No GPIO backend available. Install one of:\n"
            "  sudo apt install python3-gpiozero python3-lgpio\n"
            "  sudo apt install python3-rpi.gpio"
        )

    def set_state(self, state: bool) -> None:
        self._state = state
        if self.backend == "gpiozero" and self._gpio_led is not None:
            if state:
                self._gpio_led.on()
            else:
                self._gpio_led.off()
            return
        if self.backend == "RPi.GPIO":
            GPIO.output(LED_PIN_BCM, GPIO.HIGH if state else GPIO.LOW)


    def cleanup(self) -> None:
        self.set_state(False)
        if self.backend == "gpiozero" and self._gpio_led is not None:
            self._gpio_led.close()
        elif self.backend == "RPi.GPIO":
            GPIO.cleanup()


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


def update_led(
    mode: str,
    led_state: bool,
    next_toggle_time: float,
    led_controller: LedController,
) -> tuple[bool, float]:
    now = time.monotonic()
    blink_interval = (
        RECORD_BLINK_INTERVAL_SECONDS if mode == MODE_RECORD else DETECT_BLINK_INTERVAL_SECONDS
    )

    if now >= next_toggle_time:
        led_state = not led_state
        led_controller.set_state(led_state)
        next_toggle_time = now + blink_interval

    return led_state, next_toggle_time


def main() -> int:
    cap = configure_camera()
    led_controller = LedController()
    led_controller.setup()

    ring_buffer: deque[np.ndarray] = deque(maxlen=BUFFER_SIZE)
    prev_blurred: Optional[np.ndarray] = None
    mode = MODE_DETECT
    start_time = time.monotonic()
    last_motion_time = start_time
    last_loop_time = start_time
    fps_estimate = 0.0
    led_state = False
    next_toggle_time = start_time

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
                f"changed_pixels: {changed_pixels}",
                f"threshold: {MOTION_PIXEL_THRESHOLD}",
                f"motion_detected: {motion_detected}",
                f"fps: {fps_estimate:.1f}",
                f"warmup_active: {now - start_time < WARMUP_SECONDS}",
                f"buffer_size: {len(ring_buffer)}/{BUFFER_SIZE}",
            ]
            draw_text_block(frame, overlay_lines)
            cv2.imshow(WINDOW_NAME, frame)

            led_state, next_toggle_time = update_led(
                mode,
                led_state,
                next_toggle_time,
                led_controller,
            )

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
        led_controller.cleanup()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
