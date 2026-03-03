from __future__ import annotations

import argparse
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from fridge_cli_common import connect_db, table_exists


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Delete all rows from a table in fridge.db.")
    parser.add_argument("table_name", help="Target table name")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    conn = connect_db()
    try:
        if not table_exists(conn, args.table_name):
            raise SystemExit(f"Table does not exist: {args.table_name}")
        conn.execute(f"DELETE FROM {args.table_name};")
        conn.commit()
        print(f"Cleared table: {args.table_name}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
