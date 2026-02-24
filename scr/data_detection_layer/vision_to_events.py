from __future__ import annotations

import sqlite3
from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Dict, List, Optional, Set, Tuple

from models import VisionSession


@dataclass(frozen=True)
class DbEvent:
    session_id: str
    event_time_utc: str  # sample.timestamp (ISO-UTC-Z)
    item_name: str       # detection.class_name
    status: str          # 'IN_FRIDGE' | 'REMOVED'
    confidence: float
    track_id: int


@dataclass
class _TrackState:
    outside_evidence: int = 0
    has_inside_baseline: bool = False
    confirmed_in_fridge: bool = False
    inside_hits_window: Deque[int] = field(default_factory=deque)
    outside_hits_window: Deque[int] = field(default_factory=deque)
    pending_put_frame: Optional[int] = None
    pending_remove_frame: Optional[int] = None
    miss_count: int = 0


def _prune_window(window: Deque[int], current_frame: int, window_frames: int) -> None:
    while window and (current_frame - window[0]) >= window_frames:
        window.popleft()


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1;",
        (table_name,),
    ).fetchone()
    return row is not None


def _create_events_table_v2(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS events (
          id             INTEGER PRIMARY KEY AUTOINCREMENT,
          session_id     TEXT,
          event_time_utc TEXT NOT NULL,
          item_name      TEXT NOT NULL,
          status         TEXT NOT NULL CHECK(status IN ('IN_FRIDGE','REMOVED','PENDING')),
          confidence     REAL,
          track_id       INTEGER
        );

        CREATE INDEX IF NOT EXISTS idx_events_time ON events(event_time_utc);
        CREATE INDEX IF NOT EXISTS idx_events_item_time ON events(item_name, event_time_utc);
        """
    )


def ensure_schema_v2(conn: sqlite3.Connection) -> None:
    """
    Idempotently migrate events schema from v1 ACTION to v2 STATUS.
    """
    if not _table_exists(conn, "events"):
        _create_events_table_v2(conn)
        return

    cols = conn.execute("PRAGMA table_info(events);").fetchall()
    by_lower = {str(c[1]).lower(): str(c[1]) for c in cols}

    status_col = by_lower.get("status")
    action_col = by_lower.get("action")

    if status_col is None and action_col is not None:
        renamed = False
        try:
            conn.execute(f"ALTER TABLE events RENAME COLUMN {action_col} TO status;")
            renamed = True
        except sqlite3.OperationalError:
            renamed = False

        if not renamed:
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
                    ELSE UPPER(COALESCE(action, 'PENDING'))
                END
                WHERE status IS NULL OR TRIM(status) = '';
                """
            )

        cols = conn.execute("PRAGMA table_info(events);").fetchall()
        by_lower = {str(c[1]).lower(): str(c[1]) for c in cols}
        status_col = by_lower.get("status")
        action_col = by_lower.get("action")

    if status_col is not None and action_col is not None:
        conn.execute(
            f"""
            UPDATE events
            SET {status_col} = CASE UPPER(COALESCE({action_col}, ''))
                WHEN 'PUT_IN' THEN 'IN_FRIDGE'
                WHEN 'TAKE_OUT' THEN 'REMOVED'
                WHEN 'IN_FRIDGE' THEN 'IN_FRIDGE'
                WHEN 'REMOVED' THEN 'REMOVED'
                WHEN 'PENDING' THEN 'PENDING'
                ELSE UPPER(COALESCE({action_col}, 'PENDING'))
            END
            WHERE {status_col} IS NULL OR TRIM({status_col}) = '';
            """
        )

    if status_col is not None:
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

    _create_events_table_v2(conn)
    conn.commit()


def infer_db_events_from_vision_session(
    session: VisionSession,
    *,
    outside_evidence_min: int = 2,
    inside_hit_min: int = 2,
    outside_hit_min: int = 2,
    window_frames: int = 5,
    stable_after_transition: int = 3,
    miss_grace: int = 2,
) -> List[DbEvent]:
    """
    Convert VisionSession into status events with occlusion-tolerant evidence logic.
    """
    states: Dict[Tuple[int, str], _TrackState] = {}
    out: List[DbEvent] = []

    samples_sorted = sorted(session.samples, key=lambda s: (s.timestamp_ms, s.index))

    for frame_no, sample in enumerate(samples_sorted):
        seen_keys: Set[Tuple[int, str]] = set()

        for det in sample.detections:
            key = (int(det.track_id), det.class_name)
            seen_keys.add(key)

            st = states.get(key)
            if st is None:
                st = _TrackState()
                states[key] = st

            st.miss_count = 0

            if det.in_roi:
                st.has_inside_baseline = True
                st.inside_hits_window.append(frame_no)
                _prune_window(st.inside_hits_window, frame_no, window_frames)

                if st.pending_remove_frame is not None:
                    st.pending_remove_frame = None
                    st.outside_hits_window.clear()

                can_try_put = (
                    not st.confirmed_in_fridge
                    and st.outside_evidence >= outside_evidence_min
                    and len(st.inside_hits_window) >= inside_hit_min
                )
                if can_try_put:
                    if st.pending_put_frame is None:
                        st.pending_put_frame = frame_no
                    elif 0 < (frame_no - st.pending_put_frame) <= stable_after_transition:
                        out.append(
                            DbEvent(
                                session_id=session.session_id,
                                event_time_utc=sample.timestamp,
                                item_name=det.class_name,
                                status="IN_FRIDGE",
                                confidence=float(det.confidence),
                                track_id=int(det.track_id),
                            )
                        )
                        st.confirmed_in_fridge = True
                        st.pending_put_frame = None
                        st.outside_evidence = 0
                        st.inside_hits_window.clear()
                        st.outside_hits_window.clear()
                    elif (frame_no - st.pending_put_frame) > stable_after_transition:
                        st.pending_put_frame = frame_no
                else:
                    st.pending_put_frame = None
            else:
                st.outside_evidence += 1
                st.outside_hits_window.append(frame_no)
                _prune_window(st.outside_hits_window, frame_no, window_frames)

                if st.pending_put_frame is not None:
                    st.pending_put_frame = None
                    st.inside_hits_window.clear()

                can_try_remove = (
                    (st.has_inside_baseline or st.confirmed_in_fridge)
                    and len(st.outside_hits_window) >= outside_hit_min
                )
                if can_try_remove:
                    if st.pending_remove_frame is None:
                        st.pending_remove_frame = frame_no
                    elif 0 < (frame_no - st.pending_remove_frame) <= stable_after_transition:
                        out.append(
                            DbEvent(
                                session_id=session.session_id,
                                event_time_utc=sample.timestamp,
                                item_name=det.class_name,
                                status="REMOVED",
                                confidence=float(det.confidence),
                                track_id=int(det.track_id),
                            )
                        )
                        st.confirmed_in_fridge = False
                        st.pending_remove_frame = None
                        st.has_inside_baseline = False
                        st.outside_hits_window.clear()
                        st.inside_hits_window.clear()
                    elif (frame_no - st.pending_remove_frame) > stable_after_transition:
                        st.pending_remove_frame = frame_no
                else:
                    st.pending_remove_frame = None

        for key in list(states.keys()):
            if key in seen_keys:
                continue
            st = states[key]
            st.miss_count += 1
            if st.miss_count > miss_grace:
                del states[key]

    return out


def apply_events(conn: sqlite3.Connection, events: List[DbEvent]) -> int:
    """
    Apply events using FIFO inventory semantics in one IMMEDIATE transaction.

    IN_FRIDGE: insert new row.
    REMOVED: mark earliest IN_FRIDGE row for item_name as REMOVED (no new row).
    """
    ensure_schema_v2(conn)

    if not events:
        return 0

    changed = 0
    conn.execute("BEGIN IMMEDIATE;")
    try:
        for e in events:
            if e.status == "IN_FRIDGE":
                conn.execute(
                    """
                    INSERT INTO events (session_id, event_time_utc, item_name, status, confidence, track_id)
                    VALUES (?, ?, ?, ?, ?, ?);
                    """,
                    (e.session_id, e.event_time_utc, e.item_name, e.status, e.confidence, e.track_id),
                )
                changed += 1
            elif e.status == "REMOVED":
                row = conn.execute(
                    """
                    SELECT id
                    FROM events
                    WHERE item_name=? AND status='IN_FRIDGE'
                    ORDER BY event_time_utc ASC, id ASC
                    LIMIT 1;
                    """,
                    (e.item_name,),
                ).fetchone()
                if row is not None:
                    conn.execute("UPDATE events SET status='REMOVED' WHERE id=?;", (int(row[0]),))
                    changed += 1
            else:
                raise ValueError(f"Unsupported status: {e.status}")
        conn.commit()
    except Exception:
        conn.rollback()
        raise

    return changed


def insert_events(conn: sqlite3.Connection, events: List[DbEvent]) -> int:
    """
    Backward-compatible wrapper.
    """
    return apply_events(conn, events)
