"""
evaluate model on validation set, output mAP, Precision, Recall etc.
run: python eval_model.py
"""
from pathlib import Path
from ultralytics import YOLO

MODEL_DIR = Path(__file__).resolve().parent
REPO_ROOT = MODEL_DIR.parent.parent
DATA_YAML = REPO_ROOT / "data" / "data.yaml"
BEST_PT = REPO_ROOT / "runs" / "fruit" / "yolo11n_fruit" / "weights" / "best.pt"

def main():
    if not DATA_YAML.exists():
        print(f"can't find dataset config: {DATA_YAML}")
        return
    if not BEST_PT.exists():
        print(f"can't find weights: {BEST_PT}, please complete training first.")
        return

    model = YOLO(str(BEST_PT))
    print("evaluating on validation set...", flush=True)
    results = model.val(
        data=str(DATA_YAML),
        imgsz=640,
        split="val",
        verbose=True,
        workers=0,
    )

    m = results.box
    print("\n" + "=" * 50)
    print("validation results (Validation Results)")
    print("=" * 50)
    print(f"  mAP50:      {m.map50:.4f}")
    print(f"  mAP50-95:   {m.map:.4f}")
    prec = getattr(m, "mp", None)
    rec = getattr(m, "mr", None)
    print(f"  Precision:  {f'{prec:.4f}' if prec is not None else 'N/A'}")
    print(f"  Recall:     {f'{rec:.4f}' if rec is not None else 'N/A'}")
    if hasattr(results, "speed") and results.speed:
        print(f"  Inference:  {getattr(results.speed, 'inference', 'N/A')} ms")
    print("=" * 50)

if __name__ == "__main__":
    main()
