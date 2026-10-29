"""Unit tests for :mod:`kohakuterrarium.testing.llm_seam` and the suite guard."""

import ast
import importlib
import socket
from pathlib import Path

import pytest

from kohakuterrarium.core.agent import Agent
from kohakuterrarium.core.compact import CompactConfig
from kohakuterrarium.core.config_types import AgentConfig
from kohakuterrarium.llm import codex_auth
from kohakuterrarium.llm.base import BaseLLMProvider
from kohakuterrarium.llm.openai import OpenAIProvider
from kohakuterrarium.testing.llm import ScriptedLLM
from kohakuterrarium.testing.llm_seam import (
    LLM_FACTORY_TARGETS,
    RealProviderBlocked,
    install_llm_seam,
    real_llm_providers,
)

_SRC = Path(__file__).resolve().parents[2] / "src" / "kohakuterrarium"
_FACTORY_NAMES = {
    "create_llm_provider",
    "create_llm_from_profile_name",
    "_create_from_profile",
}


def _scripted_agent(profile: str) -> Agent:
    config = AgentConfig(name="seamed", llm_profile=profile, system_prompt="S")
    return Agent(config, llm=ScriptedLLM(["main reply"]))


class TestLeakRepro:
    def test_compaction_model_of_a_scripted_agent_is_not_a_real_provider(self):
        agent = _scripted_agent("codex/gpt-5.4")
        compact_llm = agent._build_compact_llm(CompactConfig())
        assert not isinstance(compact_llm, BaseLLMProvider)

    def test_model_switch_of_a_scripted_agent_is_not_a_real_provider(self):
        agent = _scripted_agent("codex/gpt-5.4")
        agent.switch_model("codex/gpt-5.4")
        assert not isinstance(agent.llm, BaseLLMProvider)

    def test_codex_cli_token_path_follows_home(self, tmp_path, monkeypatch):
        monkeypatch.delenv("CODEX_HOME", raising=False)
        for name in ("HOME", "USERPROFILE"):
            monkeypatch.setenv(name, str(tmp_path))
        assert codex_auth.codex_cli_token_path() == tmp_path / ".codex" / "auth.json"
        monkeypatch.setenv("CODEX_HOME", str(tmp_path / "ch"))
        assert codex_auth.codex_cli_token_path() == tmp_path / "ch" / "auth.json"


class TestSeam:
    def test_every_target_builds_from_the_factory(self, monkeypatch):
        built = ScriptedLLM(["x"])
        seam = install_llm_seam(monkeypatch, lambda: built)
        for module_name, attr in LLM_FACTORY_TARGETS:
            factory = getattr(importlib.import_module(module_name), attr)
            assert factory("anything") is built
        assert len(seam.built) == len(LLM_FACTORY_TARGETS)

    def test_real_provider_construction_fails_closed(self):
        with pytest.raises(RealProviderBlocked, match="real_llm_provider"):
            OpenAIProvider(api_key="sk-test", model="m")

    @pytest.mark.real_llm_provider
    def test_opt_out_allows_real_provider_classes(self):
        assert isinstance(OpenAIProvider(api_key="sk-test", model="m"), BaseLLMProvider)

    def test_real_block_is_scoped(self):
        agent = _scripted_agent("codex/gpt-5.4")
        with real_llm_providers():
            assert isinstance(
                OpenAIProvider(api_key="sk-test", model="m"), BaseLLMProvider
            )
            assert isinstance(
                agent._build_compact_llm(CompactConfig()), BaseLLMProvider
            )
        with pytest.raises(RealProviderBlocked):
            OpenAIProvider(api_key="sk-test", model="m")
        assert not isinstance(
            agent._build_compact_llm(CompactConfig()), BaseLLMProvider
        )

    def test_scripted_llm_is_unaffected(self):
        assert ScriptedLLM(["ok"]) is not None

    def test_target_list_covers_every_factory_import_site(self):
        found = set()
        for path in _SRC.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            module = ".".join(
                ("kohakuterrarium", *path.relative_to(_SRC).with_suffix("").parts)
            ).removesuffix(".__init__")
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module == (
                    "kohakuterrarium.bootstrap.llm"
                ):
                    for alias in node.names:
                        if alias.name in _FACTORY_NAMES:
                            found.add((module, alias.asname or alias.name))
        missing = found - set(LLM_FACTORY_TARGETS)
        assert not missing, f"unpatched provider factory imports: {sorted(missing)}"


class TestNetworkGuard:
    def test_outbound_connect_is_blocked(self):
        with pytest.raises(OSError, match="network is blocked"):
            socket.create_connection(("203.0.113.1", 443), timeout=1)

    def test_loopback_is_allowed(self):
        server = socket.socket()
        server.bind(("127.0.0.1", 0))
        server.listen()
        try:
            client = socket.create_connection(server.getsockname(), timeout=1)
            client.close()
        finally:
            server.close()

    @pytest.mark.allow_network
    def test_marker_lifts_the_block(self):
        sock = socket.socket()
        sock.settimeout(0.01)
        try:
            sock.connect_ex(("203.0.113.1", 9))
        finally:
            sock.close()
