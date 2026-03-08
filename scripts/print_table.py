from __future__ import annotations

import argparse
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from fridge_cli_common import connect_db, table_exists


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Print every row from a table in fridge.db.")
    parser.add_argument("table_name", help="Target table name")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    conn = connect_db()
    try:
        if not table_exists(conn, args.table_name):
            raise SystemExit(f"Table does not exist: {args.table_name}")

        cursor = conn.execute(f"SELECT * FROM {args.table_name} ORDER BY 1 ASC;")
        columns = [description[0] for description in cursor.description]
        print(" | ".join(columns))

        rows = cursor.fetchall()
        if not rows:
            print("(empty)")
            return

        for row in rows:
            print(" | ".join("" if value is None else str(value) for value in row))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
