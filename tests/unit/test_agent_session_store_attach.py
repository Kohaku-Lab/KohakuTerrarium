from types import SimpleNamespace

from kohakuterrarium.core.agent import Agent


class _Router:
    def __init__(self):
        self._secondary_outputs = []
        self.removed = []

    def add_secondary(self, output):
        self._secondary_outputs.append(output)

    def remove_secondary(self, output):
        self.removed.append(output)
        self._secondary_outputs = [
            o for o in self._secondary_outputs if o is not output
        ]


class _State:
    def get(self, key):
        return None


class _Store:
    def __init__(self):
        self.state = _State()


class _NativeToolOptions:
    def apply(self):
        pass


def _make_agent_like():
    return SimpleNamespace(
        config=SimpleNamespace(name="alice"),
        controller=SimpleNamespace(session_store=None),
        output_router=_Router(),
        _session_output=None,
        subagent_manager=SimpleNamespace(_session_store=None, _parent_name=None),
        trigger_manager=SimpleNamespace(_session_store=None, _agent_name=None),
        compact_manager=None,
        native_tool_options=_NativeToolOptions(),
        session=SimpleNamespace(scratchpad=None),
        plugins=None,
    )


def test_attach_session_store_is_idempotent_for_same_store():
    agent = _make_agent_like()
    store = _Store()

    Agent.attach_session_store(agent, store)
    Agent.attach_session_store(agent, store)

    assert agent.session_store is store
    assert agent.controller.session_store is store
    assert len(agent.output_router._secondary_outputs) == 1
    assert agent._session_output is agent.output_router._secondary_outputs[0]
    assert agent.output_router.removed == []


def test_attach_session_store_replaces_previous_store_sink():
    agent = _make_agent_like()
    first_store = _Store()
    second_store = _Store()

    Agent.attach_session_store(agent, first_store)
    first_output = agent._session_output
    Agent.attach_session_store(agent, second_store)

    assert agent.session_store is second_store
    assert agent.controller.session_store is second_store
    assert len(agent.output_router._secondary_outputs) == 1
    assert agent._session_output is not first_output
    assert agent.output_router._secondary_outputs[0] is agent._session_output
    assert agent.output_router.removed == [first_output]
