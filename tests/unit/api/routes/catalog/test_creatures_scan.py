"""Node routing and error handling for creature discovery."""

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from kohakuterrarium.api.deps import get_service
from kohakuterrarium.api.routes.catalog import creatures_scan


class _CatalogSender:
    def __init__(self, response):
        self.response = response
        self.requests = []

    async def request(self, **kwargs):
        self.requests.append(kwargs)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def _client(sender):
    app = FastAPI()
    app.include_router(creatures_scan.router, prefix="/creatures")
    app.dependency_overrides[get_service] = lambda: SimpleNamespace(
        host=sender, connected_nodes=lambda: ("_host", "worker")
    )
    return TestClient(app)


def test_selected_node_is_forwarded_without_host_discovery():
    entries = [
        {"name": "worker-only", "path": "@worker/creatures/general", "description": ""}
    ]
    sender = _CatalogSender({"creatures": entries})
    response = _client(sender).get("/creatures", params={"on_node": "worker"})
    assert response.status_code == 200
    assert response.json() == entries
    assert sender.requests == [
        {
            "to_node": "worker",
            "namespace": "studio.catalog",
            "type": "creatures",
            "body": {},
            "timeout": 10.0,
        }
    ]


def test_disconnected_node_does_not_fall_back_to_host():
    sender = _CatalogSender({"creatures": []})
    response = _client(sender).get("/creatures", params={"on_node": "missing"})
    assert response.status_code == 404
    assert "missing" in response.json()["detail"]
    assert sender.requests == []


@pytest.mark.parametrize(
    "payload",
    [
        TimeoutError("worker timed out"),
        {"error": {"kind": "unknown_type", "message": "unsupported creatures"}},
        {"error": "invalid response"},
        {"creatures": None},
        None,
    ],
)
def test_discovery_failure_is_explicit(payload):
    response = _client(_CatalogSender(payload)).get(
        "/creatures", params={"on_node": "worker"}
    )
    assert response.status_code == 502
    assert "Cannot load creature configs from worker" in response.json()["detail"]
