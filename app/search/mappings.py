"""Index mappings for the talks and occupancy indices."""
import os

# Named explicitly because the default model for semantic_text varies by
# Elastic version and deployment type.
INFERENCE_ID = os.environ.get("ES_INFERENCE_ID", ".elser-2-elastic")

TALKS_MAPPINGS = {
    "properties": {
        "id": {"type": "keyword"},
        "title": {"type": "text", "copy_to": "semantic"},
        "abstract": {"type": "text", "copy_to": "semantic"},
        "speaker": {"type": "keyword"},
        "tags": {"type": "keyword", "copy_to": "semantic"},
        "track": {"type": "keyword"},
        "level": {"type": "keyword"},
        "room": {"type": "keyword"},
        "start": {"type": "date"},
        "end": {"type": "date"},
        "semantic": {"type": "semantic_text", "inference_id": INFERENCE_ID},
    }
}

# One document per room, written by the CV pipeline and overwritten each time.
# area must match a room id in data/venue.json.
OCCUPANCY_MAPPINGS = {
    "properties": {
        "area": {"type": "keyword"},
        "count": {"type": "integer"},
        "capacity": {"type": "integer"},
        "ratio": {"type": "float"},
        "level": {"type": "keyword"},  # low | medium | high | full
        "ts": {"type": "date"},
    }
}

# Every reading, for smoothing and the organizer dashboard. Same mappings.
# v2 because the first occupancy-history was auto-created by its first write,
# with area as text: exact-match filters on a room never matched, and the
# per-room aggregations failed. A field's type can't change in place, so the
# app writes to a new index that it creates with these mappings itself
# (search/occupancy.py, ensure_history_index).
HISTORY_INDEX = "occupancy-history-v2"
