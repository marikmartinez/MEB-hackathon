from unittest import mock

import pytest

import occupancy
from app import create_app

AREA = {"id": "hall-a", "name": "Hall A", "type": "room", "capacity": 40}
READING = {"area": "hall-a", "count": 10}


class NoCounter:
    def last(self):
        return None


@pytest.fixture
def client():
    return create_app(occupancy.blueprint(NoCounter(), [AREA])).test_client()


def post(client, token=None, payload=READING):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return client.post("/api/occupancy", json=payload, headers=headers)


def test_writes_are_refused_when_no_token_is_configured(client, monkeypatch):
    monkeypatch.delenv("OCCUPANCY_TOKEN", raising=False)
    with mock.patch("app.record_occupancy") as record:
        assert post(client).status_code == 503
    record.assert_not_called()


@pytest.mark.parametrize("supplied", [None, "wrong"])
def test_missing_or_wrong_token_is_rejected(client, monkeypatch, supplied):
    monkeypatch.setenv("OCCUPANCY_TOKEN", "secret")
    with mock.patch("app.record_occupancy") as record:
        assert post(client, supplied).status_code == 401
    record.assert_not_called()


def test_correct_token_stores_the_reading(client, monkeypatch):
    monkeypatch.setenv("OCCUPANCY_TOKEN", "secret")
    with mock.patch("app.record_occupancy", return_value={"area": "hall-a"}) as record:
        assert post(client, "secret").status_code == 200
    record.assert_called_once_with("hall-a", 10, 40)
