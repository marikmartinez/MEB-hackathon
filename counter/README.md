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

## Service

`server.py` serves the counter over HTTP, and `replay.py` plays `frames/<area>/` as a live feed: every `TICK_SECONDS` it counts the next frame per area and POSTs `{area, count, ts}` to `OCCUPANCY_URL`.

| Endpoint | |
|---|---|
| `GET /healthz`, `/readyz` | probes |
| `GET /metrics` | `inference_latency_seconds`, `people_count{area}`, `frames_processed_total{area}`, `counts_rejected_total{reason}`, `occupancy_posts_total{result}` |
| `POST /count` | image as the body or multipart field `image` → `{count, latency_ms, boxes}`. Max 8 MB and 3840×2160 pixels (413 above that) |
| `GET /last` | latest count and last 10 `[ts, count]` per area (the app reads this) |
| `GET /last/<area>.jpg` | latest frame with boxes drawn |

Env: `FRAMES_DIR`, `TICK_SECONDS` (30), `OCCUPANCY_URL` (unset = don't post), `OCCUPANCY_TOKEN` (sent as a bearer token), `CONF` (0.15), `THREADS` (1), `REPLAY` (1).

On the cluster, `OCCUPANCY_URL` is the app's in-cluster Service (`http://app/api/occupancy`), and `OCCUPANCY_TOKEN` comes from Secret `es-credentials`, the same key the app checks. The app stores each post in Elasticsearch: the latest per room in `occupancy`, every reading in `occupancy-history-v2` for the organizer dashboard. Watch `occupancy_posts_total{result}` on `/metrics` to see whether posts land.

```bash
docker build -f counter/Dockerfile -t counter .
docker run --rm -p 8081:8080 -e TICK_SECONDS=5 counter
curl -X POST --data-binary @counter/samples/bus.jpg localhost:8081/count
```

Measured locally with the 1280px model, 1 CPU and a 768Mi limit, 5 areas: 372–391 MiB memory (steady), ~450 ms per image, so one tick of 5 areas takes about 2 s of one core.

Licence: the YOLO11 weights are AGPL-3.0 (Ultralytics); this repo is public, which satisfies that.

## First deploy: make the image public

The `build-and-deploy` action pushes the counter as its own GHCR package, `ghcr.io/marikmartinez/meb-hackathon-counter`. GHCR makes a new package **private**, and the cluster pulls without credentials, so until it's public the `counter` pod sits in `ImagePullBackOff`. Once, after the first run that builds it:

GitHub → your profile → **Packages** → `meb-hackathon-counter` → **Package settings** → **Change visibility** → **Public**.

The pod picks it up on its next pull retry (or restart it with Radar's `manage_workload`).

Also expect a short `ImagePullBackOff` on that first merge even once it's public: Argo CD syncs the merge commit, whose `counter` tag is still `latest`, before the action has pushed any counter image. It clears by itself when the action's `deploy: <sha>` commit sets the real tag. Later deploys always have the image before the tag changes.

## Frames on the cluster

The replay frames are **not** in git or the image. On the cluster they come from one Secret per room (`counter-frames-<room>`), mounted at `frames/<room>/` by `deploy/counter-deployment.yaml`. To create or refresh them, with kubectl set up (top-level README, "kubectl"):

```bash
python -m counter.mall_frames ~/path/to/mall_dataset
./scripts/upload-frames.sh
```

Each Secret must stay under 1 MiB (20 Mall frames ≈ 880 KiB); the script checks. The counter sees new frames within about a minute without a restart. A room whose Secret doesn't exist reports "unknown".

Note: the attendee page's room feed (`/api/occupancy/<room>/frame.jpg`) shows these frames on the public URL.
