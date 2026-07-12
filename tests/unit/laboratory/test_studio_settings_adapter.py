"""Unit tests for :mod:`kohakuterrarium.laboratory.adapters.studio_settings`.

Exercises the worker-side ``studio.settings`` adapter through the SAME node
client the host uses (:class:`_RemoteNodeDriveSettings`), so the full loop is
covered: node-client request -> adapter -> ``drive_settings`` module on the
(tmp) config home -> typed-error round-trip. The conftest isolates
``KT_CONFIG_DIR`` per test, so each save lands in its own file.
"""

import pytest

from kohakuterrarium.laboratory._internal.app import AppMessage
from kohakuterrarium.laboratory.adapters.studio_settings import StudioSettingsAdapter
from kohakuterrarium.studio.nodes import _RemoteNodeDriveSettings
from kohakuterrarium.terrarium.drive.config import (
    DriveRuntimeConfig,
    default_registrations,
)
from kohakuterrarium.terrarium.drive.errors import (
    DriveSettingsConflictError,
    DriveValidationError,
)
from kohakuterrarium.terrarium.engine import Terrarium

pytestmark = pytest.mark.timeout(30)


class _FakeNode:
    def __init__(self, client_id="worker-1"):
        self.client_id = client_id
        self.registered = {}
        self.unregistered = []

    def register_app_extension(self, ns, handler):
        self.registered[ns] = handler

    def unregister_app_extension(self, ns):
        self.unregistered.append(ns)
        self.registered.pop(ns, None)


class _FakeSender:
    """Routes ``request(...)`` straight into the adapter's dispatcher."""

    def __init__(self, node: _FakeNode) -> None:
        self._node = node

    async def request(self, *, to_node, namespace, type, body, timeout):
        handler = self._node.registered[namespace]
        msg = AppMessage(
            namespace=namespace,
            type=type,
            body=body,
            sender_node="_host",
            request_id="r",
            in_reply_to=None,
        )
        return await handler(msg)


async def _wired():
    engine = Terrarium(
        drive_config=DriveRuntimeConfig(enabled=True),
        drive_registrations=default_registrations(),
    )
    await engine.__aenter__()
    node = _FakeNode()
    StudioSettingsAdapter(engine, node)
    client = _RemoteNodeDriveSettings(_FakeSender(node), node.client_id)
    return engine, node, client


def _enabled_settings() -> dict:
    return {
        "schema_version": 1,
        "runtime": {"enabled": True},
        "registrations": {"generic": {"enabled": True, "options": {}}},
    }


class TestConstruction:
    def test_registers_and_detaches(self):
        node = _FakeNode()
        adapter = StudioSettingsAdapter(object(), node)
        assert StudioSettingsAdapter.NAMESPACE in node.registered
        adapter.detach()
        assert StudioSettingsAdapter.NAMESPACE in node.unregistered


class TestNodeSettingsFlow:
    async def test_status_defaults_disabled(self):
        engine, node, client = await _wired()
        try:
            status = await client.status()
            assert status["runtime"]["enabled"] is False
            assert status["node"] == "worker-1"
        finally:
            await engine.shutdown()

    async def test_save_then_get_round_trip(self):
        engine, node, client = await _wired()
        try:
            saved = await client.save(_enabled_settings())
            assert saved["ok"] is True and saved["revision"]
            got = await client.get()
            assert got["settings"]["runtime"]["enabled"] is True
            assert got["revision"] == saved["revision"]
            status = await client.status()
            names = {r["name"] for r in status["registrations"] if r.get("enabled")}
            assert "generic" in names
        finally:
            await engine.shutdown()

    async def test_stale_save_raises_conflict_over_wire(self):
        engine, node, client = await _wired()
        try:
            await client.save(_enabled_settings())
            with pytest.raises(DriveSettingsConflictError):
                await client.save(_enabled_settings(), expected_revision="deadbeef")
        finally:
            await engine.shutdown()

    async def test_validate_bad_settings_raises(self):
        engine, node, client = await _wired()
        try:
            with pytest.raises(DriveValidationError):
                await client.validate({"runtime": {"enabled": "yes-please"}})
        finally:
            await engine.shutdown()

    async def test_apply_returns_typed_result(self):
        engine, node, client = await _wired()
        try:
            await client.save(_enabled_settings())
            result = await client.apply()
            assert result["result"] in {
                "applied_live",
                "restart_required",
                "rejected",
            }
        finally:
            await engine.shutdown()

    async def test_runtime_status_reflects_live_engine(self):
        engine, node, client = await _wired()
        try:
            status = await client.runtime_status()
            assert status["enabled"] is True
        finally:
            await engine.shutdown()
