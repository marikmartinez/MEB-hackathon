"""Prometheus metrics for the counter (scraped from /metrics)."""
from prometheus_client import Counter, Gauge, Histogram

INFERENCE_LATENCY = Histogram(
    "inference_latency_seconds",
    "Time to count people in one image",
    buckets=(0.05, 0.1, 0.15, 0.2, 0.3, 0.5, 0.75, 1, 2, 5),
)
PEOPLE = Gauge("people_count", "People counted in the area's latest frame", ["area"])
FRAMES = Counter("frames_processed_total", "Frames counted", ["area"])
COUNTS_REJECTED = Counter("counts_rejected_total", "Frames or uploads that couldn't be counted", ["reason"])
PUBLISHED = Counter("occupancy_posts_total", "Occupancy updates sent to the app", ["result"])


def observe(area: str, result) -> None:
    INFERENCE_LATENCY.observe(result.latency_ms / 1000)
    PEOPLE.labels(area=area).set(result.count)
    FRAMES.labels(area=area).inc()
