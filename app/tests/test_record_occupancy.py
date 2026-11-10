from unittest import mock

import pytest
from elasticsearch import AuthorizationException, BadRequestError
from elasticsearch import ConnectionError as ESConnectionError

from search import occupancy as o
from search.mappings import OCCUPANCY_MAPPINGS

OK = {"items": [{"index": {"status": 200}}, {"index": {"status": 201}}]}


@pytest.fixture(autouse=True)
def fresh_process():
    """ensure_history_index checks once per process; start each test afresh."""
    o._history_ready = False
    yield
    o._history_ready = False


def api_error(cls, error, status):
    return cls(error, mock.Mock(status=status), {"error": {"type": error}})


def es_client(*recent_counts):
    """A client whose history holds recent_counts, given oldest first."""
    client = mock.MagicMock()
    client.options.return_value = client
    hits = [
        {"_source": {"ts": f"2026-11-09T17:00:{i:02d}+00:00", "count": count}}
        for i, count in enumerate(recent_counts)
    ]
    client.search.return_value = {"hits": {"hits": list(reversed(hits))}}
    client.bulk.return_value = OK
    return client


def record(client, count, capacity=40):
    with mock.patch.object(o, "get_client", return_value=client):
        return o.record_occupancy("hall-a", count, capacity)


def stored_docs(client):
    operations = client.bulk.call_args.kwargs["operations"]
    return operations[1], operations[3]


def test_first_reading_is_stored_as_is():
    assert record(es_client(), 20)["count"] == 20


def test_a_single_spike_cannot_mark_a_room_full():
    doc = record(es_client(20, 20), 37)
    assert doc["count"] == 20 and doc["level"] == "medium"


def test_a_confirmed_jump_is_accepted():
    doc = record(es_client(20, 20, 37), 38)
    assert doc["level"] == "full"


def test_impossible_counts_are_clamped():
    doc = record(es_client(), 500)
    assert doc["count"] == 60 and doc["ratio"] == 1.5


def test_history_keeps_the_raw_count_and_latest_the_smoothed_one():
    client = es_client(20, 20)
    record(client, 37)
    latest, history = stored_docs(client)
    assert latest["count"] == 20 and history["count"] == 37


def test_only_recent_history_for_this_room_is_read():
    client = es_client()
    record(client, 20)
    kwargs = client.search.call_args.kwargs
    assert kwargs["index"] == "occupancy-history-v2"
    assert kwargs["sort"] == [{"ts": "desc"}]
    filters = kwargs["query"]["bool"]["filter"]
    assert {"term": {"area": "hall-a"}} in filters
    assert {"range": {"ts": {"gte": f"now-{o.STALE_SECONDS}s"}}} in filters


def test_unreadable_history_still_stores_the_reading():
    client = es_client()
    client.search.side_effect = ESConnectionError("down")
    assert record(client, 20)["count"] == 20


@pytest.mark.parametrize("client", [None])
def test_no_client_returns_none(client):
    assert record(client, 20) is None


# ---------- the history index is created with its mappings ----------

def test_history_index_is_created_with_mappings_before_the_first_write():
    client = es_client()
    record(client, 20)
    client.indices.create.assert_called_once_with(index="occupancy-history-v2", mappings=OCCUPANCY_MAPPINGS)
    _, history = stored_docs(client)
    assert history["count"] == 20


def test_an_existing_history_index_is_kept_and_checked_once():
    client = es_client()
    client.indices.create.side_effect = api_error(BadRequestError, "resource_already_exists_exception", 400)
    record(client, 20)
    record(client, 21)
    assert client.indices.create.call_count == 1
    assert len(client.bulk.call_args.kwargs["operations"]) == 4


def test_history_is_skipped_not_auto_created_when_the_index_cant_be_made():
    client = es_client()
    client.indices.create.side_effect = api_error(AuthorizationException, "security_exception", 403)
    client.bulk.return_value = {"items": [{"index": {"status": 200}}]}
    doc = record(client, 20)
    operations = client.bulk.call_args.kwargs["operations"]
    assert doc["count"] == 20
    assert [op for op in operations if "index" in op] == [{"index": {"_index": "occupancy", "_id": "hall-a"}}]


def test_a_refused_create_is_retried_on_the_next_reading():
    client = es_client()
    client.indices.create.side_effect = [api_error(AuthorizationException, "security_exception", 403), None]
    client.bulk.return_value = {"items": [{"index": {"status": 200}}]}
    record(client, 20)
    client.bulk.return_value = OK
    record(client, 21)
    assert client.indices.create.call_count == 2
    assert len(client.bulk.call_args.kwargs["operations"]) == 4
