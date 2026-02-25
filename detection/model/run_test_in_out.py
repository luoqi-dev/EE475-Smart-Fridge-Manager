"""
Run in/out (PUT_IN / TAKE_OUT) detection on test_data folder.
Usage:
  python run_test_in_out.py                    # batch: run all test_data/in/* and test_data/out/*
  python run_test_in_out.py "C:\path\to\test_data"
  python run_test_in_out.py "C:\path\to\test_data\out\2"   # single folder
"""
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
TEST_DATA_DEFAULT = REPO_ROOT / "test_data"

IMG_EXTS = {".jpg", ".jpeg", ".png", ".bmp"}


def count_images(folder: Path) -> int:
    return sum(1 for p in folder.iterdir() if p.suffix.lower() in IMG_EXTS)


def main():
    test_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else TEST_DATA_DEFAULT
    if not test_dir.exists():
        print(f"Test data folder not found: {test_dir}")
        return
    if not test_dir.is_dir():
        print(f"Not a directory: {test_dir}")
        return

    from inference import run_inference_on_folder

    # Batch mode: test_data has in/ and out/ subdirs -> run each and report table
    in_dirs = sorted((test_dir / "in").iterdir()) if (test_dir / "in").is_dir() else []
    out_dirs = sorted((test_dir / "out").iterdir()) if (test_dir / "out").is_dir() else []

    if in_dirs or out_dirs:
        print("=" * 70)
        print("In/Out identification test (PUT_IN=put in, TAKE_OUT=take out)")
        print("=" * 70)
        for label, subdirs, expected in [("in", in_dirs, "PUT_IN"), ("out", out_dirs, "TAKE_OUT")]:
            for d in subdirs:
                if not d.is_dir():
                    continue
                n = count_images(d)
                if n == 0:
                    print(f"  {label}/{d.name}: (no images)")
                    continue
                put_in, take_out = run_inference_on_folder(d, show=False)
                ok = (expected == "PUT_IN" and put_in >= 1) or (expected == "TAKE_OUT" and take_out >= 1)
                status = "OK" if ok else "FAIL"
                print(f"  {label}/{d.name}: {n} frames -> PUT_IN={put_in}, TAKE_OUT={take_out} (expected {expected}) [{status}]")
        print("=" * 70)
        return

    # Single folder mode
    print("=" * 60)
    print("In/Out identification test (PUT_IN=put in, TAKE_OUT=take out)")
    print(f"Test data: {test_dir}")
    print("=" * 60)
    run_inference_on_folder(test_dir, show=False)
    print("=" * 60)


if __name__ == "__main__":
    main()
