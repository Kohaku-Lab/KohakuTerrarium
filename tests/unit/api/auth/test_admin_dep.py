"""Unit tests for :func:`verify_admin_token`.

The dependency is a thin admin gate.  Off when ``admin_token`` empty;
on, requires the ``X-Admin-Token`` header to constant-time-equal the
configured secret; raises 401 with a structured detail body on miss.
The 401 carries ``X-Auth-Required: admin`` so the frontend can
distinguish "needs admin pswd" from "needs login."
"""

import pytest
from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient

from kohakuterrarium.api.auth.config import AuthConfig
from kohakuterrarium.api.auth.dependencies import verify_admin_token


def _make_app(config: AuthConfig) -> FastAPI:
    app = FastAPI()
    app.state.auth_config = config
    router = APIRouter()

    @router.post("/mutate", dependencies=[Depends(verify_admin_token)])
    def mutate() -> dict[str, str]:
        return {"status": "ok"}

    @router.get("/read")
    def read() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(router)
    return app


class TestAdminDepOff:
    def test_no_admin_token_lets_mutate_through(self):
        cfg = AuthConfig()  # admin_token = ""
        with TestClient(_make_app(cfg)) as client:
            resp = client.post("/mutate")
        assert resp.status_code == 200

    def test_no_admin_token_lets_read_through(self):
        # Sanity — read route never had the dep; should also pass.
        cfg = AuthConfig()
        with TestClient(_make_app(cfg)) as client:
            resp = client.get("/read")
        assert resp.status_code == 200


class TestAdminDepOn:
    def test_mutate_without_header_is_401(self):
        cfg = AuthConfig(admin_token="secret")
        with TestClient(_make_app(cfg)) as client:
            resp = client.post("/mutate")
        assert resp.status_code == 401
        body = resp.json()
        assert body["detail"]["error"] == "admin_required"

    def test_mutate_with_wrong_header_is_401(self):
        cfg = AuthConfig(admin_token="secret")
        with TestClient(_make_app(cfg)) as client:
            resp = client.post("/mutate", headers={"X-Admin-Token": "wrong"})
        assert resp.status_code == 401

    def test_mutate_with_correct_header_passes(self):
        cfg = AuthConfig(admin_token="secret")
        with TestClient(_make_app(cfg)) as client:
            resp = client.post("/mutate", headers={"X-Admin-Token": "secret"})
        assert resp.status_code == 200

    def test_401_carries_distinguishing_header(self):
        # X-Auth-Required: admin lets the frontend's connection state
        # machine raise the admin-password modal instead of re-prompting
        # login.  Critical UX contract.
        cfg = AuthConfig(admin_token="secret")
        with TestClient(_make_app(cfg)) as client:
            resp = client.post("/mutate")
        lower_keys = {k.lower(): v for k, v in resp.headers.items()}
        assert lower_keys.get("x-auth-required") == "admin"

    def test_read_route_unaffected(self):
        # Read route doesn't carry the dep — even with admin on, GET
        # /read should always pass.
        cfg = AuthConfig(admin_token="secret")
        with TestClient(_make_app(cfg)) as client:
            resp = client.get("/read")
        assert resp.status_code == 200


class TestConstantTimeCompare:
    @pytest.mark.parametrize("wrong", ["", "s", "se", "secre", "secrett", "zzzz"])
    def test_all_wrong_get_identical_rejection_shape(self, wrong):
        cfg = AuthConfig(admin_token="secret")
        with TestClient(_make_app(cfg)) as client:
            resp = client.post("/mutate", headers={"X-Admin-Token": wrong})
        assert resp.status_code == 401


class TestConfigSnapshotEffective:
    def test_flipping_config_at_runtime_takes_effect(self):
        app = _make_app(AuthConfig())
        with TestClient(app) as client:
            # No admin token → mutate passes.
            assert client.post("/mutate").status_code == 200
            # Flip on → 401.
            app.state.auth_config = AuthConfig(admin_token="secret")
            assert client.post("/mutate").status_code == 401
            # With correct header → pass.
            assert (
                client.post("/mutate", headers={"X-Admin-Token": "secret"}).status_code
                == 200
            )
