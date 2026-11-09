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

# One document per area, written by the CV pipeline and overwritten each time.
# area must match an area id in data/venue.json.
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
