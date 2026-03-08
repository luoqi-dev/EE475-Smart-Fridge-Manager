from __future__ import annotations

import argparse
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from fridge_cli_common import connect_db, utc_now_iso
from src.data_detection_layer.collision_resolver import CollisionResolver, OPERATION_TAKE_OUT, ParsedOperation


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Take out one item via the backend collision resolver.")
    parser.add_argument("item_name", help="Item name to remove, for example apple")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    event_time = utc_now_iso()
    operation = ParsedOperation(
        session_id=f"manual_take_{args.item_name}",
        event_time_utc=event_time,
        item_name=args.item_name,
        operation=OPERATION_TAKE_OUT,
        confidence=1.0,
        track_id=999,
    )

    conn = connect_db()
    try:
        changed = CollisionResolver(conn).apply_operations([operation])
        if changed == 0:
            print(f"No matching IN_FRIDGE item found for item_name={args.item_name}.")
        else:
            print(
                f"Processed TAKE_OUT for item_name={args.item_name}. "
                "The resolver decided whether the item became REMOVED or PENDING."
            )
            print(f"Event time: {event_time}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
