from __future__ import annotations

import argparse

from fridge_cli_common import add_seconds, connect_db, utc_now_iso
from collision_resolver import CollisionResolver, OPERATION_PUT_IN, ParsedOperation


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Insert items into fridge.db via the backend resolver.")
    parser.add_argument("item_name", help="Item name to insert, for example apple")
    parser.add_argument("count", type=int, help="How many items to insert")
    parser.add_argument("gap_seconds", type=int, help="Seconds between inserted items")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.count <= 0:
        raise SystemExit("count must be greater than 0")
    if args.gap_seconds < 0:
        raise SystemExit("gap_seconds must be >= 0")

    base_time = utc_now_iso()
    operations = []
    for index in range(args.count):
        operations.append(
            ParsedOperation(
                session_id=f"manual_put_{args.item_name}_{index + 1}",
                event_time_utc=add_seconds(base_time, index * args.gap_seconds),
                item_name=args.item_name,
                operation=OPERATION_PUT_IN,
                confidence=1.0,
                track_id=index + 1,
            )
        )

    conn = connect_db()
    try:
        changed = CollisionResolver(conn).apply_operations(operations)
        print(f"Inserted {changed} item(s) for item_name={args.item_name}.")
        print(f"Base time: {base_time}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
