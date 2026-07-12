"""Unit tests for :mod:`kohakuterrarium.modules.user_command.aggregate`.

The collision policy is the whole point: a name from one source survives; a name
from two sources is a hard error unless exactly one carries ``override=True``.
Every branch of that matrix is pinned here, plus the provenance carried back.
"""

import pytest

from kohakuterrarium.modules.user_command.aggregate import (
    CommandContribution,
    CommandProvenance,
    UserCommandCollisionError,
    aggregate_user_commands,
)


def _c(name, source, origin="", override=False, cmd=None):
    return CommandContribution(
        name=name,
        command=cmd if cmd is not None else object(),
        provenance=CommandProvenance(source=source, origin=origin),
        override=override,
    )


class _AliasCmd:
    """A command carrying declared ``aliases`` for alias-collision tests."""

    def __init__(self, aliases):
        self.aliases = list(aliases)


class TestAggregate:
    def test_single_source_passes_through_with_provenance(self):
        a = _c("goal", "plugin", "acme-cmds")
        commands, prov = aggregate_user_commands([a])
        assert commands["goal"] is a.command
        assert prov["goal"].source == "plugin"
        assert prov["goal"].origin == "acme-cmds"

    def test_distinct_names_all_survive(self):
        a = _c("goal", "plugin", "acme-cmds")
        b = _c("clear", "builtin")
        commands, _ = aggregate_user_commands([a, b])
        assert set(commands) == {"goal", "clear"}

    def test_builtin_vs_plugin_no_override_is_hard_error(self):
        builtin = _c("goal", "builtin")
        plugin = _c("goal", "plugin", "acme-cmds")
        with pytest.raises(UserCommandCollisionError) as exc:
            aggregate_user_commands([builtin, plugin])
        # Provenance of both sides is reported.
        assert "builtin" in str(exc.value)
        assert "plugin:acme-cmds" in str(exc.value)

    def test_package_vs_plugin_no_override_is_hard_error(self):
        pkg = _c("goal", "package", "kt-other")
        plugin = _c("goal", "plugin", "acme-cmds")
        with pytest.raises(UserCommandCollisionError):
            aggregate_user_commands([pkg, plugin])

    def test_single_overrider_wins(self):
        builtin = _c("goal", "builtin")
        plugin = _c("goal", "plugin", "acme-cmds", override=True)
        commands, prov = aggregate_user_commands([builtin, plugin])
        assert commands["goal"] is plugin.command
        assert prov["goal"].source == "plugin"

    def test_constructor_override_shadows_builtin(self):
        builtin = _c("model", "builtin")
        injected = _c("model", "constructor", override=True)
        commands, prov = aggregate_user_commands([builtin, injected])
        assert commands["model"] is injected.command
        assert prov["model"].source == "constructor"

    def test_two_overriders_is_hard_error(self):
        a = _c("goal", "plugin", "one", override=True)
        b = _c("goal", "plugin", "two", override=True)
        with pytest.raises(UserCommandCollisionError) as exc:
            aggregate_user_commands([a, b])
        assert "override" in str(exc.value)

    def test_empty_input_is_empty(self):
        commands, prov = aggregate_user_commands([])
        assert commands == {}
        assert prov == {}


class TestAliasCollisions:
    """Aliases share one namespace with canonical names (R1-24)."""

    def test_alias_survives_when_unique(self):
        a = _c("model", "builtin", cmd=_AliasCmd(["llm"]))
        b = _c("plugin_cmd", "plugin", "acme", cmd=_AliasCmd(["pc"]))
        commands, _ = aggregate_user_commands([a, b])
        assert set(commands) == {"model", "plugin_cmd"}

    def test_alias_colliding_with_a_canonical_name_is_hard_error(self):
        # A plugin alias may not hijack another command's canonical name.
        builtin = _c("model", "builtin", cmd=_AliasCmd([]))
        plugin = _c("goal", "plugin", "acme", cmd=_AliasCmd(["model"]))
        with pytest.raises(UserCommandCollisionError) as exc:
            aggregate_user_commands([builtin, plugin])
        assert "model" in str(exc.value)
        assert "plugin:acme" in str(exc.value)

    def test_alias_colliding_with_another_alias_is_hard_error(self):
        a = _c("alpha", "builtin", cmd=_AliasCmd(["x"]))
        b = _c("beta", "plugin", "acme", cmd=_AliasCmd(["x"]))
        with pytest.raises(UserCommandCollisionError) as exc:
            aggregate_user_commands([a, b])
        assert "/x" in str(exc.value)

    def test_alias_equal_to_own_canonical_name_is_ignored(self):
        a = _c("thing", "builtin", cmd=_AliasCmd(["thing", "t"]))
        commands, _ = aggregate_user_commands([a])
        assert set(commands) == {"thing"}

    def test_losing_contribution_aliases_do_not_register(self):
        # The plugin loses the /goal canonical (no override) → its alias must
        # not leak into the namespace and hijack the builtin /help.
        builtin_goal = _c("goal", "builtin", cmd=_AliasCmd([]), override=True)
        plugin_goal = _c("goal", "plugin", "acme", cmd=_AliasCmd(["help"]))
        builtin_help = _c("help", "builtin", cmd=_AliasCmd([]))
        commands, prov = aggregate_user_commands(
            [builtin_goal, plugin_goal, builtin_help]
        )
        assert set(commands) == {"goal", "help"}
        assert prov["goal"].source == "builtin"
