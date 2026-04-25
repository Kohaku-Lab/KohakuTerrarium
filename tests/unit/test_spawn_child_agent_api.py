"""Wave F — ``PluginContext.spawn_child_agent`` sugar.

Covers the plugin-facing convenience wrapper that builds an Agent and
attaches it to the host session. We stub out the Agent constructor so
the test does not depend on disk config files; the behavior under
test is wiring + role labeling, not Agent build itself.
"""

from types import SimpleNamespace

import pytest

from kohakuterrarium.modules.output.router import OutputRouter
from kohakuterrarium.modules.plugin.base import PluginContext
from kohakuterrarium.session.attach import get_attach_state
from kohakuterrarium.session.store import SessionStore


class _StubOutput:
    async def start(self):
        pass

    async def stop(self):
        pass

    async def write(self, text):
        pass

    async def write_stream(self, chunk):
        pass

    async def flush(self):
        pass

    async def on_processing_start(self):
        pass

    async def on_processing_end(self):
        pass

    def on_activity(self, activity_type, detail):
        pass


def _fake_agent(name: str):
    """Duck-typed Agent surface the attach primitive needs."""
    router = OutputRouter(default_output=_StubOutput(), named_outputs={})
    return SimpleNamespace(
        config=SimpleNamespace(name=name),
        output_router=router,
        session_store=None,
    )


@pytest.fixture
def host_store(tmp_path):
    path = tmp_path / "spawn_host.kohakutr.v2"
    store = SessionStore(path)
    store.init_meta(
        session_id="spawn_host",
        config_type="agent",
        config_path="/tmp",
        pwd=str(tmp_path),
        agents=["host"],
    )
    yield store
    try:
        store.close(update_status=False)
    except Exception:
        pass


@pytest.fixture
def host_agent(host_store):
    agent = _fake_agent("host")
    agent.session_store = host_store
    return agent


@pytest.fixture
def patched_from_path(monkeypatch):
    """Replace ``Agent.from_path`` with a stub returning a duck-typed agent.

    ``PluginContext.spawn_child_agent`` calls ``Agent.from_path`` for
    the ``str`` branch; we intercept it so the test runs without a
    real config tree.
    """
    import kohakuterrarium.core.agent as agent_mod

    created: list[SimpleNamespace] = []

    def _stub_from_path(path, **kwargs):
        # Path-derived name so multiple spawns can be distinguished.
        name = (
            path.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
            if isinstance(path, str)
            else "child"
        )
        agent = _fake_agent(name or "child")
        created.append(agent)
        return agent

    monkeypatch.setattr(agent_mod.Agent, "from_path", _stub_from_path)
    return created


# ---------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------


def test_spawn_child_agent_attaches_under_plugin_role(
    host_agent, host_store, patched_from_path
):
    ctx = PluginContext(
        agent_name="host",
        _host_agent=host_agent,
        _plugin_name="memory",
    )
    child = ctx.spawn_child_agent("/configs/helper", role="reader")

    state = get_attach_state(child)
    assert state is not None
    assert state["host"] == "host"
    # Plugin role carries the plugin name + sub-role.
    assert state["role"] == "plugin:memory/reader"
    assert state["attach_seq"] == 0
    assert state["prefix"] == "host:attached:plugin:memory/reader:0"

    host_events = host_store.get_events("host")
    attached = [e for e in host_events if e["type"] == "agent_attached"]
    assert len(attached) == 1
    assert attached[0]["role"] == "plugin:memory/reader"
    assert attached[0]["attached_by"] == "plugin:memory"


def test_spawn_child_agent_default_role(host_agent, host_store, patched_from_path):
    ctx = PluginContext(_host_agent=host_agent, _plugin_name="memory")
    child = ctx.spawn_child_agent("/configs/helper")

    state = get_attach_state(child)
    assert state["role"] == "plugin:memory/child"


def test_spawn_child_agent_events_reach_host_store(
    host_agent, host_store, patched_from_path
):
    ctx = PluginContext(_host_agent=host_agent, _plugin_name="memory")
    child = ctx.spawn_child_agent("/configs/helper", role="reader")

    child.output_router.notify_activity(
        "tool_start",
        "[read] child reads",
        metadata={"job_id": "r1", "args": {}},
    )
    host_store.flush()
    attached_events = host_store.get_events("host:attached:plugin:memory/reader:0")
    assert any(e.get("call_id") == "r1" for e in attached_events)


def test_spawn_child_agent_without_store_raises(patched_from_path):
    host = _fake_agent("host")  # no session_store attached
    ctx = PluginContext(_host_agent=host, _plugin_name="memory")
    with pytest.raises(RuntimeError, match="SessionStore"):
        ctx.spawn_child_agent("/configs/helper", role="reader")


def test_spawn_child_agent_without_host_raises(patched_from_path):
    ctx = PluginContext(_plugin_name="memory")
    with pytest.raises(RuntimeError, match="host agent"):
        ctx.spawn_child_agent("/configs/helper", role="reader")


def test_spawn_child_agent_bad_type_raises(host_agent, patched_from_path):
    ctx = PluginContext(_host_agent=host_agent, _plugin_name="memory")
    with pytest.raises(TypeError):
        ctx.spawn_child_agent(42, role="reader")  # not a str / dict
