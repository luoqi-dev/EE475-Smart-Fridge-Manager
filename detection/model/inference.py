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

def run_inference_on_folder(image_dir: Path, show: bool = True):
    """
    Process a folder of images (001.jpg, 002.jpg, ...) in numerical order.
    Uses model.track() with persist=True and bytetrack for identity persistence.
    If show=False, no OpenCV window is shown (batch/headless mode).
    """
    ensure_events_dir()
    image_dir = Path(image_dir)
    if not image_dir.exists():
        image_dir.mkdir(parents=True, exist_ok=True)
        print(f"Created folder: {image_dir}")
        print("Put images there (e.g. 001.jpg, 002.jpg, ...) and run again, or run: python inference.py <path_to_folder>")
        return (0, 0)
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
        return (0, 0)

    # Load model (TFLite or .pt)
    if not MODEL_PATH.exists():
        raise FileNotFoundError(f"Model not found: {MODEL_PATH}")
    model = YOLO(str(MODEL_PATH))

    # State: track_id -> last side of line ("above" = y < 0.5, "below" = y > 0.5)
    side = {}  # track_id -> "above" | "below"
    put_in_count = 0
    take_out_count = 0

    # Demo-format output (session_id, metadata, samples, roi_definition)
    session_id = "session_" + datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    h0, w0 = 480, 640  # default if no frame read yet
    demo = {
        "session_id": session_id,
        "metadata": {
            "camera_model": "Raspberry Pi Camera Module v2",
            "resolution": "640x480",
            "fps": 2,
            "yolo_model": str(MODEL_PATH.name),
            "confidence_threshold": 0.5,
        },
        "samples": [],
        "roi_definition": {
            "name": "fridge_interior",
            "coordinates": {"x": 200, "y": 100, "width": 300, "height": 350},
        },
    }
    sample_index = 0

    for idx, img_path in enumerate(image_paths):
        frame = cv2.imread(str(img_path))
        if frame is None:
            continue
        h, w = frame.shape[:2]
        h0, w0 = h, w

        # track with persist and ByteTrack (Kalman-based identity persistence)
        results = model.track(
            frame,
            persist=True,
            tracker="bytetrack.yaml",
            verbose=False,
        )

        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        ts_ms = idx * 500  # assume ~2 fps
        frame_path_str = str(img_path.resolve())
        detections_for_sample = []
        motion_for_track = {}  # track_id -> "PUT_IN" | "TAKE_OUT" this frame

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
                    conf = float(box.conf[0]) if box.conf is not None else 0.0

                    # Bbox and center (normalized 0-1 for demo_format); use float() for JSON serializability
                    x1 = float(xyxy[0] / w)
                    y1 = float(xyxy[1] / h)
                    x2 = float(xyxy[2] / w)
                    y2 = float(xyxy[3] / h)
                    x_center = float((xyxy[0] + xyxy[2]) / 2 / w)
                    y_center = float((xyxy[1] + xyxy[3]) / 2 / h)

                    if tid is not None:
                        above_now = y_center < TRIGGER_Y
                        now_side = "above" if above_now else "below"

                        if tid in side:
                            prev_side = side[tid]
                            # Camera convention: above→below = take out, below→above = put in
                            if prev_side == "above" and now_side == "below":
                                motion = "TAKE_OUT"
                                take_out_count += 1
                                write_event(img_path.stem, tid, cls_name, motion)
                                motion_for_track[tid] = motion
                            elif prev_side == "below" and now_side == "above":
                                motion = "PUT_IN"
                                put_in_count += 1
                                write_event(img_path.stem, tid, cls_name, motion)
                                motion_for_track[tid] = motion
                        side[tid] = now_side

                    det = {
                        "class_name": str(cls_name),
                        "confidence": round(float(conf), 4),
                        "bbox": {
                            "x": float(round(x1, 4)), "y": float(round(y1, 4)),
                            "width": float(round(x2 - x1, 4)), "height": float(round(y2 - y1, 4)),
                        },
                        "center": {"x": float(round(x_center, 4)), "y": float(round(y_center, 4))},
                        "track_id": int(tid) if tid is not None else 0,
                        "in_roi": True,
                    }
                    if tid is not None and tid in motion_for_track:
                        det["motion_vector"] = motion_for_track[tid]
                    detections_for_sample.append(det)

                    # Draw box and label
                    x1_px, y1_px, x2_px, y2_px = map(int, xyxy)
                    cv2.rectangle(frame, (x1_px, y1_px), (x2_px, y2_px), (0, 255, 0), 2)
                    label = f"{cls_name}" + (f" ID:{tid}" if tid is not None else "")
                    cv2.putText(
                        frame, label, (x1_px, y1_px - 5),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1,
                    )

        demo["samples"].append({
            "frame_path": frame_path_str,
            "index": int(sample_index),
            "timestamp": ts,
            "timestamp_ms": int(ts_ms),
            "detections": detections_for_sample,
            "frame_hash": "",
        })
        sample_index += 1

        # On-screen IN/OUT count
        cv2.putText(
            frame, f"PUT_IN: {put_in_count}  TAKE_OUT: {take_out_count}",
            (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2,
        )
        if show:
            cv2.imshow("inference", frame)
            if cv2.waitKey(1) & 0xFF == ord("q"):
                break

    if show:
        cv2.destroyAllWindows()

    demo["metadata"]["resolution"] = f"{w0}x{h0}"
    out_path = EVENTS_DIR / f"{session_id}.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(demo, f, indent=2, ensure_ascii=False)
    print(f"Demo-format output: {out_path}")

    print(f"Processed {len(image_paths)} frames. PUT_IN (put in): {put_in_count}, TAKE_OUT (take out): {take_out_count}")
    return (put_in_count, take_out_count)

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
    # Default: detection/model/images. Or: python inference.py [--no-show] <path_to_image_folder>
    image_folder = MODEL_DIR / "images"
    show = True
    for arg in sys.argv[1:]:
        if arg == "--no-show":
            show = False
        elif not arg.startswith("-"):
            image_folder = Path(arg)
            break
    run_inference_on_folder(image_folder, show=show)
