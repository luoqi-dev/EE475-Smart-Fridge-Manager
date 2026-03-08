#!/usr/bin/env python3

from __future__ import annotations

import argparse
import glob
import json
import socket
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

import serial
from serial import SerialException

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from src.bridge.camera_capture import CameraCaptureManager
else:
    from .camera_capture import CameraCaptureManager


# Bridge constants
DEFAULT_BAUD = 115200
DEFAULT_SOCKET_PATH = "/tmp/fridge_bus.sock"
PORT_PATTERNS = (
    "/dev/ttyACM*",
    "/dev/ttyUSB*",
)
SESSION_ID_FORMAT = "session_%Y%m%d_%H%M%S"


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def sessions_dir() -> Path:
    return repo_root() / "data" / "sessions"


def auto_detect_port() -> Optional[str]:
    candidates = []
    for pattern in PORT_PATTERNS:
        candidates.extend(sorted(glob.glob(pattern)))
    return candidates[0] if candidates else None


def parse_event_line(line: str) -> Optional[dict]:
    if not line.startswith("FRIDGE,EVENT,"):
        return None

    parts = line.split(",")
    if len(parts) != 5:
        return None

    _, _, event_type, seq_part, t_ms_part = parts
    if event_type not in {"SESSION_START", "SESSION_END"}:
        return None
    if not seq_part.startswith("seq=") or not t_ms_part.startswith("t_ms="):
        return None

    try:
        seq = int(seq_part.split("=", 1)[1])
        t_ms = int(t_ms_part.split("=", 1)[1])
    except ValueError:
        return None

    return {"event_type": event_type, "seq": seq, "t_ms": t_ms}


def send_session_closed(socket_path: str, session_id: str) -> None:
    payload = {"type": "SESSION_CLOSED", "session_id": session_id}
    json_line = (json.dumps(payload, separators=(",", ":")) + "\n").encode("utf-8")

    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        client.connect(socket_path)
        client.sendall(json_line)
    finally:
        client.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Raspberry Pi bridge for fridge door events and integrated USB camera capture."
    )
    parser.add_argument(
        "--port",
        default=None,
        help="Serial port path. Defaults to the first /dev/ttyACM* or /dev/ttyUSB* found.",
    )
    parser.add_argument("--baud", type=int, default=DEFAULT_BAUD, help="Serial baud rate.")
    parser.add_argument(
        "--socket",
        default=DEFAULT_SOCKET_PATH,
        help="Unix domain socket path for SESSION_CLOSED messages.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    port = args.port or auto_detect_port()
    if not port:
        print("No serial port found. Checked /dev/ttyACM* and /dev/ttyUSB*.", file=sys.stderr)
        return 1

    try:
        ser = serial.Serial(port, args.baud, timeout=1)
    except SerialException as exc:
        print(f"Failed to open serial port {port}: {exc}", file=sys.stderr)
        return 1

    camera_manager = CameraCaptureManager(sessions_dir())
    active_session_id: Optional[str] = None

    print(f"[DOOR] listening on {port} @ {args.baud}", flush=True)

    try:
        with ser:
            while True:
                try:
                    raw = ser.readline()
                except SerialException as exc:
                    print(f"Serial read error: {exc}", file=sys.stderr)
                    return 1

                if not raw:
                    continue

                line = raw.decode("utf-8", errors="replace").strip()
                if not line:
                    continue

                event = parse_event_line(line)
                if event is None:
                    continue

                if event["event_type"] == "SESSION_START":
                    active_session_id = datetime.now().strftime(SESSION_ID_FORMAT)
                    print(f"[DOOR] SESSION_START {active_session_id}", flush=True)
                    try:
                        camera_manager.camera_start(active_session_id)
                    except RuntimeError as exc:
                        print(f"[CAM] {exc}", file=sys.stderr)
                    continue

                session_id = active_session_id or datetime.now().strftime(SESSION_ID_FORMAT)
                print(f"[DOOR] SESSION_END {session_id}", flush=True)
                camera_manager.camera_stop()
                active_session_id = None

                try:
                    send_session_closed(args.socket, session_id)
                    print(f"[BUS] sent SESSION_CLOSED {session_id}", flush=True)
                except OSError as exc:
                    print(f"[BUS] error for {args.socket}: {exc}", file=sys.stderr)
    finally:
        try:
            camera_manager.camera_stop()
        except Exception:
            pass

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
