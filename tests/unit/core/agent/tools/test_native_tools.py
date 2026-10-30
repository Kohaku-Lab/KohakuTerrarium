"""Unit tests for :mod:`kohakuterrarium.core.agent_native_tools`."""

import json
import types
from typing import Any

import pytest

from kohakuterrarium.core.agent_native_tools import (
    NATIVE_TOOL_OPTIONS_KEY,
    NATIVE_TOOL_OPTIONS_STATE_SUFFIX,
    NativeToolOptions,
)

# ── helpers ──────────────────────────────────────────────────────


class _FakeTool:
    """Fake provider-native tool with a fixed option schema."""

    is_provider_native = True

    def __init__(self, schema=None):
        self._schema = schema or {
            "temperature": {"type": "float", "min": 0, "max": 1},
            "size": {"type": "string"},
        }
        self.config = types.SimpleNamespace(extra={})
        self.refreshed_count = 0

    @classmethod
    def make(cls, schema=None):
        # Bind a schema function onto the *class*, not the instance —
        # the helper reads from ``type(tool).provider_native_option_schema``.
        instance = cls(schema=schema)
        type(instance).provider_native_option_schema = staticmethod(
            lambda: instance._schema
        )
        return instance

    def refresh_native_options(self):
        self.refreshed_count += 1


class _FakeRegistry:
    def __init__(self, tools=None):
        self._tools = dict(tools or {})

    def get_tool(self, name):
        return self._tools.get(name)


class _FakeScratchpad:
    def __init__(self, initial=None):
        self._data = dict(initial or {})
        self.deletes: list[str] = []
        self.sets: list[tuple] = []

    def get(self, key):
        return self._data.get(key)

    def delete(self, key):
        self.deletes.append(key)
        self._data.pop(key, None)

    def set(self, key, value, size=None):
        self.sets.append((key, value))
        self._data[key] = value


class _FakeSession:
    def __init__(self, scratchpad=None, extra=None):
        self.scratchpad = scratchpad
        self.extra = extra if extra is not None else {}


class _FakeStore:
    def __init__(self):
        self.state: dict[str, Any] = {}


def _make_agent(*, registry=None, session=None, store=None, name="alice"):
    return types.SimpleNamespace(
        config=types.SimpleNamespace(name=name),
        registry=registry,
        session=session,
        session_store=store,
    )


# ── set: explicit reset (empty dict) ─────────────────────────────


class TestExplicitReset:
    def test_empty_dict_resets(self):
        tool = _FakeTool.make()
        agent = _make_agent(registry=_FakeRegistry({"image_gen": tool}))
        opts = NativeToolOptions(agent)
        opts._values["image_gen"] = {"temperature": 0.5}
        result = opts.set("image_gen", {})
        assert result == {}
        assert opts.list() == {}
        # Registry refresh emptied extras.
        assert tool.config.extra == {}
        assert tool.refreshed_count >= 1


# ── set: partial merge ───────────────────────────────────────────


class TestSetMerge:
    def test_first_set(self):
        tool = _FakeTool.make()
        agent = _make_agent(registry=_FakeRegistry({"image_gen": tool}))
        opts = NativeToolOptions(agent)
        out = opts.set("image_gen", {"temperature": 0.5})
        assert out == {"temperature": 0.5}
        assert opts.get("image_gen") == {"temperature": 0.5}

    def test_partial_merge_preserves_prior_keys(self):
        tool = _FakeTool.make()
        agent = _make_agent(registry=_FakeRegistry({"image_gen": tool}))
        opts = NativeToolOptions(agent)
        opts.set("image_gen", {"temperature": 0.5, "size": "1024x1024"})
        # Now patch only size.
        out = opts.set("image_gen", {"size": "512x512"})
        # Temperature survives.
        assert out["temperature"] == 0.5
        assert out["size"] == "512x512"

    def test_explicit_none_deletes_key(self):
        tool = _FakeTool.make()
        agent = _make_agent(registry=_FakeRegistry({"image_gen": tool}))
        opts = NativeToolOptions(agent)
        opts.set("image_gen", {"temperature": 0.5, "size": "1024x1024"})
        out = opts.set("image_gen", {"size": None})
        assert "size" not in out
        assert out == {"temperature": 0.5}

    def test_validation_runs(self):
        tool = _FakeTool.make()
        agent = _make_agent(registry=_FakeRegistry({"image_gen": tool}))
        opts = NativeToolOptions(agent)
        with pytest.raises(ValueError):
            opts.set("image_gen", {"temperature": 99.0})  # out of [0,1]

    def test_unknown_tool_rejected(self):
        agent = _make_agent(registry=_FakeRegistry({}))
        opts = NativeToolOptions(agent)
        with pytest.raises(ValueError, match="Unknown provider-native tool"):
            opts.set("ghost", {"x": 1})

    def test_non_native_tool_rejected(self):
        tool = _FakeTool.make()
        tool.is_provider_native = False
        agent = _make_agent(registry=_FakeRegistry({"image_gen": tool}))
        opts = NativeToolOptions(agent)
        with pytest.raises(ValueError, match="Unknown provider-native tool"):
            opts.set("image_gen", {"temperature": 0.5})


# ── persistence ──────────────────────────────────────────────────


class TestPersistence:
    def test_session_store_state_written(self):
        tool = _FakeTool.make()
        store = _FakeStore()
        agent = _make_agent(
            registry=_FakeRegistry({"image_gen": tool}),
            store=store,
        )
        opts = NativeToolOptions(agent)
        opts.set("image_gen", {"temperature": 0.5})
        key = f"alice:{NATIVE_TOOL_OPTIONS_STATE_SUFFIX}"
        assert key in store.state
        assert store.state[key]["image_gen"]["temperature"] == 0.5

    def test_session_extra_written(self):
        tool = _FakeTool.make()
        session = _FakeSession(extra={})
        agent = _make_agent(
            registry=_FakeRegistry({"image_gen": tool}),
            session=session,
        )
        opts = NativeToolOptions(agent)
        opts.set("image_gen", {"temperature": 0.5})
        assert (
            session.extra[NATIVE_TOOL_OPTIONS_STATE_SUFFIX]["image_gen"]["temperature"]
            == 0.5
        )

    def test_session_extra_removed_when_empty(self):
        tool = _FakeTool.make()
        session = _FakeSession(extra={NATIVE_TOOL_OPTIONS_STATE_SUFFIX: {"x": "y"}})
        agent = _make_agent(
            registry=_FakeRegistry({"image_gen": tool}),
            session=session,
        )
        opts = NativeToolOptions(agent)
        opts.set("image_gen", {})  # reset
        assert NATIVE_TOOL_OPTIONS_STATE_SUFFIX not in session.extra

    def test_scratchpad_mirror_used_when_no_canonical(self):
        tool = _FakeTool.make()
        scratchpad = _FakeScratchpad()
        session = _FakeSession(scratchpad=scratchpad)
        session.extra = None  # disable extra mirror
        agent = _make_agent(
            registry=_FakeRegistry({"image_gen": tool}),
            session=session,
        )
        opts = NativeToolOptions(agent)
        opts.set("image_gen", {"temperature": 0.5})
        # Mirror to scratchpad with the reserved key.
        assert any(k == NATIVE_TOOL_OPTIONS_KEY for k, _ in scratchpad.sets)

    def test_scratchpad_cleared_when_canonical_present(self):
        tool = _FakeTool.make()
        store = _FakeStore()
        scratchpad = _FakeScratchpad()
        session = _FakeSession(scratchpad=scratchpad)
        agent = _make_agent(
            registry=_FakeRegistry({"image_gen": tool}),
            session=session,
            store=store,
        )
        opts = NativeToolOptions(agent)
        opts.set("image_gen", {"temperature": 0.5})
        # Canonical store wrote → scratchpad mirror deleted.
        assert NATIVE_TOOL_OPTIONS_KEY in scratchpad.deletes


# ── apply: load + migrate legacy scratchpad ──────────────────────


class TestApply:
    def test_loads_from_session_store(self):
        tool = _FakeTool.make()
        store = _FakeStore()
        agent = _make_agent(
            registry=_FakeRegistry({"image_gen": tool}),
            store=store,
            session=_FakeSession(extra={}),
        )
        key = f"alice:{NATIVE_TOOL_OPTIONS_STATE_SUFFIX}"
        store.state[key] = {"image_gen": {"temperature": 0.5}}
        opts = NativeToolOptions(agent)
        opts.apply()
        assert opts.get("image_gen") == {"temperature": 0.5}
        # Registry was refreshed.
        assert tool.config.extra == {"temperature": 0.5}

    def test_loads_from_session_extra(self):
        tool = _FakeTool.make()
        session = _FakeSession(
            extra={
                NATIVE_TOOL_OPTIONS_STATE_SUFFIX: {"image_gen": {"temperature": 0.5}}
            }
        )
        agent = _make_agent(
            registry=_FakeRegistry({"image_gen": tool}),
            session=session,
        )
        opts = NativeToolOptions(agent)
        opts.apply()
        assert opts.get("image_gen") == {"temperature": 0.5}

    def test_legacy_scratchpad_migrated(self):
        tool = _FakeTool.make()
        scratchpad = _FakeScratchpad(
            initial={
                NATIVE_TOOL_OPTIONS_KEY: json.dumps({"image_gen": {"temperature": 0.5}})
            }
        )
        session = _FakeSession(scratchpad=scratchpad)
        agent = _make_agent(
            registry=_FakeRegistry({"image_gen": tool}),
            session=session,
        )
        opts = NativeToolOptions(agent)
        opts.apply()
        assert opts.get("image_gen") == {"temperature": 0.5}
        # Legacy key wiped during migration.
        assert NATIVE_TOOL_OPTIONS_KEY in scratchpad.deletes

    def test_store_state_str_json(self):
        tool = _FakeTool.make()
        store = _FakeStore()
        agent = _make_agent(
            registry=_FakeRegistry({"image_gen": tool}),
            store=store,
            session=_FakeSession(extra={}),
        )
        key = f"alice:{NATIVE_TOOL_OPTIONS_STATE_SUFFIX}"
        store.state[key] = json.dumps({"image_gen": {"temperature": 0.5}})
        opts = NativeToolOptions(agent)
        opts.apply()
        assert opts.get("image_gen") == {"temperature": 0.5}

    def test_store_state_garbage_str_ignored(self):
        tool = _FakeTool.make()
        store = _FakeStore()
        agent = _make_agent(
            registry=_FakeRegistry({"image_gen": tool}),
            store=store,
        )
        key = f"alice:{NATIVE_TOOL_OPTIONS_STATE_SUFFIX}"
        store.state[key] = "{not json"
        opts = NativeToolOptions(agent)
        opts.apply()  # must not raise
        assert opts.list() == {}

    def test_invalid_values_logged_and_skipped(self):
        tool = _FakeTool.make()
        store = _FakeStore()
        agent = _make_agent(
            registry=_FakeRegistry({"image_gen": tool}),
            store=store,
            session=_FakeSession(extra={}),
        )
        key = f"alice:{NATIVE_TOOL_OPTIONS_STATE_SUFFIX}"
        store.state[key] = {"image_gen": {"temperature": 99.0}}
        opts = NativeToolOptions(agent)
        opts.apply()
        assert opts.list() == {}

    def test_garbage_legacy_scratchpad(self):
        scratchpad = _FakeScratchpad(initial={NATIVE_TOOL_OPTIONS_KEY: "{nope"})
        session = _FakeSession(scratchpad=scratchpad)
        agent = _make_agent(
            registry=_FakeRegistry({}),
            session=session,
        )
        opts = NativeToolOptions(agent)
        # Should not raise — garbage just skipped.
        opts.apply()
        assert opts.list() == {}


# ── read accessors ───────────────────────────────────────────────


class TestReadAccessors:
    def test_get_missing_returns_empty(self):
        agent = _make_agent()
        opts = NativeToolOptions(agent)
        assert opts.get("nope") == {}

    def test_list_returns_copy(self):
        agent = _make_agent(registry=_FakeRegistry({}))
        opts = NativeToolOptions(agent)
        opts._values["x"] = {"k": "v"}
        snap = opts.list()
        snap["x"]["k"] = "mutated"
        assert opts._values["x"]["k"] == "v"


class TestRefreshAndSetEdgeCases:
    def test_set_with_only_deletions_clears_entry(self):
        """``set`` with only ``key: None`` for the sole key removes the entry."""
        tool = _FakeTool.make()
        agent = _make_agent(registry=_FakeRegistry({"image_gen": tool}))
        opts = NativeToolOptions(agent)
        opts.set("image_gen", {"temperature": 0.5})
        out = opts.set("image_gen", {"temperature": None})
        # Empty → entry removed.
        assert out == {}
        assert opts.list() == {}

    def test_refresh_no_registry(self):
        agent = _make_agent(registry=None)
        opts = NativeToolOptions(agent)
        # Just must not raise.
        opts._refresh_in_registry("image_gen", {})

    def test_refresh_tool_missing_returns_no_op(self):
        agent = _make_agent(registry=_FakeRegistry({}))
        opts = NativeToolOptions(agent)
        opts._refresh_in_registry("image_gen", {})

    def test_refresh_non_native_tool_skipped(self):
        tool = _FakeTool.make()
        tool.is_provider_native = False
        agent = _make_agent(registry=_FakeRegistry({"image_gen": tool}))
        opts = NativeToolOptions(agent)
        opts._refresh_in_registry("image_gen", {"k": "v"})
        # extra not modified.
        assert tool.config.extra == {}

    def test_refresh_no_config_skipped(self):
        tool = _FakeTool.make()
        tool.config = None
        agent = _make_agent(registry=_FakeRegistry({"image_gen": tool}))
        opts = NativeToolOptions(agent)
        # Must not raise.
        opts._refresh_in_registry("image_gen", {"k": "v"})

    def test_apply_with_legacy_scratchpad_no_explicit(self):
        scratchpad = _FakeScratchpad(
            initial={
                NATIVE_TOOL_OPTIONS_KEY: json.dumps({"image_gen": {"temperature": 0.5}})
            }
        )
        session = _FakeSession(scratchpad=scratchpad)
        tool = _FakeTool.make()
        agent = _make_agent(
            registry=_FakeRegistry({"image_gen": tool}),
            session=session,
        )
        opts = NativeToolOptions(agent)
        opts.apply()
        # Legacy scratchpad migrated.
        assert opts.get("image_gen")

    def test_apply_non_dict_data_returns(self):
        """When _load_private_state returns non-dict (e.g. via patched
        store returning a list), apply bails."""
        agent = _make_agent(registry=_FakeRegistry({}))
        opts = NativeToolOptions(agent)
        # Inject _load_private_state to return non-dict.
        opts._load_private_state = lambda: "not a dict"  # type: ignore[method-assign]
        opts.apply()
        assert opts.list() == {}

    def test_apply_non_dict_value_skipped(self):
        """Non-dict value for a tool entry triggers the ``continue`` (line 117)."""
        tool = _FakeTool.make()
        store = _FakeStore()
        agent = _make_agent(
            registry=_FakeRegistry({"image_gen": tool}),
            store=store,
            session=_FakeSession(extra={}),
        )
        key = f"alice:{NATIVE_TOOL_OPTIONS_STATE_SUFFIX}"
        store.state[key] = {"image_gen": "not a dict"}
        opts = NativeToolOptions(agent)
        opts.apply()
        # Non-dict value silently skipped.
        assert opts.list() == {}

    def test_apply_empty_cleaned_skipped(self):
        """When validation returns empty cleaned dict, the entry is skipped (line 129)."""
        tool = _FakeTool.make()
        store = _FakeStore()
        agent = _make_agent(
            registry=_FakeRegistry({"image_gen": tool}),
            store=store,
            session=_FakeSession(extra={}),
        )
        key = f"alice:{NATIVE_TOOL_OPTIONS_STATE_SUFFIX}"
        # Empty values dict → cleaned will be empty (no options to validate).
        store.state[key] = {"image_gen": {}}
        opts = NativeToolOptions(agent)
        opts.apply()
        # Empty cleaned → skipped.
        assert opts.list() == {}


# ── _load_private_state store.state.get raises (lines 178-179) ──


class TestLoadPrivateStateStoreFailure:
    def test_store_state_raises(self):
        class _BadState:
            def get(self, key):
                raise KeyError("nope")

        store = types.SimpleNamespace(state=_BadState())
        agent = _make_agent(registry=_FakeRegistry({}), store=store)
        opts = NativeToolOptions(agent)
        out = opts._load_private_state()
        assert out == {}
