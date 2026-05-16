"""Unit tests for :mod:`kohakuterrarium.laboratory.adapters.studio_catalog`."""

from kohakuterrarium.laboratory._internal.app import AppMessage
from kohakuterrarium.laboratory.adapters import studio_catalog as mod
from kohakuterrarium.laboratory.adapters.studio_catalog import StudioCatalogAdapter


class _FakeNode:
    def __init__(self):
        self.registered = {}
        self.unregistered = []

    def register_app_extension(self, ns, handler):
        self.registered[ns] = handler

    def unregister_app_extension(self, ns):
        self.unregistered.append(ns)
        self.registered.pop(ns, None)


def _msg(type_, body=None) -> AppMessage:
    return AppMessage(
        namespace=StudioCatalogAdapter.NAMESPACE,
        type=type_,
        body=body or {},
        sender_node="ctrl",
        request_id=None,
        in_reply_to=None,
    )


# ── construction / detach ───────────────────────────────────────


class TestConstruction:
    def test_init_registers(self):
        node = _FakeNode()
        StudioCatalogAdapter(node)
        assert StudioCatalogAdapter.NAMESPACE in node.registered

    def test_detach_unregisters(self):
        node = _FakeNode()
        adapter = StudioCatalogAdapter(node)
        adapter.detach()
        assert StudioCatalogAdapter.NAMESPACE in node.unregistered


# ── list type ───────────────────────────────────────────────────


class TestListType:
    async def test_list_returns_packages(self, monkeypatch):
        monkeypatch.setattr(mod, "list_installed_packages", lambda: [{"name": "p1"}])
        node = _FakeNode()
        adapter = StudioCatalogAdapter(node)
        out = await adapter._dispatch(_msg("list"))
        assert out == {"packages": [{"name": "p1"}]}


# ── unknown type ────────────────────────────────────────────────


class TestUnknownType:
    async def test_returns_unknown_error(self):
        node = _FakeNode()
        adapter = StudioCatalogAdapter(node)
        out = await adapter._dispatch(_msg("mystery"))
        assert out["error"]["kind"] == "unknown_type"


# ── install / uninstall — host vs worker ────────────────────────


class TestHostAdapterRejectsMutation:
    async def test_install_blocked_on_host(self):
        node = _FakeNode()
        adapter = StudioCatalogAdapter(node, is_host=True)
        out = await adapter._dispatch(_msg("install", {"source": "git://x"}))
        assert out["error"]["kind"] == "denied"

    async def test_uninstall_blocked_on_host(self):
        node = _FakeNode()
        adapter = StudioCatalogAdapter(node, is_host=True)
        out = await adapter._dispatch(_msg("uninstall", {"name": "p"}))
        assert out["error"]["kind"] == "denied"


class TestWorkerAdapterRunsMutation:
    async def test_install_calls_op(self, monkeypatch):
        captured = []

        def fake_install(source, *, editable, name):
            captured.append((source, editable, name))
            return "installed-name"

        monkeypatch.setattr(mod, "install_package_op", fake_install)
        node = _FakeNode()
        adapter = StudioCatalogAdapter(node, is_host=False)
        out = await adapter._dispatch(
            _msg("install", {"source": "git://x", "editable": True})
        )
        assert out == {"installed": "installed-name"}
        assert captured == [("git://x", True, None)]

    async def test_install_validates_source(self, monkeypatch):
        node = _FakeNode()
        adapter = StudioCatalogAdapter(node, is_host=False)
        out = await adapter._dispatch(_msg("install", {}))
        assert out["error"]["kind"] == "invalid"

    async def test_install_validates_name(self, monkeypatch):
        node = _FakeNode()
        adapter = StudioCatalogAdapter(node, is_host=False)
        out = await adapter._dispatch(_msg("install", {"source": "x", "name": 42}))
        assert out["error"]["kind"] == "invalid"

    async def test_uninstall_calls_op(self, monkeypatch):
        monkeypatch.setattr(mod, "uninstall_package_op", lambda n: True)
        node = _FakeNode()
        adapter = StudioCatalogAdapter(node, is_host=False)
        out = await adapter._dispatch(_msg("uninstall", {"name": "p"}))
        assert out == {"removed": True}

    async def test_uninstall_validates_name(self):
        node = _FakeNode()
        adapter = StudioCatalogAdapter(node, is_host=False)
        out = await adapter._dispatch(_msg("uninstall", {}))
        assert out["error"]["kind"] == "invalid"


# ── dispatch error mapping ──────────────────────────────────────


class TestErrorMapping:
    async def test_keyerror_to_not_found(self, monkeypatch):
        def boom():
            raise KeyError("missing")

        monkeypatch.setattr(mod, "list_installed_packages", boom)
        node = _FakeNode()
        adapter = StudioCatalogAdapter(node)
        out = await adapter._dispatch(_msg("list"))
        assert out["error"]["kind"] == "not_found"
