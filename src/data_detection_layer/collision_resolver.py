from __future__ import annotations

import json
import sqlite3
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional, Sequence


STATUS_IN_FRIDGE = "IN_FRIDGE"
STATUS_REMOVED = "REMOVED"
STATUS_PENDING = "PENDING"

OPERATION_PUT_IN = "PUT_IN"
OPERATION_TAKE_OUT = "TAKE_OUT"

PENDING_AMBIGUOUS_MULTI_REMOVE = "PENDING_AMBIGUOUS_MULTI_REMOVE"

CASE_STATUS_OPEN = "OPEN"
CASE_STATUS_RESOLVED = "RESOLVED"
CASE_STATUS_CANCELLED = "CANCELLED"
CASE_STATUS_EXPIRED = "EXPIRED"

ACTION_STATUS_NEW = "NEW"
ACTION_STATUS_PROCESSED = "PROCESSED"
ACTION_STATUS_REJECTED = "REJECTED"


@dataclass(frozen=True)
class ParsedOperation:
    session_id: str
    event_time_utc: str
    item_name: str
    operation: str
    confidence: float
    track_id: int


@dataclass(frozen=True)
class ItemInstanceState:
    item_id: str
    item_name: str
    put_in_time_utc: str
    current_status: str
    session_id: Optional[str]
    confidence: Optional[float]
    track_id: Optional[int]
    case_id: Optional[str]
    pending_type: Optional[str]


@dataclass(frozen=True)
class CollisionCase:
    case_id: str
    item_name: str
    pending_type: str
    status: str
    created_at_utc: str
    updated_at_utc: str
    pending_item_ids: List[str]
    default_remove_ids: List[str]
    summary: str
    session_id: Optional[str]
    version: int


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _parse_utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _json_load_list(raw: str) -> List[str]:
    data = json.loads(raw)
    if not isinstance(data, list):
        raise ValueError(f"Expected JSON list, got {type(data).__name__}")
    return [str(item) for item in data]


def _json_dump_list(values: Sequence[str]) -> str:
    unique_values = list(dict.fromkeys(str(value) for value in values))
    return json.dumps(unique_values)


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1;",
        (table_name,),
    ).fetchone()
    return row is not None


def _column_names(conn: sqlite3.Connection, table_name: str) -> List[str]:
    return [str(row[1]) for row in conn.execute(f"PRAGMA table_info({table_name});").fetchall()]


def _ensure_events_base_table(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS events (
          id               INTEGER PRIMARY KEY AUTOINCREMENT,
          session_id       TEXT,
          event_time_utc   TEXT NOT NULL,
          item_name        TEXT NOT NULL,
          status           TEXT NOT NULL CHECK(status IN ('IN_FRIDGE','REMOVED','PENDING')),
          confidence       REAL,
          track_id         INTEGER,
          pending_type     TEXT NULL,
          case_id          TEXT NULL,
          updated_at_utc   TEXT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_events_time ON events(event_time_utc);
        CREATE INDEX IF NOT EXISTS idx_events_item_time ON events(item_name, event_time_utc);
        CREATE INDEX IF NOT EXISTS idx_events_case_status ON events(case_id, status, item_name);
        """
    )


def _rebuild_events_without_item_instance_id(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        DROP INDEX IF EXISTS idx_events_instance_id;

        CREATE TABLE events_v3 (
          id               INTEGER PRIMARY KEY AUTOINCREMENT,
          session_id       TEXT,
          event_time_utc   TEXT NOT NULL,
          item_name        TEXT NOT NULL,
          status           TEXT NOT NULL CHECK(status IN ('IN_FRIDGE','REMOVED','PENDING')),
          confidence       REAL,
          track_id         INTEGER,
          pending_type     TEXT NULL,
          case_id          TEXT NULL,
          updated_at_utc   TEXT NULL
        );

        INSERT INTO events_v3 (
          id,
          session_id,
          event_time_utc,
          item_name,
          status,
          confidence,
          track_id,
          pending_type,
          case_id,
          updated_at_utc
        )
        SELECT
          id,
          session_id,
          event_time_utc,
          item_name,
          status,
          confidence,
          track_id,
          pending_type,
          case_id,
          updated_at_utc
        FROM events;

        DROP TABLE events;
        ALTER TABLE events_v3 RENAME TO events;
        """
    )
    _ensure_events_base_table(conn)


def ensure_collision_schema(conn: sqlite3.Connection) -> None:
    if not _table_exists(conn, "events"):
        _ensure_events_base_table(conn)
    else:
        columns = {name.lower(): name for name in _column_names(conn, "events")}
        status_col = columns.get("status")
        action_col = columns.get("action")

        if status_col is None and action_col is not None:
            try:
                conn.execute(f"ALTER TABLE events RENAME COLUMN {action_col} TO status;")
            except sqlite3.OperationalError:
                conn.execute("ALTER TABLE events ADD COLUMN status TEXT;")
                conn.execute(
                    """
                    UPDATE events
                    SET status = CASE UPPER(COALESCE(action, ''))
                        WHEN 'PUT_IN' THEN 'IN_FRIDGE'
                        WHEN 'TAKE_OUT' THEN 'REMOVED'
                        WHEN 'IN_FRIDGE' THEN 'IN_FRIDGE'
                        WHEN 'REMOVED' THEN 'REMOVED'
                        WHEN 'PENDING' THEN 'PENDING'
                        ELSE 'PENDING'
                    END
                    WHERE status IS NULL OR TRIM(status) = '';
                    """
                )

        columns = {name.lower(): name for name in _column_names(conn, "events")}
        if "item_instance_id" in columns:
            _rebuild_events_without_item_instance_id(conn)
            columns = {name.lower(): name for name in _column_names(conn, "events")}
        if "pending_type" not in columns:
            conn.execute("ALTER TABLE events ADD COLUMN pending_type TEXT NULL;")
        if "case_id" not in columns:
            conn.execute("ALTER TABLE events ADD COLUMN case_id TEXT NULL;")
        if "updated_at_utc" not in columns:
            conn.execute("ALTER TABLE events ADD COLUMN updated_at_utc TEXT NULL;")

        if "status" in columns:
            status_col = columns["status"]
            conn.execute(
                f"""
                UPDATE events
                SET {status_col} = CASE UPPER(COALESCE({status_col}, ''))
                    WHEN 'PUT_IN' THEN 'IN_FRIDGE'
                    WHEN 'TAKE_OUT' THEN 'REMOVED'
                    WHEN 'IN_FRIDGE' THEN 'IN_FRIDGE'
                    WHEN 'REMOVED' THEN 'REMOVED'
                    WHEN 'PENDING' THEN 'PENDING'
                    ELSE {status_col}
                END;
                """
            )

        _ensure_events_base_table(conn)

    conn.execute(
        """
        UPDATE events
        SET updated_at_utc = COALESCE(updated_at_utc, event_time_utc)
        WHERE updated_at_utc IS NULL OR TRIM(updated_at_utc) = '';
        """
    )

    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS collision_cases (
          case_id                  TEXT PRIMARY KEY,
          item_name                TEXT NOT NULL,
          pending_type             TEXT NOT NULL,
          status                   TEXT NOT NULL CHECK(status IN ('OPEN','RESOLVED','CANCELLED','EXPIRED')),
          created_at_utc           TEXT NOT NULL,
          updated_at_utc           TEXT NOT NULL,
          pending_item_ids_json    TEXT NOT NULL,
          default_remove_ids_json  TEXT NOT NULL,
          summary                  TEXT NOT NULL,
          session_id               TEXT NULL,
          version                  INTEGER NOT NULL DEFAULT 0
        );

        CREATE UNIQUE INDEX IF NOT EXISTS idx_collision_cases_open_unique
        ON collision_cases(item_name, pending_type)
        WHERE status = 'OPEN';

        CREATE INDEX IF NOT EXISTS idx_collision_cases_status_updated
        ON collision_cases(status, updated_at_utc);

        CREATE TABLE IF NOT EXISTS collision_actions (
          action_id             TEXT PRIMARY KEY,
          case_id               TEXT NOT NULL,
          created_at_utc        TEXT NOT NULL,
          remove_item_ids_json  TEXT NOT NULL,
          note                  TEXT NULL,
          processed_at_utc      TEXT NULL,
          status                TEXT NOT NULL CHECK(status IN ('NEW','PROCESSED','REJECTED')) DEFAULT 'NEW'
        );

        CREATE INDEX IF NOT EXISTS idx_collision_actions_status_created
        ON collision_actions(status, created_at_utc);
        """
    )
    conn.commit()


def _row_to_case(row: sqlite3.Row) -> CollisionCase:
    return CollisionCase(
        case_id=str(row["case_id"]),
        item_name=str(row["item_name"]),
        pending_type=str(row["pending_type"]),
        status=str(row["status"]),
        created_at_utc=str(row["created_at_utc"]),
        updated_at_utc=str(row["updated_at_utc"]),
        pending_item_ids=_json_load_list(str(row["pending_item_ids_json"])),
        default_remove_ids=_json_load_list(str(row["default_remove_ids_json"])),
        summary=str(row["summary"]),
        session_id=row["session_id"],
        version=int(row["version"]),
    )


def get_in_fridge_instances(conn: sqlite3.Connection, item_name: str) -> List[ItemInstanceState]:
    rows = conn.execute(
        """
        SELECT id,
               item_name,
               status,
               session_id,
               confidence,
               track_id,
               case_id,
               pending_type,
               event_time_utc
        FROM events
        WHERE item_name = ?
          AND status = 'IN_FRIDGE'
        ORDER BY event_time_utc ASC, id ASC;
        """,
        (item_name,),
    ).fetchall()
    return [
        ItemInstanceState(
            item_id=str(row[0]),
            item_name=str(row[1]),
            current_status=str(row[2]),
            session_id=row[3],
            confidence=row[4],
            track_id=row[5],
            case_id=row[6],
            pending_type=row[7],
            put_in_time_utc=str(row[8]),
        )
        for row in rows
    ]


def get_open_collision_case(
    conn: sqlite3.Connection,
    *,
    item_name: str,
    pending_type: str,
) -> Optional[CollisionCase]:
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        """
        SELECT case_id,
               item_name,
               pending_type,
               status,
               created_at_utc,
               updated_at_utc,
               pending_item_ids_json,
               default_remove_ids_json,
               summary,
               session_id,
               version
        FROM collision_cases
        WHERE item_name = ?
          AND pending_type = ?
          AND status = 'OPEN'
        LIMIT 1;
        """,
        (item_name, pending_type),
    ).fetchone()
    conn.row_factory = None
    if row is None:
        return None
    return _row_to_case(row)


def _build_case_summary(item_name: str, pending_type: str, pending_item_ids: Sequence[str]) -> str:
    if pending_type == PENDING_AMBIGUOUS_MULTI_REMOVE:
        count = len(list(pending_item_ids))
        suffix = "item" if count == 1 else "items"
        return f"Confirm which {item_name} {suffix} should be removed ({count} pending choice(s))."
    return f"Resolve pending case for {item_name}."


def _get_item_row(conn: sqlite3.Connection, item_id: str) -> Optional[sqlite3.Row]:
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        """
        SELECT id,
               item_name,
               status,
               session_id,
               confidence,
               track_id,
               case_id,
               pending_type,
               event_time_utc
        FROM events
        WHERE id = ?
        LIMIT 1;
        """,
        (item_id,),
    ).fetchone()
    conn.row_factory = None
    return row


def _insert_inventory_item(
    conn: sqlite3.Connection,
    *,
    session_id: Optional[str],
    event_time_utc: str,
    item_name: str,
    confidence: Optional[float],
    track_id: Optional[int],
) -> int:
    cursor = conn.execute(
        """
        INSERT INTO events (
          session_id,
          event_time_utc,
          item_name,
          status,
          confidence,
          track_id,
          pending_type,
          case_id,
          updated_at_utc
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
        """,
        (
            session_id,
            event_time_utc,
            item_name,
            STATUS_IN_FRIDGE,
            confidence,
            track_id,
            None,
            None,
            event_time_utc,
        ),
    )
    return int(cursor.lastrowid)


def _update_item_status(
    conn: sqlite3.Connection,
    *,
    item_id: str,
    session_id: Optional[str],
    updated_at_utc: str,
    status: str,
    confidence: Optional[float],
    track_id: Optional[int],
    pending_type: Optional[str] = None,
    case_id: Optional[str] = None,
) -> None:
    conn.execute(
        """
        UPDATE events
        SET session_id = ?,
            status = ?,
            confidence = ?,
            track_id = ?,
            pending_type = ?,
            case_id = ?,
            updated_at_utc = ?
        WHERE id = ?;
        """,
        (
            session_id,
            status,
            confidence,
            track_id,
            pending_type,
            case_id,
            updated_at_utc,
            int(item_id),
        ),
    )


class CollisionResolver:
    def __init__(self, conn: sqlite3.Connection, *, auto_resolve_window_seconds: int = 60):
        self.conn = conn
        self.auto_resolve_window_seconds = auto_resolve_window_seconds
        ensure_collision_schema(self.conn)

    def apply_operations(self, operations: Sequence[ParsedOperation]) -> int:
        if not operations:
            return 0

        changed = 0
        self.conn.execute("BEGIN IMMEDIATE;")
        try:
            for operation in operations:
                if operation.operation == OPERATION_PUT_IN:
                    changed += self._handle_put_in(operation)
                elif operation.operation == OPERATION_TAKE_OUT:
                    changed += self._handle_take_out(operation)
                else:
                    raise ValueError(f"Unsupported operation: {operation.operation}")
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        return changed

    def _handle_put_in(self, operation: ParsedOperation) -> int:
        _insert_inventory_item(
            self.conn,
            session_id=operation.session_id,
            event_time_utc=operation.event_time_utc,
            item_name=operation.item_name,
            confidence=operation.confidence,
            track_id=operation.track_id,
        )
        return 1

    def _handle_take_out(self, operation: ParsedOperation) -> int:
        candidates = get_in_fridge_instances(self.conn, operation.item_name)
        if not candidates:
            return 0

        if len(candidates) == 1:
            candidate = candidates[0]
            _update_item_status(
                self.conn,
                item_id=candidate.item_id,
                session_id=operation.session_id,
                updated_at_utc=operation.event_time_utc,
                status=STATUS_REMOVED,
                confidence=operation.confidence,
                track_id=operation.track_id,
            )
            return 1

        span_seconds = (
            _parse_utc(candidates[-1].put_in_time_utc) - _parse_utc(candidates[0].put_in_time_utc)
        ).total_seconds()
        default_candidate = candidates[0]

        if span_seconds <= self.auto_resolve_window_seconds:
            _update_item_status(
                self.conn,
                item_id=default_candidate.item_id,
                session_id=operation.session_id,
                updated_at_utc=operation.event_time_utc,
                status=STATUS_REMOVED,
                confidence=operation.confidence,
                track_id=operation.track_id,
            )
            return 1

        case = get_open_collision_case(
            self.conn,
            item_name=operation.item_name,
            pending_type=PENDING_AMBIGUOUS_MULTI_REMOVE,
        )
        pending_ids = [] if case is None else list(case.pending_item_ids)
        default_remove_ids = [] if case is None else list(case.default_remove_ids)

        if default_candidate.item_id not in pending_ids:
            pending_ids.append(default_candidate.item_id)
        if default_candidate.item_id not in default_remove_ids:
            default_remove_ids.append(default_candidate.item_id)

        now = operation.event_time_utc
        if case is None:
            case_id = str(uuid.uuid4())
            self.conn.execute(
                """
                INSERT INTO collision_cases (
                  case_id,
                  item_name,
                  pending_type,
                  status,
                  created_at_utc,
                  updated_at_utc,
                  pending_item_ids_json,
                  default_remove_ids_json,
                  summary,
                  session_id,
                  version
                )
                VALUES (?, ?, ?, 'OPEN', ?, ?, ?, ?, ?, ?, 0);
                """,
                (
                    case_id,
                    operation.item_name,
                    PENDING_AMBIGUOUS_MULTI_REMOVE,
                    now,
                    now,
                    _json_dump_list(pending_ids),
                    _json_dump_list(default_remove_ids),
                    _build_case_summary(operation.item_name, PENDING_AMBIGUOUS_MULTI_REMOVE, pending_ids),
                    operation.session_id,
                ),
            )
        else:
            case_id = case.case_id
            self.conn.execute(
                """
                UPDATE collision_cases
                SET updated_at_utc = ?,
                    pending_item_ids_json = ?,
                    default_remove_ids_json = ?,
                    summary = ?,
                    session_id = COALESCE(session_id, ?),
                    version = version + 1
                WHERE case_id = ?;
                """,
                (
                    now,
                    _json_dump_list(pending_ids),
                    _json_dump_list(default_remove_ids),
                    _build_case_summary(operation.item_name, PENDING_AMBIGUOUS_MULTI_REMOVE, pending_ids),
                    operation.session_id,
                    case_id,
                ),
            )

        _update_item_status(
            self.conn,
            item_id=default_candidate.item_id,
            session_id=operation.session_id,
            updated_at_utc=operation.event_time_utc,
            status=STATUS_PENDING,
            confidence=operation.confidence,
            track_id=operation.track_id,
            pending_type=PENDING_AMBIGUOUS_MULTI_REMOVE,
            case_id=case_id,
        )
        return 1


class CollisionActionConsumer:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        ensure_collision_schema(self.conn)

    def process_new_actions(self, *, limit: Optional[int] = None) -> int:
        query = """
            SELECT action_id
            FROM collision_actions
            WHERE status = 'NEW'
            ORDER BY created_at_utc ASC, action_id ASC
        """
        params: Sequence[object] = ()
        if limit is not None:
            query += " LIMIT ?"
            params = (limit,)

        action_ids = [str(row[0]) for row in self.conn.execute(query, params).fetchall()]
        processed = 0
        for action_id in action_ids:
            processed += self._process_action(action_id)
        return processed

    def poll_forever(self, *, interval_seconds: float = 1.0) -> None:
        while True:
            self.process_new_actions()
            time.sleep(interval_seconds)

    def _process_action(self, action_id: str) -> int:
        self.conn.execute("BEGIN IMMEDIATE;")
        try:
            self.conn.row_factory = sqlite3.Row
            action = self.conn.execute(
                """
                SELECT action_id,
                       case_id,
                       created_at_utc,
                       remove_item_ids_json,
                       note,
                       processed_at_utc,
                       status
                FROM collision_actions
                WHERE action_id = ?
                LIMIT 1;
                """,
                (action_id,),
            ).fetchone()
            self.conn.row_factory = None
            if action is None:
                self.conn.commit()
                return 0
            if action["status"] == ACTION_STATUS_PROCESSED:
                self.conn.commit()
                return 0
            if action["status"] != ACTION_STATUS_NEW:
                self.conn.commit()
                return 0

            self.conn.row_factory = sqlite3.Row
            case_row = self.conn.execute(
                """
                SELECT case_id,
                       item_name,
                       pending_type,
                       status,
                       created_at_utc,
                       updated_at_utc,
                       pending_item_ids_json,
                       default_remove_ids_json,
                       summary,
                       session_id,
                       version
                FROM collision_cases
                WHERE case_id = ?
                LIMIT 1;
                """,
                (action["case_id"],),
            ).fetchone()
            self.conn.row_factory = None
            if case_row is None or case_row["status"] != CASE_STATUS_OPEN:
                rejected_at = _utc_now()
                self.conn.execute(
                    """
                    UPDATE collision_actions
                    SET status = ?, processed_at_utc = ?
                    WHERE action_id = ?;
                    """,
                    (ACTION_STATUS_REJECTED, rejected_at, action_id),
                )
                self.conn.commit()
                return 0

            case = _row_to_case(case_row)
            requested_remove_ids = _json_load_list(str(action["remove_item_ids_json"]))
            candidate_ids = self._candidate_ids_for_case(case)
            remove_ids = [item_id for item_id in requested_remove_ids if item_id in candidate_ids]

            for item_id in remove_ids:
                current = _get_item_row(self.conn, item_id)
                if current is None or current["status"] == STATUS_REMOVED:
                    continue
                _update_item_status(
                    self.conn,
                    item_id=item_id,
                    session_id=case.session_id,
                    updated_at_utc=str(action["created_at_utc"]),
                    status=STATUS_REMOVED,
                    confidence=current["confidence"],
                    track_id=current["track_id"],
                    case_id=case.case_id,
                )

            for pending_id in case.pending_item_ids:
                if pending_id in remove_ids:
                    continue
                current = _get_item_row(self.conn, pending_id)
                if current is None or current["status"] != STATUS_PENDING:
                    continue
                _update_item_status(
                    self.conn,
                    item_id=pending_id,
                    session_id=case.session_id,
                    updated_at_utc=str(action["created_at_utc"]),
                    status=STATUS_IN_FRIDGE,
                    confidence=current["confidence"],
                    track_id=current["track_id"],
                )

            processed_at = _utc_now()
            self.conn.execute(
                """
                UPDATE collision_cases
                SET status = 'RESOLVED',
                    updated_at_utc = ?,
                    summary = ?,
                    version = version + 1
                WHERE case_id = ?;
                """,
                (
                    processed_at,
                    f"Resolved collision case for {case.item_name}.",
                    case.case_id,
                ),
            )
            self.conn.execute(
                """
                UPDATE collision_actions
                SET status = 'PROCESSED',
                    processed_at_utc = ?
                WHERE action_id = ?;
                """,
                (processed_at, action_id),
            )
            self.conn.commit()
            return 1
        except Exception:
            self.conn.rollback()
            raise
        finally:
            self.conn.row_factory = None

    def _candidate_ids_for_case(self, case: CollisionCase) -> List[str]:
        rows = self.conn.execute(
            """
            SELECT CAST(id AS TEXT)
            FROM events
            WHERE item_name = ?
              AND status IN ('IN_FRIDGE', 'PENDING')
            ORDER BY event_time_utc ASC, id ASC;
            """,
            (case.item_name,),
        ).fetchall()
        candidate_ids = [str(row[0]) for row in rows]
        for pending_id in case.pending_item_ids:
            if pending_id not in candidate_ids:
                candidate_ids.append(pending_id)
        return candidate_ids
