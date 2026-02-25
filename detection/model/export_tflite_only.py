"""
export best.pt for deploy 。try TFLite first if failed then  ONNX（Raspberry Pi could use with onnxruntime）。
"""
import sys
import shutil
from pathlib import Path
from ultralytics import YOLO

def _log(msg):
    print(msg, flush=True)

PROJECT_ROOT = Path(__file__).resolve().parent
DATA_YAML = PROJECT_ROOT / "data" / "data.yaml"
OUTPUT_TFLITE = PROJECT_ROOT / "fruit_model_optimized.tflite"
OUTPUT_ONNX = PROJECT_ROOT / "fruit_model_optimized.onnx"

BEST_PT = PROJECT_ROOT / "runs" / "fruit" / "yolo11n_fruit" / "weights" / "best.pt"

def copy_tflite(export_dir, out_path):
    for f in export_dir.glob("*.tflite"):
        shutil.copy(f, out_path)
        return True
    for f in export_dir.rglob("*.tflite"):
        shutil.copy(f, out_path)
        return True
    return False

def main():
    _log("export_tflite_only: start export")
    if not BEST_PT.exists():
        _log(f"can't find {BEST_PT}, please change BEST_PT to the actual best.pt path")
        return
    _log(f"use weights: {BEST_PT}")
    model = YOLO(str(BEST_PT))
    export_dir = BEST_PT.parent

    # 1. try TFLite (INT8 then FP32)
    for use_int8 in (True, False):
        try:
            model.export(
                format="tflite",
                int8=use_int8,
                imgsz=640,
                data=str(DATA_YAML),
                nms=True,
            )
            if copy_tflite(export_dir, OUTPUT_TFLITE):
                _log(f"saved TFLite: {OUTPUT_TFLITE} ({'INT8' if use_int8 else 'FP32'})")
                return
        except Exception as e:
            _log(f"TFLite {'INT8' if use_int8 else 'FP32'} failed: {e}")
            if use_int8:
                _log("try FP32 TFLite...")
            else:
                pass  # try ONNX

    # 2. try ONNX (compatible with Ultralytics inference, Raspberry Pi use onnxruntime)
    _log("try ONNX (TFLite conversion has known issues)...")
    try:
        model.export(format="onnx", imgsz=640, opset=18, simplify=True)
        onnx_src = export_dir / "best.onnx"
        if onnx_src.exists():
            shutil.copy(onnx_src, OUTPUT_ONNX)
            _log(f"saved ONNX: {OUTPUT_ONNX}")
            _log("inference use inference.py (will auto load .onnx). Raspberry Pi: pip install onnxruntime")
            return
    except Exception as e:
        _log(f"ONNX export failed: {e}")
    _log("please manually use runs/fruit/yolo11n_fruit/weights/best.pt or best.onnx")

if __name__ == "__main__":
    main()
