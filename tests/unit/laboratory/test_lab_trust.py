"""R1-04: Laboratory workers must not trust peer-worker control-plane traffic.

The host forwards APP envelopes between authenticated clients, and ``from_node``
is attacker-controllable on the client-to-client hop. So:

- worker control-plane adapters (``studio.settings`` drive_save/apply and
  ``terrarium.runtime`` drive_*) must reject any envelope whose
  ``sender_node`` is not the host, and must not mutate disk/runtime;
- the host must refuse to forward these host-only namespaces client-to-client.
"""

import pytest

from kohakuterrarium.laboratory._internal.app import AppMessage, build_app_envelope
from kohakuterrarium.laboratory._internal.app_acl import is_host_only_client_forward
from kohakuterrarium.laboratory._internal.protocol import HOST_NODE_ID
from kohakuterrarium.laboratory.adapters.studio_settings import StudioSettingsAdapter
from kohakuterrarium.laboratory.adapters.terrarium_runtime_drive import (
    handle_drive_request,
)
from kohakuterrarium.studio.identity import drive_settings as _ds
from kohakuterrarium.terrarium.creature_host import Creature
from kohakuterrarium.terrarium.drive.config import (
    DriveRuntimeConfig,
    default_registrations,
)
from kohakuterrarium.terrarium.drive.models import ActorRef
from kohakuterrarium.terrarium.drive.requests import CreateDriveRequest
from kohakuterrarium.terrarium.drive.wire import pack_create_request
from kohakuterrarium.terrarium.engine import Terrarium
from kohakuterrarium.terrarium.service import LocalTerrariumService
from kohakuterrarium.testing.terrarium import _FakeAgent

pytestmark = pytest.mark.timeout(30)

OPS = ActorRef("service", "ops")


class _FakeNode:
    def __init__(self, client_id="worker-1"):
        self.client_id = client_id
        self.registered = {}

    def register_app_extension(self, ns, handler):
        self.registered[ns] = handler

    def unregister_app_extension(self, ns):
        self.registered.pop(ns, None)


def _msg(namespace, type_, body, *, sender):
    return AppMessage(
        namespace=namespace,
        type=type_,
        body=body,
        sender_node=sender,
        request_id="r",
        in_reply_to=None,
    )


async def _drive_service():
    engine = Terrarium(
        drive_config=DriveRuntimeConfig(enabled=True),
        drive_registrations=default_registrations(),
    )
    await engine.__aenter__()
    root = Creature(
        creature_id="root",
        name="root",
        agent=_FakeAgent(name="root"),
        is_privileged=True,
    )
    await engine.add_creature(root)
    return LocalTerrariumService(engine), engine, root.graph_id


def _create_body(gid, *, actor="service:mallory"):
    req = CreateDriveRequest(
        kind="generic",
        title="forged",
        scope_type="graph",
        scope_id=gid,
        owner=ActorRef.parse(actor),
        owner_scope="service",
        created_by=ActorRef.parse(actor),
        spec={},
    )
    return {
        "request": pack_create_request(req),
        "graph_id": gid,
        "actor": actor,
        "is_privileged": True,
    }


# ---------------------------------------------------------------------------
# worker-side: reject non-host origin
# ---------------------------------------------------------------------------


async def test_drive_runtime_rejects_peer_sender():
    service, engine, gid = await _drive_service()
    try:
        peer = _msg(
            "terrarium.runtime", "drive_create", _create_body(gid), sender="worker-2"
        )
        resp = await handle_drive_request(service, peer)
        assert "error" in resp  # rejected, not a view

        # No drive was created — the forged control-plane write did nothing.
        views = await service.list_drives(actor=OPS, graph_id=gid, is_privileged=True)
        assert views == ()

        # The legitimate host-originated path still works.
        ok = await handle_drive_request(
            service,
            _msg(
                "terrarium.runtime",
                "drive_create",
                _create_body(gid),
                sender=HOST_NODE_ID,
            ),
        )
        assert "view" in ok
    finally:
        await engine.shutdown()


async def test_studio_settings_rejects_peer_sender(tmp_path, monkeypatch):
    monkeypatch.setenv("KT_CONFIG_DIR", str(tmp_path))
    engine = Terrarium(
        drive_config=DriveRuntimeConfig(enabled=True),
        drive_registrations=default_registrations(),
    )
    await engine.__aenter__()
    try:
        node = _FakeNode()
        StudioSettingsAdapter(engine, node)
        handler = node.registered["studio.settings"]
        settings = {
            "schema_version": 1,
            "runtime": {"enabled": True},
            "registrations": {"generic": {"enabled": True}},
        }
        peer = _msg(
            "studio.settings",
            "drive_save",
            {"settings": settings, "expected_revision": None},
            sender="worker-2",
        )
        resp = await handler(peer)
        assert "error" in resp  # rejected

        # Nothing persisted — no settings revision was written to disk.
        status = _ds.settings_status("worker-1")
        assert status["settings_revision"] is None
        assert status["runtime"]["enabled"] is False
    finally:
        await engine.shutdown()


# ---------------------------------------------------------------------------
# host-side: never forward host-only namespaces client-to-client
# ---------------------------------------------------------------------------


def test_host_acl_flags_client_to_client_control_plane():
    for ns in ("studio.settings", "terrarium.runtime"):
        env = build_app_envelope(
            from_node="worker-1",
            to_node="worker-2",
            namespace=ns,
            type="drive_save",
            body={},
        )
        assert is_host_only_client_forward(env) is True

    # A benign user-defined namespace is still forwardable client-to-client.
    ok = build_app_envelope(
        from_node="worker-1",
        to_node="worker-2",
        namespace="user.app",
        type="ping",
        body={},
    )
    assert is_host_only_client_forward(ok) is False
