from __future__ import annotations

import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from fridge_cli_common import connect_db, table_exists


TABLES = (
    "events",
    "collision_cases",
    "collision_actions",
)


def print_table(conn, table_name: str) -> None:
    cursor = conn.execute(f"SELECT * FROM {table_name} ORDER BY 1 ASC;")
    columns = [description[0] for description in cursor.description]

    print(f"\n=== {table_name} ===")
    print(" | ".join(columns))

    rows = cursor.fetchall()
    if not rows:
        print("(empty)")
        return

    for row in rows:
        print(" | ".join("" if value is None else str(value) for value in row))


def main() -> None:
    conn = connect_db()
    try:
        for table_name in TABLES:
            if not table_exists(conn, table_name):
                raise SystemExit(f"Table does not exist: {table_name}")

        for table_name in TABLES:
            print_table(conn, table_name)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
