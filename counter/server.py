"""HTTP service around the person counter.

    GET  /healthz, /readyz
    GET  /metrics            Prometheus
    POST /count              image (raw body or multipart field "image") -> JSON count
    GET  /last               latest count per area (JSON)
    GET  /last/<area>.jpg    latest frame for an area, with boxes drawn

Config (env): FRAMES_DIR, TICK_SECONDS (30), OCCUPANCY_URL (unset = don't post),
CONF (0.35), THREADS (1), REPLAY (1 = run the replay loop).
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from flask import Flask, Response, abort, jsonify, request
from PIL import UnidentifiedImageError
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from counter import metrics
from counter.model import PersonCounter
from counter.replay import Replayer

MAX_UPLOAD = 8 * 1024 * 1024


def create_app(replay: bool | None = None) -> Flask:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD

    counter = PersonCounter(conf=float(os.environ.get("CONF", "0.35")), threads=int(os.environ.get("THREADS", "1")))
    replayer = Replayer(
        counter,
        frames_dir=Path(os.environ.get("FRAMES_DIR", Path(__file__).parent / "frames")),
        tick_seconds=float(os.environ.get("TICK_SECONDS", "30")),
        occupancy_url=os.environ.get("OCCUPANCY_URL"),
    )
    app.extensions["replayer"] = replayer
    if replay if replay is not None else os.environ.get("REPLAY", "1") == "1":
        replayer.start()

    @app.get("/healthz")
    @app.get("/readyz")
    def health():
        return "ok\n", 200, {"Content-Type": "text/plain"}

    @app.get("/metrics")
    def prom():
        return Response(generate_latest(), mimetype=CONTENT_TYPE_LATEST)

    @app.post("/count")
    def count():
        # Only touch request.files for multipart: otherwise Flask parses a
        # raw upload sent as x-www-form-urlencoded (curl's default) as a form
        # and the body is gone.
        if request.mimetype == "multipart/form-data":
            upload = request.files.get("image")
            data = upload.read() if upload else b""
        else:
            data = request.get_data()
        if not data:
            metrics.COUNTS_REJECTED.labels(reason="empty_upload").inc()
            return jsonify(error="send an image as the body or as multipart field 'image'"), 400
        try:
            result = counter.count(data)
        except (UnidentifiedImageError, OSError):
            metrics.COUNTS_REJECTED.labels(reason="bad_image").inc()
            return jsonify(error="not a readable image"), 400
        metrics.INFERENCE_LATENCY.observe(result.latency_ms / 1000)
        return jsonify(
            count=result.count,
            latency_ms=round(result.latency_ms, 1),
            boxes=[[round(b.x1), round(b.y1), round(b.x2), round(b.y2), round(b.score, 3)] for b in result.boxes],
        )

    @app.get("/last")
    def last_all():
        return jsonify(
            {
                area: {"count": s.count, "frame": s.frame, "ts": s.ts, "latency_ms": round(s.latency_ms, 1)}
                for area in replayer.areas()
                if (s := replayer.snapshot(area))
            }
        )

    @app.get("/last/<area>.jpg")
    def last_frame(area: str):
        snap = replayer.snapshot(area)
        if not snap:
            abort(404)
        return Response(snap.annotated_jpeg, mimetype="image/jpeg", headers={"Cache-Control": "no-store"})

    return app
