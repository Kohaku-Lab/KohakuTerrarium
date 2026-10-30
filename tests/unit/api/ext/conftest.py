"""Fixtures for the UI-extension API tests: real UI packages and L4 users."""

import uuid
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from kohakuterrarium.api.auth.config import AuthConfig
from kohakuterrarium.api.auth.db import (
    _reset_migration_state_for_tests,
    connection,
    ensure_migrated,
)
from kohakuterrarium.api.auth.users import create_user
from kohakuterrarium.api.deps import set_service
from kohakuterrarium.packages import locations
from kohakuterrarium.packages.slots.ui import invalidate_ui_cache

HANDLER_SOURCE = """
import sys

from fastapi import APIRouter, Depends

from kohakuterrarium.api.ext import current_engine, package_name
from kohakuterrarium.modules.plugin.base import BasePlugin
from kohakuterrarium.modules.ui_owner import (
    OwnerArgumentError,
    OwnerForbiddenError,
    UIOwnerMixin,
)

router = APIRouter()
CLOSED = []
sys.__dict__.setdefault("_kt_ui_loads", []).append(__name__)


@router.get("/hello")
async def hello(engine=Depends(current_engine), pkg=Depends(package_name)):
    return {"package": pkg, "creatures": len(engine.list_creatures())}


class Handler:
    instances = 0

    def __init__(self):
        type(self).instances += 1
        self.bound = False

    def ui_state(self):
        return {"bound": self.bound}

    def ui_call(self, action, args):
        if action == "bind":
            self.bound = True
            self.ui_emit("state", self.ui_state())
            return {"ok": True}
        if action == "bad_args":
            raise OwnerArgumentError("missing 'n'")
        if action == "boom":
            raise ValueError("handler bug")
        raise OwnerArgumentError(f"unknown action {action}")

    def close(self):
        CLOSED.append(id(self))


class UserHandler(UIOwnerMixin):
    instances = 0

    def __init__(self):
        type(self).instances += 1

    def ui_state(self, context=None):
        return {"user": context.user_id if context else None}

    def ui_call(self, action, args, context=None):
        if action == "ping_me":
            self.ui_emit("pong", {"for": context.user_id}, user_id=context.user_id)
        elif action == "broadcast":
            self.ui_emit("pong", {"for": "all"})
        return context.user_id


class WhoHandler:
    def ui_state(self, context=None):
        return {"multi_user": context.multi_user, "is_admin": context.is_admin,
                "user": context.user_id}

    def ui_call(self, action, args, context=None):
        return self.ui_state(context)


class GuardHandler:
    def ui_state(self, context=None):
        raise OwnerForbiddenError("members only")

    def ui_call(self, action, args, context=None):
        raise OwnerForbiddenError("members only")


class CounterPlugin(BasePlugin):
    name = "counter"

    def __init__(self, options=None):
        super().__init__()
        self.count = 0

    def ui_state(self):
        return {"count": self.count}

    def ui_call(self, action, args):
        self.count += 1
        return self.count
"""


def write_ui_package(root: Path, name: str, ui: dict | None = None) -> str:
    """Write package ``name`` under ``root``; return its Python module name."""
    module = f"kt_ui_{uuid.uuid4().hex[:8]}"
    pkg = root / name
    (pkg / "ui" / "sub").mkdir(parents=True)
    (pkg / "ui" / "index.js").write_text("export default (kt) => {}\n")
    (pkg / "ui" / "sub" / "a.css").write_text("body{}\n")
    (pkg / "ui" / "en.json").write_text('{"hello": "Hello {name}"}')
    (pkg / "secret.txt").write_text("do not serve")
    (pkg / f"{module}.py").write_text(HANDLER_SOURCE)
    block = {
        "entry": "ui/index.js",
        "styles": ["ui/sub/a.css"],
        "i18n": {"en": "ui/en.json"},
        "api": f"{module}:router",
        "handler": f"{module}:Handler",
        "contributes": [
            {"kind": "settings", "id": "bot", "label": "Bot"},
            {"kind": "panel", "id": "chats", "owner": "plugin:counter"},
        ],
    }
    if ui is not None:
        block = {
            **block,
            **{
                k: (v.format(module=module) if isinstance(v, str) else v)
                for k, v in ui.items()
            },
        }
    manifest = {
        "name": name,
        "version": "0.3.0",
        "plugins": [{"name": "counter", "module": module, "class": "CounterPlugin"}],
        "ui": block,
    }
    (pkg / "kohaku.yaml").write_text(yaml.safe_dump(manifest))
    invalidate_ui_cache()
    return module


@pytest.fixture
def packages_dir(tmp_path, monkeypatch) -> Path:
    root = tmp_path / "pkgs"
    root.mkdir()
    monkeypatch.setattr(locations, "PACKAGES_DIR", root)
    invalidate_ui_cache()
    yield root
    invalidate_ui_cache()


@pytest.fixture
def ui_package(packages_dir):
    """Factory: install a UI package named ``name``; returns its module name."""

    def make(name: str = "uipkg", ui: dict | None = None) -> str:
        return write_ui_package(packages_dir, name, ui)

    return make


@pytest.fixture
def reset_service():
    set_service(None)
    yield
    set_service(None)


@pytest.fixture
def l4_users(tmp_path, monkeypatch, reset_service):
    """An L4 (multi_user=required) app with logged-in users A and B.

    Yields ``(app, client_a, client_b, user_a_id, user_b_id)``.
    """
    monkeypatch.setenv("KT_AUTH_DB", str(tmp_path / "auth.db"))
    _reset_migration_state_for_tests()
    ensure_migrated()
    with connection() as conn:
        user_a = create_user(conn, "alice", "pw-a", role="admin", bcrypt_rounds=4)
        user_b = create_user(conn, "bob", "pw-b", role="user", bcrypt_rounds=4)
    from kohakuterrarium.api.app import create_app

    app = create_app()
    app.state.auth_config = AuthConfig(
        multi_user="required", registration="invite_only", bcrypt_rounds=4
    )
    with TestClient(app) as client_a, TestClient(app) as client_b:
        for client, (name, pw) in (
            (client_a, ("alice", "pw-a")),
            (client_b, ("bob", "pw-b")),
        ):
            resp = client.post(
                "/api/auth/login", json={"username": name, "password": pw}
            )
            assert resp.status_code == 200, resp.text
        yield app, client_a, client_b, user_a.id, user_b.id
    _reset_migration_state_for_tests()
