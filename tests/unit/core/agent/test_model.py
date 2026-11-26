"""Unit tests for :mod:`kohakuterrarium.core.agent.model`."""

import json
import types

import httpx
import pytest
from openai import AsyncOpenAI

from kohakuterrarium.core.agent import model as am
from kohakuterrarium import Agent, Terrarium
from kohakuterrarium.core.config.types import AgentConfig
from kohakuterrarium.core.agent.history.compact import AgentCompactMixin
from kohakuterrarium.core.agent.history.selection import restore_selections
from kohakuterrarium.core.agent.model import AgentModelMixin
from kohakuterrarium.llm.profiles.backends import save_yaml_store
from kohakuterrarium.llm.openai.provider import OpenAIProvider
from kohakuterrarium.terrarium.service.service import LocalTerrariumService


@pytest.mark.real_llm_provider
@pytest.mark.parametrize("persisted", [True, False], ids=["saved", "temporary"])
async def test_go_session_header_survives_model_switches(
    tmp_path, monkeypatch, persisted
):
    monkeypatch.setenv("OPENCODE_GO_API_KEY", "local-request-capture")
    save_yaml_store(
        {"presets": {"opencode-go": {"a": {"model": "a"}, "b": {"model": "b"}}}}
    )
    requests = []

    def capture(request):
        assert (
            str(request.url)
            == "https://opencode.ai/inference/go/openai/v1/chat/completions"
        )
        model = json.loads(request.content)["model"]
        requests.append((model, request.headers["x-opencode-session"]))
        return httpx.Response(
            200,
            json={
                "id": "reply",
                "object": "chat.completion",
                "created": 0,
                "model": model,
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "ok"},
                        "finish_reason": "stop",
                    }
                ],
            },
        )

    def capturing_client(**options):
        client = httpx.AsyncClient(transport=httpx.MockTransport(capture))
        return AsyncOpenAI(**options, http_client=client)

    monkeypatch.setattr(
        "kohakuterrarium.llm.openai.provider.AsyncOpenAI", capturing_client
    )
    saved_ids = []
    async with Terrarium(session_dir=tmp_path) as engine:
        service = LocalTerrariumService(engine)
        for _ in range(2):
            creature = await engine.add_creature(
                AgentConfig(name="chat"),
                llm="opencode-go/a",
                pwd=str(tmp_path),
                session=persisted,
                io="headless",
            )
            if persisted:
                saved_ids.append(creature.agent.session_store.session_id)
            for model in (None, "b", "a"):
                if model:
                    await service.switch_model(
                        creature.creature_id, f"opencode-go/{model}"
                    )
                await creature.agent.controller.llm.chat_complete(
                    [{"role": "user", "content": "hello"}]
                )

    models, ids = zip(*requests)
    assert models == ("a", "b", "a", "a", "b", "a")
    assert ids[0] == ids[1] == ids[2]
    assert ids[3] == ids[4] == ids[5]
    assert ids[0] and ids[3] and ids[0] != ids[3]
    if persisted:
        assert [ids[0], ids[3]] == saved_ids


@pytest.mark.real_llm_provider
@pytest.mark.parametrize("fail_first_close", [False, True])
async def test_replaced_clients_stay_open_for_borrowers_and_close_on_stop(
    tmp_path, monkeypatch, fail_first_close
):
    monkeypatch.setenv("KT_CLOSE_TEST_KEY", "dummy-local-key")
    save_yaml_store(
        {
            "version": 2,
            "backends": {
                "close-test": {
                    "backend_type": "openai",
                    "base_url": "http://127.0.0.1:9/v1",
                    "api_key_env": "KT_CLOSE_TEST_KEY",
                }
            },
            "presets": {"close-test": {"next": {"model": "test-model"}}},
        }
    )
    original = OpenAIProvider(
        model="test-model", api_key="dummy-local-key", base_url="http://127.0.0.1:9/v1"
    )
    agent = await Agent.build(
        AgentConfig(
            name="switch",
            compact={"enabled": False, "compact_model": "close-test/next"},
        ),
        llm=original,
        pwd=tmp_path,
        io="headless",
    )
    providers = [original]
    close_client = original._client.close
    close_calls = 0

    async def observed_close():
        nonlocal close_calls
        close_calls += 1
        if fail_first_close and close_calls == 1:
            raise RuntimeError("client close failed")
        await close_client()

    monkeypatch.setattr(original._client, "close", observed_close)
    try:
        await agent.start()
        borrower = original.fork()
        for _ in range(2):
            providers.append(agent.compact_manager._llm)
            agent.switch_model("close-test/next")
            providers.append(agent.llm)
        providers.append(agent.compact_manager._llm)
        assert all(not p._client.is_closed() for p in providers)
        await borrower.close()
        assert not original._client.is_closed()
        if fail_first_close:
            with pytest.raises(RuntimeError, match="client close failed"):
                await agent.stop()
            assert all(p._client.is_closed() for p in providers[1:])
        await agent.stop()
        assert all(p._client.is_closed() for p in providers)
        await agent.stop()
        assert close_calls == (2 if fail_first_close else 1)
    finally:
        await agent.stop()
        for provider in providers:
            await provider.close()


class _Router:
    def __init__(self):
        self.calls: list[tuple] = []

    def notify_activity(self, kind, msg, metadata=None):
        self.calls.append((kind, msg, metadata))


def _build_agent(
    *,
    profile=None,
    new_llm=None,
    has_compact=True,
    compact_max=0,
    llm_max=10000,
    identifier="openai/gpt-4",
    llm_profile="",
    llm_model="",
):
    """Compose a duck-typed agent that satisfies AgentModelMixin's surface."""

    class _Agent(AgentModelMixin, AgentCompactMixin):
        pass

    a = _Agent()
    a.llm = types.SimpleNamespace(model="prev", _profile_max_context=llm_max)
    a.controller = types.SimpleNamespace(llm=a.llm)
    # Sub-agent manager — task + interactive sub-agents resolve their LLM
    # from its ``llm`` attribute at spawn time.
    a.subagent_manager = types.SimpleNamespace(llm=a.llm)
    if has_compact:
        a.compact_manager = types.SimpleNamespace(
            _llm=a.llm,
            config=types.SimpleNamespace(
                threshold=0.8,
                max_tokens=8000,
            ),
        )
    else:
        a.compact_manager = None
    a.output_router = _Router()
    a.config = types.SimpleNamespace(
        name="alice",
        llm_profile=llm_profile,
        model=llm_model,
        provider="",
        variation_selections={},
    )
    a._llm_selector = None
    a._llm_identifier = ""
    a.session_store = None
    a._new_llm = new_llm
    return a


# ── switch_model ─────────────────────────────────────────────────


class TestSwitchModel:
    def test_unknown_profile_raises(self, monkeypatch):
        monkeypatch.setattr(am, "resolve_controller_llm", lambda *a, **k: None)
        a = _build_agent()
        with pytest.raises(ValueError, match="not found"):
            a.switch_model("ghost")

    def test_swaps_llm_and_emits_session_info(self, monkeypatch):
        new_llm = types.SimpleNamespace(model="gpt-5", _profile_max_context=20000)
        provider_tool_syncs = []
        monkeypatch.setattr(am, "sync_provider_tools", provider_tool_syncs.append)
        monkeypatch.setattr(am, "resolve_controller_llm", lambda *a, **k: object())
        monkeypatch.setattr(am, "create_llm_from_profile_name", lambda n: new_llm)
        monkeypatch.setattr(am, "profile_to_identifier", lambda p: "openai/gpt-5")

        # Stub compact LLM construction so we don't go into bootstrap.
        captured = []

        def fake_build(self, cfg):
            captured.append(cfg)
            return new_llm  # share

        monkeypatch.setattr(AgentCompactMixin, "_build_compact_llm", fake_build)

        a = _build_agent()
        out = a.switch_model("openai/gpt-5")
        assert out == "openai/gpt-5"
        # LLM swapped.
        assert a.llm is new_llm
        assert a.controller.llm is new_llm
        assert provider_tool_syncs == [a]
        assert a._llm_identifier == "openai/gpt-5"
        assert a._llm_selector == "openai/gpt-5"
        # The swap MUST propagate to the sub-agent manager — sub-agents
        # resolve their LLM from ``subagent_manager.llm`` at spawn time,
        # so a sub-agent dispatched after a ``/model`` switch (or the
        # frontend model-selection modal, which routes here too) would
        # otherwise still run on the model the agent booted with.
        assert a.subagent_manager.llm is new_llm
        # Compact manager rebuilt + max_tokens updated.
        assert a.compact_manager._llm is new_llm
        assert a.compact_manager.config.max_tokens == 20000
        # session_info activity emitted with compact_threshold = max * threshold.
        kind, msg, meta = a.output_router.calls[0]
        assert kind == "session_info"
        assert meta["llm_name"] == "openai/gpt-5"
        assert meta["max_context"] == 20000
        assert meta["compact_threshold"] == 16000  # 20000 * 0.8

    def test_switch_registers_emergency_drop_on_new_provider(self, monkeypatch):
        # A drop after the switch must sync the controller conversation
        # like the boot provider does — the new provider needs the same
        # callback registration.
        registered = []
        new_llm = types.SimpleNamespace(
            model="gpt-5",
            _profile_max_context=0,
            on_emergency_drop=lambda cb: registered.append(cb),
        )
        monkeypatch.setattr(am, "resolve_controller_llm", lambda *a, **k: object())
        monkeypatch.setattr(am, "create_llm_from_profile_name", lambda n: new_llm)
        monkeypatch.setattr(am, "profile_to_identifier", lambda p: "openai/gpt-5")
        monkeypatch.setattr(
            AgentCompactMixin, "_build_compact_llm", lambda self, cfg: new_llm
        )

        a = _build_agent()
        a._on_provider_emergency_drop = lambda msgs: None
        a.switch_model("openai/gpt-5")
        assert registered == [a._on_provider_emergency_drop]

    def test_compact_manager_optional(self, monkeypatch):
        new_llm = types.SimpleNamespace(model="x", _profile_max_context=0)
        monkeypatch.setattr(am, "resolve_controller_llm", lambda *a, **k: object())
        monkeypatch.setattr(am, "create_llm_from_profile_name", lambda n: new_llm)
        monkeypatch.setattr(am, "profile_to_identifier", lambda p: "x/y")
        a = _build_agent(has_compact=False)
        out = a.switch_model("x/y")
        assert out == "x/y"
        # No notify_activity crash even without a compact manager.
        assert a.output_router.calls

    def test_compact_uses_distinct_llm(self, monkeypatch):
        new_llm = types.SimpleNamespace(model="m", _profile_max_context=4000)
        distinct_compact = types.SimpleNamespace(
            model="compact-m", _profile_max_context=8000
        )
        monkeypatch.setattr(am, "resolve_controller_llm", lambda *a, **k: object())
        monkeypatch.setattr(am, "create_llm_from_profile_name", lambda n: new_llm)
        monkeypatch.setattr(am, "profile_to_identifier", lambda p: "id")
        monkeypatch.setattr(
            AgentCompactMixin,
            "_build_compact_llm",
            lambda self, cfg: distinct_compact,
        )
        a = _build_agent()
        a.switch_model("id")
        # max_tokens picked from the distinct compact provider's profile.
        assert a.compact_manager.config.max_tokens == 8000


# ── llm_identifier ───────────────────────────────────────────────


class TestLLMIdentifier:
    def test_cached_returned_directly(self):
        a = _build_agent()
        a._llm_identifier = "cached/id"
        assert a.llm_identifier() == "cached/id"

    def test_resolved_via_profile_when_empty(self, monkeypatch):
        monkeypatch.setattr(am, "resolve_controller_llm", lambda *a, **k: object())
        monkeypatch.setattr(am, "profile_to_identifier", lambda p: "resolved/id")
        a = _build_agent(llm_profile="x")
        assert a.llm_identifier() == "resolved/id"
        # Cached now.
        assert a._llm_identifier == "resolved/id"

    def test_falls_back_to_llm_model_when_unresolvable(self, monkeypatch):
        monkeypatch.setattr(am, "resolve_controller_llm", lambda *a, **k: None)
        a = _build_agent()
        a.llm.model = "raw-name"
        assert a.llm_identifier() == "raw-name"

    def test_uses_llm_when_present(self, monkeypatch):
        captured = {}

        def fake_resolve(data, **k):
            captured["data"] = data
            return None

        monkeypatch.setattr(am, "resolve_controller_llm", fake_resolve)
        a = _build_agent()
        a._llm_selector = "override/profile"
        a.llm.model = "fallback"
        a.llm_identifier()
        assert captured["data"]["llm"] == "override/profile"


class TestContextSelectionRoundTrip:
    """``@context=<n>`` survives a switch, its persisted selector and a restore."""

    @staticmethod
    def _real_profile_llm(monkeypatch):
        def build(selector):
            profile = am.resolve_controller_llm({}, llm=selector)
            return types.SimpleNamespace(
                model=profile.model,
                _profile_max_context=profile.max_context,
                _profile_context_window=profile.context_window,
            )

        monkeypatch.setattr(am, "create_llm_from_profile_name", build)
        monkeypatch.setattr(am, "sync_provider_tools", lambda agent: None)
        monkeypatch.setattr(
            AgentCompactMixin, "_build_compact_llm", lambda self, cfg: self.llm
        )

    @pytest.mark.parametrize(
        "selector,identifier,tokens",
        [
            (
                "codex/gpt-6.1-sol@reasoning=high,context=500000",
                "codex/gpt-6.1-sol@context=500k,reasoning=high",
                500_000,
            ),
            # Saved selections from the removed context groups.
            (
                "codex/gpt-6-astra@context=272k",
                "codex/gpt-6-astra@context=272k",
                272_000,
            ),
            ("codex/gpt-5.6-sol@1m", "codex/gpt-5.6-sol@context=1m", 1_000_000),
        ],
    )
    def test_switch_persist_and_restore_keep_the_number(
        self, monkeypatch, selector, identifier, tokens
    ):
        self._real_profile_llm(monkeypatch)
        store = types.SimpleNamespace(state={}, meta={})
        a = _build_agent()
        a.session_store = store
        assert a.switch_model(selector) == identifier
        assert a.llm._profile_max_context == tokens
        assert a.compact_manager.config.max_tokens == tokens
        _kind, _msg, meta = a.output_router.calls[-1]
        assert meta["llm_name"] == identifier
        assert meta["max_context"] == tokens
        assert meta["context_window"] == 1_000_000
        assert store.state["alice:model_selection"] == identifier

        resumed = _build_agent()
        restore_selections(resumed, store)
        assert resumed.llm_identifier() == identifier
        assert resumed.llm._profile_max_context == tokens

    def test_config_variation_selections_resolve_the_identifier(self):
        a = _build_agent(llm_profile="codex/gpt-6.1-sol")
        a.config.variation_selections = {"context": "400000"}
        assert a.llm_identifier() == "codex/gpt-6.1-sol@context=400k"
