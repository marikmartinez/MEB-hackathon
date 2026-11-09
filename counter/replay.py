"""Replay pre-extracted frames as if each area's camera were screenshotted.

Every TICK_SECONDS, for each area folder in FRAMES_DIR, take the next frame
(looping), count people, record it, and POST {area, count, ts} to
OCCUPANCY_URL if one is set.
"""
from __future__ import annotations

import io
import json
import logging
import os
import threading
import time
import urllib.request
from collections import deque
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from counter import metrics
from counter.model import CountResult, PersonCounter, draw_boxes

log = logging.getLogger(__name__)

HISTORY_LEN = 10


@dataclass
class Snapshot:
    area: str
    frame: str
    count: int
    latency_ms: float
    ts: float
    annotated_jpeg: bytes


class Replayer:
    def __init__(
        self,
        counter: PersonCounter,
        frames_dir: Path,
        tick_seconds: float = 30.0,
        occupancy_url: str | None = None,
        post_timeout: float = 5.0,
    ):
        self.counter = counter
        self.frames_dir = frames_dir
        self.tick_seconds = tick_seconds
        self.occupancy_url = occupancy_url or None
        self.post_timeout = post_timeout
        self.latest: dict[str, Snapshot] = {}
        # Recent (ts, count) per area, oldest first, so readers can smooth
        # and sanity-check counts without keeping state of their own.
        self.history: dict[str, deque[tuple[float, int]]] = {}
        self._positions: dict[str, int] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def areas(self) -> dict[str, list[Path]]:
        if not self.frames_dir.is_dir():
            return {}
        out = {}
        for d in sorted(p for p in self.frames_dir.iterdir() if p.is_dir()):
            frames = sorted(d.glob("*.jpg")) + sorted(d.glob("*.png"))
            if frames:
                out[d.name] = sorted(frames)
        return out

    def tick(self) -> list[Snapshot]:
        """Process one frame per area. Safe to call directly (tests, manual runs)."""
        done = []
        for area, frames in self.areas().items():
            pos = self._positions.get(area, 0) % len(frames)
            self._positions[area] = pos + 1
            path = frames[pos]
            try:
                img = Image.open(path)
                result = self.counter.count(img)
            except Exception:
                log.exception("couldn't count %s", path)
                metrics.COUNTS_REJECTED.labels(reason="unreadable_frame").inc()
                continue
            snap = self._record(area, path.name, img, result)
            self._publish(snap)
            done.append(snap)
        return done

    def _record(self, area: str, frame: str, img: Image.Image, result: CountResult) -> Snapshot:
        buf = _jpeg(draw_boxes(img, result))
        snap = Snapshot(area, frame, result.count, result.latency_ms, time.time(), buf)
        with self._lock:
            self.latest[area] = snap
            self.history.setdefault(area, deque(maxlen=HISTORY_LEN)).append((snap.ts, snap.count))
        metrics.observe(area, result)
        log.info("area=%s frame=%s count=%d latency_ms=%.0f", area, frame, result.count, result.latency_ms)
        return snap

    def _publish(self, snap: Snapshot) -> None:
        if not self.occupancy_url:
            return
        body = json.dumps({"area": snap.area, "count": snap.count, "ts": snap.ts}).encode()
        headers = {"Content-Type": "application/json"}
        token = os.environ.get("OCCUPANCY_TOKEN")
        if token:
            headers["Authorization"] = f"Bearer {token}"
        req = urllib.request.Request(self.occupancy_url, data=body, method="POST", headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=self.post_timeout) as resp:
                resp.read()
            metrics.PUBLISHED.labels(result="ok").inc()
        except Exception as e:  # the app may not be up yet; keep counting regardless
            metrics.PUBLISHED.labels(result="error").inc()
            log.warning("couldn't post occupancy for %s: %s", snap.area, e)

    def snapshot(self, area: str) -> Snapshot | None:
        with self._lock:
            return self.latest.get(area)

    def recent(self, area: str) -> list[tuple[float, int]]:
        with self._lock:
            return list(self.history.get(area, ()))

    def start(self) -> None:
        if self._thread:
            return
        self._thread = threading.Thread(target=self._run, name="replay", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        log.info("replaying %s every %ss -> %s", self.frames_dir, self.tick_seconds, self.occupancy_url)
        while not self._stop.is_set():
            started = time.monotonic()
            try:
                self.tick()
            except Exception:
                log.exception("replay tick failed")
            self._stop.wait(max(0.0, self.tick_seconds - (time.monotonic() - started)))


def _jpeg(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=80)
    return buf.getvalue()
