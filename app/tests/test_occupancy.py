import json

import pytest

import occupancy
from app import create_app
from occupancy import area_status, level_for, smooth, snapshot

AREA = {"id": "hall-a", "name": "Hall A", "type": "room", "capacity": 80}
NOW = 1_000_000.0


def hist(*counts, step=30.0, end=NOW):
    """History with the last frame at `end`."""
    start = end - step * (len(counts) - 1)
    return [(start + i * step, c) for i, c in enumerate(counts)]


@pytest.mark.parametrize(
    "ratio,level", [(0, "low"), (0.49, "low"), (0.5, "medium"), (0.79, "medium"), (0.8, "high"), (1.0, "full"), (1.4, "full")]
)
def test_levels(ratio, level):
    assert level_for(ratio) == level


def test_steady_counts_smooth():
    value, held = smooth(hist(40, 42, 38, 40), 80)
    assert 38 <= value <= 42 and not held


def test_clamps_impossible_counts():
    value, _ = smooth(hist(500), 80)
    assert value == 120  # 1.5 x capacity
    value, _ = smooth(hist(-3), 80)
    assert value == 0


def test_single_spike_is_held():
    value, held = smooth(hist(20, 20, 70), 80)
    assert value == 20 and held


def test_spike_then_back_is_ignored():
    value, held = smooth(hist(20, 20, 70, 21), 80)
    assert 20 <= value <= 21 and not held


def test_confirmed_jump_is_accepted():
    value, held = smooth(hist(20, 20, 70, 72), 80)
    assert value == 71 and not held


def test_small_absolute_changes_are_not_spikes():
    # 2 -> 6 is +200% but only 4 people: normal movement, not a glitch
    value, held = smooth(hist(2, 6), 80)
    assert value == 4 and not held


def test_opposite_spikes_do_not_confirm_each_other():
    value, held = smooth(hist(40, 40, 5, 75), 80)
    assert value == 40 and held


def test_status_from_reading():
    # EMA: 66 -> 67 -> 69 = 86% of 80
    s = area_status(AREA, {"history": hist(66, 68, 71)}, 30, NOW)
    assert s.level == "high" and s.count == 69 and s.raw == 71 and not s.stale


def test_stale_reading_is_unknown_but_keeps_last_count():
    s = area_status(AREA, {"history": hist(20, end=NOW - 91)}, 30, NOW)
    assert s.stale and s.level == "unknown" and s.count == 20


def test_missing_reading_is_unknown():
    s = area_status(AREA, None, 30, NOW)
    assert s.level == "unknown" and s.count is None


def test_snapshot_counter_down():
    snap = snapshot([AREA], None, now=NOW)
    assert snap["source"] == "unavailable"
    assert snap["areas"][0]["level"] == "unknown"


def test_snapshot_uses_counter_tick():
    data = {"tick_seconds": 120, "areas": {"hall-a": {"history": hist(10, end=NOW - 300)}}}
    # 300 s old is within 3 ticks of 120 s
    assert snapshot([AREA], data, now=NOW)["areas"][0]["level"] == "low"


def test_venue_file_is_valid():
    venue = occupancy.load_venue()
    ids = {a["id"] for a in venue}
    assert len(ids) == len(venue)
    for a in venue:
        assert a["capacity"] > 0 and a["type"] in {"room", "corridor"}, a["id"]
    connections = json.loads(occupancy.VENUE_PATH.read_text())["connections"]
    for pair in connections:
        assert len(pair) == 2 and set(pair) <= ids, pair


def test_counter_replay_areas_exist_in_venue():
    # The counter's frames/<area>/ folders must use venue ids, or those
    # areas never get a level.
    from counter.mall_frames import DEFAULT_STRETCHES

    ids = {a["id"] for a in occupancy.load_venue()}
    assert set(DEFAULT_STRETCHES) <= ids


class FakeClient:
    def __init__(self, data):
        self.data = data

    def last(self):
        return self.data


def test_api(monkeypatch):
    import time

    now = time.time()
    data = {"tick_seconds": 30, "areas": {"hall-a": {"history": hist(70, 72, end=now)}}}
    app = create_app(occupancy.blueprint(FakeClient(data), [AREA]))
    c = app.test_client()
    body = c.get("/api/occupancy").get_json()
    assert body["source"] == "ok" and body["areas"][0]["level"] == "high"
    assert c.get("/api/occupancy/hall-a").get_json()["count"] == 71
    assert c.get("/api/occupancy/nowhere").status_code == 404


def test_counter_client_handles_unreachable_counter():
    client = occupancy.CounterClient("http://127.0.0.1:9")
    assert client.last() is None
