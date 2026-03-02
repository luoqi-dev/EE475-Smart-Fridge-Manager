from __future__ import annotations

import json
import sqlite3
import sys
import uuid
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scr" / "data_detection_layer"))

from collision_resolver import (  # noqa: E402
    CollisionActionConsumer,
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
            ParsedOperation("demo_put_1", "2026-03-01T12:00:00.000Z", "apple", OPERATION_PUT_IN, 0.98, 1),
            ParsedOperation("demo_put_2", "2026-03-01T12:03:00.000Z", "apple", OPERATION_PUT_IN, 0.97, 2),
            ParsedOperation("demo_put_3", "2026-03-01T12:07:00.000Z", "apple", OPERATION_PUT_IN, 0.96, 3),
        ]
    )


def simulate_take_out(conn: sqlite3.Connection) -> None:
    resolver = CollisionResolver(conn, auto_resolve_window_seconds=60)
    resolver.apply_operations(
        [
            ParsedOperation("demo_take_1", "2026-03-01T12:10:00.000Z", "apple", OPERATION_TAKE_OUT, 0.92, 99),
        ]
    )


def fetch_inventory_rows(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT id,
               session_id,
               event_time_utc,
               item_name,
               status,
               confidence,
               track_id,
               pending_type,
               case_id,
               updated_at_utc
        FROM events
        ORDER BY event_time_utc ASC, id ASC;
        """
    ).fetchall()
    conn.row_factory = None
    return rows


def fetch_open_case(conn: sqlite3.Connection) -> sqlite3.Row | None:
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        """
        SELECT case_id,
               item_name,
               pending_type,
               status,
               summary,
               pending_item_ids_json,
               default_remove_ids_json,
               created_at_utc,
               updated_at_utc
        FROM collision_cases
        WHERE status = 'OPEN'
        ORDER BY updated_at_utc DESC
        LIMIT 1;
        """
    ).fetchone()
    conn.row_factory = None
    return row


def fetch_case_candidates(conn: sqlite3.Connection, item_name: str) -> list[sqlite3.Row]:
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT id,
               item_name,
               status,
               track_id,
               event_time_utc AS put_in_time_utc
        FROM events
        WHERE item_name = ?
          AND status IN ('IN_FRIDGE', 'PENDING')
        ORDER BY event_time_utc ASC, id ASC;
        """,
        (item_name,),
    ).fetchall()
    conn.row_factory = None
    return rows


def print_inventory_snapshot(conn: sqlite3.Connection, title: str) -> None:
    print(f"\n=== {title} ===")
    rows = fetch_inventory_rows(conn)
    if not rows:
        print("(empty)")
        return

    for idx, row in enumerate(rows, start=1):
        print(
            f"{idx}. id={row['id']} item={row['item_name']} "
            f"put_in={row['event_time_utc']} status={row['status']} "
            f"track_id={row['track_id']} pending_type={row['pending_type']}"
        )


def prompt_user_for_choice(candidates: list[sqlite3.Row], default_remove_ids: list[str]) -> str:
    default_id = default_remove_ids[0] if default_remove_ids else None
    print("\nConsole frontend:")
    print("A collision is open because multiple apples could match the removal.")
    print("Choose which apple was actually removed.")
    if default_id is not None:
        print(f"Backend default candidate id: {default_id}")
    print("The script is paused here and waiting for your console input.")

    for idx, row in enumerate(candidates, start=1):
        default_marker = " [default]" if str(row["id"]) == default_id else ""
        print(
            f"  {idx}. Apple put in at {row['put_in_time_utc']} "
            f"(id={row['id']}, status={row['status']}){default_marker}"
        )

    while True:
        raw = input("Type 1, 2, or 3 and press Enter: ").strip()
        if raw.isdigit():
            choice = int(raw)
            if 1 <= choice <= len(candidates):
                return str(candidates[choice - 1]["id"])
        print("Invalid choice. Please enter exactly 1, 2, or 3.")


def insert_user_action(conn: sqlite3.Connection, case_id: str, remove_item_id: str) -> str:
    action_id = str(uuid.uuid4())
    conn.execute(
        """
        INSERT INTO collision_actions (
          action_id,
          case_id,
          created_at_utc,
          remove_item_ids_json,
          note,
          status
        )
        VALUES (?, ?, ?, ?, ?, 'NEW');
        """,
        (
            action_id,
            case_id,
            "2026-03-01T12:10:30.000Z",
            json.dumps([remove_item_id]),
            "Console demo user choice.",
        ),
    )
    conn.commit()
    return action_id


def print_resolution_summary(conn: sqlite3.Connection, action_id: str, case_id: str) -> None:
    print("\n=== Resolution Result ===")
    action_row = conn.execute(
        "SELECT status, processed_at_utc FROM collision_actions WHERE action_id = ?;",
        (action_id,),
    ).fetchone()
    print(f"collision_action status={action_row[0]} processed_at_utc={action_row[1]}")

    case_row = conn.execute(
        "SELECT status, summary, version FROM collision_cases WHERE case_id = ?;",
        (case_id,),
    ).fetchone()
    print(f"collision_case status={case_row[0]} version={case_row[2]} summary={case_row[1]}")

    rows = conn.execute(
        """
        SELECT id, status, event_time_utc
        FROM events
        WHERE item_name = 'apple'
        ORDER BY event_time_utc ASC, id ASC;
        """
    ).fetchall()
    for row in rows:
        print(f"id={row[0]} final_status={row[1]} put_in={row[2]}")


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


def print_full_db_snapshot(conn: sqlite3.Connection, title: str) -> None:
    print(f"\n================ {title} ================")
    print_inventory_snapshot(conn, "Inventory Table")
    print_full_table(conn, "events")
    print_full_table(conn, "collision_cases")
    print_full_table(conn, "collision_actions")


def main() -> None:
    print(f"Using demo database: {DEMO_DB_PATH}")
    conn = connect_db(DEMO_DB_PATH)
    try:
        reset_demo_db(conn)
        seed_three_apples(conn)
        print_full_db_snapshot(conn, "Initial fridge.db State")

        print("\nSimulating one TAKE_OUT(apple) event with >60s gaps between apples...")
        simulate_take_out(conn)
        print_full_db_snapshot(conn, "Middle fridge.db State After TAKE_OUT")

        open_case = fetch_open_case(conn)
        if open_case is None:
            print("No open collision case was created.")
            return

        print("\n=== Open Collision Case ===")
        print(f"case_id={open_case['case_id']}")
        print(f"item_name={open_case['item_name']}")
        print(f"pending_type={open_case['pending_type']}")
        print(f"summary={open_case['summary']}")
        print(f"pending_item_ids_json={open_case['pending_item_ids_json']}")
        print(f"default_remove_ids_json={open_case['default_remove_ids_json']}")

        candidates = fetch_case_candidates(conn, str(open_case["item_name"]))
        print("\n=== Candidates Shown To Console Frontend ===")
        for idx, row in enumerate(candidates, start=1):
            print(f"{idx}. id={row['id']} put_in={row['put_in_time_utc']} status={row['status']} track_id={row['track_id']}")

        selected_item_id = prompt_user_for_choice(
            candidates,
            json.loads(str(open_case["default_remove_ids_json"])),
        )
        print(f"\nUser selected id={selected_item_id}")

        action_id = insert_user_action(conn, str(open_case["case_id"]), selected_item_id)
        print(f"Inserted collision_actions row action_id={action_id}")

        processed = CollisionActionConsumer(conn).process_new_actions()
        print(f"Backend consumer processed {processed} action(s).")

        print_resolution_summary(conn, action_id, str(open_case["case_id"]))
        print_full_db_snapshot(conn, "Final fridge.db State")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
