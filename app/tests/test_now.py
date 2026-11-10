from datetime import datetime, timedelta, timezone
from unittest import mock

import pytest

import occupancy
from app import create_app
from search import now as n
from search import recommend as r

TZ = timezone(timedelta(hours=-7))
ROOMS = {
    "hall-a": {"name": "Hall A", "capacity": 40},
    "room-101": {"name": "Room 101", "capacity": 80},
}


def at(hours, minutes=0):
    return datetime(2026, 11, 9, hours, minutes, tzinfo=TZ).isoformat()


def talk(talk_id, room, start, end):
    return {
        "id": talk_id, "title": f"Talk {talk_id}", "speaker": "A", "room": room,
        "tags": ["postgres"], "track": "data", "abstract": "Text.", "level": "beginner",
        "start": start, "end": end,
    }


TALKS = [
    talk("a1", "hall-a", at(14), at(14, 50)),
    talk("a2", "hall-a", at(15), at(16)),
    talk("b1", "room-101", at(14, 10), at(15)),
]


@pytest.fixture(autouse=True)
def reset_cache():
    n._talks, n._rooms, n._expires = [], [], 0.0
    r._occupancy, r._occupancy_read_at = {}, None


def states(levels, suggestion_results=(), wall=0.0, talks=TALKS):
    with mock.patch.object(n, "list_talks", return_value={"talks": talks}), \
            mock.patch.object(n, "current_levels", return_value=levels), \
            mock.patch.object(
                n, "recommend", return_value={"results": list(suggestion_results)}
            ) as recommend:
        return n.room_states(ROOMS, wall), recommend


def candidate(talk_id, room, level):
    return {
        **talk(talk_id, room, at(14), at(15)),
        "why": {"crowd_level": level, "shared_tags": ["postgres"]},
    }


def test_one_real_minute_is_one_conference_hour():
    start, _, _ = n.simulated_now(TALKS, 0.0)
    later, _, _ = n.simulated_now(TALKS, 60.0)
    assert later - start == timedelta(hours=1)


def test_clock_loops_over_the_schedule():
    # The schedule spans 14:00-16:00, which is 120 real seconds.
    assert n.simulated_now(TALKS, 0.0)[0] == n.simulated_now(TALKS, 120.0)[0]
    assert n.simulated_now(TALKS, 0.0)[0].isoformat() == at(14)


def test_clock_keeps_the_schedules_utc_offset():
    assert n.simulated_now(TALKS, 30.0)[0].isoformat().endswith("-07:00")


def test_room_shows_the_talk_running_now():
    result, _ = states({"hall-a": "low"}, wall=30.0)  # 14:30
    hall = result["rooms"][0]
    assert hall["talk"]["id"] == "a1" and hall["suggestion"] is None
    assert hall["next"]["id"] == "a2"


def test_room_between_talks_has_no_current_talk():
    result, _ = states({}, wall=55.0)  # 14:55
    hall = result["rooms"][0]
    assert hall["talk"] is None and hall["next"]["id"] == "a2"
    assert hall["crowd_level"] == "unknown"


@pytest.mark.parametrize("level", ["high", "full"])
def test_crowded_room_gets_a_quieter_similar_talk(level):
    result, recommend = states({"hall-a": level}, [candidate("b1", "room-101", "low")], wall=30.0)
    suggestion = result["rooms"][0]["suggestion"]
    assert suggestion["id"] == "b1" and suggestion["room"] == "room-101"
    assert suggestion["crowd_level"] == "low"
    assert recommend.call_args.kwargs["at"] == at(14, 30)


def test_quiet_room_makes_no_search():
    _, recommend = states({"hall-a": "low", "room-101": "medium"}, wall=30.0)
    recommend.assert_not_called()


def test_equally_crowded_or_unknown_rooms_are_not_suggested():
    results = [candidate("x", "room-101", "high"), candidate("y", "room-102", "unknown")]
    result, _ = states({"hall-a": "high"}, results, wall=30.0)
    assert result["rooms"][0]["suggestion"] is None


def test_takes_the_most_relevant_quieter_talk():
    results = [candidate("x", "room-101", "full"), candidate("y", "room-102", "medium"),
               candidate("z", "room-103", "low")]
    result, _ = states({"hall-a": "full"}, results, wall=30.0)
    assert result["rooms"][0]["suggestion"]["id"] == "y"


def test_rooms_are_cached_briefly_but_the_clock_keeps_moving():
    first, _ = states({"hall-a": "low"}, wall=30.0)
    with mock.patch.object(n, "list_talks") as list_talks:
        second = n.room_states(ROOMS, 31.0)
    list_talks.assert_not_called()
    assert second["rooms"] == first["rooms"]
    assert second["now"] == at(14, 31)


def test_no_talks_returns_an_empty_state():
    result, _ = states({}, talks=[])
    assert result["now"] is None and result["rooms"] == []


def test_query_can_be_limited_to_a_moment():
    moment = at(14, 30)
    constraints = r._constraints(TALKS[0], moment)
    assert {"range": {"start": {"lte": moment}}} in constraints["filter"]
    assert {"range": {"end": {"gt": moment}}} in constraints["filter"]


def test_fallback_only_returns_talks_running_at_that_moment():
    with mock.patch.object(r, "get_client", return_value=None):
        result = r.recommend("t16", at="2026-11-09T17:55:00-07:00")
    ids = {item["id"] for item in result["results"]}
    assert ids == {"t17", "t19"}  # t18 and t20 end at 17:50


def test_api_route_returns_the_state():
    class NoCounter:
        def last(self):
            return None

    area = {"id": "hall-a", "name": "Hall A", "type": "room", "capacity": 40}
    client = create_app(occupancy.blueprint(NoCounter(), [area])).test_client()
    with mock.patch("app.room_states", return_value={"now": "x", "rooms": []}):
        response = client.get("/api/now")
    assert response.status_code == 200 and response.get_json()["now"] == "x"
