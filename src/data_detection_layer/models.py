from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional


# ----------------------------
# Contract (Vision Schema)
# ----------------------------
# This module defines the *internal trusted model* for Vision packets.
# Rule of thumb:
# - JSON (dict) is external/untrusted
# - dataclass is internal/trusted
#
# Top-level contract:
# - session_id: str (required)  e.g., "session_YYYYMMDD_HHMMSS" generated from session start time
# - metadata: Metadata (required)
# - samples: List[Sample] (required, can be empty)
# - roi_definition: RoiDefinition (required)
#
# Validation philosophy:
# - Fail fast on missing required fields or type/value violations
# - Allow some optional fields where schema indicates "can be absent"
#   (e.g., frame_path can be missing for some samples in your example)


@dataclass(frozen=True)
class BBox:
    """
    Contract:
    - x, y: int >= 0 (pixel coordinate of top-left corner)
    - width, height: int > 0 (pixel size)
    """
    x: int
    y: int
    width: int
    height: int


@dataclass(frozen=True)
class Center:
    """
    Contract:
    - x, y: float (pixel coordinate of bbox center)
    Note: allow int/float in JSON, store as float internally.
    """
    x: float
    y: float


@dataclass(frozen=True)
class Detection:
    """
    Contract (one YOLO detection in a frame):
    - class_name: str (required)
    - confidence: float in [0.0, 1.0] (required)
    - bbox: BBox (required)
    - center: Center (required)
    - track_id: int >= 0 (required)
      'track_id' is persistent across frames for the same physical item
    - in_roi: bool (required)
      True if the item center (or bbox) is inside fridge ROI at detection time
    """
    class_name: str
    confidence: float
    bbox: BBox
    center: Center
    track_id: int
    in_roi: bool


@dataclass(frozen=True)
class Sample:
    """
    Contract (one frame sample):
    - index: int >= 0 (required) frame sequence index
    - timestamp: str (required) ISO-8601 UTC timestamp, e.g. "2026-01-30T14:30:52.123Z"
    - timestamp_ms: int >= 0 (required) milliseconds since session start
    - detections: List[Detection] (required, can be empty)
    - frame_hash: str (required) evidence hash (e.g., SHA-256 hex prefix)
    - frame_path: Optional[str] (optional) path to the stored frame image
      Your example shows it sometimes present, sometimes absent.
    """
    index: int
    timestamp: str
    timestamp_ms: int
    detections: List[Detection]
    frame_hash: str
    frame_path: Optional[str] = None


@dataclass(frozen=True)
class Metadata:
    """
    Contract (capture + model metadata):
    - camera_model: str (required)
    - resolution: str (required) format: "WIDTHxHEIGHT" (e.g. "640x480")
    - fps: int > 0 (required)
    - yolo_model: str (required)
    - confidence_threshold: float in [0.0, 1.0] (required)
    """
    camera_model: str
    resolution: str
    fps: int
    yolo_model: str
    confidence_threshold: float


@dataclass(frozen=True)
class RoiDefinition:
    """
    Contract:
    - name: str (required)
    - coordinates: BBox (required) ROI rectangle in pixel coordinates
    """
    name: str
    coordinates: BBox


@dataclass(frozen=True)
class VisionSession:
    """
    Contract (top-level packet):
    - session_id: str (required)
    - metadata: Metadata (required)
    - samples: List[Sample] (required, can be empty)
    - roi_definition: RoiDefinition (required)
    """
    session_id: str
    metadata: Metadata
    samples: List[Sample]
    roi_definition: RoiDefinition

