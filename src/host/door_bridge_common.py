#!/usr/bin/env python3

import argparse
import glob
import json
import socket
import sys
from datetime import datetime
from typing import Iterable, Optional

import serial
from serial import SerialException


def auto_detect_port(patterns: Iterable[str]) -> Optional[str]:
    candidates = []
    for pattern in patterns:
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


def parse_args(description: str, port_help: str) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--port", default=None, help=port_help)
    parser.add_argument("--baud", type=int, default=115200, help="Serial baud rate.")
    parser.add_argument(
        "--socket",
        default="/tmp/fridge_bus.sock",
        help="Unix domain socket path for SESSION_CLOSED messages.",
    )
    return parser.parse_args()


def run_bridge(args: argparse.Namespace, auto_patterns: Iterable[str], missing_port_message: str) -> int:
    port = args.port or auto_detect_port(auto_patterns)
    if not port:
        print(missing_port_message, file=sys.stderr)
        return 1

    try:
        ser = serial.Serial(port, args.baud, timeout=1)
    except SerialException as exc:
        print(f"Failed to open serial port {port}: {exc}", file=sys.stderr)
        return 1

    print(f"[DOOR] listening on {port} @ {args.baud}", flush=True)

    active_session_id: Optional[str] = None

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

            event_type = event["event_type"]
            seq = event["seq"]
            t_ms = event["t_ms"]
            print(f"[DOOR] {event_type} seq={seq} t_ms={t_ms}", flush=True)

            if event_type == "SESSION_START":
                active_session_id = "session_" + datetime.now().strftime("%Y%m%d_%H%M%S")
                print(f"[DOOR] active session_id={active_session_id}", flush=True)
                print("camera on", flush=True)
                continue

            print("camera off", flush=True)
            session_id = active_session_id or ("session_" + datetime.now().strftime("%Y%m%d_%H%M%S"))
            active_session_id = None

            try:
                send_session_closed(args.socket, session_id)
            except OSError as exc:
                print(f"Socket error for {args.socket}: {exc}", file=sys.stderr)

    return 0
