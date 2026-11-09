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

## Service

`server.py` serves the counter over HTTP, and `replay.py` plays `frames/<area>/` as a live feed: every `TICK_SECONDS` it counts the next frame per area and POSTs `{area, count, ts}` to `OCCUPANCY_URL`.

| Endpoint | |
|---|---|
| `GET /healthz`, `/readyz` | probes |
| `GET /metrics` | `inference_latency_seconds`, `people_count{area}`, `frames_processed_total{area}`, `counts_rejected_total{reason}`, `occupancy_posts_total{result}` |
| `POST /count` | image as the body or multipart field `image` → `{count, latency_ms, boxes}` |
| `GET /last` | latest count per area |
| `GET /last/<area>.jpg` | latest frame with boxes drawn |

Env: `FRAMES_DIR`, `TICK_SECONDS` (30), `OCCUPANCY_URL` (unset = don't post), `CONF` (0.35), `THREADS` (1), `REPLAY` (1).

```bash
docker build -f counter/Dockerfile -t counter .
docker run --rm -p 8081:8080 -e TICK_SECONDS=5 counter
curl -X POST --data-binary @counter/samples/bus.jpg localhost:8081/count
```

Measured locally with 1 CPU and a 768Mi limit: about 160 MiB memory, 110–240 ms per image.

When adding frames for an area, hand-count a few and add them to `ground_truth.json` (e.g. `"frames/hall-a/0003.jpg": 12`) so `eval.py` keeps measuring accuracy on our own footage.

Licence: the YOLO11 weights are AGPL-3.0 (Ultralytics); this repo is public, which satisfies that.
