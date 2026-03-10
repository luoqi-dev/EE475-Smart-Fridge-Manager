"""
Finetune data_2 model (best.pt) on data_3 dataset.
- Pretrained: runs/fruit/yolo11n_data2/weights/best.pt (9 classes)
- data_3: 11 classes (0-8 same as data_2, 9=Carrot, 10=Lime); only 0,1,5,9,10 have samples in data_3.
Run after preparing data_3:  python data_3/prepare_data3_yolo.py
Then:  python train_finetune_data3.py
"""
from pathlib import Path

import torch
from ultralytics import YOLO

# Paths
MODEL_DIR = Path(__file__).resolve().parent
REPO_ROOT = MODEL_DIR.parent.parent
BEST_PT = REPO_ROOT / "runs" / "fruit" / "yolo11n_data2" / "weights" / "best.pt"
# data_3 is next to EE475-Smart-Fridge-Manager (e.g. EE476/data_3)
DATA3_ROOT = REPO_ROOT.parent / "data_3"
DATA3_YAML = DATA3_ROOT / "data.yaml"


def main():
    if not BEST_PT.exists():
        raise FileNotFoundError(f"Pretrained weights not found: {BEST_PT}")
    if not DATA3_YAML.exists():
        raise FileNotFoundError(
            f"data_3 not prepared. Run: python {DATA3_ROOT / 'prepare_data3_yolo.py'}"
        )

    # GPU if available, else CPU (device=0 = first GPU; "cpu" = CPU only)
    device = 0 if torch.cuda.is_available() else "cpu"
    print(f"Using device: {device}")

    model = YOLO(str(BEST_PT))
    results = model.train(
        data=str(DATA3_YAML),
        epochs=80,
        imgsz=640,
        device=device,
        project=str(REPO_ROOT / "runs" / "fruit"),
        name="yolo11n_data3_finetune",
        exist_ok=True,
        batch=16,
        patience=25,
        lr0=1e-4,   # lower LR for finetuning
        lrf=0.01,
    )
    print(f"Done. Best weights: {Path(results.save_dir) / 'weights' / 'best.pt'}")


if __name__ == "__main__":
    main()
