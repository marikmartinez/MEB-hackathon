"""Tests for recommend() with Elasticsearch mocked. Run from app/: python -m pytest"""
from datetime import datetime, timedelta, timezone
from unittest import mock

import pytest
from elasticsearch import ConnectionError as ESConnectionError

from search import recommend as r

CHOSEN = {
    "id": "t16", "title": "Sharding Postgres", "abstract": "Splitting tables.",
    "speaker": "A", "tags": ["postgres", "databases"], "track": "data", "level": "advanced",
    "room": "hall-a",
    "start": "2026-11-09T17:00:00-07:00", "end": "2026-11-09T18:00:00-07:00",
}


def talk(talk_id, room, tags):
    return {**CHOSEN, "id": talk_id, "room": room, "tags": tags}


RELATED = talk("t17", "hall-b", ["postgres", "databases"])
ALSO_RELATED = talk("t18", "room-101", ["databases"])
UNRELATED = talk("t19", "room-102", ["security"])
SCORES = [(RELATED, 12.0), (ALSO_RELATED, 8.0), (UNRELATED, 3.0)]


def seconds_ago(seconds):
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat()


def occupancy(age=0, **ratios):
    return {
        room.replace("_", "-"): {
            "area": room.replace("_", "-"), "ratio": ratio, "ts": seconds_ago(age),
        }
        for room, ratio in ratios.items()
    }


def es_client(rooms=None):
    client = mock.MagicMock()
    client.options.return_value = client
    client.get.return_value = {"_source": CHOSEN}

    def search(**kwargs):
        if kwargs["index"] == "occupancy":
            hits = [{"_source": doc} for doc in (rooms or {}).values()]
        else:
            hits = [{"_source": t, "_score": score} for t, score in SCORES]
        return {"hits": {"hits": hits}}

    client.search.side_effect = search
    return client


@pytest.fixture(autouse=True)
def reset_occupancy_cache():
    r._occupancy = {}
    r._occupancy_read_at = None


def recommend_with(client, **kwargs):
    with mock.patch.object(r, "get_client", return_value=client):
        return r.recommend("t16", **kwargs)


def ids(result):
    return [item["id"] for item in result["results"]]


def test_crowd_penalty_grows_with_crowding_and_is_clamped():
    assert r.crowd_penalty(0) == 0
    assert r.crowd_penalty(0.3) < r.crowd_penalty(0.8) < r.crowd_penalty(1.0)
    assert r.crowd_penalty(1.4) == 1
    assert r.crowd_penalty(-1) == 0


@pytest.mark.parametrize("ratio, level", [
    (None, "unknown"), (0.2, "low"), (0.5, "medium"), (0.75, "high"), (0.9, "full"),
])
def test_level_for(ratio, level):
    assert r.level_for(ratio) == level


def test_relevance_decides_when_rooms_are_equally_quiet():
    result = recommend_with(es_client())
    assert ids(result) == ["t17", "t18", "t19"]
    assert result["results"][0]["score"] == 1.0
    assert result["source"] == "elasticsearch"


def test_a_busy_room_drops_below_a_quieter_related_talk():
    rooms = occupancy(hall_b=0.85, room_101=0.1)
    assert ids(recommend_with(es_client(rooms)))[0] == "t18"


def test_moderately_busy_related_talk_beats_empty_unrelated_talk():
    rooms = occupancy(hall_b=0.6, room_101=0.6, room_102=0.0)
    assert ids(recommend_with(es_client(rooms)))[-1] == "t19"


def test_full_rooms_are_dropped():
    assert "t17" not in ids(recommend_with(es_client(occupancy(hall_b=0.9))))


def test_full_level_from_the_pipeline_drops_a_room():
    rooms = {"hall-b": {"area": "hall-b", "ratio": 0.5, "level": "full", "ts": seconds_ago(0)}}
    assert "t17" not in ids(recommend_with(es_client(rooms)))


def test_stale_full_room_is_unknown_and_not_dropped():
    result = recommend_with(es_client(occupancy(age=r.STALE_SECONDS + 5, hall_b=0.95)))
    in_hall_b = next(item for item in result["results"] if item["id"] == "t17")
    assert in_hall_b["why"]["crowd_level"] == "unknown"
    assert in_hall_b["score"] == 1.0


def test_recent_reading_still_counts():
    result = recommend_with(es_client(occupancy(age=r.STALE_SECONDS - 30, hall_b=0.95)))
    assert "t17" not in ids(result)


@pytest.mark.parametrize("ts", [None, "not-a-date"])
def test_reading_without_a_usable_timestamp_is_ignored(ts):
    rooms = {"hall-b": {"area": "hall-b", "ratio": 0.95, "ts": ts}}
    assert "t17" in ids(recommend_with(es_client(rooms)))


def test_chosen_talks_stale_room_reads_unknown():
    rooms = occupancy(age=r.STALE_SECONDS + 5, hall_a=0.95)
    assert recommend_with(es_client(rooms))["crowd_level"] == "unknown"


def test_fallback_ignores_last_known_levels_once_stale():
    recommend_with(es_client(occupancy(hall_b=0.95)))
    r._occupancy["hall-b"]["ts"] = seconds_ago(r.STALE_SECONDS + 5)
    assert "t17" in ids(recommend_with(None))


def test_k_limits_results():
    assert len(recommend_with(es_client(), k=2)["results"]) == 2


def test_result_explains_itself():
    rooms = occupancy(hall_a=0.95, hall_b=0.6)
    result = recommend_with(es_client(rooms))
    assert result["crowd_level"] == "full"
    in_hall_b = next(item for item in result["results"] if item["id"] == "t17")
    assert in_hall_b["why"] == {"shared_tags": ["databases", "postgres"], "crowd_level": "medium"}


def test_query_is_limited_to_overlapping_talks_and_excludes_the_chosen_one():
    client = es_client()
    recommend_with(client)
    call = next(c for c in client.search.call_args_list if c.kwargs["index"] == "talks")
    query = call.kwargs["query"]["bool"]
    assert query["must_not"] == [{"ids": {"values": ["t16"]}}]
    assert {"range": {"start": {"lt": CHOSEN["end"]}}} in query["filter"]
    assert {"range": {"end": {"gt": CHOSEN["start"]}}} in query["filter"]
    assert query["minimum_should_match"] == 1


def test_stale_and_fresh_readings_are_told_apart():
    rooms = {
        **occupancy(age=r.STALE_SECONDS + 1, hall_a=0.9),
        **occupancy(age=r.STALE_SECONDS - 5, hall_b=0.2),
    }
    client = es_client(rooms)
    result = recommend_with(client)

    assert result["crowd_level"] == "unknown"  # hall-a is stale
    in_hall_b = next(item for item in result["results"] if item["id"] == "t17")
    assert in_hall_b["why"]["crowd_level"] == "low"
    call = next(c for c in client.search.call_args_list if c.kwargs["index"] == "occupancy")
    assert call.kwargs["source"] == ["area", "ratio", "level", "ts"]


def test_occupancy_is_cached_between_requests():
    client = es_client()
    recommend_with(client)
    recommend_with(client)
    reads = [c for c in client.search.call_args_list if c.kwargs["index"] == "occupancy"]
    assert len(reads) == 1


def test_falls_back_without_a_client():
    result = recommend_with(None)
    assert result["source"] == "fallback"
    assert result["results"]
    assert "t16" not in ids(result)


def test_fallback_keeps_the_last_known_crowd_levels():
    # In the seed schedule, t17 is the Hall B talk that overlaps t16.
    recommend_with(es_client(occupancy(hall_b=0.95)))
    client = es_client()
    client.get.side_effect = ESConnectionError("down")
    result = recommend_with(client)
    assert result["source"] == "fallback"
    assert "t17" not in ids(result)


def test_unknown_talk_raises():
    with mock.patch.object(r, "get_client", return_value=None):
        with pytest.raises(r.TalkNotFound):
            r.recommend("nope")
