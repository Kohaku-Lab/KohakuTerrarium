"""Unit tests for :mod:`kohakuterrarium.core.config`."""

import pytest

from kohakuterrarium.core import config as cfg_mod
from kohakuterrarium.core.config import (
    _construct_agent_config,
    _find_config_file,
    _load_base_config_data,
    _load_config_file,
    _load_json,
    _load_prompt_chain,
    _load_toml,
    _load_yaml,
    _parse_input_config,
    _parse_output_config,
    _parse_output_config_item,
    _parse_subagent_config,
    _parse_tool_config,
    _parse_trigger_config,
    _render_prompt_context,
    _resolve_base_config_path,
    _resolve_inheritance,
    build_agent_config,
    load_agent_config,
)
from kohakuterrarium.core.config_types import (
    AgentConfig,
    InputConfig,
)

# ── file loaders ─────────────────────────────────────────────────


class TestFileLoaders:
    def test_load_yaml(self, tmp_path):
        p = tmp_path / "x.yaml"
        p.write_text("a: 1\nb: hello\n")
        assert _load_yaml(p) == {"a": 1, "b": "hello"}

    def test_load_yaml_empty(self, tmp_path):
        p = tmp_path / "x.yaml"
        p.write_text("")
        assert _load_yaml(p) == {}

    def test_load_json(self, tmp_path):
        p = tmp_path / "x.json"
        p.write_text('{"k": 2}')
        assert _load_json(p) == {"k": 2}

    def test_load_toml(self, tmp_path):
        p = tmp_path / "x.toml"
        p.write_text('key = "val"\n')
        assert _load_toml(p) == {"key": "val"}


class TestFindConfigFile:
    def test_yaml_found(self, tmp_path):
        (tmp_path / "config.yaml").write_text("name: x")
        assert _find_config_file(tmp_path) == tmp_path / "config.yaml"

    def test_yml_found(self, tmp_path):
        (tmp_path / "config.yml").write_text("name: y")
        assert _find_config_file(tmp_path) == tmp_path / "config.yml"

    def test_json_found(self, tmp_path):
        (tmp_path / "config.json").write_text("{}")
        assert _find_config_file(tmp_path) == tmp_path / "config.json"

    def test_toml_found(self, tmp_path):
        (tmp_path / "config.toml").write_text("name = 'z'")
        assert _find_config_file(tmp_path) == tmp_path / "config.toml"

    def test_none_when_missing(self, tmp_path):
        assert _find_config_file(tmp_path) is None

    def test_yaml_wins_priority(self, tmp_path):
        (tmp_path / "config.yaml").write_text("a: 1")
        (tmp_path / "config.json").write_text("{}")
        assert _find_config_file(tmp_path).name == "config.yaml"


class TestLoadConfigFile:
    def test_yaml(self, tmp_path):
        p = tmp_path / "config.yaml"
        p.write_text("a: 1")
        assert _load_config_file(p) == {"a": 1}

    def test_yml(self, tmp_path):
        p = tmp_path / "config.yml"
        p.write_text("a: 2")
        assert _load_config_file(p) == {"a": 2}

    def test_json(self, tmp_path):
        p = tmp_path / "config.json"
        p.write_text('{"a": 3}')
        assert _load_config_file(p) == {"a": 3}

    def test_toml(self, tmp_path):
        p = tmp_path / "config.toml"
        p.write_text('a = "v"')
        assert _load_config_file(p) == {"a": "v"}

    def test_unsupported_format(self, tmp_path):
        p = tmp_path / "config.xml"
        p.write_text("<root/>")
        with pytest.raises(ValueError, match="Unsupported"):
            _load_config_file(p)


# ── _resolve_base_config_path ────────────────────────────────────


class TestResolveBaseConfigPath:
    def test_package_reference(self, monkeypatch, tmp_path):
        target = tmp_path / "pkg-target"
        target.mkdir()

        def fake_resolve(ref):
            return target

        monkeypatch.setattr(cfg_mod, "resolve_package_path", fake_resolve)
        assert _resolve_base_config_path("@pkg/path", tmp_path) == target

    def test_package_reference_with_quotes(self, monkeypatch, tmp_path):
        target = tmp_path / "x"
        target.mkdir()
        monkeypatch.setattr(cfg_mod, "resolve_package_path", lambda r: target)
        assert _resolve_base_config_path('"@pkg/x"', tmp_path) == target

    def test_package_reference_failure_returns_none(self, monkeypatch, tmp_path):
        def fail(ref):
            raise FileNotFoundError("nope")

        monkeypatch.setattr(cfg_mod, "resolve_package_path", fail)
        assert _resolve_base_config_path("@pkg/x", tmp_path) is None

    def test_creatures_path_resolves_via_walking_up(self, tmp_path):
        # /root/creatures/base and child is /root/sub/child
        creatures_dir = tmp_path / "creatures" / "base"
        creatures_dir.mkdir(parents=True)
        child_dir = tmp_path / "sub" / "child"
        child_dir.mkdir(parents=True)
        out = _resolve_base_config_path("creatures/base", child_dir)
        assert out == creatures_dir

    def test_creatures_path_not_found(self, tmp_path):
        child_dir = tmp_path / "x" / "y"
        child_dir.mkdir(parents=True)
        # No creatures/ directory anywhere up the tree.
        assert _resolve_base_config_path("creatures/base", child_dir) is None

    def test_relative_to_child_dir(self, tmp_path):
        sibling = tmp_path / "sibling"
        sibling.mkdir()
        child = tmp_path / "child"
        child.mkdir()
        out = _resolve_base_config_path("../sibling", child)
        assert out == sibling.resolve()

    def test_relative_missing_returns_none(self, tmp_path):
        child = tmp_path / "child"
        child.mkdir()
        assert _resolve_base_config_path("../nope", child) is None


# ── _parse_*_config ──────────────────────────────────────────────


class TestParseInputConfig:
    def test_none_returns_default(self):
        c = _parse_input_config(None)
        assert isinstance(c, InputConfig)
        assert c.type == "cli"

    def test_full_fields(self):
        data = {
            "type": "custom",
            "module": "./input.py",
            "class": "MyInput",
            "prompt": "$ ",
            "custom_field": "x",
        }
        c = _parse_input_config(data)
        assert c.type == "custom"
        assert c.module == "./input.py"
        assert c.class_name == "MyInput"
        assert c.prompt == "$ "
        # Reserved keys excluded; extra keys land in options.
        assert c.options == {"custom_field": "x"}


class TestParseTriggerConfig:
    def test_minimal(self):
        c = _parse_trigger_config({"type": "timer"})
        assert c.type == "timer"
        assert c.options == {}

    def test_all_fields(self):
        c = _parse_trigger_config(
            {
                "type": "timer",
                "module": "m.py",
                "class": "C",
                "prompt": "p",
                "name": "tick",
                "interval": 5,
            }
        )
        assert c.name == "tick"
        assert c.options == {"interval": 5}


class TestParseToolConfig:
    def test_all_fields(self):
        c = _parse_tool_config(
            {
                "name": "bash",
                "type": "custom",
                "module": "x.py",
                "class": "X",
                "doc": "doc.md",
                "extra": True,
            }
        )
        assert c.name == "bash"
        assert c.options == {"extra": True}


class TestParseOutputConfig:
    def test_none_default(self):
        c = _parse_output_config(None)
        assert c.type == "stdout"

    def test_named_outputs(self):
        c = _parse_output_config(
            {
                "type": "tts",
                "named_outputs": {"discord": {"type": "discord", "channel_id": "123"}},
            }
        )
        assert "discord" in c.named_outputs
        assert c.named_outputs["discord"].type == "discord"

    def test_item_options_captured(self):
        item = _parse_output_config_item({"type": "tts", "voice": "v1"})
        assert item.options == {"voice": "v1"}


class TestParseSubagentConfig:
    def test_string_shorthand(self):
        c = _parse_subagent_config("explore")
        assert c.name == "explore"
        assert c.type == "builtin"

    def test_dict_form(self):
        c = _parse_subagent_config(
            {
                "name": "custom",
                "type": "custom",
                "module": "m.py",
                "config": "CFG",
                "tools": ["bash"],
                "can_modify": True,
                "interactive": True,
                "output_to": "external",
            }
        )
        assert c.config_name == "CFG"
        assert c.tools == ["bash"]
        assert c.can_modify is True
        # Extra fields land in options.
        assert c.options == {"output_to": "external"}


# ── _construct_agent_config ──────────────────────────────────────


class TestConstructAgentConfig:
    def test_minimal(self, tmp_path):
        c = _construct_agent_config({}, tmp_path)
        assert isinstance(c, AgentConfig)
        # Defaults applied.
        assert c.name == tmp_path.name
        assert c.version == "1.0"
        assert c.temperature == 0.7

    def test_controller_data_wins_over_top_level(self, tmp_path):
        c = _construct_agent_config(
            {
                "controller": {"temperature": 0.5, "model": "gpt-4"},
                "temperature": 0.9,
                "model": "gpt-3",
            },
            tmp_path,
        )
        # controller.* wins.
        assert c.temperature == 0.5
        assert c.model == "gpt-4"

    def test_lists_and_dicts_passed_through(self, tmp_path):
        c = _construct_agent_config(
            {
                "name": "a",
                "tools": [{"name": "bash"}],
                "subagents": ["explore"],
                "triggers": [{"type": "timer"}],
                "mcp_servers": [{"name": "fs"}],
                "plugins": [{"name": "budget"}],
                "memory": {"backend": "model2vec"},
                "framework_hint_overrides": {"x": "y"},
                "skills": ["s1"],
                "default_plugins": ["auto-compact"],
            },
            tmp_path,
        )
        assert c.tools[0].name == "bash"
        assert c.subagents[0].name == "explore"
        assert c.triggers[0].type == "timer"
        assert c.mcp_servers == [{"name": "fs"}]
        assert c.plugins == [{"name": "budget"}]
        assert c.memory == {"backend": "model2vec"}
        assert c.framework_hint_overrides == {"x": "y"}
        assert c.skills == ["s1"]
        assert c.default_plugins == ["auto-compact"]

    def test_framework_hints_legacy_alias(self, tmp_path):
        c = _construct_agent_config(
            {"framework_hints": {"a": "b"}},
            tmp_path,
        )
        assert c.framework_hint_overrides == {"a": "b"}


# ── load_agent_config / build_agent_config ───────────────────────


class TestLoadAgentConfig:
    def test_loads_from_folder(self, tmp_path):
        (tmp_path / "config.yaml").write_text("name: x\ntemperature: 0.1\n")
        c = load_agent_config(tmp_path)
        assert c.name == "x"
        assert c.temperature == 0.1
        assert c.agent_path == tmp_path

    def test_loads_from_file_directly(self, tmp_path):
        f = tmp_path / "config.yaml"
        f.write_text("name: x\n")
        c = load_agent_config(f)
        assert c.name == "x"
        # Parent used as agent folder.
        assert c.agent_path == tmp_path

    def test_missing_path_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_agent_config(tmp_path / "ghost")

    def test_no_config_in_folder(self, tmp_path):
        with pytest.raises(FileNotFoundError, match="No config file"):
            load_agent_config(tmp_path)

    def test_env_interpolation(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MY_MODEL", "gpt-7")
        (tmp_path / "config.yaml").write_text("name: a\nmodel: ${MY_MODEL}\n")
        c = load_agent_config(tmp_path)
        assert c.model == "gpt-7"


# ── inheritance ──────────────────────────────────────────────────


class TestResolveInheritance:
    def test_no_base_passthrough(self, tmp_path):
        data = {"name": "x"}
        out = _resolve_inheritance(data, tmp_path)
        assert out == data

    def test_base_unresolvable_returns_original(self, tmp_path, monkeypatch):
        # Reference to a creatures/ path that doesn't exist anywhere.
        out = _resolve_inheritance(
            {"name": "x", "base_config": "creatures/missing"}, tmp_path
        )
        assert "_base_path" not in out

    def test_base_merge_succeeds(self, tmp_path):
        # /root/creatures/base + child at /root/sub/x
        base_dir = tmp_path / "creatures" / "base"
        base_dir.mkdir(parents=True)
        (base_dir / "config.yaml").write_text("model: base-model\ntemperature: 0.2\n")
        child_dir = tmp_path / "sub"
        child_dir.mkdir()
        out = _resolve_inheritance(
            {"name": "c", "base_config": "creatures/base", "temperature": 0.9},
            child_dir,
        )
        # Child overrides base.
        assert out["temperature"] == 0.9
        # Base value preserved.
        assert out["model"] == "base-model"
        # _base_path tracked.
        assert out["_base_path"] == base_dir

    def test_base_dir_has_no_config(self, tmp_path):
        base_dir = tmp_path / "creatures" / "empty"
        base_dir.mkdir(parents=True)
        child_dir = tmp_path / "sub"
        child_dir.mkdir()
        out = _resolve_inheritance({"base_config": "creatures/empty"}, child_dir)
        # No merge — _base_path not present.
        assert "_base_path" not in out


class TestLoadBaseConfigData:
    def test_recursive_inheritance(self, tmp_path):
        # grandparent → parent → child
        gp = tmp_path / "creatures" / "gp"
        gp.mkdir(parents=True)
        (gp / "config.yaml").write_text("model: gp-model\nname: gp\n")
        parent = tmp_path / "creatures" / "parent"
        parent.mkdir(parents=True)
        (parent / "config.yaml").write_text(
            "name: parent\nbase_config: creatures/gp\ntemperature: 0.4\n"
        )
        out = _load_base_config_data(parent)
        # GP fields propagated through parent.
        assert out["model"] == "gp-model"
        # Parent's own value wins.
        assert out["temperature"] == 0.4

    def test_prompt_chain_tracked(self, tmp_path):
        base_dir = tmp_path / "creatures" / "base"
        base_dir.mkdir(parents=True)
        (base_dir / "config.yaml").write_text(
            "system_prompt_file: prompt.md\nname: base\n"
        )
        (base_dir / "prompt.md").write_text("BASE PROMPT")
        out = _load_base_config_data(base_dir)
        assert out["_prompt_chain"] == [str(base_dir / "prompt.md")]

    def test_no_config_returns_none(self, tmp_path):
        base = tmp_path / "creatures" / "empty"
        base.mkdir(parents=True)
        assert _load_base_config_data(base) is None


# ── _load_prompt_chain / _render_prompt_context ──────────────────


class TestLoadPromptChain:
    def test_chain_concatenated(self, tmp_path):
        p1 = tmp_path / "base.md"
        p1.write_text("BASE")
        p2 = tmp_path / "child.md"
        p2.write_text("CHILD")
        cfg = AgentConfig(name="a", agent_path=tmp_path, system_prompt_file="child.md")
        _load_prompt_chain(cfg, {"_prompt_chain": [str(p1)]})
        assert "BASE" in cfg.system_prompt
        assert "CHILD" in cfg.system_prompt

    def test_no_duplicates_in_chain(self, tmp_path):
        p1 = tmp_path / "shared.md"
        p1.write_text("SHARED")
        cfg = AgentConfig(name="a", agent_path=tmp_path, system_prompt_file="shared.md")
        # Same file in both chain and as child's own.
        _load_prompt_chain(cfg, {"_prompt_chain": [str(p1)]})
        # Should appear only once.
        assert cfg.system_prompt.count("SHARED") == 1

    def test_inline_prompt_appended(self, tmp_path):
        cfg = AgentConfig(name="a", agent_path=tmp_path)
        _load_prompt_chain(cfg, {"_inline_system_prompt": "I am inline"})
        assert "I am inline" in cfg.system_prompt

    def test_no_sources_keeps_default(self, tmp_path):
        cfg = AgentConfig(name="a", agent_path=tmp_path)
        _load_prompt_chain(cfg, {})
        # Default unchanged.
        assert cfg.system_prompt == "You are a helpful assistant."

    def test_falls_back_to_base_path_for_missing(self, tmp_path):
        base = tmp_path / "base"
        base.mkdir()
        (base / "prompt.md").write_text("from-base")
        child = tmp_path / "child"
        child.mkdir()
        cfg = AgentConfig(name="a", agent_path=child, system_prompt_file="prompt.md")
        _load_prompt_chain(cfg, {"_base_path": base})
        assert "from-base" in cfg.system_prompt


class TestRenderPromptContext:
    def test_no_context_files_no_op(self, tmp_path):
        cfg = AgentConfig(name="a", agent_path=tmp_path, system_prompt="static")
        _render_prompt_context(cfg)
        assert cfg.system_prompt == "static"

    def test_renders_with_context(self, tmp_path):
        ctx_file = tmp_path / "char.md"
        ctx_file.write_text("Alice")
        cfg = AgentConfig(
            name="a",
            agent_path=tmp_path,
            system_prompt="hello {{ character }}!",
            prompt_context_files={"character": "char.md"},
        )
        _render_prompt_context(cfg)
        assert cfg.system_prompt == "hello Alice!"

    def test_missing_context_file_leaves_prompt_unrendered(self, tmp_path):
        cfg = AgentConfig(
            name="a",
            agent_path=tmp_path,
            system_prompt="hello {{ character }}!",
            prompt_context_files={"character": "missing.md"},
        )
        _render_prompt_context(cfg)
        # The only context file is missing → no context vars collected →
        # the render step is skipped entirely and the prompt is left
        # verbatim (the ``{{ character }}`` placeholder is untouched).
        assert cfg.system_prompt == "hello {{ character }}!"


class TestBuildAgentConfig:
    def test_full_pipeline(self, tmp_path):
        # Sets up base + child with file-based prompt.
        base = tmp_path / "creatures" / "base"
        base.mkdir(parents=True)
        (base / "config.yaml").write_text("name: base\nsystem_prompt_file: prompt.md\n")
        (base / "prompt.md").write_text("BASE_P")
        child = tmp_path / "creatures" / "child"
        child.mkdir(parents=True)
        cfg = build_agent_config(
            {
                "name": "child",
                "base_config": "creatures/base",
                "system_prompt": "INLINE",
            },
            child,
        )
        # Final prompt is the chain + inline.
        assert "BASE_P" in cfg.system_prompt
        assert "INLINE" in cfg.system_prompt
        assert cfg.name == "child"
