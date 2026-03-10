from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Dict, List, Optional

try:
    from .collision_resolver import (
        CollisionResolver,
        OPERATION_PUT_IN,
        OPERATION_TAKE_OUT,
        ParsedOperation,
        ensure_collision_schema,
    )
    from .models import VisionSession
except ImportError:
    from collision_resolver import (
        CollisionResolver,
        OPERATION_PUT_IN,
        OPERATION_TAKE_OUT,
        ParsedOperation,
        ensure_collision_schema,
    )
    from models import VisionSession


# Segment merge threshold: a same-class gap shorter than this stays in one segment.
MAX_MERGE_GAP_FRAMES = 5
# Minimum number of detection frames required before a segment can emit an event.
MIN_FRAMES = 3
# Minimum vertical movement needed to classify a segment as PUT_IN or TAKE_OUT.
MIN_DELTA_Y = 30


@dataclass(frozen=True)
class DbEvent:
    session_id: str
    event_time_utc: str
    item_name: str
    status: str
    confidence: float
    track_id: int


@dataclass
class _SegmentDetection:
    frame_no: int
    timestamp: str
    class_name: str
    confidence: float
    center_y: float
    track_id: int


@dataclass
class _DetectionSegment:
    class_name: str
    detections: List[_SegmentDetection]


@dataclass(frozen=True)
class SegmentDecision:
    class_name: str
    first_frame_index: int
    last_frame_index: int
    first_y: float
    last_y: float
    delta_y: float
    detection_count: int
    event_time_utc: str
    confidence: float
    track_id: int
    operation: Optional[str]


def _collect_detections_by_class(session: VisionSession) -> Dict[str, List[_SegmentDetection]]:
    grouped: Dict[str, List[_SegmentDetection]] = {}
    samples_sorted = sorted(session.samples, key=lambda s: (s.timestamp_ms, s.index))

    for frame_no, sample in enumerate(samples_sorted):
        detections_by_class: Dict[str, _SegmentDetection] = {}
        for det in sample.detections:
            candidate = _SegmentDetection(
                frame_no=frame_no,
                timestamp=sample.timestamp,
                class_name=det.class_name,
                confidence=float(det.confidence),
                center_y=float(det.center.y),
                track_id=int(det.track_id),
            )
            current = detections_by_class.get(det.class_name)
            if current is None or candidate.confidence > current.confidence:
                detections_by_class[det.class_name] = candidate

        for class_name, det in detections_by_class.items():
            grouped.setdefault(class_name, []).append(det)

    return grouped


def _merge_detections_into_segments(
    detections_by_class: Dict[str, List[_SegmentDetection]],
) -> List[_DetectionSegment]:
    segments: List[_DetectionSegment] = []

    for class_name, detections in detections_by_class.items():
        if not detections:
            continue

        current_segment: List[_SegmentDetection] = [detections[0]]
        last_frame_no = detections[0].frame_no

        for det in detections[1:]:
            gap_frames = det.frame_no - last_frame_no - 1
            if gap_frames >= MAX_MERGE_GAP_FRAMES:
                segments.append(_DetectionSegment(class_name=class_name, detections=current_segment))
                current_segment = [det]
            else:
                current_segment.append(det)
            last_frame_no = det.frame_no

        segments.append(_DetectionSegment(class_name=class_name, detections=current_segment))

    return segments


def _classify_segment(
    segment: _DetectionSegment,
) -> Optional[SegmentDecision]:
    if len(segment.detections) < MIN_FRAMES:
        return None

    first_det = segment.detections[0]
    last_det = segment.detections[-1]
    delta_y = last_det.center_y - first_det.center_y

    if abs(delta_y) < MIN_DELTA_Y:
        operation = None
    else:
        operation = OPERATION_TAKE_OUT if delta_y > 0 else OPERATION_PUT_IN

    return SegmentDecision(
        class_name=segment.class_name,
        first_frame_index=first_det.frame_no,
        last_frame_index=last_det.frame_no,
        first_y=first_det.center_y,
        last_y=last_det.center_y,
        delta_y=delta_y,
        detection_count=len(segment.detections),
        event_time_utc=last_det.timestamp,
        confidence=last_det.confidence,
        track_id=first_det.track_id,
        operation=operation,
    )


def analyze_operation_segments(session: VisionSession) -> List[SegmentDecision]:
    # Public helper for notebooks/tests: exposes the same segment and direction
    # decisions used by the production operation inference path.
    detections_by_class = _collect_detections_by_class(session)
    segments = _merge_detections_into_segments(detections_by_class)

    decisions: List[SegmentDecision] = []
    for segment in sorted(segments, key=lambda seg: seg.detections[0].frame_no):
        decision = _classify_segment(segment)
        if decision is not None:
            decisions.append(decision)

    return decisions


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
    del (
        outside_evidence_min,
        inside_hit_min,
        outside_hit_min,
        window_frames,
        stable_after_transition,
        miss_grace,
    )

    operations: List[ParsedOperation] = []
    for decision in analyze_operation_segments(session):
        if decision.operation is None:
            continue
        operations.append(
            ParsedOperation(
                session_id=session.session_id,
                event_time_utc=decision.event_time_utc,
                item_name=decision.class_name,
                operation=decision.operation,
                confidence=decision.confidence,
                track_id=decision.track_id,
            )
        )

    return operations


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
