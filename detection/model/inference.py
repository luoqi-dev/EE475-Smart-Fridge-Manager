"""
Inference script: load YOLO model, detect fruits in each frame.
Output: per-frame fruit class and bounding box (coordinates).
Writes: data/sessions/<session_id>/vision.json (session_id from socket or auto-generated).
Output format matches data_detection_layer (bbox/center in pixels, track_id, in_roi, frame_hash).
"""
import hashlib
import json
import re
from pathlib import Path
from datetime import datetime, timezone
import cv2
from ultralytics import YOLO

# ---------- Config ----------
MODEL_DIR = Path(__file__).resolve().parent
REPO_ROOT = MODEL_DIR.parent.parent
EVENTS_DIR = MODEL_DIR / "events"
# data/sessions/<session_id>/ — hardware stores frames in frames/ subdir; AI writes vision.json in session dir
SESSIONS_BASE_DIR = REPO_ROOT / "data" / "sessions"
FRAMES_SUBDIR = "frames"  # hardware: data/sessions/<session_id>/frames/xxx.jpg
VISION_OUTPUT_BASE = REPO_ROOT / "data" / "sessions"

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
    h0, w0 = 480, 640
    demo = {
        "session_id": session_id,
        "metadata": {
            "camera_model": "",
            "resolution": "640x480",
            "fps": 2,
            "yolo_model": str(MODEL_PATH.name),
            "confidence_threshold": CONFIDENCE_THRESHOLD,
        },
        "samples": [],
    }
    sample_index = 0

    for idx, img_path in enumerate(image_paths):
        frame = cv2.imread(str(img_path))
        if frame is None:
            continue
        h, w = frame.shape[:2]
        h0, w0 = h, w

        results = model(frame, conf=CONFIDENCE_THRESHOLD, verbose=False)

        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        ts_ms = idx * 500
        detections_for_sample = []

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

                    x1_px = int(xyxy[0])
                    y1_px = int(xyxy[1])
                    x2_px = int(xyxy[2])
                    y2_px = int(xyxy[3])
                    w_px = x2_px - x1_px
                    h_px = y2_px - y1_px
                    x_center_px = (xyxy[0] + xyxy[2]) / 2.0
                    y_center_px = (xyxy[1] + xyxy[3]) / 2.0

                    det = {
                        "class_name": str(cls_name).lower(),
                        "confidence": round(float(conf), 2),
                        "bbox": {
                            "x": x1_px,
                            "y": y1_px,
                            "width": w_px,
                            "height": h_px,
                        },
                        "center": {"x": float(x_center_px), "y": float(y_center_px)},
                        "track_id": 0,
                        "in_roi": False,
                    }
                    detections_for_sample.append(det)
                    cv2.rectangle(frame, (x1_px, y1_px), (x2_px, y2_px), (0, 255, 0), 2)
                    label = f"{cls_name} {conf:.2f}"
                    cv2.putText(
                        frame, label, (x1_px, y1_px - 5),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1,
                    )

        frame_hash = hashlib.sha256(cv2.imencode(".jpg", frame)[1].tobytes()).hexdigest()
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

    demo["metadata"]["resolution"] = f"{w0}x{h0}"
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
