"""``/api/app/secret-store``: API keys in the system keychain, opt in."""

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

keyring = pytest.importorskip("keyring")
from keyring.backend import KeyringBackend  # noqa: E402
from keyring.backends import fail  # noqa: E402
from keyring.errors import PasswordDeleteError  # noqa: E402

from kohakuterrarium.api.routes import app_secrets  # noqa: E402
from kohakuterrarium.llm.profiles import api_keys  # noqa: E402


class MemoryKeyring(KeyringBackend):
    priority = 5

    def __init__(self):
        super().__init__()
        self.items = {}

    def get_password(self, service, username):
        return self.items.get((service, username))

    def set_password(self, service, username, password):
        self.items[(service, username)] = password

    def delete_password(self, service, username):
        if (service, username) not in self.items:
            raise PasswordDeleteError("missing")
        del self.items[(service, username)]


@pytest.fixture
def vault():
    previous = keyring.get_keyring()
    backend = MemoryKeyring()
    keyring.set_keyring(backend)
    yield backend
    keyring.set_keyring(previous)


def _client(address="127.0.0.1"):
    app = FastAPI()
    app.include_router(app_secrets.router, prefix="/api/app")
    return TestClient(app, client=(address, 5000))


def test_status_reports_the_file_store_and_an_available_keychain(vault):
    body = _client().get("/api/app/secret-store").json()

    assert body == {
        "local": True,
        "mode": "file",
        "active": False,
        "available": True,
        "backend": "MemoryKeyring",
    }


def test_switching_moves_the_keys_and_back(vault):
    api_keys.save_api_key("openai", "sk-route-secret")
    client = _client()

    on = client.post("/api/app/secret-store", json={"mode": "keychain"}).json()

    assert on["mode"] == "keychain" and on["active"] is True and on["moved"] == 1
    assert vault.items[("KohakuTerrarium", "openai")] == "sk-route-secret"

    off = client.post("/api/app/secret-store", json={"mode": "file"}).json()

    assert off["mode"] == "file" and off["active"] is False and off["moved"] == 1
    assert vault.items == {}
    assert api_keys.get_api_key("openai") == "sk-route-secret"


def test_without_a_keychain_the_switch_is_a_conflict():
    previous = keyring.get_keyring()
    keyring.set_keyring(fail.Keyring())
    try:
        client = _client()
        assert client.get("/api/app/secret-store").json()["available"] is False

        response = client.post("/api/app/secret-store", json={"mode": "keychain"})

        assert response.status_code == 409
        assert "no system keychain" in response.json()["detail"]
    finally:
        keyring.set_keyring(previous)


def test_an_unknown_mode_is_a_conflict(vault):
    response = _client().post("/api/app/secret-store", json={"mode": "cloud"})
    assert response.status_code == 409


def test_other_machines_cannot_move_the_keys(vault):
    api_keys.save_api_key("openai", "sk-route-secret")

    response = _client("203.0.113.9").post(
        "/api/app/secret-store", json={"mode": "keychain"}
    )

    assert response.status_code == 403
    assert vault.items == {}
    assert _client("203.0.113.9").get("/api/app/secret-store").json()["local"] is False
