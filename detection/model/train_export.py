"""
Train YOLO11n on fruit dataset and export to TFLite (INT8) for Raspberry Pi.
- Dataset: ./data/data.yaml (train/valid under ./data)
- Output: best.pt then fruit_model_optimized.tflite
"""
import shutil
from pathlib import Path
from ultralytics import YOLO

# Paths (project root = EE475-Smart-Fridge-Manager)
PROJECT_ROOT = Path(__file__).resolve().parent
DATA_YAML = PROJECT_ROOT / "data" / "data.yaml"
OUTPUT_TFLITE = PROJECT_ROOT / "fruit_model_optimized.tflite"

def main():
    if not DATA_YAML.exists():
        raise FileNotFoundError(f"Dataset config not found: {DATA_YAML}")

    # 1. Train YOLO11n
    model = YOLO("yolo11n.pt")
    results = model.train(
        data=str(DATA_YAML),
        epochs=100,
        imgsz=640,
        device=0,  # 0 = first GPU; if PyTorch doesn't support your GPU (e.g. RTX 50 series sm_120), it will fall back to CPU
        project=str(PROJECT_ROOT / "runs" / "fruit"),
        name="yolo11n_fruit",
        exist_ok=True,
    )

    # 2. Locate best.pt (saved in run dir)
    best_pt = Path(results.save_dir) / "weights" / "best.pt"
    if not best_pt.exists():
        best_pt = Path(results.save_dir) / "best.pt"
    if not best_pt.exists():
        raise FileNotFoundError(f"best.pt not found under {results.save_dir}")

    # 3. Export to TFLite with INT8 quantization for Raspberry Pi
    export_model = YOLO(str(best_pt))
    export_model.export(
        format="tflite",
        int8=True,
        imgsz=640,
        data=str(DATA_YAML),
        nms=True,
    )

    # 4. Copy/move exported TFLite to desired name (export saves as best_saved_model/* or best_float32.tflite / best_integer_quant.tflite)
    export_dir = best_pt.parent
    for f in export_dir.glob("*.tflite"):
        shutil.copy(f, OUTPUT_TFLITE)
        print(f"Saved TFLite model: {OUTPUT_TFLITE}")
        break
    else:
        # try saved_model folder
        for tflite in Path(best_pt.parent).rglob("*.tflite"):
            shutil.copy(tflite, OUTPUT_TFLITE)
            print(f"Saved TFLite model: {OUTPUT_TFLITE}")
            break
        else:
            print("TFLite export completed; check run directory for .tflite file.")

if __name__ == "__main__":
    main()
