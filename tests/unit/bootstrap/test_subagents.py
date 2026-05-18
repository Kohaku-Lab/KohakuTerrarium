"""Unit tests for :mod:`kohakuterrarium.bootstrap.subagents`."""

import textwrap


from kohakuterrarium.bootstrap.subagents import (
    create_subagent_config,
    init_subagents,
)
from kohakuterrarium.core.config_types import (
    AgentConfig,
    SubAgentConfigItem,
)
from kohakuterrarium.core.loader import ModuleLoader
from kohakuterrarium.core.registry import Registry
from kohakuterrarium.modules.subagent import SubAgentManager
from kohakuterrarium.modules.subagent.config import SubAgentConfig

# ── create_subagent_config: builtin ─────────────────────────────


class TestCreateSubAgentConfigBuiltin:
    def test_unknown_builtin_returns_none(self):
        item = SubAgentConfigItem(name="no_such_subagent", type="builtin")
        assert create_subagent_config(item, loader=None) is None

    def test_known_builtin_loads(self):
        # ``explore`` is a real builtin sub-agent — it MUST resolve.
        item = SubAgentConfigItem(name="explore", type="builtin")
        cfg = create_subagent_config(item, loader=None)
        assert isinstance(cfg, SubAgentConfig)
        assert cfg.name == "explore"

    def test_overlays_options(self):
        item = SubAgentConfigItem(
            name="explore",
            type="builtin",
            options={
                "extra_prompt": "extra text",
                "extra_prompt_file": "extra.md",
                "default_plugins": ["auto-compact"],
                "plugins": [{"name": "x"}],
                "compact": {"max_tokens": 1000},
                "model": "gpt-4",
                "notify_controller_on_background_complete": False,
            },
        )
        cfg = create_subagent_config(item, loader=None)
        # ``explore`` is a real builtin — overlay MUST apply to the loaded config.
        assert cfg is not None
        assert cfg.extra_prompt == "extra text"
        assert cfg.extra_prompt_file == "extra.md"
        assert cfg.default_plugins == ["auto-compact"]
        assert cfg.notify_controller_on_background_complete is False
        assert cfg.model == "gpt-4"


# ── create_subagent_config: custom with module + config_name ────


class TestCreateSubAgentConfigCustomModule:
    def test_no_loader_returns_none(self):
        item = SubAgentConfigItem(
            name="custom_sa",
            type="custom",
            module="some/module.py",
            config_name="MY_CFG",
        )
        assert create_subagent_config(item, loader=None) is None

    def test_load_failure_returns_none(self, tmp_path):
        loader = ModuleLoader(agent_path=tmp_path)
        item = SubAgentConfigItem(
            name="custom_sa",
            type="custom",
            module="missing.py",
            config_name="X",
        )
        assert create_subagent_config(item, loader=loader) is None

    def test_load_success(self, tmp_path):
        custom = tmp_path / "custom"
        custom.mkdir()
        (custom / "sa.py").write_text(textwrap.dedent("""
                from kohakuterrarium.modules.subagent.config import SubAgentConfig

                MY_CFG = SubAgentConfig(
                    name="my_sa",
                    description="custom sub-agent",
                    tools=["bash"],
                )
                """))
        loader = ModuleLoader(agent_path=tmp_path)
        item = SubAgentConfigItem(
            name="my_sa",
            type="custom",
            module="custom/sa.py",
            config_name="MY_CFG",
        )
        cfg = create_subagent_config(item, loader=loader)
        assert cfg is not None
        assert cfg.name == "my_sa"


# ── create_subagent_config: inline custom config from options ───


class TestCreateSubAgentConfigInline:
    def test_inline_options_built_into_config(self):
        item = SubAgentConfigItem(
            name="inline_sa",
            type="custom",
            description="inline description",
            tools=["read", "write"],
            options={
                "system_prompt": "You are inline.",
                "max_turns": 5,
            },
        )
        cfg = create_subagent_config(item, loader=None)
        assert cfg is not None
        assert cfg.name == "inline_sa"
        assert cfg.system_prompt == "You are inline."
        assert cfg.max_turns == 5
        assert cfg.tools == ["read", "write"]


# ── create_subagent_config: unknown type ────────────────────────


class TestUnknownType:
    def test_unknown_type_returns_none(self):
        item = SubAgentConfigItem(name="x", type="totally_unknown")
        assert create_subagent_config(item, loader=None) is None


# ── init_subagents ──────────────────────────────────────────────


class TestInitSubAgents:
    def test_registers_into_manager_and_registry(self, tmp_path):
        from kohakuterrarium.core.executor import Executor

        cfg = AgentConfig(
            name="a",
            subagents=[
                SubAgentConfigItem(
                    name="inline_sa",
                    type="custom",
                    description="d",
                    options={"system_prompt": "x"},
                ),
            ],
        )
        ex = Executor()
        manager = SubAgentManager(
            parent_registry=Registry(),
            llm=None,
            agent_path=tmp_path,
            job_store=ex.job_store,
        )
        registry = Registry()
        init_subagents(cfg, manager, registry, loader=None)
        assert "inline_sa" in manager.list_subagents()
        assert "inline_sa" in registry.list_subagents()

    def test_skips_failed_entries(self, tmp_path):
        from kohakuterrarium.core.executor import Executor

        cfg = AgentConfig(
            name="a",
            subagents=[
                SubAgentConfigItem(name="ghost", type="builtin"),
                SubAgentConfigItem(
                    name="inline_sa",
                    type="custom",
                    options={"system_prompt": "ok"},
                ),
            ],
        )
        ex = Executor()
        manager = SubAgentManager(
            parent_registry=Registry(),
            llm=None,
            agent_path=tmp_path,
            job_store=ex.job_store,
        )
        registry = Registry()
        init_subagents(cfg, manager, registry, loader=None)
        # ghost skipped; inline_sa registered.
        assert "ghost" not in manager.list_subagents()
        assert "inline_sa" in manager.list_subagents()
