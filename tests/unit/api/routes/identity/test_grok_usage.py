"""Host and remote dispatch for GET /settings/grok-usage."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from kohakuterrarium.api.deps import get_service
from kohakuterrarium.api.routes.identity import grok as grok_routes


class _Service:
    def __init__(self):
        self.calls = []

    def connected_nodes(self):
        return ["worker-a"]

    @property
    def host(self):
        return self

    async def request(self, *, to_node, namespace, type, body, timeout):
        self.calls.append(
            {
                "to_node": to_node,
                "namespace": namespace,
                "type": type,
                "body": body,
                "timeout": timeout,
            }
        )
        return {
            "status": "ok",
            "source": "live",
            "credential_source": "grok-cli",
            "captured_at": 10.0,
            "window": {"period": "weekly", "used_percent": 1.0, "resets_at": 20.0},
            "products": [{"name": "GrokBuild", "used_percent": 1.0}],
            "prepaid_balance": 0,
        }


def _client(service):
    app = FastAPI()
    app.include_router(grok_routes.router, prefix="/settings")
    app.dependency_overrides[get_service] = lambda: service
    return TestClient(app)


class TestGrokUsageRoute:
    def test_host_returns_service_payload(self, monkeypatch):
        payload = grok_routes_empty = {
            "status": "not_logged_in",
            "source": "live",
            "credential_source": None,
            "captured_at": None,
            "window": None,
            "products": [],
            "prepaid_balance": None,
        }
        seen = {}

        async def fake_usage():
            seen["called"] = True
            return payload

        monkeypatch.setattr(grok_routes, "get_usage", fake_usage)
        response = _client(object()).get("/settings/grok-usage?node=_host")

        assert seen == {"called": True}
        assert response.status_code == 200
        assert response.json() == grok_routes_empty

    def test_remote_dispatches_grok_usage_without_host_fetch(self, monkeypatch):
        async def fail_usage():
            raise AssertionError("host usage must not run for a worker node")

        monkeypatch.setattr(grok_routes, "get_usage", fail_usage)
        service = _Service()
        response = _client(service).get("/settings/grok-usage?node=worker-a")

        assert response.status_code == 200
        assert response.json()["status"] == "ok"
        assert service.calls == [
            {
                "to_node": "worker-a",
                "namespace": "studio.identity",
                "type": "grok_usage",
                "body": {},
                "timeout": 90.0,
            }
        ]
