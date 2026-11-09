"""Recommend talks that are related to a chosen talk and not too crowded."""
import json
import logging
import time
from functools import lru_cache
from pathlib import Path

from elasticsearch import ApiError, TransportError

from search.client import get_client

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
TALK_FIELDS = [
    "id", "title", "abstract", "speaker", "tags", "track", "level", "room",
    "start", "end",
]
OVERFETCH = 20
OCCUPANCY_TTL_SECONDS = 10
FULL_RATIO = 0.9

# The last occupancy read is kept after it expires, so the fallback can still
# use crowd levels when Elasticsearch goes down.
_occupancy = {}
_occupancy_read_at = None


class TalkNotFound(LookupError):
    pass


@lru_cache(maxsize=1)
def _seed_talks():
    return json.loads((DATA_DIR / "talks.json").read_text())


def crowd_penalty(ratio):
    """Map an occupancy ratio to 0-1: gentle while quiet, steep near capacity."""
    return min(max(ratio, 0.0), 1.0) ** 2


def level_for(ratio):
    if ratio is None:
        return "unknown"
    if ratio < 0.5:
        return "low"
    if ratio < 0.75:
        return "medium"
    if ratio < FULL_RATIO:
        return "high"
    return "full"


def _read_occupancy(client):
    global _occupancy, _occupancy_read_at
    now = time.monotonic()
    if _occupancy_read_at is not None and now - _occupancy_read_at < OCCUPANCY_TTL_SECONDS:
        return _occupancy
    try:
        result = client.options(max_retries=0).search(
            index="occupancy",
            size=200,
            source=["area", "ratio", "level"],
            query={"match_all": {}},
        )
    except (ApiError, TransportError):
        logger.warning("couldn't read occupancy, using the last known levels")
        return _occupancy
    _occupancy = {h["_source"]["area"]: h["_source"] for h in result["hits"]["hits"]}
    _occupancy_read_at = now
    return _occupancy


def _room_level(occupancy, room_id):
    room = occupancy.get(room_id, {})
    return room.get("level") or level_for(room.get("ratio"))


def _rerank(talk, candidates, occupancy, k, source):
    top = max((relevance for _, relevance in candidates), default=0) or 1
    results = []
    for candidate, relevance in candidates:
        ratio = occupancy.get(candidate["room"], {}).get("ratio")
        level = _room_level(occupancy, candidate["room"])
        if level == "full" or (ratio is not None and ratio >= FULL_RATIO):
            continue

        score = (relevance / top) * (1 - crowd_penalty(ratio or 0.0))
        shared_tags = sorted(set(talk.get("tags", [])) & set(candidate.get("tags", [])))
        results.append({
            **{field: candidate.get(field) for field in TALK_FIELDS if field != "abstract"},
            "score": round(score, 3),
            "why": {"shared_tags": shared_tags, "crowd_level": level},
        })
    results.sort(key=lambda result: result["score"], reverse=True)
    return {
        "talk_id": talk["id"],
        "crowd_level": _room_level(occupancy, talk["room"]),
        "source": source,
        "results": results[:k],
    }


def _constraints(talk):
    return {
        "filter": [
            {"range": {"start": {"lt": talk["end"]}}},
            {"range": {"end": {"gt": talk["start"]}}},
        ],
        "must_not": [{"ids": {"values": [talk["id"]]}}],
    }


def _relevance_queries(talk):
    return [
        {
            "semantic": {
                "field": "semantic",
                "query": f"{talk['title']}. {talk['abstract']}",
            }
        },
        {
            "more_like_this": {
                "fields": ["title", "abstract", "tags"],
                "like": [{"_index": "talks", "_id": talk["id"]}],
                # The defaults ignore rare terms, which is every term in a
                # schedule this small.
                "min_term_freq": 1,
                "min_doc_freq": 1,
            }
        },
        {
            "bool": {
                "should": [
                    {"terms": {"tags": talk.get("tags", []), "boost": 2.0}},
                    {"term": {"track": {"value": talk["track"], "boost": 1.5}}},
                ]
            }
        },
    ]


def _search(client, talk):
    # A plain bool, not RRF: RRF keeps only rank positions, which lets an
    # unrelated talk in an empty room beat a related talk in a busy one.
    return client.search(
        index="talks",
        query={
            "bool": {
                "should": _relevance_queries(talk),
                "minimum_should_match": 1,
                **_constraints(talk),
            }
        },
        size=OVERFETCH,
        source=TALK_FIELDS,
    )


def _recommend_es(client, talk_id, k):
    client = client.options(max_retries=0)
    talk = client.get(index="talks", id=talk_id, source_includes=TALK_FIELDS)["_source"]
    occupancy = _read_occupancy(client)
    hits = _search(client, talk)["hits"]["hits"]
    candidates = [(hit["_source"], hit["_score"]) for hit in hits]
    return _rerank(talk, candidates, occupancy, k, "elasticsearch")


def _recommend_fallback(talk_id, k):
    talks = _seed_talks()
    talk = next((t for t in talks if t["id"] == talk_id), None)
    if talk is None:
        raise TalkNotFound(talk_id)

    tags = set(talk.get("tags", []))
    # ISO timestamps with the same offset compare correctly as strings.
    candidates = [
        (other, 1 + len(tags & set(other.get("tags", []))))
        for other in talks
        if other["id"] != talk_id
        and other["start"] < talk["end"]
        and other["end"] > talk["start"]
    ]
    return _rerank(talk, candidates, _occupancy, k, "fallback")


def list_talks(q=None):
    """List talks by start time, or search them when q is given."""
    client = get_client()
    if client is not None:
        query = (
            {"multi_match": {"query": q, "fields": ["title^3", "abstract", "tags", "speaker"]}}
            if q
            else {"match_all": {}}
        )
        try:
            result = client.options(max_retries=0).search(
                index="talks",
                size=100,
                source=TALK_FIELDS,
                query=query,
                sort=None if q else [{"start": "asc"}, {"id": "asc"}],
            )
            talks = [hit["_source"] for hit in result["hits"]["hits"]]
            return {"source": "elasticsearch", "talks": talks}
        except (ApiError, TransportError) as err:
            logger.warning("talk list falling back: %s", type(err).__name__)

    talks = sorted(_seed_talks(), key=lambda talk: (talk["start"], talk["id"]))
    if q:
        needle = q.lower()
        talks = [
            talk for talk in talks
            if needle in " ".join(
                [talk["title"], talk["abstract"], talk["speaker"], *talk["tags"]]
            ).lower()
        ]
    return {"source": "fallback", "talks": talks}


def recommend(talk_id, k=5):
    """Return up to k talks at about the same time, best first.

    Raises TalkNotFound for an unknown talk. If Elasticsearch can't answer,
    falls back to tag overlap from the seed schedule.
    """
    client = get_client()
    if client is not None:
        try:
            return _recommend_es(client, talk_id, k)
        except (ApiError, TransportError) as err:
            logger.warning("recommendations falling back: %s", type(err).__name__)
    return _recommend_fallback(talk_id, k)
