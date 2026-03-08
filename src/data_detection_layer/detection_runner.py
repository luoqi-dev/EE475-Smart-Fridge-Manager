from __future__ import annotations

import json
import os
import socket
import sqlite3
import time
import argparse
from pathlib import Path
from typing import List, Optional, Set

# Local package imports. Fallback keeps notebooks/direct module loading working.
try:
    from .collision_resolver import CollisionActionConsumer, CollisionResolver, ensure_collision_schema
    from .loader import (
        load_and_validate_vision_json,
        MissingFieldError,
        TypeValidationError,
        ValueValidationError,
    )
    from .vision_to_events import DbEvent, infer_operations_from_vision_session
except ImportError:
    from collision_resolver import CollisionActionConsumer, CollisionResolver, ensure_collision_schema
    from loader import (
        load_and_validate_vision_json,
        MissingFieldError,
        TypeValidationError,
        ValueValidationError,
    )
    from vision_to_events import DbEvent, infer_operations_from_vision_session


# ----------------------------
# Config
# ----------------------------

DEFAULT_SOCKET_PATH = "/tmp/vision_to_backend.sock"
DEFAULT_DB_REL = Path("data/db/fridge.db")
DEFAULT_SESSIONS_REL = Path("data/sessions")

POLL_INTERVAL_SEC = 0.2
ACTION_POLL_INTERVAL_SEC = 1.0


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
         until we find BOTH 'data/' and 'src/' at the same level.

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
        if (current / "data").is_dir() and (current / "src").is_dir():
            return current
        current = current.parent

    raise RuntimeError(
        "Could not locate project root (expected a directory containing both 'data/' and 'src/'). "
        "Set FRIDGE_ROOT or pass explicit_root."
    )




# ----------------------------
# Wait for a valid vision.json
# ----------------------------

def wait_for_valid_vision_json(project_root: Path, session_id: str) -> Path:
    """
    Wait for a valid vision JSON under:
      <PROJECT_ROOT>/data/sessions/<session_id>/{vision.json|visoin.json}

    Conditions:
      1) session directory exists
      2) file exists
      3) JSON is fully written (json.load succeeds)
      4) contract validation passes (load_and_validate_vision_json)
    """
    session_dir = project_root / DEFAULT_SESSIONS_REL / session_id
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


def _db_debug_enabled() -> bool:
    """
    Enable detailed DB post-write logging only when FRIDGE_DB_DEBUG=1.
    """
    return os.environ.get("FRIDGE_DB_DEBUG", "0").strip() == "1"


def _print_db_debug(
    conn: sqlite3.Connection,
    *,
    session_id: str,
    inferred_events_count: int,
    db_rows_affected: int,
    db_events: List[DbEvent],
) -> None:
    print(
        "[DetectionRunner][DB] Summary: "
        f"session_id={session_id}, "
        f"inferred_events_count={inferred_events_count}, "
        f"db_rows_affected={db_rows_affected}"
    )

    rows = conn.execute(
        """
        SELECT id, session_id, event_time_utc, item_name, status, confidence, track_id, pending_type, case_id
        FROM events
        WHERE session_id = ?
        ORDER BY event_time_utc ASC, id ASC;
        """,
        (session_id,),
    ).fetchall()

    print(f"[DetectionRunner][DB] Rows for session_id={session_id}:")
    if not rows:
        print("  []")
    else:
        for r in rows:
            print(
                "  "
                f"id={r[0]} time={r[2]} item={r[3]} status={r[4]} conf={r[5]} "
                f"track_id={r[6]} pending_type={r[7]} case_id={r[8]}"
            )

    item_names: Set[str] = {e.item_name for e in db_events}
    if not item_names:
        print("[DetectionRunner][DB] Inventory snapshot skipped: no inferred events.")
        return

    for item_name in sorted(item_names):
        counts = conn.execute(
            """
            SELECT status, COUNT(*) as cnt
            FROM events
            WHERE item_name = ?
            GROUP BY status
            ORDER BY status;
            """,
            (item_name,),
        ).fetchall()

        print(f"[DetectionRunner][DB] Inventory snapshot for item={item_name}:")
        if not counts:
            print("  counts=[]")
        else:
            compact_counts = ", ".join([f"{status}:{cnt}" for status, cnt in counts])
            print(f"  counts=[{compact_counts}]")

        fifo_row = conn.execute(
            """
            SELECT id, event_time_utc
            FROM events
            WHERE item_name = ? AND status = 'IN_FRIDGE'
            ORDER BY event_time_utc ASC, id ASC
            LIMIT 1;
            """,
            (item_name,),
        ).fetchone()

        if fifo_row is None:
            print("  earliest_in_fridge=None")
        else:
            print(f"  earliest_in_fridge=id={fifo_row[0]}, time={fifo_row[1]}")


def process_session_to_events(project_root: Path, session_id: str) -> int:
    """
    End-to-end:
      - wait for vision.json
      - load dataclass
      - infer IN_FRIDGE/REMOVED events
      - insert into SQLite events table

    Returns:
      number of inserted events
    """
    print(f"[DetectionRunner] Processing session: {session_id}")

    vision_path = wait_for_valid_vision_json(project_root, session_id)
    print(f"[DetectionRunner] Using vision file: {vision_path}")

    session = load_and_validate_vision_json(str(vision_path))
    print(f"[DetectionRunner] Parsed VisionSession: samples={len(session.samples)}")

    operations = infer_operations_from_vision_session(session)
    print(f"[DetectionRunner] Inferred {len(operations)} high-level operations.")
    db_events = [
        DbEvent(
            session_id=operation.session_id,
            event_time_utc=operation.event_time_utc,
            item_name=operation.item_name,
            status="IN_FRIDGE" if operation.operation == "PUT_IN" else "REMOVED",
            confidence=operation.confidence,
            track_id=operation.track_id,
        )
        for operation in operations
    ]

    db_path = project_root / DEFAULT_DB_REL
    print(f"[DetectionRunner] Writing to database: {db_path}")

    conn = connect_db(db_path)
    try:
        ensure_collision_schema(conn)
        inserted = CollisionResolver(conn).apply_operations(operations)
        processed_actions = CollisionActionConsumer(conn).process_new_actions()
        print(
            "[DetectionRunner] Database updates complete: "
            f"rows_inserted_or_updated={inserted}, processed_actions={processed_actions}."
        )
        if _db_debug_enabled():
            _print_db_debug(
                conn,
                session_id=session.session_id,
                inferred_events_count=len(operations),
                db_rows_affected=inserted,
                db_events=db_events,
            )
        return inserted
    finally:
        conn.close()


def process_pending_collision_actions(project_root: Path) -> int:
    db_path = project_root / DEFAULT_DB_REL
    conn = connect_db(db_path)
    try:
        ensure_collision_schema(conn)
        return CollisionActionConsumer(conn).process_new_actions()
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
        if sock_file.is_socket():
            sock_file.unlink()
        else:
            raise RuntimeError(f"Refusing to remove non-socket path: {sock_file}")

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
                processed_actions = process_pending_collision_actions(project_root)
                if processed_actions:
                    print(f"[DetectionRunner] Processed {processed_actions} queued collision action(s).")
                print("[DetectionRunner] Listening...")
                continue

            try:
                buf = b""
                while True:
                    chunk = conn.recv(4096)
                    if not chunk:
                        break
                    buf += chunk

                if not buf:
                    continue

                try:
                    msg = json.loads(buf.decode("utf-8"))
                except json.JSONDecodeError:
                    print("[DetectionRunner] Invalid JSON payload.")
                    continue

                session_id = msg.get("session_id")
                if session_id is None:
                    print("[DetectionRunner] Missing session_id:", msg)
                    continue

                session_id = str(session_id).strip()
                status = str(msg.get("status", "")).strip().lower()
                if status not in {"success", "empty"}:
                    print("[DetectionRunner] Invalid status:", msg)
                    continue

                print(f"\n[DetectionRunner] Received session_id: {session_id}, status: {status}")
                if status == "empty":
                    print("[DetectionRunner] Empty session. Skipping JSON processing.")
                    print("[DetectionRunner] Ready for next session_id...")
                    continue

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


def run_collision_action_worker(
    *,
    project_root: Path,
    poll_interval_sec: float = ACTION_POLL_INTERVAL_SEC,
) -> None:
    print(f"[CollisionActionWorker] Project root: {project_root}")
    print(f"[CollisionActionWorker] Poll interval: {poll_interval_sec}s")
    print("[CollisionActionWorker] Watching collision_actions for NEW rows...")

    try:
        while True:
            processed_actions = process_pending_collision_actions(project_root)
            if processed_actions:
                print(f"[CollisionActionWorker] Processed {processed_actions} queued collision action(s).")
            time.sleep(poll_interval_sec)
    except KeyboardInterrupt:
        print("\n[CollisionActionWorker] Shutting down...")


# ----------------------------
# Entry point
# ----------------------------

def main() -> None:
    """
    Main entry for deployment.

    Recommended:
      export FRIDGE_ROOT=/path/to/EE475-Smart-Fridge-Manager
      python -m src.data_detection_layer.detection_runner
    """
    # For local dev: you can temporarily hardcode explicit_root if you want.
    # project_root = get_project_root("/Users/liluoqi/Documents/Luoqi_github/EE475-Smart-Fridge-Manager")

    parser = argparse.ArgumentParser(description="Detection runner and collision action worker.")
    parser.add_argument(
        "--actions-only",
        action="store_true",
        help="Run only the collision action polling worker.",
    )
    parser.add_argument(
        "--action-poll-interval",
        type=float,
        default=ACTION_POLL_INTERVAL_SEC,
        help="Polling interval in seconds for the actions-only worker.",
    )
    args = parser.parse_args()

    project_root = get_project_root()
    if args.actions_only:
        run_collision_action_worker(
            project_root=project_root,
            poll_interval_sec=args.action_poll_interval,
        )
        return
    run_socket_listener(project_root=project_root)


if __name__ == "__main__":
    main()
