from datetime import datetime, timedelta, timezone
from unittest import mock

import pytest
from elasticsearch import ConnectionError as ESConnectionError

import occupancy
from app import create_app
from search import history as h

ROOMS = {
    "hall-a": {"id": "hall-a", "name": "Hall A", "type": "room", "capacity": 40},
    "hall-b": {"id": "hall-b", "name": "Hall B", "type": "room", "capacity": 200},
}
TALKS = [
    {"id": "t1", "title": "Early", "speaker": "A", "room": "hall-a",
     "start": "2026-11-09T14:00:00-07:00", "end": "2026-11-09T14:50:00-07:00"},
    {"id": "t2", "title": "Now", "speaker": "B", "room": "hall-b",
     "start": "2026-11-09T15:00:00-07:00", "end": "2026-11-09T16:00:00-07:00"},
    {"id": "t3", "title": "Later", "speaker": "C", "room": "hall-a",
     "start": "2026-11-09T17:00:00-07:00", "end": "2026-11-09T18:00:00-07:00"},
]
# 15:30 at the venue: t1 ended, t2 on now, t3 upcoming.
NOW = datetime(2026, 11, 9, 22, 30, tzinfo=timezone.utc)


def es(result=None, error=None):
    client = mock.MagicMock()
    client.options.return_value = client
    if error:
        client.search.side_effect = error
    else:
        client.search.return_value = result
    return client


# ---------- windows and buckets ----------

def test_day_window_runs_from_first_talk_until_now():
    start, end = h.window("day", TALKS, NOW)
    assert start == datetime(2026, 11, 9, 21, 0, tzinfo=timezone.utc)
    assert end == NOW


def test_day_window_stops_at_the_last_talk():
    late = NOW + timedelta(days=1)
    assert h.window("day", TALKS, late)[1] == datetime(2026, 11, 10, 1, 0, tzinfo=timezone.utc)


def test_day_window_before_the_conference_shows_the_last_hour():
    early = datetime(2026, 11, 9, 12, 0, tzinfo=timezone.utc)
    assert h.window("day", TALKS, early) == (early - timedelta(hours=1), early)


def test_fixed_ranges_end_now():
    assert h.window("1h", TALKS, NOW) == (NOW - timedelta(hours=1), NOW)


@pytest.mark.parametrize("hours, expected", [(1, 60), (4, 120), (12, 360)])
def test_interval_keeps_buckets_bounded(hours, expected):
    assert h.interval_seconds(NOW - timedelta(hours=hours), NOW) == expected


# ---------- talk status ----------

def test_talk_status():
    assert [h.talk_status(t, NOW) for t in TALKS] == ["ended", "live", "upcoming"]


# ---------- room history ----------

HISTORY_RESULT = {"aggregations": {"rooms": {"buckets": [
    {"key": "hall-a", "over_time": {"buckets": [
        {"key": 1000, "ratio": {"value": 0.51234}, "peak": {"value": 0.6}, "people": {"value": 20.44}},
    ]}},
    {"key": "not-a-room", "over_time": {"buckets": []}},
]}}}


def test_room_history_shapes_series_and_keeps_empty_rooms():
    client = es(HISTORY_RESULT)
    with mock.patch.object(h, "get_client", return_value=client):
        out = h.room_history(NOW - timedelta(hours=1), NOW, ROOMS)
    assert out["interval"] == 60
    assert out["rooms"]["hall-a"] == [{"t": 1000, "ratio": 0.512, "peak": 0.6, "people": 20.4}]
    assert out["rooms"]["hall-b"] == []
    assert "not-a-room" not in out["rooms"]


def test_room_history_queries_the_window_on_history():
    client = es(HISTORY_RESULT)
    start = NOW - timedelta(hours=1)
    with mock.patch.object(h, "get_client", return_value=client):
        h.room_history(start, NOW, ROOMS)
    kwargs = client.search.call_args.kwargs
    assert kwargs["index"] == "occupancy-history-v2"
    assert kwargs["query"] == {"range": {"ts": {"gte": start.isoformat(), "lte": NOW.isoformat()}}}
    hist = kwargs["aggs"]["rooms"]["aggs"]["over_time"]["date_histogram"]
    assert hist["fixed_interval"] == "60s" and hist["min_doc_count"] == 1


def test_room_history_is_none_when_es_is_down():
    with mock.patch.object(h, "get_client", return_value=es(error=ESConnectionError("down"))):
        assert h.room_history(NOW - timedelta(hours=1), NOW, ROOMS) is None


def test_room_history_is_none_without_a_client():
    with mock.patch.object(h, "get_client", return_value=None):
        assert h.room_history(NOW - timedelta(hours=1), NOW, ROOMS) is None


# ---------- talk stats ----------

TALK_RESULT = {"aggregations": {"talks": {"buckets": {
    "t1": {"doc_count": 0, "avg_ratio": {"value": None}, "peak_ratio": {"value": None}, "avg_people": {"value": None}},
    "t2": {"doc_count": 12, "avg_ratio": {"value": 0.4}, "peak_ratio": {"value": 0.55}, "avg_people": {"value": 80.0}},
}}}}


def test_talk_stats_query_one_filter_per_started_talk():
    client = es(TALK_RESULT)
    with mock.patch.object(h, "get_client", return_value=client):
        h.talk_stats(TALKS, NOW)
    filters = client.search.call_args.kwargs["aggs"]["talks"]["filters"]["filters"]
    assert set(filters) == {"t1", "t2"}
    assert filters["t2"]["bool"]["filter"] == [
        {"term": {"area": "hall-b"}},
        {"range": {"ts": {"gte": TALKS[1]["start"], "lt": TALKS[1]["end"]}}},
    ]


def test_talk_stats_without_readings_are_empty_not_zero():
    with mock.patch.object(h, "get_client", return_value=es(TALK_RESULT)):
        stats = h.talk_stats(TALKS, NOW)
    assert stats["t1"] == {"readings": 0, "avg_ratio": None, "peak_ratio": None, "avg_people": None}
    assert stats["t2"]["avg_ratio"] == 0.4


def test_no_started_talks_skips_the_query():
    client = es(TALK_RESULT)
    with mock.patch.object(h, "get_client", return_value=client):
        assert h.talk_stats(TALKS, datetime(2026, 11, 9, 12, tzinfo=timezone.utc)) == {}
    client.search.assert_not_called()


# ---------- routes ----------

class NoCounter:
    def last(self):
        return None


@pytest.fixture
def client():
    with mock.patch("app.load_rooms", return_value=ROOMS):
        app = create_app(occupancy.blueprint(NoCounter(), list(ROOMS.values())))
    with mock.patch("app.list_talks", return_value={"source": "seed", "talks": TALKS}):
        yield app.test_client()


def test_organizer_page_renders(client):
    resp = client.get("/organizer")
    assert resp.status_code == 200 and b"organizer.js" in resp.data


@pytest.mark.parametrize("path, current, other", [
    ("/", 'href="/" aria-current="page"', 'href="/organizer">'),
    ("/organizer", 'href="/organizer" aria-current="page"', 'href="/">'),
])
def test_each_page_links_to_the_other(client, path, current, other):
    html = client.get(path).get_data(as_text=True)
    assert current in html and other in html


def test_history_rejects_unknown_ranges(client):
    assert client.get("/api/organizer/history?range=7d").status_code == 400


def test_history_lists_every_room_even_without_data(client):
    with mock.patch.object(h, "room_history", return_value={"interval": 60, "rooms": {"hall-a": [{"t": 1, "ratio": 0.5, "peak": 0.5, "people": 20}]}}):
        data = client.get("/api/organizer/history?range=1h").get_json()
    assert data["source"] == "elasticsearch"
    assert [r["id"] for r in data["rooms"]] == ["hall-a", "hall-b"]
    assert data["rooms"][1]["points"] == []


def test_history_reports_es_down(client):
    with mock.patch.object(h, "room_history", return_value=None):
        data = client.get("/api/organizer/history").get_json()
    assert data["source"] == "unavailable"
    assert all(r["points"] == [] for r in data["rooms"])


def test_talks_join_stats_and_derive_peak_people_from_the_smoothed_peak(client):
    stats = {"t2": {"readings": 12, "avg_ratio": 0.4, "peak_ratio": 0.55, "avg_people": 80.0}}
    with mock.patch.object(h, "talk_stats", return_value=stats):
        data = client.get("/api/organizer/talks").get_json()
    t2 = next(t for t in data["talks"] if t["id"] == "t2")
    assert t2["room_name"] == "Hall B" and t2["capacity"] == 200
    assert t2["peak_people"] == 110 and t2["peak_level"] == "medium"
    t1 = next(t for t in data["talks"] if t["id"] == "t1")
    assert t1["readings"] == 0 and t1["peak_people"] is None and t1["peak_level"] == "unknown"


def test_talks_report_es_down(client):
    with mock.patch.object(h, "talk_stats", return_value=None):
        data = client.get("/api/organizer/talks").get_json()
    assert data["source"] == "unavailable" and len(data["talks"]) == 3
