"""Record each room's latest crowd level."""
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from elasticsearch import ApiError, TransportError

from search.client import get_client
from search.recommend import level_for

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def load_rooms():
    venue = json.loads((DATA_DIR / "venue.json").read_text())
    return {area["id"]: area for area in venue["areas"] if area["type"] == "room"}


def record_occupancy(area, count, capacity):
    """Store the latest reading for a room. Returns it, or None on failure.

    The room's id is the document id, so only the latest value is kept in
    occupancy. Every reading is also appended to occupancy-history.
    """
    client = get_client()
    if client is None:
        return None

    ratio = round(count / capacity, 3)
    doc = {
        "area": area,
        "count": count,
        "capacity": capacity,
        "ratio": ratio,
        "level": level_for(ratio),
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    try:
        result = client.options(max_retries=0).bulk(
            operations=[
                {"index": {"_index": "occupancy", "_id": area}},
                doc,
                {"index": {"_index": "occupancy-history"}},
                doc,
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
