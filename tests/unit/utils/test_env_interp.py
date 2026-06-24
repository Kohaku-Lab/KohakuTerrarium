"""Unit tests for ``utils/env_interp.py`` — ``${VAR}`` interpolation.

Behavior-first: assert the exact expansion for set / unset / default
cases, recursion into dict + list, non-string passthrough, and that the
lookup is live (re-running after the environment changes picks up the
new value — there is no import-time snapshot).
"""

from kohakuterrarium.utils.env_interp import interpolate_env_vars


class TestScalarExpansion:
    def test_set_var_expands(self, monkeypatch):
        monkeypatch.setenv("KT_X", "host.example")
        assert interpolate_env_vars("${KT_X}/v1") == "host.example/v1"

    def test_unset_var_no_default_collapses_to_empty(self, monkeypatch):
        monkeypatch.delenv("KT_MISSING", raising=False)
        assert interpolate_env_vars("a${KT_MISSING}b") == "ab"

    def test_default_used_when_unset(self, monkeypatch):
        monkeypatch.delenv("KT_MISSING", raising=False)
        assert interpolate_env_vars("${KT_MISSING:fallback}") == "fallback"

    def test_default_ignored_when_set(self, monkeypatch):
        monkeypatch.setenv("KT_X", "real")
        assert interpolate_env_vars("${KT_X:fallback}") == "real"

    def test_empty_default_collapses(self, monkeypatch):
        monkeypatch.delenv("KT_MISSING", raising=False)
        assert interpolate_env_vars("${KT_MISSING:}") == ""

    def test_multiple_vars_in_one_string(self, monkeypatch):
        monkeypatch.setenv("KT_A", "1")
        monkeypatch.setenv("KT_B", "2")
        assert interpolate_env_vars("${KT_A}-${KT_B}") == "1-2"

    def test_plain_string_unchanged(self):
        assert interpolate_env_vars("no markers here") == "no markers here"


class TestRecursion:
    def test_dict_and_list_recurse(self, monkeypatch):
        monkeypatch.setenv("KT_X", "v")
        out = interpolate_env_vars(
            {"url": "${KT_X}", "nested": ["${KT_X}", {"k": "${KT_X}"}]}
        )
        assert out == {"url": "v", "nested": ["v", {"k": "v"}]}

    def test_non_string_scalars_passthrough(self):
        assert interpolate_env_vars(7) == 7
        assert interpolate_env_vars(None) is None
        assert interpolate_env_vars(True) is True


class TestLiveRefresh:
    def test_reexpansion_picks_up_new_value(self, monkeypatch):
        # No import-time snapshot: changing the env and re-calling yields
        # the new value. This is what makes config refresh work.
        monkeypatch.setenv("KT_X", "first")
        assert interpolate_env_vars("${KT_X}") == "first"
        monkeypatch.setenv("KT_X", "second")
        assert interpolate_env_vars("${KT_X}") == "second"
