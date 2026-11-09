"""Record each room's latest crowd level."""
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from elasticsearch import ApiError, TransportError

from occupancy import smooth
from search.client import get_client
from search.levels import STALE_SECONDS, level_for

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
HISTORY_WINDOW = 10


def load_rooms():
    venue = json.loads((DATA_DIR / "venue.json").read_text())
    return {area["id"]: area for area in venue["areas"] if area["type"] == "room"}


def _recent_counts(client, area):
    """Return this room's recent raw counts, oldest first, or [] if unavailable."""
    try:
        result = client.options(max_retries=0).search(
            index="occupancy-history",
            size=HISTORY_WINDOW,
            source=["count", "ts"],
            query={
                "bool": {
                    "filter": [
                        {"term": {"area": area}},
                        {"range": {"ts": {"gte": f"now-{STALE_SECONDS}s"}}},
                    ]
                }
            },
            sort=[{"ts": "desc"}],
        )
    except (ApiError, TransportError) as err:
        logger.warning("couldn't read occupancy history: %s", type(err).__name__)
        return []
    hits = result["hits"]["hits"]
    return [(hit["_source"]["ts"], hit["_source"]["count"]) for hit in reversed(hits)]


def record_occupancy(area, count, capacity):
    """Smooth and store the latest reading for a room. Returns it, or None on failure.

    The room's id is the document id, so only the latest smoothed value is kept
    in occupancy. occupancy-history keeps each raw count, which the smoothing
    needs, so a single misdetected frame can't mark a room as full.
    """
    client = get_client()
    if client is None:
        return None

    ts = datetime.now(timezone.utc).isoformat()
    smoothed, _ = smooth(_recent_counts(client, area) + [(ts, count)], capacity)
    ratio = round(smoothed / capacity, 3)
    doc = {
        "area": area,
        "count": round(smoothed),
        "capacity": capacity,
        "ratio": ratio,
        "level": level_for(ratio),
        "ts": ts,
    }
    try:
        result = client.options(max_retries=0).bulk(
            operations=[
                {"index": {"_index": "occupancy", "_id": area}},
                doc,
                {"index": {"_index": "occupancy-history"}},
                {**doc, "count": count},
            ]
        )
    except (ApiError, TransportError) as err:
        logger.warning("couldn't record occupancy: %s", type(err).__name__)
        return None

    latest, history = (item["index"] for item in result["items"])
    if "error" in history:
        logger.warning("couldn't append occupancy history: %s", history["error"]["type"])
    if "error" in latest:
        logger.warning("couldn't record occupancy: %s", latest["error"]["type"])
        return None
    return doc
