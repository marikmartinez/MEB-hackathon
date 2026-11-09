# counter: people counting

Counts people in camera snapshots with **YOLO11-nano**, exported to ONNX and run on CPU with `onnxruntime` (no PyTorch at runtime). About 100 ms per image on one core.

| File | What |
|---|---|
| `model.py` | `PersonCounter.count(image)` → count, boxes, latency. CLI: `python -m counter.model IMG...` |
| `eval.py` | Compares counts with hand counts in `ground_truth.json`, prints MAE |
| `extract_frames.py` | Video → still frames in `frames/<area>/`, our stand-in for camera screenshots |
| `models/yolo11n.onnx` | Model (10.7 MB), exported with `yolo export model=yolo11n.pt format=onnx imgsz=640 opset=12 simplify=True` |
| `samples/` | Ultralytics sample images used by tests |

```bash
python -m venv .venv && .venv/bin/pip install -r counter/requirements-dev.txt
.venv/bin/python -m pytest counter/tests
.venv/bin/python -m counter.eval
.venv/bin/python -m counter.extract_frames path/to/hall.mp4 hall-a --every 10 --max 60
```

When adding frames for an area, hand-count a few and add them to `ground_truth.json` (e.g. `"frames/hall-a/0003.jpg": 12`) so `eval.py` keeps measuring accuracy on our own footage.

Licence: the YOLO11 weights are AGPL-3.0 (Ultralytics); this repo is public, which satisfies that.
