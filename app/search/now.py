"""What's happening in each room on the simulated conference clock.

One real minute is one conference hour, looping over the day's schedule. The
clock is computed from real time, so every pod and viewer agrees without any
shared state.
"""
import threading
import time
from datetime import datetime, timedelta

from search.recommend import TalkNotFound, current_levels, list_talks, recommend

SPEED = 60
ROOMS_CACHE_SECONDS = 2
CROWD_RANK = {"low": 0, "medium": 1, "high": 2, "full": 3}
TALK_SUMMARY_FIELDS = ["id", "title", "speaker", "start", "end"]

_lock = threading.Lock()
_talks = []
_rooms = []
_expires = 0.0


def _parse(timestamp):
    return datetime.fromisoformat(timestamp)


def simulated_now(talks, wall=None):
    """Return (now, first start, last end) for a non-empty list of talks."""
    wall = time.time() if wall is None else wall
    start = min(_parse(talk["start"]) for talk in talks)
    end = max(_parse(talk["end"]) for talk in talks)
    real_seconds = (end - start).total_seconds() / SPEED
    elapsed = (wall % real_seconds) * SPEED
    return (start + timedelta(seconds=elapsed)).replace(microsecond=0), start, end


def _summary(talk):
    return {field: talk[field] for field in TALK_SUMMARY_FIELDS}


def _suggestion(talk, level, now):
    """Return the most similar talk running now in a less crowded room, if any."""
    try:
        results = recommend(talk["id"], at=now.isoformat())["results"]
    except TalkNotFound:
        return None
    for candidate in results:
        if CROWD_RANK.get(candidate["why"]["crowd_level"], 99) < CROWD_RANK[level]:
            return {
                **_summary(candidate),
                "room": candidate["room"],
                "crowd_level": candidate["why"]["crowd_level"],
                "shared_tags": candidate["why"]["shared_tags"],
            }
    return None


def _room_state(room_id, room, talks, levels, now):
    here = sorted(
        (talk for talk in talks if talk["room"] == room_id),
        key=lambda talk: talk["start"],
    )
    current = next((t for t in here if _parse(t["start"]) <= now < _parse(t["end"])), None)
    upcoming = next((t for t in here if _parse(t["start"]) > now), None)
    level = levels.get(room_id, "unknown")
    crowded = current is not None and level in ("high", "full")
    return {
        "id": room_id,
        "name": room["name"],
        "crowd_level": level,
        "talk": _summary(current) if current else None,
        "next": _summary(upcoming) if upcoming else None,
        "suggestion": _suggestion(current, level, now) if crowded else None,
    }


def room_states(rooms, wall=None):
    """Return the simulated time and what's happening in each room."""
    global _talks, _rooms, _expires
    wall = time.time() if wall is None else wall
    # Only one request refreshes at a time. The others use the last result, so
    # a slow Elasticsearch can't tie up every worker thread.
    if wall >= _expires and _lock.acquire(blocking=False):
        try:
            talks = list_talks()["talks"]
            if talks:
                now, _, _ = simulated_now(talks, wall)
                levels = current_levels()
                _rooms = [
                    _room_state(room_id, room, talks, levels, now)
                    for room_id, room in rooms.items()
                ]
            _talks = talks
            _expires = wall + ROOMS_CACHE_SECONDS
        finally:
            _lock.release()

    if not _talks:
        return {"now": None, "start": None, "end": None, "speed": SPEED, "rooms": []}
    now, start, end = simulated_now(_talks, wall)
    return {
        "now": now.isoformat(),
        "start": start.isoformat(),
        "end": end.isoformat(),
        "speed": SPEED,
        "rooms": _rooms,
    }
