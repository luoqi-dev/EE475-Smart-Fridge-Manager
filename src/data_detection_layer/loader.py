from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

try:
    from .models import (
        BBox,
        Center,
        Detection,
        Metadata,
        RoiDefinition,
        Sample,
        VisionSession,
    )
except ImportError:
    from models import (
        BBox,
        Center,
        Detection,
        Metadata,
        RoiDefinition,
        Sample,
        VisionSession,
    )


# ----------------------------
# Explicit Errors (Fail Fast)
# ----------------------------

class VisionPacketError(Exception):
    """Base class for all Vision packet validation errors."""


class MissingFieldError(VisionPacketError):
    """Raised when a required field is missing."""


class TypeValidationError(VisionPacketError):
    """Raised when a field has the wrong Python type (e.g., str vs int)."""


class ValueValidationError(VisionPacketError):
    """Raised when a field value violates constraints (range/format/etc.)."""


# ----------------------------
# Helpers
# ----------------------------

_SESSION_ID_RE = re.compile(r"^session_\d{8}_\d{6}$")  # session_YYYYMMDD_HHMMSS
_RESOLUTION_RE = re.compile(r"^\d+x\d+$")             # "640x480"
_ISO_UTC_Z_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z$")


def _require(obj: Dict[str, Any], key: str, path: str) -> Any:
    if key not in obj:
        raise MissingFieldError(f"Missing required field at {path}: '{key}'")
    return obj[key]


def _require_type(value: Any, expected: type, path: str) -> None:
    if not isinstance(value, expected):
        raise TypeValidationError(
            f"Type error at {path}: expected {expected.__name__}, got {type(value).__name__}"
        )


def _require_int_ge(value: Any, min_v: int, path: str) -> int:
    _require_type(value, int, path)
    if value < min_v:
        raise ValueValidationError(f"Value error at {path}: expected int >= {min_v}, got {value}")
    return value


def _require_int_gt(value: Any, min_v: int, path: str) -> int:
    _require_type(value, int, path)
    if value <= min_v:
        raise ValueValidationError(f"Value error at {path}: expected int > {min_v}, got {value}")
    return value


def _require_float_01(value: Any, path: str) -> float:
    # allow int/float in JSON; normalize to float
    if not isinstance(value, (int, float)):
        raise TypeValidationError(
            f"Type error at {path}: expected number (int/float), got {type(value).__name__}"
        )
    v = float(value)
    if not (0.0 <= v <= 1.0):
        raise ValueValidationError(f"Value error at {path}: expected in [0,1], got {v}")
    return v


def _require_str(value: Any, path: str) -> str:
    _require_type(value, str, path)
    return value


def _parse_bbox(raw: Dict[str, Any], path: str) -> BBox:
    _require_type(raw, dict, path)
    x = _require_int_ge(_require(raw, "x", path), 0, f"{path}.x")
    y = _require_int_ge(_require(raw, "y", path), 0, f"{path}.y")
    w = _require_int_gt(_require(raw, "width", path), 0, f"{path}.width")
    h = _require_int_gt(_require(raw, "height", path), 0, f"{path}.height")
    return BBox(x=x, y=y, width=w, height=h)


def _parse_center(raw: Dict[str, Any], path: str) -> Center:
    _require_type(raw, dict, path)
    x = _require(raw, "x", path)
    y = _require(raw, "y", path)
    if not isinstance(x, (int, float)):
        raise TypeValidationError(f"Type error at {path}.x: expected number, got {type(x).__name__}")
    if not isinstance(y, (int, float)):
        raise TypeValidationError(f"Type error at {path}.y: expected number, got {type(y).__name__}")
    return Center(x=float(x), y=float(y))


def _parse_detection(raw: Dict[str, Any], path: str) -> Detection:
    _require_type(raw, dict, path)
    class_name = _require_str(_require(raw, "class_name", path), f"{path}.class_name")
    confidence = _require_float_01(_require(raw, "confidence", path), f"{path}.confidence")
    bbox = _parse_bbox(_require(raw, "bbox", path), f"{path}.bbox")
    center = _parse_center(_require(raw, "center", path), f"{path}.center")
    track_id = _require_int_ge(_require(raw, "track_id", path), 0, f"{path}.track_id")

    in_roi = _require(raw, "in_roi", path)
    if not isinstance(in_roi, bool):
        raise TypeValidationError(f"Type error at {path}.in_roi: expected bool, got {type(in_roi).__name__}")

    return Detection(
        class_name=class_name,
        confidence=confidence,
        bbox=bbox,
        center=center,
        track_id=track_id,
        in_roi=in_roi,
    )


def _parse_sample(raw: Dict[str, Any], path: str) -> Sample:
    _require_type(raw, dict, path)

    index = _require_int_ge(_require(raw, "index", path), 0, f"{path}.index")

    timestamp = _require_str(_require(raw, "timestamp", path), f"{path}.timestamp")
    if not _ISO_UTC_Z_RE.match(timestamp):
        raise ValueValidationError(
            f"Value error at {path}.timestamp: expected ISO-8601 UTC with 'Z', got '{timestamp}'"
        )

    timestamp_ms = _require_int_ge(_require(raw, "timestamp_ms", path), 0, f"{path}.timestamp_ms")

    detections_raw = _require(raw, "detections", path)
    if not isinstance(detections_raw, list):
        raise TypeValidationError(f"Type error at {path}.detections: expected list, got {type(detections_raw).__name__}")

    detections: List[Detection] = []
    for j, det_raw in enumerate(detections_raw):
        detections.append(_parse_detection(det_raw, f"{path}.detections[{j}]"))

    frame_hash = _require_str(_require(raw, "frame_hash", path), f"{path}.frame_hash")
    # Optional: soft validation - hash should be non-empty
    if len(frame_hash.strip()) == 0:
        raise ValueValidationError(f"Value error at {path}.frame_hash: expected non-empty string")

    frame_path: Optional[str] = None
    if "frame_path" in raw and raw["frame_path"] is not None:
        frame_path = _require_str(raw["frame_path"], f"{path}.frame_path")
        if len(frame_path.strip()) == 0:
            raise ValueValidationError(f"Value error at {path}.frame_path: expected non-empty string")

    return Sample(
        index=index,
        timestamp=timestamp,
        timestamp_ms=timestamp_ms,
        detections=detections,
        frame_hash=frame_hash,
        frame_path=frame_path,
    )


def _parse_metadata(raw: Dict[str, Any], path: str) -> Metadata:
    _require_type(raw, dict, path)

    camera_model = _require_str(_require(raw, "camera_model", path), f"{path}.camera_model")

    resolution = _require_str(_require(raw, "resolution", path), f"{path}.resolution")
    if not _RESOLUTION_RE.match(resolution):
        raise ValueValidationError(
            f"Value error at {path}.resolution: expected 'WIDTHxHEIGHT' (e.g. 640x480), got '{resolution}'"
        )

    fps = _require_int_gt(_require(raw, "fps", path), 0, f"{path}.fps")

    yolo_model = _require_str(_require(raw, "yolo_model", path), f"{path}.yolo_model")

    confidence_threshold = _require_float_01(
        _require(raw, "confidence_threshold", path),
        f"{path}.confidence_threshold"
    )

    return Metadata(
        camera_model=camera_model,
        resolution=resolution,
        fps=fps,
        yolo_model=yolo_model,
        confidence_threshold=confidence_threshold,
    )


def _parse_roi_definition(raw: Dict[str, Any], path: str) -> RoiDefinition:
    _require_type(raw, dict, path)
    name = _require_str(_require(raw, "name", path), f"{path}.name")
    coords_raw = _require(raw, "coordinates", path)
    coords = _parse_bbox(coords_raw, f"{path}.coordinates")
    return RoiDefinition(name=name, coordinates=coords)


# ----------------------------
# Public Loader API
# ----------------------------

def load_and_validate_vision_json(path: str) -> VisionSession:
    """
    Pipeline:
    1) read JSON -> dict
    2) validate top-level keys + nested value constraints
    3) construct VisionSession dataclass (trusted internal model)

    Raises:
    - MissingFieldError: required fields missing
    - TypeValidationError: types mismatch
    - ValueValidationError: value/format invalid
    """
    with open(path, "r") as f:
        raw = json.load(f)

    if not isinstance(raw, dict):
        raise TypeValidationError(f"Type error at $: expected object(dict), got {type(raw).__name__}")

    # Top-level required fields
    session_id = _require_str(_require(raw, "session_id", "$"), "$.session_id")
    if not _SESSION_ID_RE.match(session_id):
        raise ValueValidationError(
            f"Value error at $.session_id: expected 'session_YYYYMMDD_HHMMSS', got '{session_id}'"
        )

    metadata = _parse_metadata(_require(raw, "metadata", "$"), "$.metadata")

    samples_raw = _require(raw, "samples", "$")
    if not isinstance(samples_raw, list):
        raise TypeValidationError(f"Type error at $.samples: expected list, got {type(samples_raw).__name__}")

    samples: List[Sample] = []
    for i, sample_raw in enumerate(samples_raw):
        samples.append(_parse_sample(sample_raw, f"$.samples[{i}]"))

    roi_definition = _parse_roi_definition(_require(raw, "roi_definition", "$"), "$.roi_definition")

    return VisionSession(
        session_id=session_id,
        metadata=metadata,
        samples=samples,
        roi_definition=roi_definition,
    )
