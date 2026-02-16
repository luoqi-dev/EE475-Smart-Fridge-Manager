from __future__ import annotations

import json
import os
import socket
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

# Local imports (your repo structure)
from loader import (
    load_and_validate_vision_json,
    MissingFieldError,
    TypeValidationError,
    ValueValidationError,
)
from vision_to_events import infer_db_events_from_vision_session, insert_events


# ----------------------------
# Config
# ----------------------------

DEFAULT_SOCKET_PATH = "/tmp/fridge_bus.sock"
DEFAULT_DB_REL = Path("data/db/fridge.db")
DEFAULT_SESSIONS_REL = Path("data/sessions")

POLL_INTERVAL_SEC = 0.2


# ----------------------------
# Root directory abstraction
# ----------------------------

def get_project_root(explicit_root: str | Path | None = None) -> Path:
    """
    Resolve project root directory (EE475-Smart-Fridge-Manager).

    Priority:
      1) explicit_root
      2) ENV: FRIDGE_ROOT
      3) auto-detect upwards from __file__ (script) OR cwd (jupyter),
         until we find BOTH 'data/' and 'scr/' at the same level.

    Works in:
      - CLI python -m ...
      - Jupyter notebook (no __file__)
      - Raspberry Pi / Linux
    """
    if explicit_root is not None:
        return Path(explicit_root).expanduser().resolve()

    env_root = os.environ.get("FRIDGE_ROOT")
    if env_root:
        return Path(env_root).expanduser().resolve()

    try:
        # script/module mode
        current = Path(__file__).resolve().parent
    except NameError:
        # jupyter mode
        current = Path.cwd().resolve()

    while current != current.parent:
        if (current / "data").is_dir() and (current / "scr").is_dir():
            return current
        current = current.parent

    raise RuntimeError(
        "Could not locate project root (expected a directory containing both 'data/' and 'scr/'). "
        "Set FRIDGE_ROOT or pass explicit_root."
    )




# ----------------------------
# Wait for a valid vision.json
# ----------------------------

def wait_for_valid_vision_json(project_root: Path, session_id: str) -> Path:
    """
    Wait for a valid vision JSON under:
      <PROJECT_ROOT>/data/sessions/session_<session_id>/{vision.json|visoin.json}

    Conditions:
      1) session directory exists
      2) file exists
      3) JSON is fully written (json.load succeeds)
      4) contract validation passes (load_and_validate_vision_json)
    """
    session_dir = project_root / DEFAULT_SESSIONS_REL / f"session_{session_id}"
    candidate_files = [
        session_dir / "vision.json",
        session_dir / "visoin.json",
    ]

    print(f"[DetectionRunner] Waiting for session directory: {session_dir}")

    while True:
        if not session_dir.exists():
            time.sleep(POLL_INTERVAL_SEC)
            continue

        print(f"[DetectionRunner] Session directory found.")

        for path in candidate_files:
            if not path.is_file():
                continue

            print(f"[DetectionRunner] Found vision file candidate: {path}")

            try:
                with open(path, "r", encoding="utf-8") as f:
                    json.load(f)
            except json.JSONDecodeError:
                print("[DetectionRunner] vision.json not fully written yet...")
                time.sleep(POLL_INTERVAL_SEC)
                break
            except OSError:
                print("[DetectionRunner] vision.json temporarily inaccessible...")
                time.sleep(POLL_INTERVAL_SEC)
                break

            try:
                _ = load_and_validate_vision_json(str(path))
                print(f"[DetectionRunner] vision.json validated successfully.")
                return path
            except (MissingFieldError, TypeValidationError, ValueValidationError) as e:
                print(f"[DetectionRunner] vision.json contract invalid: {e}")
                time.sleep(POLL_INTERVAL_SEC)
                break

        time.sleep(POLL_INTERVAL_SEC)

# ----------------------------
# Database helpers
# ----------------------------

def connect_db(db_path: Path) -> sqlite3.Connection:
    """
    Open SQLite with recommended pragmas.
    """
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def process_session_to_events(project_root: Path, session_id: str) -> int:
    """
    End-to-end:
      - wait for vision.json
      - load dataclass
      - infer PUT_IN/TAKE_OUT events
      - insert into SQLite events table

    Returns:
      number of inserted events
    """
    print(f"[DetectionRunner] Processing session: {session_id}")

    vision_path = wait_for_valid_vision_json(project_root, session_id)
    print(f"[DetectionRunner] Using vision file: {vision_path}")

    session = load_and_validate_vision_json(str(vision_path))
    print(f"[DetectionRunner] Parsed VisionSession: samples={len(session.samples)}")

    db_events = infer_db_events_from_vision_session(session)
    print(f"[DetectionRunner] Inferred {len(db_events)} DB events.")

    db_path = project_root / DEFAULT_DB_REL
    print(f"[DetectionRunner] Writing to database: {db_path}")

    conn = connect_db(db_path)
    try:
        inserted = insert_events(conn, db_events)
        print(f"[DetectionRunner] Successfully inserted {inserted} rows into events table.")
        return inserted
    finally:
        conn.close()


# ----------------------------
# Socket listener (UDS)
# ----------------------------
def run_socket_listener(
    *,
    project_root: Path,
    socket_path: str = DEFAULT_SOCKET_PATH,
) -> None:
    """
    Keep listening forever.
    Every 1 second prints heartbeat if no session received.
    """

    sock_file = Path(socket_path)

    if sock_file.exists():
        sock_file.unlink()

    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(socket_path)
    server.listen(5)

    # 🔥 关键：1 秒 timeout
    server.settimeout(1.0)

    print(f"[DetectionRunner] Listening on {socket_path}")
    print(f"[DetectionRunner] Project root: {project_root}")
    print("[DetectionRunner] Ready. Waiting for session_id...")

    try:
        while True:
            try:
                conn, _ = server.accept()
            except socket.timeout:
                # 每 1 秒执行一次
                print("[DetectionRunner] Listening...")
                continue

            try:
                data = conn.recv(4096)
                if not data:
                    continue

                try:
                    msg = json.loads(data.decode("utf-8"))
                except json.JSONDecodeError:
                    print("[DetectionRunner] Invalid JSON:", data)
                    continue

                session_id = msg.get("session_id")
                if session_id is None:
                    print("[DetectionRunner] Missing session_id:", msg)
                    continue

                session_id = str(session_id).strip()

                print(f"\n[DetectionRunner] Received session_id: {session_id}")

                try:
                    inserted = process_session_to_events(project_root, session_id)
                    print(f"[DetectionRunner] Inserted {inserted} rows into events")
                except Exception as e:
                    print(f"[DetectionRunner] ERROR processing session: {e}")

                print("[DetectionRunner] Ready for next session_id...")

            finally:
                conn.close()

    except KeyboardInterrupt:
        print("\n[DetectionRunner] Shutting down...")

    finally:
        server.close()
        if sock_file.exists():
            sock_file.unlink()


# ----------------------------
# Entry point
# ----------------------------

def main() -> None:
    """
    Main entry for deployment.

    Recommended:
      export FRIDGE_ROOT=/path/to/EE475-Smart-Fridge-Manager
      python -m scr.data_detection_layer.detection_runner
    """
    # For local dev: you can temporarily hardcode explicit_root if you want.
    # project_root = get_project_root("/Users/liluoqi/Documents/Luoqi_github/EE475-Smart-Fridge-Manager")

    project_root = get_project_root()
    run_socket_listener(project_root=project_root)


if __name__ == "__main__":
    main()
