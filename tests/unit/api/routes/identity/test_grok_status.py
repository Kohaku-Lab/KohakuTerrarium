"""Tests for the redacted Grok subscription status route."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from kohakuterrarium.api.deps import get_service
from kohakuterrarium.api.routes.identity import grok as grok_routes


class TestGrokStatusRoute:
    def test_host_status_is_returned_without_secrets(self, monkeypatch):
        monkeypatch.setattr(
            grok_routes,
            "get_status",
            lambda: {
                "authenticated": True,
                "source": "grok-cli",
                "sources": ["grok-cli"],
            },
        )
        app = FastAPI()
        app.include_router(grok_routes.router, prefix="/settings")
        app.dependency_overrides[get_service] = lambda: object()

        response = TestClient(app).get("/settings/grok-status?node=_host")

        assert response.status_code == 200
        assert response.json() == {
            "authenticated": True,
            "source": "grok-cli",
            "sources": ["grok-cli"],
        }
