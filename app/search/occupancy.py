"""Record each room's latest crowd level."""
import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path

from elasticsearch import ApiError, BadRequestError, TransportError

from occupancy import smooth
from search.client import get_client
from search.levels import STALE_SECONDS, level_for
from search.mappings import HISTORY_INDEX, OCCUPANCY_MAPPINGS

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
HISTORY_WINDOW = 10

_history_ready = False
_history_lock = threading.Lock()


def ensure_history_index(client):
    """Create the history index with its mappings if it doesn't exist yet.

    Without this, the first write auto-creates it with guessed mappings
    (area as text), and every per-room filter and aggregation then fails.
    Returns False if the index might not have the right mappings, in which
    case callers skip writing to it. Checked once per process.
    """
    global _history_ready
    if _history_ready:
        return True
    with _history_lock:
        if _history_ready:
            return True
        try:
            client.options(max_retries=0).indices.create(
                index=HISTORY_INDEX, mappings=OCCUPANCY_MAPPINGS
            )
            logger.warning("created index %s", HISTORY_INDEX)
        except BadRequestError as err:
            if err.error != "resource_already_exists_exception":
                logger.error("couldn't create %s: %s", HISTORY_INDEX, err.error)
                return False
        except (ApiError, TransportError) as err:
            logger.error("couldn't create %s: %s", HISTORY_INDEX, type(err).__name__)
            return False
        _history_ready = True
        return True


def load_rooms():
    venue = json.loads((DATA_DIR / "venue.json").read_text())
    return {area["id"]: area for area in venue["areas"] if area["type"] == "room"}


def _recent_counts(client, area):
    """Return this room's recent raw counts, oldest first, or [] if unavailable."""
    try:
        result = client.options(max_retries=0).search(
            index=HISTORY_INDEX,
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
    in occupancy. The history index keeps each raw count, which the smoothing
    needs, so a single misdetected frame can't mark a room as full.
    """
    client = get_client()
    if client is None:
        return None

    history_ok = ensure_history_index(client)
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
    operations = [{"index": {"_index": "occupancy", "_id": area}}, doc]
    # Skipped rather than written when the index might lack its mappings:
    # a write would auto-create it wrong, which is the bug this guards against.
    if history_ok:
        operations += [{"index": {"_index": HISTORY_INDEX}}, {**doc, "count": count}]
    try:
        result = client.options(max_retries=0).bulk(operations=operations)
    except (ApiError, TransportError) as err:
        logger.warning("couldn't record occupancy: %s", type(err).__name__)
        return None

    items = [item["index"] for item in result["items"]]
    latest = items[0]
    if len(items) > 1 and "error" in items[1]:
        logger.warning("couldn't append occupancy history: %s", items[1]["error"]["type"])
    if "error" in latest:
        logger.warning("couldn't record occupancy: %s", latest["error"]["type"])
        return None
    return doc
