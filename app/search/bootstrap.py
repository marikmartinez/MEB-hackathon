"""Create the indices if they're missing and load the seed schedule.

Run as a one-off Job: python -m search.bootstrap

Safe to re-run: each talk's id is its document id, so a re-run overwrites talks
instead of duplicating them. An index that already exists keeps its mapping;
delete it first to apply a mapping change.
"""
import json
import sys
from pathlib import Path

from elasticsearch import BadRequestError
from elasticsearch.helpers import bulk

from search.client import get_client
from search.mappings import OCCUPANCY_MAPPINGS, TALKS_MAPPINGS

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

# Far longer than the app's timeout: the model can take minutes to warm up.
BULK_TIMEOUT_SECONDS = 300


def load_talks():
    talks = json.loads((DATA_DIR / "talks.json").read_text())
    venue = json.loads((DATA_DIR / "venue.json").read_text())

    ids = [talk["id"] for talk in talks]
    if len(set(ids)) != len(ids):
        raise ValueError("talks.json has duplicate ids")

    rooms = {room["id"] for room in venue["rooms"]}
    unknown = sorted({talk["room"] for talk in talks} - rooms)
    if unknown:
        raise ValueError(f"talks.json uses rooms not in venue.json: {unknown}")
    return talks


def ensure_index(client, name, mappings):
    try:
        client.indices.create(index=name, mappings=mappings)
        print(f"created index {name}")
    except BadRequestError as err:
        if err.error != "resource_already_exists_exception":
            raise
        print(f"index {name} already exists, keeping its mapping")


def load_into_es(client, talks):
    actions = ({"_index": "talks", "_id": t["id"], "_source": t} for t in talks)
    loaded, errors = bulk(
        client, actions, chunk_size=10, raise_on_error=False, refresh=True
    )
    if errors:
        print(f"{len(errors)} talks failed, first error: {errors[0]}", file=sys.stderr)
        return False

    found = client.count(
        index="talks", query={"ids": {"values": [t["id"] for t in talks]}}
    )["count"]
    print(f"loaded {loaded} talks, {found} of {len(talks)} found in the index")
    return found == len(talks)


def main():
    talks = load_talks()
    client = get_client()
    if client is None:
        print("ES_URL and ES_API_KEY must be set and valid", file=sys.stderr)
        return 1

    client = client.options(
        request_timeout=BULK_TIMEOUT_SECONDS, max_retries=3, retry_on_timeout=True
    )
    ensure_index(client, "talks", TALKS_MAPPINGS)
    ensure_index(client, "occupancy", OCCUPANCY_MAPPINGS)
    ensure_index(client, "occupancy-history", OCCUPANCY_MAPPINGS)
    return 0 if load_into_es(client, talks) else 1


if __name__ == "__main__":
    sys.exit(main())
