"""
Inference script: load YOLO model, detect fruits in each frame.
Output: per-frame fruit class and bounding box (coordinates).
Writes: data/sessions/<session_id>/vision.json (session_id from socket or auto-generated).
Output format matches data_detection_layer (bbox/center in pixels, track_id, in_roi, frame_hash).
"""
import json
import math
import re
from pathlib import Path
from datetime import datetime, timezone
import cv2
from ultralytics import YOLO

# ---------- Config ----------
MODEL_DIR = Path(__file__).resolve().parent
# Runtime assumption: process is started from project root (EE475-Smart-Fridge-Manager).
REPO_ROOT = Path.cwd().resolve()
EVENTS_DIR = MODEL_DIR / "events"
# data/sessions/<session_id>/ — hardware stores frames in frames/ subdir; AI writes vision.json in session dir
SESSIONS_BASE_DIR = REPO_ROOT / "data" / "sessions"
FRAMES_SUBDIR = "frames"  # hardware: data/sessions/<session_id>/frames/xxx.jpg
VISION_OUTPUT_BASE = REPO_ROOT / "data" / "sessions"

# Shared frame-size convention for downstream geometry.
FRAME_WIDTH = 640
FRAME_HEIGHT = 480

ROI_X = 0
ROI_Y = 0
ROI_WIDTH = FRAME_WIDTH
ROI_HEIGHT = int(0.5 * FRAME_HEIGHT)

# Lightweight fallback tracker settings (class-aware nearest-center matching).
TRACK_MAX_DISTANCE_PX = 80.0
TRACK_MISS_GRACE = 2

def _get_model_path():
    for name in ("fruit_model_optimized_data2.onnx",):
        p = MODEL_DIR / name
        if p.exists():
            return p
    pt = REPO_ROOT / "runs" / "fruit" / "yolo11n_data2" / "weights" / "best.pt"
    if pt.exists():
        return pt
    raise FileNotFoundError("can't find model, run export_tflite_only.py or place fruit_model_optimized_data2.onnx in detection/model")

MODEL_PATH = _get_model_path()

# Fallback when model has no names (e.g. ONNX): same order as data_2/data.yaml (fruit_model_optimized_data2)
# class_0 = Apple, class_1 = Banana, ...
DATA2_CLASS_NAMES = ["Apple", "Banana", "Grapes", "Kiwi", "Mango", "Orange", "Pineapple", "Sugerapple", "Watermelon"]

# Only keep detections with confidence >= this (avoids "class_0" on blank frames)
CONFIDENCE_THRESHOLD = 0.65

def ensure_events_dir():
    EVENTS_DIR.mkdir(parents=True, exist_ok=True)

def get_class_name(model, class_id: int) -> str:
    """Resolve class name from model names dict or id. Uses DATA2_CLASS_NAMES when model has no names (e.g. ONNX)."""
    if hasattr(model, "names") and model.names is not None:
        return model.names.get(int(class_id), f"class_{class_id}")
    cid = int(class_id)
    if 0 <= cid < len(DATA2_CLASS_NAMES):
        return DATA2_CLASS_NAMES[cid]
    return f"class_{class_id}"

def run_inference_for_session(session_id: str, show: bool = False):
    """
    Run inference on the frames directory for the given session_id.
    Frames path: data/sessions/<session_id>/frames/ (e.g. 001.jpg, 002.jpg, ...).
    Called by socket listener when hardware sends SESSION_CLOSED.
    """
    frames_dir = SESSIONS_BASE_DIR / session_id / FRAMES_SUBDIR
    return run_inference_on_folder(frames_dir, show=show, session_id=session_id)


def run_inference_on_folder(image_dir: Path, show: bool = True, session_id=None):
    """
    Process a folder of images in numerical order.
    For each frame: detect fruits, output class + bbox (normalized 0-1).
    If show=False, no OpenCV window (batch/headless).
    If session_id is provided (e.g. from socket), use it for output JSON; else generate one.
    """
    ensure_events_dir()
    image_dir = Path(image_dir)
    if not image_dir.exists():
        image_dir.mkdir(parents=True, exist_ok=True)
        print(f"Created folder: {image_dir}")
        print("Put images there (e.g. 001.jpg, 002.jpg, ...) and run again, or run: python inference.py <path_to_folder>")
        return
    if not image_dir.is_dir():
        raise NotADirectoryError(str(image_dir))

    exts = {".jpg", ".jpeg", ".png", ".bmp"}
    paths = []
    for p in image_dir.iterdir():
        if p.suffix.lower() in exts:
            num = re.sub(r"[^0-9]", "", p.stem)
            paths.append((int(num) if num else 0, p))
    paths.sort(key=lambda x: x[0])
    image_paths = [p for _, p in paths]

    if not image_paths:
        print(f"No images found in {image_dir}")
        return

    if not MODEL_PATH.exists():
        raise FileNotFoundError(f"Model not found: {MODEL_PATH}")
    model = YOLO(str(MODEL_PATH))

    if session_id is None:
        session_id = "session_" + datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    next_track_id = 1
    active_tracks = {}
    demo = {
        "session_id": session_id,
        "metadata": {
            "camera_model": "U20CAM-1080p-1",
            "resolution": f"{FRAME_WIDTH}x{FRAME_HEIGHT}",
            "fps": 30,
            "yolo_model": str(MODEL_PATH.name),
            "confidence_threshold": CONFIDENCE_THRESHOLD,
        },
        "samples": [],
        "roi_definition": {
            "name": "fridge_interior",
            "coordinates": {
                "x": ROI_X,
                "y": ROI_Y,
                "width": ROI_WIDTH,
                "height": ROI_HEIGHT,
            },
        },
    }
    sample_index = 0

    for idx, img_path in enumerate(image_paths):
        frame = cv2.imread(str(img_path))
        if frame is None:
            continue
        h, w = frame.shape[:2]
        scale_x = FRAME_WIDTH / float(max(w, 1))
        scale_y = FRAME_HEIGHT / float(max(h, 1))

        results = model(frame, conf=CONFIDENCE_THRESHOLD, verbose=False)

        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        ts_ms = idx * 500
        raw_detections = []

        if results and len(results) > 0:
            r = results[0]
            boxes = r.boxes
            if boxes is not None:
                for i in range(len(boxes)):
                    box = boxes[i]
                    xyxy = box.xyxy[0].cpu().numpy()
                    cls_id = int(box.cls[0])
                    cls_name = get_class_name(model, cls_id)
                    conf = float(box.conf[0]) if box.conf is not None else 0.0
                    if conf < CONFIDENCE_THRESHOLD:
                        continue

                    x1_px = int(round(float(xyxy[0]) * scale_x))
                    y1_px = int(round(float(xyxy[1]) * scale_y))
                    x2_px = int(round(float(xyxy[2]) * scale_x))
                    y2_px = int(round(float(xyxy[3]) * scale_y))
                    x1_px = max(0, min(FRAME_WIDTH - 1, x1_px))
                    y1_px = max(0, min(FRAME_HEIGHT - 1, y1_px))
                    x2_px = max(x1_px + 1, min(FRAME_WIDTH, x2_px))
                    y2_px = max(y1_px + 1, min(FRAME_HEIGHT, y2_px))
                    w_px = max(1, x2_px - x1_px)
                    h_px = max(1, y2_px - y1_px)
                    x_center_px = x1_px + (w_px / 2.0)
                    y_center_px = y1_px + (h_px / 2.0)

                    raw_detections.append(
                        {
                            "class_name": str(cls_name).lower(),
                            "confidence": round(float(conf), 2),
                            "bbox": {
                                "x": x1_px,
                                "y": y1_px,
                                "width": w_px,
                                "height": h_px,
                            },
                            "center": {"x": float(x_center_px), "y": float(y_center_px)},
                        }
                    )
                    cv2.rectangle(frame, (x1_px, y1_px), (x2_px, y2_px), (0, 255, 0), 2)
                    label = f"{cls_name} {conf:.2f}"
                    cv2.putText(
                        frame, label, (x1_px, y1_px - 5),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1,
                    )

        available_track_ids = set(active_tracks.keys())
        assigned_track_ids = set()
        detections_for_sample = []
        for det in raw_detections:
            cls_name = det["class_name"]
            cx = float(det["center"]["x"])
            cy = float(det["center"]["y"])
            best_id = None
            best_dist = None
            for tid in available_track_ids:
                if tid in assigned_track_ids:
                    continue
                track = active_tracks.get(tid)
                if track is None or track["class_name"] != cls_name:
                    continue
                tx, ty = track["center"]
                dist = math.hypot(cx - tx, cy - ty)
                if dist > TRACK_MAX_DISTANCE_PX:
                    continue
                if best_dist is None or dist < best_dist:
                    best_dist = dist
                    best_id = tid

            if best_id is None:
                best_id = next_track_id
                next_track_id += 1
                active_tracks[best_id] = {"class_name": cls_name, "center": (cx, cy), "misses": 0}
            else:
                active_tracks[best_id]["center"] = (cx, cy)
                active_tracks[best_id]["misses"] = 0

            # y_up: downside is the starting point, increasing upwards (bottom=0, up=positive); use y_up to check in_roi
            y_up_center = h - cy
            y_up_roi_min = h - (ROI_Y + ROI_HEIGHT)
            y_up_roi_max = h - ROI_Y
            in_roi = (
                ROI_X <= cx < (ROI_X + ROI_WIDTH)
                and y_up_roi_min <= y_up_center <= y_up_roi_max
            )
            det["track_id"] = int(best_id)
            det["in_roi"] = bool(in_roi)
            detections_for_sample.append(det)
            assigned_track_ids.add(best_id)

        for tid in list(active_tracks.keys()):
            if tid in assigned_track_ids:
                continue
            active_tracks[tid]["misses"] += 1
            if active_tracks[tid]["misses"] > TRACK_MISS_GRACE:
                del active_tracks[tid]

        # Temporary rule: frame_hash mirrors one representative track_id.
        frame_hash = str(detections_for_sample[0]["track_id"]) if detections_for_sample else "0"
        demo["samples"].append({
            "index": int(sample_index),
            "timestamp": ts,
            "timestamp_ms": int(ts_ms),
            "detections": detections_for_sample,
            "frame_hash": frame_hash,
        })
        sample_index += 1

        if show:
            cv2.imshow("inference", frame)
            if cv2.waitKey(1) & 0xFF == ord("q"):
                break

    if show:
        cv2.destroyAllWindows()

    # output to data/sessions/<session_id>/vision.json
    out_dir = VISION_OUTPUT_BASE / session_id
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "vision.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(demo, f, indent=2, ensure_ascii=False)
    print(f"Output: {out_path}")
    print(f"Processed {len(image_paths)} frames.")

if __name__ == "__main__":
    import sys
    image_folder = MODEL_DIR / "images"
    show = True
    for arg in sys.argv[1:]:
        if arg == "--no-show":
            show = False
        elif not arg.startswith("-"):
            image_folder = Path(arg)
            break
    run_inference_on_folder(image_folder, show=show)
