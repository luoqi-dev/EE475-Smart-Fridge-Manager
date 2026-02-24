"""
Inference script: load YOLO11 TFLite model, track objects, detect line crossing (PUT_IN/TAKE_OUT),
write JSON events to ./events/, and visualize with OpenCV.
Supports: image folder (001.jpg, 002.jpg, ...) for ~50-frame sequence.
"""
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
TRIGGER_Y = 0.5  # virtual line: horizontal at 50% image height (0~1). y < 0.5 = above, y > 0.5 = below

def _get_model_path():
    for name in ("fruit_model_optimized.tflite", "fruit_model_optimized.onnx", "fruit_model_optimized.pt"):
        p = MODEL_DIR / name
        if p.exists():
            return p
    pt = REPO_ROOT / "runs" / "fruit" / "yolo11n_fruit" / "weights" / "best.pt"
    if pt.exists():
        return pt
    raise FileNotFoundError("can't find model, run export_tflite_only.py or place fruit_model_optimized.onnx in detection/model")

MODEL_PATH = _get_model_path()

def ensure_events_dir():
    EVENTS_DIR.mkdir(parents=True, exist_ok=True)

def get_class_name(model, class_id: int) -> str:
    """Resolve class name from model names dict or id."""
    if hasattr(model, "names") and model.names is not None:
        return model.names.get(int(class_id), f"class_{class_id}")
    return f"class_{class_id}"

def run_inference_on_folder(image_dir: Path):
    """
    Process a folder of images (001.jpg, 002.jpg, ...) in numerical order.
    Uses model.track() with persist=True and bytetrack for identity persistence.
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

    # Collect image paths and sort numerically (001, 002, ...)
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

    # Load model (TFLite or .pt)
    if not MODEL_PATH.exists():
        raise FileNotFoundError(f"Model not found: {MODEL_PATH}")
    model = YOLO(str(MODEL_PATH))

    # State: track_id -> last side of line ("above" = y < 0.5, "below" = y > 0.5)
    side = {}  # track_id -> "above" | "below"
    put_in_count = 0
    take_out_count = 0

    for idx, img_path in enumerate(image_paths):
        frame = cv2.imread(str(img_path))
        if frame is None:
            continue
        h, w = frame.shape[:2]

        # track with persist and ByteTrack (Kalman-based identity persistence)
        results = model.track(
            frame,
            persist=True,
            tracker="bytetrack.yaml",
            verbose=False,
        )

        # Draw trigger line
        line_y = int(TRIGGER_Y * h)
        cv2.line(frame, (0, line_y), (w, line_y), (0, 255, 255), 2)
        cv2.putText(
            frame, "y=0.5", (10, line_y - 5),
            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1,
        )

        if results and len(results) > 0:
            r = results[0]
            boxes = r.boxes
            if boxes is not None:
                for i in range(len(boxes)):
                    box = boxes[i]
                    xyxy = box.xyxy[0].cpu().numpy()
                    tid = int(box.id[0]) if box.id is not None else None
                    cls_id = int(box.cls[0])
                    cls_name = get_class_name(model, cls_id)

                    # Centroid (normalized 0–1)
                    x_center = (xyxy[0] + xyxy[2]) / 2 / w
                    y_center = (xyxy[1] + xyxy[3]) / 2 / h

                    if tid is not None:
                        above_now = y_center < TRIGGER_Y
                        now_side = "above" if above_now else "below"

                        if tid in side:
                            prev_side = side[tid]
                            if prev_side == "above" and now_side == "below":
                                motion = "PUT_IN"
                                put_in_count += 1
                                write_event(img_path.stem, tid, cls_name, motion)
                            elif prev_side == "below" and now_side == "above":
                                motion = "TAKE_OUT"
                                take_out_count += 1
                                write_event(img_path.stem, tid, cls_name, motion)
                        side[tid] = now_side

                    # Draw box and label
                    x1, y1, x2, y2 = map(int, xyxy)
                    cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 0), 2)
                    label = f"{cls_name}" + (f" ID:{tid}" if tid is not None else "")
                    cv2.putText(
                        frame, label, (x1, y1 - 5),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1,
                    )

        # On-screen IN/OUT count
        cv2.putText(
            frame, f"PUT_IN: {put_in_count}  TAKE_OUT: {take_out_count}",
            (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2,
        )
        cv2.imshow("inference", frame)
        if cv2.waitKey(1) & 0xFF == ord("q"):
            break

    cv2.destroyAllWindows()
    print(f"Processed {len(image_paths)} frames. PUT_IN: {put_in_count}, TAKE_OUT: {take_out_count}")

def write_event(frame_id: str, track_id: int, object_class: str, motion_vector: str):
    """Write one JSON event to ./events/ for the Data Processing Layer watcher."""
    ensure_events_dir()
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    payload = {
        "timestamp": ts,
        "track_id": track_id,
        "object_class": object_class,
        "motion_vector": motion_vector,
    }
    fname = EVENTS_DIR / f"event_{frame_id}_{track_id}_{motion_vector}.json"
    with open(fname, "w") as f:
        json.dump(payload, f, indent=2)

if __name__ == "__main__":
    import sys
    # Default: detection/model/images (001.jpg, 002.jpg, ...). Or: python inference.py <path_to_image_folder>
    image_folder = MODEL_DIR / "images"
    if len(sys.argv) > 1:
        image_folder = Path(sys.argv[1])
    run_inference_on_folder(image_folder)
