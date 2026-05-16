"""Unit tests for :mod:`kohakuterrarium.studio.catalog.builtins`."""

import pytest

from kohakuterrarium.studio.catalog import builtins as builtins_mod

# ── list_builtin_tool_entries ───────────────────────────────


class TestListBuiltinToolEntries:
    def test_includes_known_builtin_tools_with_full_shape(self):
        out = builtins_mod.list_builtin_tool_entries()
        by_name = {e["name"]: e for e in out}
        # bash is a known builtin tool; it must surface with the full
        # catalog entry shape.
        assert "bash" in by_name
        bash = by_name["bash"]
        assert bash["source"] == "builtin"
        assert bash["type"] == "builtin"
        assert bash["execution_mode"] in ("direct", "background", "stateful")
        assert isinstance(bash["needs_context"], bool)
        assert isinstance(bash["has_doc"], bool)
        # Every entry has a non-empty name + a description string.
        assert all(e["name"] and isinstance(e["description"], str) for e in out)


# ── list_builtin_subagent_entries ───────────────────────────


class TestListBuiltinSubagentEntries:
    def test_includes_known_subagents_with_full_shape(self):
        out = builtins_mod.list_builtin_subagent_entries()
        by_name = {e["name"]: e for e in out}
        # The builtin sub-agent catalog ships explore/plan/research/etc.
        assert by_name, "expected at least one builtin sub-agent"
        sample = next(iter(by_name.values()))
        assert sample["source"] == "builtin"
        assert isinstance(sample["tools"], list)
        assert isinstance(sample["can_modify"], bool)
        assert isinstance(sample["interactive"], bool)


# ── list_universal_trigger_entries ──────────────────────────


class TestListUniversalTriggerEntries:
    def test_only_universal_triggers_with_full_shape(self):
        out = builtins_mod.list_universal_trigger_entries()
        # Every entry is sourced from a universal trigger class.
        for e in out:
            assert e["source"] == "builtin"
            assert e["type"] == "trigger"
            assert e["name"]
            assert isinstance(e["param_schema"], dict)


# ── get_tool_doc / get_subagent_doc ─────────────────────────


class TestGetDoc:
    def test_unknown_tool_returns_none(self):
        assert builtins_mod.get_tool_doc("definitely-not-a-tool") is None

    def test_unknown_subagent_returns_none(self):
        assert builtins_mod.get_subagent_doc("definitely-not-a-subagent") is None


# ── list_extension_packages ─────────────────────────────────


class TestListExtensionPackages:
    def test_delegates_to_list_packages(self, monkeypatch):
        monkeypatch.setattr(builtins_mod, "list_packages", lambda: [{"name": "demo"}])
        out = builtins_mod.list_extension_packages()
        assert out == [{"name": "demo"}]


# ── get_extension_modules ───────────────────────────────────


class TestGetExtensionModules:
    def test_delegates(self, monkeypatch):
        captured = []

        def _get(pkg_name, module_type):
            captured.append((pkg_name, module_type))
            return ["entry"]

        monkeypatch.setattr(builtins_mod, "get_package_modules", _get)
        out = builtins_mod.get_extension_modules("pkg", "tools")
        assert out == ["entry"]
        assert captured == [("pkg", "tools")]


# ── extension_module_types ──────────────────────────────────


class TestExtensionModuleTypes:
    def test_returns_cli_order_tuple(self):
        # Exact tuple in the order the CLI surfaces them.
        assert builtins_mod.extension_module_types() == (
            "tools",
            "plugins",
            "llm_presets",
        )


# ── list_builtins ────────────────────────────────────────────


class TestListBuiltins:
    def test_tools_matches_tool_entries(self):
        # "tools" dispatches to list_builtin_tool_entries.
        assert (
            builtins_mod.list_builtins("tools")
            == builtins_mod.list_builtin_tool_entries()
        )

    def test_tool_singular_equals_plural(self):
        assert builtins_mod.list_builtins("tool") == builtins_mod.list_builtins("tools")

    def test_subagents_matches_subagent_entries(self):
        assert (
            builtins_mod.list_builtins("subagents")
            == builtins_mod.list_builtin_subagent_entries()
        )

    def test_subagent_singular_equals_plural(self):
        assert builtins_mod.list_builtins("subagent") == builtins_mod.list_builtins(
            "subagents"
        )

    def test_triggers_matches_trigger_entries(self):
        assert (
            builtins_mod.list_builtins("triggers")
            == builtins_mod.list_universal_trigger_entries()
        )

    def test_trigger_singular_equals_plural(self):
        assert builtins_mod.list_builtins("trigger") == builtins_mod.list_builtins(
            "triggers"
        )

    def test_none_returns_concatenation_in_order(self):
        tools = builtins_mod.list_builtins("tools")
        subagents = builtins_mod.list_builtins("subagents")
        triggers = builtins_mod.list_builtins("triggers")
        # None == tools ++ subagents ++ triggers, exactly.
        assert builtins_mod.list_builtins(None) == tools + subagents + triggers

    def test_unknown_raises(self):
        with pytest.raises(ValueError, match="Unknown builtin kind"):
            builtins_mod.list_builtins("garbage")


# ── builtin_info ────────────────────────────────────────────


class TestBuiltinInfo:
    def test_unknown_returns_none(self):
        assert builtins_mod.builtin_info("definitely-not-a-builtin") is None

    def test_tool_lookup(self, monkeypatch):
        monkeypatch.setattr(
            builtins_mod,
            "list_builtin_tool_entries",
            lambda: [{"name": "bash", "type": "builtin"}],
        )
        monkeypatch.setattr(builtins_mod, "list_builtin_subagent_entries", lambda: [])
        monkeypatch.setattr(builtins_mod, "list_universal_trigger_entries", lambda: [])
        out = builtins_mod.builtin_info("bash")
        assert out["name"] == "bash"

    def test_subagent_lookup(self, monkeypatch):
        monkeypatch.setattr(builtins_mod, "list_builtin_tool_entries", lambda: [])
        monkeypatch.setattr(
            builtins_mod,
            "list_builtin_subagent_entries",
            lambda: [{"name": "explorer"}],
        )
        monkeypatch.setattr(builtins_mod, "list_universal_trigger_entries", lambda: [])
        out = builtins_mod.builtin_info("explorer")
        assert out["name"] == "explorer"

    def test_trigger_lookup(self, monkeypatch):
        monkeypatch.setattr(builtins_mod, "list_builtin_tool_entries", lambda: [])
        monkeypatch.setattr(builtins_mod, "list_builtin_subagent_entries", lambda: [])
        monkeypatch.setattr(
            builtins_mod,
            "list_universal_trigger_entries",
            lambda: [{"name": "tick"}],
        )
        out = builtins_mod.builtin_info("tick")
        assert out["name"] == "tick"


# ── exception path in tool entry ─────────────────────────────


class TestToolEntryFallback:
    def test_execution_mode_failure_falls_back(self, monkeypatch):
        """When tool.execution_mode raises, falls back to 'direct'."""

        class _BadTool:
            description = "x"
            needs_context = False
            require_manual_read = False

            @property
            def execution_mode(self):
                raise RuntimeError("bad")

        monkeypatch.setattr(builtins_mod, "list_builtin_tools", lambda: ["bad"])
        monkeypatch.setattr(builtins_mod, "get_builtin_tool", lambda n: _BadTool())
        out = builtins_mod.list_builtin_tool_entries()
        assert out[0]["execution_mode"] == "direct"

    def test_tool_is_none_skipped(self, monkeypatch):
        monkeypatch.setattr(builtins_mod, "list_builtin_tools", lambda: ["ghost"])
        monkeypatch.setattr(builtins_mod, "get_builtin_tool", lambda n: None)
        assert builtins_mod.list_builtin_tool_entries() == []

    def test_subagent_cfg_is_none_skipped(self, monkeypatch):
        monkeypatch.setattr(builtins_mod, "list_builtin_subagents", lambda: ["ghost"])
        monkeypatch.setattr(builtins_mod, "get_builtin_subagent_config", lambda n: None)
        assert builtins_mod.list_builtin_subagent_entries() == []

    def test_non_universal_trigger_skipped(self, monkeypatch):
        class _T:
            universal = False
            setup_tool_name = "x"
            setup_description = "d"
            setup_param_schema = {}
            setup_require_manual_read = False

        monkeypatch.setattr(
            builtins_mod, "list_universal_trigger_classes", lambda: [_T]
        )
        assert builtins_mod.list_universal_trigger_entries() == []
