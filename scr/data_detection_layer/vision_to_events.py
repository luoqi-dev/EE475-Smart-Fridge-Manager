from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from models import VisionSession


@dataclass(frozen=True)
class DbEvent:
    session_id: str
    event_time_utc: str     # sample.timestamp (ISO-UTC-Z)
    item_name: str          # detection.class_name
    action: str             # 'PUT_IN' | 'TAKE_OUT'
    confidence: float
    track_id: int


def infer_db_events_from_vision_session(session: VisionSession) -> List[DbEvent]:
    """
    Convert VisionSession (detections timeline) into DB events.

    Rule (MVP):
      - Track per object key = (track_id, class_name)
      - Transition:
          False -> True  => PUT_IN
          True  -> False => TAKE_OUT
      - First observation sets baseline state; no event on first sighting.

    Returns:
      List[DbEvent] sorted by time order of samples.
    """
    # key: (track_id, class_name) -> last in_roi state (bool)
    last_state: Dict[Tuple[int, str], bool] = {}

    out: List[DbEvent] = []

    # Ensure time order
    samples_sorted = sorted(session.samples, key=lambda s: (s.timestamp_ms, s.index))

    for sample in samples_sorted:
        for det in sample.detections:
            key = (det.track_id, det.class_name)
            prev = last_state.get(key)

            # First time seeing this tracked object => establish baseline only
            if prev is None:
                last_state[key] = det.in_roi
                continue

            # Transition detection
            if prev is False and det.in_roi is True:
                out.append(
                    DbEvent(
                        session_id=session.session_id,
                        event_time_utc=sample.timestamp,
                        item_name=det.class_name,
                        action="PUT_IN",
                        confidence=float(det.confidence),
                        track_id=int(det.track_id),
                    )
                )
            elif prev is True and det.in_roi is False:
                out.append(
                    DbEvent(
                        session_id=session.session_id,
                        event_time_utc=sample.timestamp,
                        item_name=det.class_name,
                        action="TAKE_OUT",
                        confidence=float(det.confidence),
                        track_id=int(det.track_id),
                    )
                )

            # Update last state
            last_state[key] = det.in_roi

    return out


def insert_events(conn: sqlite3.Connection, events: List[DbEvent]) -> int:
    """
    Insert DbEvent list into minimal events table.

    Returns:
      number of inserted rows
    """
    if not events:
        return 0

    with conn:  # transaction
        conn.executemany(
            """
            INSERT INTO events (session_id, event_time_utc, item_name, action, confidence, track_id)
            VALUES (?, ?, ?, ?, ?, ?);
            """,
            [
                (e.session_id, e.event_time_utc, e.item_name, e.action, e.confidence, e.track_id)
                for e in events
            ],
        )
    return len(events)

