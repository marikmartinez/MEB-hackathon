"""Occupancy over time, from occupancy-history, for the organizer dashboard.

Each history document is one counter reading: the raw count plus the
smoothed ratio and level the app stored with it (search/occupancy.py). The
dashboard uses the smoothed ratio for shape and peaks, so one misdetected
frame can't make a talk look packed, and the raw count for people.
"""
import logging
import math
from datetime import datetime, timedelta, timezone

from elasticsearch import ApiError, TransportError

from search.client import get_client

logger = logging.getLogger(__name__)

INDEX = "occupancy-history"
# Enough points for a smooth line at dashboard width; more is noise.
MAX_BUCKETS = 120
# The counter posts every 30 s, so a finer bucket is mostly empty.
MIN_INTERVAL_SECONDS = 60
RANGES = {"1h": timedelta(hours=1), "4h": timedelta(hours=4)}
DEFAULT_RANGE = "day"


def _parse(ts):
    return datetime.fromisoformat(ts).astimezone(timezone.utc)


def day_window(talks, now):
    """The conference day: first talk's start until the last talk's end or now,
    whichever is earlier. Falls back to the last 4 hours with no talks."""
    if not talks:
        return now - RANGES["4h"], now
    start = min(_parse(t["start"]) for t in talks)
    end = max(_parse(t["end"]) for t in talks)
    if now <= start:
        return now - RANGES["1h"], now
    return start, min(end, now)


def window(range_name, talks, now):
    if range_name in RANGES:
        return now - RANGES[range_name], now
    return day_window(talks, now)


def interval_seconds(start, end):
    span = max((end - start).total_seconds(), 0)
    per_bucket = span / MAX_BUCKETS
    return max(MIN_INTERVAL_SECONDS, math.ceil(per_bucket / 60) * 60)


def room_history(start, end, rooms):
    """Per-room series between start and end, or None if Elasticsearch is
    unavailable. rooms maps room id -> venue entry (for capacity).

    Returns {"interval": seconds, "rooms": {id: [{"t", "ratio", "peak", "people"}, ...]}}
    with t in epoch milliseconds, oldest first. Empty buckets are left out:
    a gap in the line means no readings, not an empty room.
    """
    client = get_client()
    if client is None:
        return None
    interval = interval_seconds(start, end)
    try:
        result = client.options(max_retries=0).search(
            index=INDEX,
            size=0,
            query={"range": {"ts": {"gte": start.isoformat(), "lte": end.isoformat()}}},
            aggs={
                "rooms": {
                    "terms": {"field": "area", "size": max(len(rooms), 1)},
                    "aggs": {
                        "over_time": {
                            "date_histogram": {
                                "field": "ts",
                                "fixed_interval": f"{interval}s",
                                "min_doc_count": 1,
                            },
                            "aggs": {
                                "ratio": {"avg": {"field": "ratio"}},
                                "peak": {"max": {"field": "ratio"}},
                                "people": {"avg": {"field": "count"}},
                            },
                        }
                    },
                }
            },
        )
    except (ApiError, TransportError) as err:
        logger.warning("couldn't read occupancy history: %s", type(err).__name__)
        return None

    series = {room_id: [] for room_id in rooms}
    for room in result["aggregations"]["rooms"]["buckets"]:
        if room["key"] not in series:
            continue
        series[room["key"]] = [
            {
                "t": b["key"],
                "ratio": _round(b["ratio"]["value"]),
                "peak": _round(b["peak"]["value"]),
                "people": _round(b["people"]["value"], 1),
            }
            for b in room["over_time"]["buckets"]
        ]
    return {"interval": interval, "rooms": series}


def talk_status(talk, now):
    if now < _parse(talk["start"]):
        return "upcoming"
    if now >= _parse(talk["end"]):
        return "ended"
    return "live"


def talk_stats(talks, now):
    """Occupancy during each talk's slot in its room, or None if Elasticsearch
    is unavailable. Returns {talk id: {"avg_ratio", "peak_ratio", "avg_people",
    "readings"}}; a talk with no readings has readings 0 and
    None for the rest. Upcoming talks aren't queried."""
    client = get_client()
    if client is None:
        return None
    started = [t for t in talks if talk_status(t, now) != "upcoming"]
    if not started:
        return {}
    filters = {
        t["id"]: {
            "bool": {
                "filter": [
                    {"term": {"area": t["room"]}},
                    {"range": {"ts": {"gte": t["start"], "lt": t["end"]}}},
                ]
            }
        }
        for t in started
    }
    try:
        result = client.options(max_retries=0).search(
            index=INDEX,
            size=0,
            aggs={
                "talks": {
                    "filters": {"filters": filters},
                    "aggs": {
                        "avg_ratio": {"avg": {"field": "ratio"}},
                        "peak_ratio": {"max": {"field": "ratio"}},
                        "avg_people": {"avg": {"field": "count"}},
                    },
                }
            },
        )
    except (ApiError, TransportError) as err:
        logger.warning("couldn't read talk occupancy: %s", type(err).__name__)
        return None

    stats = {}
    for talk_id, b in result["aggregations"]["talks"]["buckets"].items():
        readings = b["doc_count"]
        stats[talk_id] = {
            "readings": readings,
            "avg_ratio": _round(b["avg_ratio"]["value"]) if readings else None,
            "peak_ratio": _round(b["peak_ratio"]["value"]) if readings else None,
            "avg_people": _round(b["avg_people"]["value"], 1) if readings else None,
        }
    return stats


def _round(value, digits=3):
    return None if value is None else round(value, digits)
