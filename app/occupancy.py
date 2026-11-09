"""Crowd levels per area, from the counter's recent counts.

The counter service (one instance) keeps the last few raw counts per area.
This module reads them on request and turns them into a sanity-checked level,
so the app keeps no state and every replica/worker gives the same answer.

Sanity checks, applied in order over each area's history:
- clamp:      counts outside [0, 1.5 x capacity] are clamped
- spike guard: a jump of more than max(50% of the smoothed value, 5 people)
              is held until a second frame confirms it
- smoothing:  exponential moving average (alpha 0.5, about the last 3 frames)
- staleness:  no new frame for STALE_TICKS ticks -> level "unknown"
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path


from flask import Blueprint, Response, jsonify
from search.levels import level_for

log = logging.getLogger(__name__)

VENUE_PATH = Path(__file__).parent / "data" / "venue.json"
COUNTER_URL = os.environ.get("COUNTER_URL", "http://counter")
FETCH_TIMEOUT = 2.0
CACHE_SECONDS = 5.0
MAX_FRAME_BYTES = 2 * 1024 * 1024  # annotated frames are ~50 KB; refuse anything odd

EMA_ALPHA = 0.5
MAX_OVER_CAPACITY = 1.5
SPIKE_FRACTION = 0.5
SPIKE_MIN_PEOPLE = 5
STALE_TICKS = 3
DEFAULT_TICK_SECONDS = 30.0

@dataclass
class AreaStatus:
    id: str
    name: str
    type: str  # room | corridor, from venue.json
    capacity: int
    level: str  # low | medium | high | full | unknown
    count: int | None = None  # smoothed
    raw: int | None = None  # latest frame, before checks
    ratio: float | None = None
    updated: float | None = None
    stale: bool = True
    held_spike: bool = False  # latest frame is an unconfirmed jump


def smooth(history: list[tuple[float, int]], capacity: int) -> tuple[float | None, bool]:
    """Run the checks over [(ts, count), ...] oldest first.

    Returns (smoothed count, whether the latest frame is a held spike).
    """
    smoothed: float | None = None
    pending: float | None = None
    held = False
    upper = capacity * MAX_OVER_CAPACITY
    for _, raw in history:
        c = min(max(float(raw), 0.0), upper)
        if smoothed is None:
            smoothed, held = c, False
            continue
        jump = c - smoothed
        if abs(jump) > max(SPIKE_FRACTION * smoothed, SPIKE_MIN_PEOPLE):
            if pending is not None and (pending - smoothed) * jump > 0:
                # Two frames in a row agree: a real change. Move to them.
                smoothed, pending, held = (pending + c) / 2, None, False
            else:
                pending, held = c, True
            continue
        pending, held = None, False
        smoothed = EMA_ALPHA * c + (1 - EMA_ALPHA) * smoothed
    return smoothed, held


def area_status(area: dict, reading: dict | None, tick_seconds: float, now: float) -> AreaStatus:
    status = AreaStatus(area["id"], area["name"], area.get("type", "area"), area["capacity"], level="unknown")
    if not reading or not reading.get("history"):
        return status
    history = [(float(ts), int(n)) for ts, n in reading["history"]]
    smoothed, held = smooth(history, area["capacity"])
    status.raw = history[-1][1]
    status.updated = history[-1][0]
    status.held_spike = held
    status.stale = now - status.updated > STALE_TICKS * tick_seconds
    if smoothed is None:
        return status
    status.count = round(smoothed)
    status.ratio = round(smoothed / area["capacity"], 3)
    if not status.stale:
        status.level = level_for(status.ratio)
    return status


def load_venue(path: Path = VENUE_PATH) -> list[dict]:
    return json.loads(path.read_text())["areas"]


class CounterClient:
    """Reads the counter's /last, cached briefly so page refreshes don't hammer it."""

    def __init__(self, base_url: str = COUNTER_URL, cache_seconds: float = CACHE_SECONDS):
        self.base_url = base_url.rstrip("/")
        self.cache_seconds = cache_seconds
        self._cached: tuple[float, dict | None] = (-1e9, None)
        self._lock = threading.Lock()

    def last(self) -> dict | None:
        with self._lock:
            fetched_at, data = self._cached
            if time.monotonic() - fetched_at < self.cache_seconds:
                return data
            try:
                with urllib.request.urlopen(f"{self.base_url}/last", timeout=FETCH_TIMEOUT) as resp:
                    data = json.load(resp)
            except Exception as e:
                log.warning("counter unavailable at %s: %s", self.base_url, e)
                data = None
            self._cached = (time.monotonic(), data)
            return data

    def frame(self, area_id: str) -> bytes | None:
        """Latest annotated JPEG for an area, or None if there isn't one.

        area_id must already be checked against the venue: it goes into the URL.
        """
        url = f"{self.base_url}/last/{urllib.parse.quote(area_id, safe='')}.jpg"
        try:
            with urllib.request.urlopen(url, timeout=FETCH_TIMEOUT) as resp:
                data = resp.read(MAX_FRAME_BYTES + 1)
        except Exception as e:  # 404 = no frame yet; anything else = counter down
            log.info("no frame for %s: %s", area_id, e)
            return None
        return data if len(data) <= MAX_FRAME_BYTES else None


def snapshot(venue: list[dict], counter_data: dict | None, now: float | None = None) -> dict:
    now = time.time() if now is None else now
    readings = (counter_data or {}).get("areas", {})
    tick = float((counter_data or {}).get("tick_seconds") or DEFAULT_TICK_SECONDS)
    return {
        "source": "ok" if counter_data is not None else "unavailable",
        "generated_at": now,
        "areas": [asdict(area_status(a, readings.get(a["id"]), tick, now)) for a in venue],
    }


def blueprint(client: CounterClient | None = None, venue: list[dict] | None = None) -> Blueprint:
    bp = Blueprint("occupancy", __name__)
    client = client or CounterClient()
    venue = venue if venue is not None else load_venue()

    @bp.get("/api/occupancy")
    def all_areas():
        return jsonify(snapshot(venue, client.last()))

    @bp.get("/api/occupancy/<area_id>")
    def one_area(area_id: str):
        snap = snapshot(venue, client.last())
        for a in snap["areas"]:
            if a["id"] == area_id:
                return jsonify(source=snap["source"], **a)
        return jsonify(error=f"unknown area {area_id}"), 404

    @bp.get("/api/occupancy/<area_id>/frame.jpg")
    def area_frame(area_id: str):
        # Only venue ids reach the counter: no arbitrary paths through this proxy.
        if area_id not in {a["id"] for a in venue}:
            return jsonify(error=f"unknown area {area_id}"), 404
        data = client.frame(area_id)
        if data is None:
            return jsonify(error="no camera frame yet"), 404
        return Response(data, mimetype="image/jpeg", headers={"Cache-Control": "no-store"})

    return bp
