# counter: people counting

Counts people in camera snapshots with **YOLO11-nano**, exported to ONNX at a **1280px** input and run on CPU with `onnxruntime` (no PyTorch at runtime). About 400 ms per image on one core.

## Accuracy

Measured on 200 frames of the Mall dataset (Chen, Loy, Gong, Xiang, BMVC 2012; overhead shopping-mall camera, 13–53 people per frame) against its annotated counts:

| Setup | MAE (people) | Bias | ms/frame |
|---|---|---|---|
| 640px, conf 0.35 (first version) | 15.6 | −15.6 | ~100 |
| **1280px, conf 0.15 (current)** | **3.65** | **−3.1** | **~400** |

The larger input matters more than a larger model: people at the back of an overhead shot are only a few pixels tall at 640px. YOLO11-small at 960px was worse (MAE 6.9) and slower.

## Files

| File | What |
|---|---|
| `model.py` | `PersonCounter.count(image)` → count, boxes, latency. CLI: `python -m counter.model IMG...` |
| `eval.py` | Compares counts with known counts (`ground_truth.json`, or the Mall dataset with `--mall`), prints MAE and bias |
| `mall_frames.py` | Builds `frames/<area>/` from a local Mall dataset copy, a different stretch of footage per area |
| `extract_frames.py` | Video → still frames in `frames/<area>/`, for our own footage |
| `models/yolo11n.onnx` | Model (11 MB): `yolo export model=yolo11n.pt format=onnx imgsz=1280 opset=12 simplify=True` |
| `samples/` | Ultralytics sample images used by tests |

```bash
python -m venv .venv && .venv/bin/pip install -r counter/requirements-dev.txt
.venv/bin/python -m pytest counter/tests
.venv/bin/python -m counter.eval --mall ~/path/to/mall_dataset --every 10 --quiet
.venv/bin/python -m counter.mall_frames ~/path/to/mall_dataset
```

**The Mall dataset is not in this repo.** We haven't confirmed its licence allows redistribution, so `frames/*/` is git-ignored and `eval.py --mall` / `mall_frames.py` read a local copy.

Licence: the YOLO11 weights are AGPL-3.0 (Ultralytics); this repo is public, which satisfies that.
