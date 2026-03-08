# src/db/item_projector.py
#
# Projection layer: apply inferred actions to the "items" table.
# Current scope (per your request): update ONLY the items table.
#
# Design notes:
# - This module assumes you already have "Action" events (PUT_IN/TAKE_OUT) derived from VisionSession.
# - It does NOT write to events/event_resolution/pending_actions yet.
# - Matching policy for TAKE_OUT:
#     1) Try track_id match to an active instance
#     2) Fallback to FIFO (oldest put_in_time_utc)
#
# Safety:
# - Uses transactions at the batch level via project_actions_to_items()
# - Raises errors on invalid states (e.g., TAKE_OUT when quantity is 0)

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, Optional, Sequence


# ----------------------------
# Public Action model (minimal)
# ----------------------------

@dataclass(frozen=True)
class ActionEvent:
    """
    Minimal action event required to update the items table.

    Notes:
    - event_id is the FK used by items.put_in_event_id / take_out_event_id.
      In your current phase, you may not insert into events table yet.
      You can supply a synthetic ID (but it must exist in events table due to FK).
      If FK enforcement is ON (recommended), event_id MUST be a valid events.event_id.

    - If you haven't started writing events, either:
        A) Temporarily disable foreign keys during early prototyping (NOT recommended long-term), or
        B) Insert a minimal row into events first, then pass its event_id here, or
        C) Modify schema to allow NULL put_in_event_id temporarily (also not recommended).
    """
    event_id: int
    event_type: str                 # 'PUT_IN' or 'TAKE_OUT'
    item_name: str                  # canonical class name, e.g. 'apple'
    event_time_utc: str             # ISO8601 e.g. "2026-01-30T14:31:00.623Z"
    track_id: Optional[int] = None  # if available


# ----------------------------
# Utilities
# ----------------------------

def utc_now_iso() -> str:
    """Return current UTC time in ISO8601 with 'Z'."""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _normalize_event_type(event_type: str) -> str:
    t = event_type.strip().upper()
    if t not in {"PUT_IN", "TAKE_OUT"}:
        raise ValueError(f"Unsupported event_type: {event_type!r} (expected 'PUT_IN' or 'TAKE_OUT')")
    return t


# ----------------------------
# Read helpers
# ----------------------------

def get_item_quantity(conn: sqlite3.Connection, item_name: str) -> int:
    """
    Return how many instances of item_name are currently in the fridge (status='IN').
    """
    cur = conn.execute(
        "SELECT COUNT(*) FROM items WHERE item_name=? AND status='IN';",
        (item_name,),
    )
    row = cur.fetchone()
    return int(row[0]) if row else 0


def get_active_item_instance_ids_fifo(conn: sqlite3.Connection, item_name: str) -> list[int]:
    """
    Return active item_instance_id for item_name in FIFO order (oldest put_in first).
    """
    cur = conn.execute(
        """
        SELECT item_instance_id
        FROM items
        WHERE item_name=? AND status='IN'
        ORDER BY put_in_time_utc ASC, item_instance_id ASC;
        """,
        (item_name,),
    )
    return [int(r[0]) for r in cur.fetchall()]


def find_active_item_by_track_id(conn: sqlite3.Connection, item_name: str, track_id: int) -> Optional[int]:
    """
    Try to find an active item instance whose last_track_id matches track_id.
    Returns item_instance_id if found, else None.
    """
    cur = conn.execute(
        """
        SELECT item_instance_id
        FROM items
        WHERE item_name=? AND status='IN' AND last_track_id=?
        ORDER BY put_in_time_utc ASC, item_instance_id ASC
        LIMIT 1;
        """,
        (item_name, track_id),
    )
    row = cur.fetchone()
    return int(row[0]) if row else None


# ----------------------------
# Write helpers
# ----------------------------

def insert_item(
    conn: sqlite3.Connection,
    *,
    item_name: str,
    put_in_event_id: int,
    put_in_time_utc: str,
    track_id: Optional[int],
    updated_at_utc: Optional[str] = None,
) -> int:
    """
    Insert a new item instance as status='IN'.

    Returns:
        item_instance_id (int)
    """
    if updated_at_utc is None:
        updated_at_utc = utc_now_iso()

    cur = conn.execute(
        """
        INSERT INTO items (
          item_name, status,
          put_in_event_id, put_in_time_utc,
          take_out_event_id, take_out_time_utc,
          last_track_id,
          updated_at_utc
        )
        VALUES (?, 'IN', ?, ?, NULL, NULL, ?, ?);
        """,
        (item_name, put_in_event_id, put_in_time_utc, track_id, updated_at_utc),
    )
    return int(cur.lastrowid)


def mark_item_removed(
    conn: sqlite3.Connection,
    *,
    item_instance_id: int,
    take_out_event_id: int,
    take_out_time_utc: str,
    track_id: Optional[int],
    updated_at_utc: Optional[str] = None,
) -> None:
    """
    Mark an item instance as removed.
    """
    if updated_at_utc is None:
        updated_at_utc = utc_now_iso()

    conn.execute(
        """
        UPDATE items
        SET status='REMOVED',
            take_out_event_id=?,
            take_out_time_utc=?,
            last_track_id=COALESCE(?, last_track_id),
            updated_at_utc=?
        WHERE item_instance_id=?;
        """,
        (take_out_event_id, take_out_time_utc, track_id, updated_at_utc, item_instance_id),
    )


# Optional: pending marking (not used yet, but kept for forward compatibility)
def mark_item_pending_out(
    conn: sqlite3.Connection,
    *,
    item_instance_id: int,
    updated_at_utc: Optional[str] = None,
) -> None:
    if updated_at_utc is None:
        updated_at_utc = utc_now_iso()

    conn.execute(
        """
        UPDATE items
        SET status='PENDING_OUT',
            updated_at_utc=?
        WHERE item_instance_id=?;
        """,
        (updated_at_utc, item_instance_id),
    )


# ----------------------------
# Projection logic
# ----------------------------

def apply_put_in(conn: sqlite3.Connection, action: ActionEvent) -> int:
    """
    Apply a PUT_IN action to items table.

    Returns:
        item_instance_id
    """
    if _normalize_event_type(action.event_type) != "PUT_IN":
        raise ValueError("apply_put_in called with non-PUT_IN action")

    return insert_item(
        conn,
        item_name=action.item_name,
        put_in_event_id=action.event_id,
        put_in_time_utc=action.event_time_utc,
        track_id=action.track_id,
    )


def _choose_take_out_target(
    conn: sqlite3.Connection,
    *,
    item_name: str,
    track_id: Optional[int],
) -> Optional[int]:
    """
    Choose which item instance to remove.
    Policy:
      1) Track match
      2) FIFO fallback
    """
    if track_id is not None:
        matched = find_active_item_by_track_id(conn, item_name, track_id)
        if matched is not None:
            return matched

    fifo_ids = get_active_item_instance_ids_fifo(conn, item_name)
    if not fifo_ids:
        return None
    return fifo_ids[0]


def apply_take_out(conn: sqlite3.Connection, action: ActionEvent) -> int:
    """
    Apply a TAKE_OUT action to items table.

    Returns:
        item_instance_id that was removed

    Raises:
        RuntimeError if there is no active instance to remove.
    """
    if _normalize_event_type(action.event_type) != "TAKE_OUT":
        raise ValueError("apply_take_out called with non-TAKE_OUT action")

    target_id = _choose_take_out_target(conn, item_name=action.item_name, track_id=action.track_id)
    if target_id is None:
        raise RuntimeError(
            f"TAKE_OUT for item_name={action.item_name!r} but no active instances exist (status='IN')."
        )

    mark_item_removed(
        conn,
        item_instance_id=target_id,
        take_out_event_id=action.event_id,
        take_out_time_utc=action.event_time_utc,
        track_id=action.track_id,
    )
    return target_id


def project_actions_to_items(
    conn: sqlite3.Connection,
    actions: Sequence[ActionEvent],
    *,
    strict: bool = True,
) -> None:
    """
    Apply a batch of actions in a single transaction.

    Args:
        conn: sqlite3.Connection
        actions: ordered list of ActionEvent (usually time-ordered)
        strict:
            - True: raise on first error and rollback whole batch
            - False: best-effort apply; errors are collected and raised at end

    Notes:
      - You should pass actions sorted by event_time_utc / timestamp_ms.
      - For strict correctness with foreign keys ON, the referenced events.event_id must exist.
    """
    errors: list[Exception] = []

    # One transaction for the whole batch.
    with conn:
        for a in actions:
            try:
                et = _normalize_event_type(a.event_type)
                if et == "PUT_IN":
                    apply_put_in(conn, a)
                elif et == "TAKE_OUT":
                    apply_take_out(conn, a)
            except Exception as e:
                if strict:
                    raise
                errors.append(e)

    if errors:
        # If strict=False, transaction still committed. Caller can decide how to handle.
        raise RuntimeError(f"Non-strict projection completed with {len(errors)} errors: {errors!r}")


# ----------------------------
# Convenience: connect helper
# ----------------------------

def connect_db(db_path: str, *, timeout_sec: float = 30.0) -> sqlite3.Connection:
    """
    Open SQLite connection with recommended pragmas.
    """
    conn = sqlite3.connect(db_path, timeout=timeout_sec)
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA journal_mode = WAL;")
    return conn


# ----------------------------
# Demo main (optional)
# ----------------------------

def main() -> None:
    """
    Demo runner: projects a tiny set of actions into items table.

    IMPORTANT:
      This demo assumes events rows exist with event_id=1..N due to FK constraints.
      If your DB has foreign_keys=ON (recommended), you must insert into events first.
    """
    db_path = "data/db/fridge.db"
    conn = connect_db(db_path)

    # Example: update only items (requires those event_ids to exist in events table).
    actions = [
        ActionEvent(event_id=1, event_type="PUT_IN",  item_name="apple",  event_time_utc="2026-02-11T05:00:00Z", track_id=12),
        ActionEvent(event_id=2, event_type="PUT_IN",  item_name="apple",  event_time_utc="2026-02-11T05:01:00Z", track_id=13),
        ActionEvent(event_id=3, event_type="TAKE_OUT", item_name="apple",  event_time_utc="2026-02-11T05:02:00Z", track_id=12),
    ]

    try:
        project_actions_to_items(conn, actions, strict=True)
        print("Projected actions -> items successfully.")
        print("Current apple qty:", get_item_quantity(conn, "apple"))
    finally:
        conn.close()


if __name__ == "__main__":
    main()

