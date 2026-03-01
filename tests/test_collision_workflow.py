from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
import uuid
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scr" / "data_detection_layer"))

from collision_resolver import (  # noqa: E402
    ACTION_STATUS_PROCESSED,
    CASE_STATUS_OPEN,
    CASE_STATUS_RESOLVED,
    CollisionActionConsumer,
    CollisionResolver,
    OPERATION_PUT_IN,
    OPERATION_TAKE_OUT,
    PENDING_AMBIGUOUS_MULTI_REMOVE,
    ParsedOperation,
    ensure_collision_schema,
    get_in_fridge_instances,
)


class CollisionWorkflowTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "collision_test.db"
        self.conn = sqlite3.connect(str(self.db_path))
        ensure_collision_schema(self.conn)
        self.resolver = CollisionResolver(self.conn, auto_resolve_window_seconds=60)
        self.consumer = CollisionActionConsumer(self.conn)

    def tearDown(self) -> None:
        self.conn.close()
        self.temp_dir.cleanup()

    def test_pending_case_and_user_selects_non_default_instance(self) -> None:
        self._seed_apples(
            [
                "2026-03-01T12:00:00.000Z",
                "2026-03-01T12:02:30.000Z",
                "2026-03-01T12:05:00.000Z",
            ]
        )

        self.resolver.apply_operations(
            [
                ParsedOperation(
                    session_id="session_take_one",
                    event_time_utc="2026-03-01T12:06:00.000Z",
                    item_name="apple",
                    operation=OPERATION_TAKE_OUT,
                    confidence=0.91,
                    track_id=99,
                )
            ]
        )

        open_case = self._fetch_open_case()
        self.assertIsNotNone(open_case)
        pending_ids = json.loads(open_case["pending_item_ids_json"])
        self.assertEqual(len(pending_ids), 1)

        all_instances = get_in_fridge_instances(self.conn, "apple")
        remaining_ids = [instance.item_instance_id for instance in all_instances]
        self.assertEqual(len(remaining_ids), 2)

        non_default_remove_id = remaining_ids[-1]
        action_id = str(uuid.uuid4())
        self.conn.execute(
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
                open_case["case_id"],
                "2026-03-01T12:06:30.000Z",
                json.dumps([non_default_remove_id]),
                "User chose a later apple.",
            ),
        )
        self.conn.commit()

        processed = self.consumer.process_new_actions()
        self.assertEqual(processed, 1)

        latest_status = self._latest_status_map("apple")
        self.assertEqual(latest_status[non_default_remove_id], "REMOVED")
        self.assertEqual(latest_status[pending_ids[0]], "IN_FRIDGE")

        resolved_case = self.conn.execute(
            "SELECT status FROM collision_cases WHERE case_id = ?;",
            (open_case["case_id"],),
        ).fetchone()
        self.assertEqual(resolved_case[0], CASE_STATUS_RESOLVED)

        action_status = self.conn.execute(
            "SELECT status FROM collision_actions WHERE action_id = ?;",
            (action_id,),
        ).fetchone()
        self.assertEqual(action_status[0], ACTION_STATUS_PROCESSED)

    def test_multiple_ambiguous_take_outs_merge_into_single_open_case(self) -> None:
        self._seed_apples(
            [
                "2026-03-01T12:00:00.000Z",
                "2026-03-01T12:02:30.000Z",
                "2026-03-01T12:05:00.000Z",
            ]
        )

        self.resolver.apply_operations(
            [
                ParsedOperation(
                    session_id="session_take_a",
                    event_time_utc="2026-03-01T12:06:00.000Z",
                    item_name="apple",
                    operation=OPERATION_TAKE_OUT,
                    confidence=0.92,
                    track_id=101,
                ),
                ParsedOperation(
                    session_id="session_take_b",
                    event_time_utc="2026-03-01T12:06:10.000Z",
                    item_name="apple",
                    operation=OPERATION_TAKE_OUT,
                    confidence=0.93,
                    track_id=102,
                ),
            ]
        )

        rows = self.conn.execute(
            """
            SELECT case_id, status, pending_item_ids_json, default_remove_ids_json, pending_type
            FROM collision_cases;
            """
        ).fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][1], CASE_STATUS_OPEN)
        self.assertEqual(rows[0][4], PENDING_AMBIGUOUS_MULTI_REMOVE)
        self.assertEqual(len(json.loads(rows[0][2])), 2)
        self.assertEqual(len(json.loads(rows[0][3])), 2)

        pending_events = self.conn.execute(
            """
            SELECT COUNT(*)
            FROM events
            WHERE item_name = 'apple' AND status = 'PENDING';
            """
        ).fetchone()
        self.assertEqual(pending_events[0], 2)

    def _seed_apples(self, timestamps: list[str]) -> None:
        operations = [
            ParsedOperation(
                session_id=f"session_put_{index}",
                event_time_utc=timestamp,
                item_name="apple",
                operation=OPERATION_PUT_IN,
                confidence=0.95,
                track_id=index,
            )
            for index, timestamp in enumerate(timestamps, start=1)
        ]
        inserted = self.resolver.apply_operations(operations)
        self.assertEqual(inserted, len(timestamps))

    def _fetch_open_case(self) -> sqlite3.Row | None:
        self.conn.row_factory = sqlite3.Row
        row = self.conn.execute(
            """
            SELECT case_id, status, pending_item_ids_json, default_remove_ids_json
            FROM collision_cases
            WHERE status = 'OPEN'
            LIMIT 1;
            """
        ).fetchone()
        self.conn.row_factory = None
        return row

    def _latest_status_map(self, item_name: str) -> dict[str, str]:
        rows = self.conn.execute(
            """
            WITH latest AS (
              SELECT e.item_instance_id, e.status
              FROM events e
              INNER JOIN (
                SELECT item_instance_id, MAX(id) AS max_id
                FROM events
                GROUP BY item_instance_id
              ) latest_ids
                ON latest_ids.item_instance_id = e.item_instance_id
               AND latest_ids.max_id = e.id
            )
            SELECT item_instance_id, status
            FROM latest
            WHERE item_instance_id IN (
              SELECT item_instance_id
              FROM events
              WHERE item_name = ?
            );
            """,
            (item_name,),
        ).fetchall()
        return {str(item_id): str(status) for item_id, status in rows}


if __name__ == "__main__":
    unittest.main()
