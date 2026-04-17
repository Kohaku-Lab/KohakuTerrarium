"""
Creature Hierarchy and Agent Inheritance Tests

Tests for:
- base_config loading and merging
- Child overrides parent values
- System prompt comes from child if specified, parent otherwise
- Tools list from child if specified, parent otherwise
- Recursive inheritance (grandparent -> parent -> child)

These tests run offline without API keys.
"""

from pathlib import Path

import pytest
import yaml

from kohakuterrarium.core.config import (
    _merge_configs,
    _resolve_base_config_path,
    load_agent_config,
)


@pytest.fixture
def tmp_creatures(tmp_path):
    """Create a temporary creature hierarchy for testing."""
    # Base creature: general
    general = tmp_path / "creatures" / "general"
    general.mkdir(parents=True)
    (general / "prompts").mkdir()

    (general / "config.yaml").write_text(
        yaml.dump(
            {
                "name": "general",
                "version": "1.0",
                "controller": {
                    "model": "base-model",
                    "reasoning_effort": "medium",
                    "tool_format": "native",
                },
                "system_prompt_file": "prompts/system.md",
                "skill_mode": "dynamic",
                "tools": [
                    {"name": "bash", "type": "builtin"},
                    {"name": "read", "type": "builtin"},
                    {"name": "write", "type": "builtin"},
                    {"name": "think", "type": "builtin"},
                ],
                "subagents": [
                    {"name": "explore", "type": "builtin"},
                    {"name": "plan", "type": "builtin"},
                ],
            }
        )
    )
    (general / "prompts" / "system.md").write_text(
        "# General Agent\n\nYou are a general-purpose assistant.\n"
    )

    # SWE creature inheriting from general
    swe = tmp_path / "creatures" / "swe"
    swe.mkdir(parents=True)
    (swe / "prompts").mkdir()

    (swe / "config.yaml").write_text(
        yaml.dump(
            {
                "name": "swe",
                "version": "1.0",
                "base_config": "../general",
                "controller": {"reasoning_effort": "high"},
                "system_prompt_file": "prompts/system.md",
            }
        )
    )
    (swe / "prompts" / "system.md").write_text(
        "# SWE Agent\n\nYou are a software engineering agent.\n"
    )

    # Agent inheriting from swe (two-level inheritance via creatures/ path)
    agent = tmp_path / "agents" / "my_agent"
    agent.mkdir(parents=True)

    (agent / "config.yaml").write_text(
        yaml.dump(
            {
                "name": "my_agent",
                "version": "2.0",
                "base_config": "creatures/swe",
                "controller": {
                    "model": "agent-model",
                    "api_key_env": "MY_KEY",
                    "base_url": "https://example.com/v1",
                },
                "input": {"type": "cli", "prompt": "> "},
                "output": {"type": "stdout", "controller_direct": True},
            }
        )
    )

    return tmp_path


class TestMergeConfigs:
    """Tests for the _merge_configs function."""

    def test_child_overrides_scalars(self):
        base = {"name": "base", "version": "1.0", "skill_mode": "dynamic"}
        child = {"name": "child", "version": "2.0"}
        result = _merge_configs(base, child)
        assert result["name"] == "child"
        assert result["version"] == "2.0"
        assert result["skill_mode"] == "dynamic"  # inherited

    def test_child_extends_tool_lists(self):
        base = {
            "tools": [{"name": "bash"}, {"name": "read"}],
            "subagents": [{"name": "explore"}],
        }
        child = {"tools": [{"name": "grep"}]}
        result = _merge_configs(base, child)
        # Child tools extend base tools (not replace)
        assert len(result["tools"]) == 3
        tool_names = [t["name"] for t in result["tools"]]
        assert tool_names == ["bash", "read", "grep"]
        assert result["subagents"] == [{"name": "explore"}]  # inherited

    def test_child_wins_on_identity_collision(self):
        """Child entry with same name replaces base entry in place."""
        base = {
            "tools": [
                {"name": "bash", "type": "builtin"},
                {"name": "read", "type": "builtin"},
            ],
        }
        child = {
            "tools": [
                {"name": "bash", "type": "custom", "module": "./tools/bash.py"},
                {"name": "grep", "type": "builtin"},
            ]
        }
        result = _merge_configs(base, child)
        # Size = unique names across both (bash, read, grep)
        assert len(result["tools"]) == 3
        tool_names = [t["name"] for t in result["tools"]]
        # bash stays at its original position; grep appended
        assert tool_names == ["bash", "read", "grep"]
        # bash was overridden by the child definition
        bash = result["tools"][0]
        assert bash["type"] == "custom"
        assert bash["module"] == "./tools/bash.py"

    def test_dicts_are_shallow_merged(self):
        base = {"controller": {"model": "base-model", "temperature": 0.7}}
        child = {"controller": {"model": "child-model"}}
        result = _merge_configs(base, child)
        assert result["controller"]["model"] == "child-model"
        assert result["controller"]["temperature"] == 0.7

    def test_none_values_not_applied(self):
        base = {"name": "base", "model": "base-model"}
        child = {"name": "child", "model": None}
        result = _merge_configs(base, child)
        assert result["model"] == "base-model"

    def test_base_config_key_excluded(self):
        base = {"name": "base"}
        child = {"name": "child", "base_config": "../base"}
        result = _merge_configs(base, child)
        assert "base_config" not in result

    def test_no_inherit_replaces_tools(self):
        base = {
            "tools": [{"name": "bash"}, {"name": "read"}, {"name": "write"}],
            "subagents": [{"name": "explore"}, {"name": "plan"}],
        }
        child = {
            "no_inherit": ["tools"],
            "tools": [{"name": "think"}],
        }
        result = _merge_configs(base, child)
        # tools replaced (not extended)
        assert [t["name"] for t in result["tools"]] == ["think"]
        # subagents still inherited (not in no_inherit)
        assert [s["name"] for s in result["subagents"]] == ["explore", "plan"]
        # no_inherit itself not in result
        assert "no_inherit" not in result

    def test_no_inherit_replaces_both(self):
        base = {
            "tools": [{"name": "bash"}, {"name": "read"}],
            "subagents": [{"name": "explore"}],
        }
        child = {
            "no_inherit": ["tools", "subagents"],
            "tools": [{"name": "think"}],
            "subagents": [],
        }
        result = _merge_configs(base, child)
        assert [t["name"] for t in result["tools"]] == ["think"]
        assert result["subagents"] == []

    def test_no_inherit_empty_keeps_extend(self):
        base = {"tools": [{"name": "bash"}]}
        child = {"no_inherit": [], "tools": [{"name": "think"}]}
        result = _merge_configs(base, child)
        # Empty no_inherit = normal extend behavior
        assert [t["name"] for t in result["tools"]] == ["bash", "think"]

    def test_plugins_merge_child_wins(self):
        """Plugins follow the same identity-union rule as tools."""
        base = {
            "plugins": [
                {"name": "logger", "class": "LogPlugin"},
                {"name": "guard", "class": "BaseGuard"},
            ]
        }
        child = {
            "plugins": [
                {"name": "guard", "class": "StrictGuard"},
                {"name": "rate_limit", "class": "RateLimit"},
            ]
        }
        result = _merge_configs(base, child)
        names = [p["name"] for p in result["plugins"]]
        assert names == ["logger", "guard", "rate_limit"]
        guard = next(p for p in result["plugins"] if p["name"] == "guard")
        assert guard["class"] == "StrictGuard"

    def test_mcp_servers_merge_child_wins(self):
        """mcp_servers use the same identity-union rule."""
        base = {
            "mcp_servers": [
                {"name": "sqlite", "transport": "stdio", "command": "old"},
            ]
        }
        child = {
            "mcp_servers": [
                {"name": "sqlite", "transport": "stdio", "command": "new"},
                {"name": "web", "transport": "http", "url": "https://x"},
            ]
        }
        result = _merge_configs(base, child)
        names = [s["name"] for s in result["mcp_servers"]]
        assert names == ["sqlite", "web"]
        sqlite = next(s for s in result["mcp_servers"] if s["name"] == "sqlite")
        assert sqlite["command"] == "new"

    def test_triggers_with_name_identity(self):
        """Triggers with matching name override in place; unnamed concat."""
        base = {
            "triggers": [
                {"name": "heartbeat", "type": "timer", "options": {"interval": 60}},
                {"type": "context"},
            ]
        }
        child = {
            "triggers": [
                {"name": "heartbeat", "type": "timer", "options": {"interval": 10}},
                {"type": "channel", "options": {"channel": "tasks"}},
            ]
        }
        result = _merge_configs(base, child)
        # 1 base-named + 1 base-unnamed + 0 overrides (heartbeat replaces in place)
        # + 1 new unnamed child entry = 3 total
        assert len(result["triggers"]) == 3
        # heartbeat replaced in place at position 0
        heartbeat = result["triggers"][0]
        assert heartbeat["name"] == "heartbeat"
        assert heartbeat["options"]["interval"] == 10
        # unnamed child entry appended at the end
        assert result["triggers"][-1]["type"] == "channel"

    def test_no_inherit_universal_for_controller(self):
        """no_inherit works on dict fields (not just identity lists)."""
        base = {"controller": {"model": "base-model", "temperature": 0.7}}
        child = {
            "no_inherit": ["controller"],
            "controller": {"model": "child-model"},
        }
        result = _merge_configs(base, child)
        # Inherited controller dropped; child's controller is the only one
        assert result["controller"] == {"model": "child-model"}

    def test_no_inherit_universal_for_plugins(self):
        """no_inherit drops inherited plugins list."""
        base = {
            "plugins": [
                {"name": "p1"},
                {"name": "p2"},
            ]
        }
        child = {"no_inherit": ["plugins"], "plugins": [{"name": "only"}]}
        result = _merge_configs(base, child)
        assert [p["name"] for p in result["plugins"]] == ["only"]

    def test_prompt_mode_replace_drops_chain(self):
        """prompt_mode: replace wipes inherited _prompt_chain and inline."""
        base = {
            "_prompt_chain": ["/path/to/base.md", "/path/to/parent.md"],
            "_inline_system_prompt": "old inline",
            "system_prompt_file": "prompts/old.md",
        }
        child = {
            "prompt_mode": "replace",
            "system_prompt_file": "prompts/new.md",
            "system_prompt": "new inline",
        }
        result = _merge_configs(base, child)
        assert "_prompt_chain" not in result
        assert result["_inline_system_prompt"] == "new inline"
        assert result["system_prompt_file"] == "prompts/new.md"


class TestResolveBaseConfigPath:
    """Tests for base_config path resolution."""

    def test_relative_path(self, tmp_path):
        child = tmp_path / "creatures" / "swe"
        child.mkdir(parents=True)
        base = tmp_path / "creatures" / "general"
        base.mkdir(parents=True)

        result = _resolve_base_config_path("../general", child)
        assert result is not None
        assert result.name == "general"

    def test_creatures_prefix_path(self, tmp_path):
        creatures = tmp_path / "creatures" / "swe"
        creatures.mkdir(parents=True)
        general = tmp_path / "creatures" / "general"
        general.mkdir(parents=True)

        agent = tmp_path / "agents" / "my_agent"
        agent.mkdir(parents=True)

        result = _resolve_base_config_path("creatures/swe", agent)
        assert result is not None
        assert result.name == "swe"

    def test_nonexistent_path_returns_none(self, tmp_path):
        child = tmp_path / "creatures" / "swe"
        child.mkdir(parents=True)

        result = _resolve_base_config_path("../nonexistent", child)
        assert result is None


class TestLoadAgentConfigInheritance:
    """Tests for config inheritance via load_agent_config."""

    def test_basic_inheritance(self, tmp_creatures):
        """Child inherits tools and subagents from parent."""
        swe = tmp_creatures / "creatures" / "swe"
        config = load_agent_config(swe)

        assert config.name == "swe"
        # Inherits tools from general (swe doesn't define tools)
        assert len(config.tools) == 4
        tool_names = [t.name for t in config.tools]
        assert "bash" in tool_names
        assert "think" in tool_names

        # Inherits subagents from general
        assert len(config.subagents) == 2
        agent_names = [s.name for s in config.subagents]
        assert "explore" in agent_names

    def test_child_overrides_controller(self, tmp_creatures):
        """Child controller values override parent."""
        swe = tmp_creatures / "creatures" / "swe"
        config = load_agent_config(swe)

        # SWE overrides reasoning_effort but inherits model from general
        assert config.reasoning_effort == "high"
        assert config.model == "base-model"
        assert config.tool_format == "native"  # inherited

    def test_child_system_prompt_appended(self, tmp_creatures):
        """Child system prompt is appended to base, not replaced."""
        swe = tmp_creatures / "creatures" / "swe"
        config = load_agent_config(swe)

        # Base prompt is included (from general)
        assert "General Agent" in config.system_prompt
        assert "general-purpose assistant" in config.system_prompt
        # Child prompt is appended (from swe)
        assert "SWE Agent" in config.system_prompt
        assert "software engineering" in config.system_prompt

    def test_inherited_system_prompt(self, tmp_creatures):
        """Parent system prompt is used when child doesn't have the file."""
        # Create a creature that specifies parent's prompt file but has no
        # local prompt file -- should fall back to base path
        bare = tmp_creatures / "creatures" / "bare"
        bare.mkdir(parents=True)
        (bare / "config.yaml").write_text(
            yaml.dump(
                {
                    "name": "bare",
                    "base_config": "../general",
                    # system_prompt_file inherited from general
                }
            )
        )

        config = load_agent_config(bare)
        assert "General Agent" in config.system_prompt

    def test_two_level_inheritance(self, tmp_creatures):
        """Agent inheriting from swe which inherits from general."""
        agent = tmp_creatures / "agents" / "my_agent"
        config = load_agent_config(agent)

        assert config.name == "my_agent"
        assert config.version == "2.0"
        # Model overridden by agent
        assert config.model == "agent-model"
        # reasoning_effort from swe (not general)
        assert config.reasoning_effort == "high"
        # tool_format from general (via swe)
        assert config.tool_format == "native"
        # Tools inherited from general (neither swe nor agent define tools)
        assert len(config.tools) == 4
        # Input from agent
        assert config.input.type == "cli"

    def test_child_tools_extend_parent(self, tmp_creatures):
        """When child defines tools, they extend parent tools."""
        custom = tmp_creatures / "creatures" / "custom"
        custom.mkdir(parents=True)
        (custom / "config.yaml").write_text(
            yaml.dump(
                {
                    "name": "custom",
                    "base_config": "../general",
                    "tools": [{"name": "grep", "type": "builtin"}],
                }
            )
        )

        config = load_agent_config(custom)
        # grep extends general's 4 tools (bash, read, write, think)
        assert len(config.tools) == 5
        tool_names = [t.name for t in config.tools]
        assert "grep" in tool_names
        assert "bash" in tool_names

    def test_inline_system_prompt_appended_to_chain(self, tmp_creatures):
        """Inline system_prompt from child is appended to file-based chain."""
        from kohakuterrarium.core.config import build_agent_config

        general = tmp_creatures / "creatures" / "general"
        config_data = {
            "name": "inline_test",
            "base_config": str(general),
            "system_prompt": "ROLE: You are a specialized worker.",
        }
        config = build_agent_config(config_data, tmp_creatures)

        # Base prompt from general's system.md is present
        assert "General Agent" in config.system_prompt
        # Inline prompt is appended (not lost)
        assert "ROLE: You are a specialized worker." in config.system_prompt

    def test_no_base_config(self, tmp_creatures):
        """Config without base_config loads normally."""
        general = tmp_creatures / "creatures" / "general"
        config = load_agent_config(general)

        assert config.name == "general"
        assert len(config.tools) == 4
        assert config.model == "base-model"

    def test_missing_base_config_warns(self, tmp_creatures):
        """Missing base_config path logs warning and continues."""
        broken = tmp_creatures / "creatures" / "broken"
        broken.mkdir(parents=True)
        (broken / "config.yaml").write_text(
            yaml.dump(
                {
                    "name": "broken",
                    "base_config": "../nonexistent",
                }
            )
        )

        # Should not raise -- just loads child-only config
        config = load_agent_config(broken)
        assert config.name == "broken"
        assert len(config.tools) == 0

    def test_mcp_servers_plugins_memory_populated(self, tmp_creatures):
        """mcp_servers, plugins, memory survive construction (no silent drop)."""
        custom = tmp_creatures / "creatures" / "with_extras"
        custom.mkdir(parents=True)
        (custom / "config.yaml").write_text(
            yaml.dump(
                {
                    "name": "with_extras",
                    "base_config": "../general",
                    "mcp_servers": [
                        {"name": "sqlite", "transport": "stdio", "command": "mcps"},
                    ],
                    "plugins": [
                        {"name": "logger", "class": "LogPlugin"},
                    ],
                    "memory": {
                        "embedding": {"provider": "model2vec", "model": "@base"},
                    },
                }
            )
        )

        config = load_agent_config(custom)
        assert len(config.mcp_servers) == 1
        assert config.mcp_servers[0]["name"] == "sqlite"
        assert len(config.plugins) == 1
        assert config.plugins[0]["name"] == "logger"
        assert config.memory["embedding"]["provider"] == "model2vec"

    def test_prompt_mode_replace_in_full_load(self, tmp_creatures):
        """prompt_mode: replace drops base prompt entirely."""
        clean = tmp_creatures / "creatures" / "clean"
        clean.mkdir(parents=True)
        (clean / "prompts").mkdir()
        (clean / "config.yaml").write_text(
            yaml.dump(
                {
                    "name": "clean",
                    "base_config": "../general",
                    "prompt_mode": "replace",
                    "system_prompt_file": "prompts/system.md",
                }
            )
        )
        (clean / "prompts" / "system.md").write_text(
            "# Clean Agent\n\nFresh prompt only."
        )

        config = load_agent_config(clean)
        # Base prompt must NOT appear
        assert "General Agent" not in config.system_prompt
        assert "general-purpose assistant" not in config.system_prompt
        # Child prompt IS the prompt
        assert "Clean Agent" in config.system_prompt
        assert "Fresh prompt only" in config.system_prompt

    def test_child_wins_for_overridden_tool_in_full_load(self, tmp_creatures):
        """A child redeclaring a base tool by name gets its own definition."""
        override = tmp_creatures / "creatures" / "override"
        override.mkdir(parents=True)
        (override / "config.yaml").write_text(
            yaml.dump(
                {
                    "name": "override",
                    "base_config": "../general",
                    "tools": [
                        {
                            "name": "bash",
                            "type": "custom",
                            "module": "./tools/bash.py",
                            "class": "SafeBash",
                        },
                    ],
                }
            )
        )

        config = load_agent_config(override)
        bash = next(t for t in config.tools if t.name == "bash")
        assert bash.type == "custom"
        assert bash.module == "./tools/bash.py"
        assert bash.class_name == "SafeBash"


class TestRealCreatures:
    """Tests that verify the actual creatures/ configs load correctly."""

    @pytest.fixture
    def project_root(self):
        """Get the project root directory."""
        return Path(__file__).resolve().parent.parent.parent

    def test_general_creature_loads(self, project_root):
        general = project_root / "creatures" / "general"
        if not general.exists():
            pytest.skip("creatures/general not found")
        config = load_agent_config(general)
        assert config.name == "general"
        assert len(config.tools) > 0
        assert "KohakuTerrarium" in config.system_prompt

    def test_swe_creature_loads(self, project_root):
        swe = project_root / "creatures" / "swe"
        if not swe.exists():
            pytest.skip("creatures/swe not found")
        config = load_agent_config(swe)
        assert config.name == "swe"
        # Should inherit tools from general
        assert len(config.tools) > 0
        # General prompt is included (inherited)
        assert "KohakuTerrarium" in config.system_prompt
        # SWE prompt is appended
        assert "Software Engineering" in config.system_prompt

    def test_researcher_creature_loads(self, project_root):
        researcher = project_root / "creatures" / "researcher"
        if not researcher.exists():
            pytest.skip("creatures/researcher not found")
        config = load_agent_config(researcher)
        assert config.name == "researcher"
        # Researcher inherits all tools from general
        tool_names = [t.name for t in config.tools]
        assert "bash" in tool_names
        assert "web_fetch" in tool_names
        # General prompt is included (inherited)
        assert "KohakuTerrarium" in config.system_prompt
        # Researcher prompt is appended
        assert "Research Methodology" in config.system_prompt

    def test_root_creature_loads(self, project_root):
        root = project_root / "creatures" / "root"
        if not root.exists():
            pytest.skip("creatures/root not found")
        config = load_agent_config(root)
        assert config.name == "root"
        # Root extends general tools with terrarium management
        tool_names = [t.name for t in config.tools]
        assert "bash" in tool_names  # inherited from general
        assert "send_message" in tool_names  # inherited from general
        assert "terrarium_status" in tool_names  # root-specific
        assert "creature_start" in tool_names  # root-specific
        # General prompt is included (inherited)
        assert "KohakuTerrarium" in config.system_prompt
        # Root prompt is appended
        assert "Terrarium Management" in config.system_prompt
