from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.data_detection_layer.collision_resolver import (
    CollisionResolver,
    OPERATION_PUT_IN,
    OPERATION_TAKE_OUT,
    ParsedOperation,
    ensure_collision_schema,
)


DEMO_DB_PATH = REPO_ROOT / "data" / "db" / "fridge.db"


def connect_db(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    ensure_collision_schema(conn)
    return conn


def reset_demo_db(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM collision_actions;")
    conn.execute("DELETE FROM collision_cases;")
    conn.execute("DELETE FROM events;")
    conn.commit()


def seed_three_apples(conn: sqlite3.Connection) -> None:
    resolver = CollisionResolver(conn, auto_resolve_window_seconds=60)
    resolver.apply_operations(
        [
            ParsedOperation("multi_put_1", "2026-03-01T12:00:00.000Z", "apple", OPERATION_PUT_IN, 0.98, 1),
            ParsedOperation("multi_put_2", "2026-03-01T12:03:00.000Z", "apple", OPERATION_PUT_IN, 0.97, 2),
            ParsedOperation("multi_put_3", "2026-03-01T12:07:00.000Z", "apple", OPERATION_PUT_IN, 0.96, 3),
        ]
    )


def simulate_two_take_outs_without_confirmation(conn: sqlite3.Connection) -> None:
    resolver = CollisionResolver(conn, auto_resolve_window_seconds=60)
    resolver.apply_operations(
        [
            ParsedOperation("multi_take_1", "2026-03-01T12:10:00.000Z", "apple", OPERATION_TAKE_OUT, 0.92, 99),
            ParsedOperation("multi_take_2", "2026-03-01T12:11:00.000Z", "apple", OPERATION_TAKE_OUT, 0.91, 100),
        ]
    )


def print_inventory_table(conn: sqlite3.Connection, title: str) -> None:
    print(f"\n=== {title} ===")
    rows = conn.execute(
        """
        SELECT id,
               item_name,
               event_time_utc,
               status,
               track_id,
               pending_type,
               case_id
        FROM events
        ORDER BY event_time_utc ASC, id ASC;
        """
    ).fetchall()
    if not rows:
        print("(empty)")
        return
    for idx, row in enumerate(rows, start=1):
        print(
            f"{idx}. id={row[0]} item={row[1]} put_in={row[2]} "
            f"status={row[3]} track_id={row[4]} pending_type={row[5]} case_id={row[6]}"
        )


def print_open_case_summary(conn: sqlite3.Connection) -> None:
    row = conn.execute(
        """
        SELECT case_id,
               item_name,
               pending_type,
               status,
               pending_item_ids_json,
               default_remove_ids_json,
               summary,
               version
        FROM collision_cases
        WHERE status = 'OPEN'
        LIMIT 1;
        """
    ).fetchone()
    print("\n=== Open Collision Case ===")
    if row is None:
        print("(no open case)")
        return
    print(f"case_id={row[0]}")
    print(f"item_name={row[1]}")
    print(f"pending_type={row[2]}")
    print(f"status={row[3]}")
    print(f"pending_item_ids_json={row[4]}")
    print(f"default_remove_ids_json={row[5]}")
    print(f"summary={row[6]}")
    print(f"version={row[7]}")


def print_full_table(conn: sqlite3.Connection, table_name: str) -> None:
    print(f"\n=== Full Table: {table_name} ===")
    cursor = conn.execute(f"SELECT * FROM {table_name} ORDER BY 1 ASC;")
    columns = [description[0] for description in cursor.description]
    print(" | ".join(columns))
    rows = cursor.fetchall()
    if not rows:
        print("(empty)")
        return
    for row in rows:
        print(" | ".join("" if value is None else str(value) for value in row))


def main() -> None:
    print(f"Using demo database: {DEMO_DB_PATH}")
    conn = connect_db(DEMO_DB_PATH)
    try:
        reset_demo_db(conn)
        seed_three_apples(conn)

        print_inventory_table(conn, "Initial Inventory Table")
        print("\nSimulating first TAKE_OUT(apple) with no user confirmation...")
        print("Simulating second TAKE_OUT(apple) while the first apple is still pending...")
        simulate_two_take_outs_without_confirmation(conn)

        print_inventory_table(conn, "Inventory Table After Two TAKE_OUT Operations")
        print_open_case_summary(conn)
        print_full_table(conn, "events")
        print_full_table(conn, "collision_cases")
        print_full_table(conn, "collision_actions")

        print("\nResult:")
        print("- There is still only one OPEN collision case.")
        print("- That one case now contains two pending apple ids.")
        print("- The user can later submit one confirmation action selecting which two apples should be removed.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
