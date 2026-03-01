from __future__ import annotations

import sqlite3
from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Dict, List, Optional, Set, Tuple

from collision_resolver import (
    CollisionResolver,
    OPERATION_PUT_IN,
    OPERATION_TAKE_OUT,
    ParsedOperation,
    ensure_collision_schema,
)
from models import VisionSession


@dataclass(frozen=True)
class DbEvent:
    session_id: str
    event_time_utc: str
    item_name: str
    status: str
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


def ensure_schema_v2(conn: sqlite3.Connection) -> None:
    ensure_collision_schema(conn)


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
    operations = infer_operations_from_vision_session(
        session,
        outside_evidence_min=outside_evidence_min,
        inside_hit_min=inside_hit_min,
        outside_hit_min=outside_hit_min,
        window_frames=window_frames,
        stable_after_transition=stable_after_transition,
        miss_grace=miss_grace,
    )
    return [
        DbEvent(
            session_id=operation.session_id,
            event_time_utc=operation.event_time_utc,
            item_name=operation.item_name,
            status="IN_FRIDGE" if operation.operation == OPERATION_PUT_IN else "REMOVED",
            confidence=operation.confidence,
            track_id=operation.track_id,
        )
        for operation in operations
    ]


def infer_operations_from_vision_session(
    session: VisionSession,
    *,
    outside_evidence_min: int = 2,
    inside_hit_min: int = 2,
    outside_hit_min: int = 2,
    window_frames: int = 5,
    stable_after_transition: int = 3,
    miss_grace: int = 2,
) -> List[ParsedOperation]:
    states: Dict[Tuple[int, str], _TrackState] = {}
    out: List[ParsedOperation] = []

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
                            ParsedOperation(
                                session_id=session.session_id,
                                event_time_utc=sample.timestamp,
                                item_name=det.class_name,
                                operation=OPERATION_PUT_IN,
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
                            ParsedOperation(
                                session_id=session.session_id,
                                event_time_utc=sample.timestamp,
                                item_name=det.class_name,
                                operation=OPERATION_TAKE_OUT,
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
    ensure_collision_schema(conn)
    operations = [
        ParsedOperation(
            session_id=event.session_id,
            event_time_utc=event.event_time_utc,
            item_name=event.item_name,
            operation=OPERATION_PUT_IN if event.status == "IN_FRIDGE" else OPERATION_TAKE_OUT,
            confidence=event.confidence,
            track_id=event.track_id,
        )
        for event in events
    ]
    return CollisionResolver(conn).apply_operations(operations)


def insert_events(conn: sqlite3.Connection, events: List[DbEvent]) -> int:
    return apply_events(conn, events)
