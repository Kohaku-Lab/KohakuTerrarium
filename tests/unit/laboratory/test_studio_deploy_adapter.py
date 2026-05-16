"""Unit tests for :mod:`kohakuterrarium.laboratory.adapters.studio_deploy`."""

import base64
import hashlib

import pytest

from kohakuterrarium.laboratory._internal.app import AppMessage
from kohakuterrarium.laboratory.adapters.studio_deploy import StudioDeployAdapter
from kohakuterrarium.laboratory.adapters.terrarium_files import TerrariumFilesAdapter


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


class _FakeNode:
    def __init__(self):
        self.registered = {}
        self.unregistered = []

    def register_app_extension(self, ns, handler):
        self.registered[ns] = handler

    def unregister_app_extension(self, ns):
        self.unregistered.append(ns)
        self.registered.pop(ns, None)


class _FakeEngine:
    pass


def _msg(type_, body=None) -> AppMessage:
    return AppMessage(
        namespace=StudioDeployAdapter.NAMESPACE,
        type=type_,
        body=body or {},
        sender_node="ctrl",
        request_id=None,
        in_reply_to=None,
    )


@pytest.fixture
def adapter(monkeypatch, tmp_path):
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    return StudioDeployAdapter(_FakeEngine(), _FakeNode())


# ── construction ────────────────────────────────────────────────


class TestConstruction:
    def test_registers_and_detaches(self, adapter):
        assert StudioDeployAdapter.NAMESPACE in adapter._node.registered
        adapter.detach()
        assert StudioDeployAdapter.NAMESPACE in adapter._node.unregistered

    def test_with_shared_files_adapter(self, tmp_path, monkeypatch):
        monkeypatch.setenv("USERPROFILE", str(tmp_path))
        monkeypatch.setenv("HOME", str(tmp_path))
        node = _FakeNode()
        engine = _FakeEngine()
        files = TerrariumFilesAdapter(engine, node)
        adapter = StudioDeployAdapter(engine, node, files_adapter=files)
        assert adapter._files is files


# ── unknown type ────────────────────────────────────────────────


class TestUnknownType:
    async def test_returns_unknown(self, adapter):
        out = await adapter._dispatch(_msg("mystery"))
        assert out["error"]["kind"] == "unknown_type"


# ── push_creature_bundle ────────────────────────────────────────


class TestPushCreatureBundle:
    async def test_happy_path(self, adapter):
        files = {"f.txt": [_sha(b"hi"), _b64(b"hi")]}
        out = await adapter._dispatch(
            _msg(
                "push_creature_bundle",
                {"name": "my-creature", "files": files},
            )
        )
        assert "target_path" in out
        assert out["deployed"] == ["f.txt"]
        assert out["conflicts"] == []

    async def test_missing_name(self, adapter):
        out = await adapter._dispatch(_msg("push_creature_bundle", {"files": {}}))
        assert out["error"]["kind"] == "invalid"

    async def test_invalid_name_with_path_separator(self, adapter):
        out = await adapter._dispatch(
            _msg(
                "push_creature_bundle",
                {"name": "../escape", "files": {}},
            )
        )
        assert out["error"]["kind"] == "invalid"

    async def test_invalid_name_starts_with_dot(self, adapter):
        out = await adapter._dispatch(
            _msg(
                "push_creature_bundle",
                {"name": ".hidden", "files": {}},
            )
        )
        assert out["error"]["kind"] == "invalid"

    async def test_invalid_name_special_chars(self, adapter):
        out = await adapter._dispatch(
            _msg(
                "push_creature_bundle",
                {"name": "my$name", "files": {}},
            )
        )
        assert out["error"]["kind"] == "invalid"

    async def test_files_not_dict(self, adapter):
        out = await adapter._dispatch(
            _msg(
                "push_creature_bundle",
                {"name": "valid-name", "files": "not a dict"},
            )
        )
        assert out["error"]["kind"] == "invalid"

    async def test_empty_files_dict_succeeds(self, adapter):
        out = await adapter._dispatch(
            _msg(
                "push_creature_bundle",
                {"name": "empty-bundle", "files": {}},
            )
        )
        assert out["deployed"] == []
        assert out["conflicts"] == []


# ── error-mapping branches ───────────────────────────────────────


class TestDispatchErrorTranslation:
    async def test_value_error_maps_to_invalid(self, adapter, monkeypatch):
        # A plain ValueError (not a ScopeError) raised inside the op is
        # still translated to the ``invalid`` error kind.
        async def _boom(body):
            raise ValueError("bad bundle shape")

        monkeypatch.setattr(adapter, "_op_push_creature_bundle", _boom)
        out = await adapter._dispatch(
            _msg("push_creature_bundle", {"name": "x", "files": {}})
        )
        assert out["error"]["kind"] == "invalid"
        assert "bad bundle shape" in out["error"]["message"]

    async def test_key_error_maps_to_not_found(self, adapter, monkeypatch):
        # A KeyError surfaces as ``not_found``.
        async def _boom(body):
            raise KeyError("missing scope")

        monkeypatch.setattr(adapter, "_op_push_creature_bundle", _boom)
        out = await adapter._dispatch(
            _msg("push_creature_bundle", {"name": "x", "files": {}})
        )
        assert out["error"]["kind"] == "not_found"


# ── partial-deploy result forwarding ─────────────────────────────


class TestPartialResultForwarding:
    async def test_partial_keys_forwarded_from_files_adapter(
        self, adapter, monkeypatch
    ):
        # When the underlying push_bundle reports a partial deploy, the
        # studio adapter MUST forward ``partial`` / ``remaining`` /
        # ``error`` — otherwise the caller sees ``conflicts: []`` and
        # wrongly assumes a clean deploy.
        async def _partial(body):
            return {
                "deployed": ["a.txt"],
                "conflicts": [],
                "partial": True,
                "remaining": ["b.txt"],
                "error": "failed to commit 'b.txt': disk full",
            }

        monkeypatch.setattr(adapter._files, "_op_push_bundle", _partial)
        out = await adapter._dispatch(
            _msg(
                "push_creature_bundle",
                {"name": "partial-creature", "files": {"a.txt": ["h", "b"]}},
            )
        )
        assert out["deployed"] == ["a.txt"]
        assert out["partial"] is True
        assert out["remaining"] == ["b.txt"]
        assert "disk full" in out["error"]
        assert "target_path" in out
