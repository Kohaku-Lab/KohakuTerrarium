"""Unit tests for :mod:`kohakuterrarium.terrarium.group_hooks`.

The module is a registration/dispatch shim: studio-tier behaviour is
plugged in at import time, and when nothing is registered every call
degrades gracefully.  Each test restores module globals afterwards so
the tests are order-independent.
"""

import pytest

from kohakuterrarium.terrarium import group_hooks


@pytest.fixture(autouse=True)
def _reset_hooks():
    """Snapshot + restore the module-level hook globals."""
    saved = (
        group_hooks._store_attach,
        group_hooks._name_apply,
        group_hooks._spawnable,
        group_hooks._workspace_resolver,
    )
    group_hooks._store_attach = None
    group_hooks._name_apply = None
    group_hooks._spawnable = None
    group_hooks._workspace_resolver = None
    yield
    (
        group_hooks._store_attach,
        group_hooks._name_apply,
        group_hooks._spawnable,
        group_hooks._workspace_resolver,
    ) = saved


class _Creature:
    def __init__(self):
        self.name = None


# ── attach_session_store ────────────────────────────────────


class TestAttachSessionStore:
    def test_noop_when_unregistered(self):
        # No hook → silently returns, nothing raised.
        group_hooks.attach_session_store(object(), _Creature())

    def test_dispatches_with_kwargs(self):
        calls = []

        def hook(engine, creature, *, config_path="", config_type="agent"):
            calls.append((engine, creature, config_path, config_type))

        group_hooks.register_store_attach(hook)
        eng, cr = object(), _Creature()
        group_hooks.attach_session_store(
            eng, cr, config_path="/x/agent.yaml", config_type="creature"
        )
        assert calls == [(eng, cr, "/x/agent.yaml", "creature")]

    def test_defaults_passed_through(self):
        seen = {}

        def hook(engine, creature, *, config_path="", config_type="agent"):
            seen["config_path"] = config_path
            seen["config_type"] = config_type

        group_hooks.register_store_attach(hook)
        group_hooks.attach_session_store(object(), _Creature())
        assert seen == {"config_path": "", "config_type": "agent"}

    def test_hook_exception_is_swallowed(self):
        def hook(engine, creature, *, config_path="", config_type="agent"):
            raise RuntimeError("store attach blew up")

        group_hooks.register_store_attach(hook)
        # Must not propagate.
        group_hooks.attach_session_store(object(), _Creature())


# ── apply_creature_name ─────────────────────────────────────


class TestApplyCreatureName:
    def test_fallback_sets_name_directly(self):
        cr = _Creature()
        group_hooks.apply_creature_name(cr, "alice")
        assert cr.name == "alice"

    def test_registered_hook_used(self):
        calls = []

        def hook(creature, name):
            calls.append((creature, name))
            creature.name = f"hooked:{name}"

        group_hooks.register_name_apply(hook)
        cr = _Creature()
        group_hooks.apply_creature_name(cr, "bob")
        assert calls == [(cr, "bob")]
        assert cr.name == "hooked:bob"

    def test_hook_exception_falls_back_to_direct_assignment(self):
        def hook(creature, name):
            raise ValueError("bad name apply")

        group_hooks.register_name_apply(hook)
        cr = _Creature()
        group_hooks.apply_creature_name(cr, "carol")
        # Fell back to the plain assignment.
        assert cr.name == "carol"


# ── list_spawnable ──────────────────────────────────────────


class TestListSpawnable:
    def test_empty_when_unregistered(self):
        assert group_hooks.list_spawnable(None) == []
        assert group_hooks.list_spawnable(object()) == []

    def test_registered_hook_returns_catalog(self):
        ws = object()

        def hook(workspace):
            assert workspace is ws
            return [{"name": "explorer"}, {"name": "writer"}]

        group_hooks.register_spawnable(hook)
        out = group_hooks.list_spawnable(ws)
        assert out == [{"name": "explorer"}, {"name": "writer"}]

    def test_hook_exception_returns_empty_list(self):
        def hook(workspace):
            raise RuntimeError("catalog scan failed")

        group_hooks.register_spawnable(hook)
        assert group_hooks.list_spawnable(object()) == []


# ── resolve_workspace ───────────────────────────────────────


class TestResolveWorkspace:
    def test_none_when_unregistered(self):
        assert group_hooks.resolve_workspace(object(), _Creature()) is None

    def test_registered_hook_returns_handle(self):
        sentinel = object()

        def hook(engine, creature):
            return sentinel

        group_hooks.register_workspace_resolver(hook)
        assert group_hooks.resolve_workspace(object(), _Creature()) is sentinel

    def test_hook_exception_returns_none(self):
        def hook(engine, creature):
            raise KeyError("no workspace")

        group_hooks.register_workspace_resolver(hook)
        assert group_hooks.resolve_workspace(object(), _Creature()) is None

    def test_hook_returning_none_is_passed_through(self):
        def hook(engine, creature):
            return None

        group_hooks.register_workspace_resolver(hook)
        assert group_hooks.resolve_workspace(object(), _Creature()) is None


# ── register_* setters ──────────────────────────────────────


class TestRegisterSetters:
    def test_each_setter_assigns_its_global(self):
        a, b, c, d = (lambda *x, **k: None for _ in range(4))
        group_hooks.register_store_attach(a)
        group_hooks.register_name_apply(b)
        group_hooks.register_spawnable(c)
        group_hooks.register_workspace_resolver(d)
        assert group_hooks._store_attach is a
        assert group_hooks._name_apply is b
        assert group_hooks._spawnable is c
        assert group_hooks._workspace_resolver is d
