"""Unit tests for :mod:`kohakuterrarium.laboratory.adapters.studio_identity`."""

from kohakuterrarium.laboratory._internal.app import AppMessage
from kohakuterrarium.laboratory.adapters import studio_identity as mod
from kohakuterrarium.laboratory.adapters.studio_identity import StudioIdentityAdapter


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
        namespace=StudioIdentityAdapter.NAMESPACE,
        type=type_,
        body=body or {},
        sender_node="w",
        request_id=None,
        in_reply_to=None,
    )


# ── construction ────────────────────────────────────────────────


class TestConstruction:
    def test_init_and_detach(self):
        node = _FakeNode()
        adapter = StudioIdentityAdapter(node)
        assert StudioIdentityAdapter.NAMESPACE in node.registered
        adapter.detach()
        assert StudioIdentityAdapter.NAMESPACE in node.unregistered


# ── get_api_key ─────────────────────────────────────────────────


class TestGetApiKey:
    async def test_known_key(self, monkeypatch):
        monkeypatch.setattr(mod, "get_existing_key", lambda p: "sk-abc")
        adapter = StudioIdentityAdapter(_FakeNode())
        out = await adapter._dispatch(_msg("get_api_key", {"provider": "openai"}))
        assert out == {"key": "sk-abc"}

    async def test_missing_provider(self, monkeypatch):
        adapter = StudioIdentityAdapter(_FakeNode())
        out = await adapter._dispatch(_msg("get_api_key", {}))
        assert out["error"]["kind"] == "invalid"

    async def test_missing_key_returns_not_found(self, monkeypatch):
        monkeypatch.setattr(mod, "get_existing_key", lambda p: "")
        adapter = StudioIdentityAdapter(_FakeNode())
        out = await adapter._dispatch(_msg("get_api_key", {"provider": "openai"}))
        assert out["error"]["kind"] == "not_found"


# ── get_profile / list_profiles ─────────────────────────────────


class TestGetProfile:
    async def test_known_profile(self, monkeypatch):
        monkeypatch.setattr(
            mod, "list_profiles_payload", lambda: [{"name": "p1", "model": "x"}]
        )
        adapter = StudioIdentityAdapter(_FakeNode())
        out = await adapter._dispatch(_msg("get_profile", {"name": "p1"}))
        assert out == {"profile": {"name": "p1", "model": "x"}}

    async def test_missing(self, monkeypatch):
        monkeypatch.setattr(mod, "list_profiles_payload", lambda: [])
        adapter = StudioIdentityAdapter(_FakeNode())
        out = await adapter._dispatch(_msg("get_profile", {"name": "p1"}))
        assert out["error"]["kind"] == "not_found"

    async def test_missing_name(self):
        adapter = StudioIdentityAdapter(_FakeNode())
        out = await adapter._dispatch(_msg("get_profile", {}))
        assert out["error"]["kind"] == "invalid"

    async def test_list_profiles(self, monkeypatch):
        monkeypatch.setattr(mod, "list_profiles_payload", lambda: [{"name": "p1"}])
        adapter = StudioIdentityAdapter(_FakeNode())
        out = await adapter._dispatch(_msg("list_profiles"))
        assert out == {"profiles": [{"name": "p1"}]}


# ── MCP servers ─────────────────────────────────────────────────


class TestMcpServers:
    async def test_get_mcp_server_known(self, monkeypatch):
        monkeypatch.setattr(mod, "load_servers", lambda: [{"name": "s1"}])
        adapter = StudioIdentityAdapter(_FakeNode())
        out = await adapter._dispatch(_msg("get_mcp_server", {"name": "s1"}))
        assert out == {"server": {"name": "s1"}}

    async def test_get_mcp_server_missing(self, monkeypatch):
        monkeypatch.setattr(mod, "load_servers", lambda: [])
        adapter = StudioIdentityAdapter(_FakeNode())
        out = await adapter._dispatch(_msg("get_mcp_server", {"name": "s1"}))
        assert out["error"]["kind"] == "not_found"

    async def test_get_mcp_server_missing_name(self):
        adapter = StudioIdentityAdapter(_FakeNode())
        out = await adapter._dispatch(_msg("get_mcp_server", {}))
        assert out["error"]["kind"] == "invalid"

    async def test_list_mcp_servers(self, monkeypatch):
        monkeypatch.setattr(mod, "load_servers", lambda: [{"n": 1}])
        adapter = StudioIdentityAdapter(_FakeNode())
        out = await adapter._dispatch(_msg("list_mcp_servers"))
        assert out == {"servers": [{"n": 1}]}


# ── unknown type ────────────────────────────────────────────────


class TestUnknown:
    async def test_unknown_type(self):
        adapter = StudioIdentityAdapter(_FakeNode())
        out = await adapter._dispatch(_msg("mystery"))
        assert out["error"]["kind"] == "unknown_type"


# ── error mapping: LookupError ──────────────────────────────────


class TestLookupErrorMapping:
    async def test_lookup_error_maps_to_not_found(self, monkeypatch):
        # A LookupError that is NOT a KeyError (KeyError has its own
        # earlier arm) is still translated to the ``not_found`` kind.
        adapter = StudioIdentityAdapter(_FakeNode())

        def _boom(body):
            raise LookupError("profile registry offline")

        monkeypatch.setattr(adapter, "_op_get_profile", _boom)
        out = await adapter._dispatch(_msg("get_profile", {"name": "p"}))
        assert out["error"]["kind"] == "not_found"
        assert "profile registry offline" in out["error"]["message"]


# ── get_codex_token ─────────────────────────────────────────────


class TestGetCodexToken:
    async def test_known(self, monkeypatch):
        from kohakuterrarium.llm.codex_auth import CodexTokens

        def _fake_load(cls, path=None):
            return CodexTokens(
                access_token="at-1",
                refresh_token="rt-1",
                expires_at=9999999999,
                id_token="id-1",
                account_id="acc-1",
            )

        monkeypatch.setattr(mod.CodexTokens, "load", classmethod(_fake_load))
        adapter = StudioIdentityAdapter(_FakeNode())
        out = await adapter._dispatch(_msg("get_codex_token"))
        assert out == {
            "tokens": {
                "access_token": "at-1",
                "refresh_token": "rt-1",
                "expires_at": 9999999999,
                "id_token": "id-1",
                "account_id": "acc-1",
            }
        }

    async def test_missing(self, monkeypatch):
        monkeypatch.setattr(
            mod.CodexTokens, "load", classmethod(lambda cls, path=None: None)
        )
        adapter = StudioIdentityAdapter(_FakeNode())
        out = await adapter._dispatch(_msg("get_codex_token"))
        assert out.get("error", {}).get("kind") == "not_found"
