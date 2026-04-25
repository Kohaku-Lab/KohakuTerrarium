"""Pin: ``/branch`` slash command lists and switches sibling branches.

The CLI/TUI had no way to navigate the ``<1/N>`` branches that
``/regen`` and ``/edit`` create. This command closes that gap; it is
the parity command for the frontend's chevron navigator.
"""

import pytest

from kohakuterrarium.builtins.user_commands.branch import BranchCommand
from kohakuterrarium.core.conversation import Conversation
from kohakuterrarium.modules.user_command.base import UserCommandContext
from kohakuterrarium.session.store import SessionStore


class _FakeAgentConfig:
    name = "alice"


class _FakeController:
    def __init__(self, conv):
        self.conversation = conv


class _FakeAgent:
    def __init__(self, store: SessionStore):
        self.config = _FakeAgentConfig()
        self.session_store = store
        self.controller = _FakeController(Conversation())
        self._branch_view: dict[int, int] = {}


def _two_branch_session(tmp_path) -> tuple[_FakeAgent, SessionStore]:
    path = tmp_path / "session.kohakutr.v2"
    store = SessionStore(str(path))
    store.init_meta(
        session_id="s",
        config_type="agent",
        config_path="x",
        pwd=str(tmp_path),
        agents=["alice"],
    )
    # Two branches of turn 1.
    for branch_id, content in ((1, "first reply"), (2, "second reply")):
        store.append_event(
            "alice",
            "user_message",
            {"content": "hi"},
            turn_index=1,
            branch_id=branch_id,
        )
        store.append_event(
            "alice",
            "text_chunk",
            {"content": content, "chunk_seq": 0},
            turn_index=1,
            branch_id=branch_id,
        )
    return _FakeAgent(store), store


@pytest.mark.asyncio
async def test_branch_list_shows_multi_branch_turns(tmp_path):
    agent, store = _two_branch_session(tmp_path)
    cmd = BranchCommand()
    ctx = UserCommandContext(agent=agent)
    result = await cmd._execute("", ctx)
    assert result.error is None
    assert "turn 1" in result.output
    assert "[1, 2]" in result.output
    store.close(update_status=False)


@pytest.mark.asyncio
async def test_branch_switch_rebuilds_conversation(tmp_path):
    agent, store = _two_branch_session(tmp_path)
    cmd = BranchCommand()
    ctx = UserCommandContext(agent=agent)
    result = await cmd._execute("1 1", ctx)
    assert result.error is None
    msgs = agent.controller.conversation.get_messages()
    assert any(m.role == "assistant" and "first reply" in m.content for m in msgs)
    assert agent._branch_view == {1: 1}
    store.close(update_status=False)


@pytest.mark.asyncio
async def test_branch_invalid_id_returns_error(tmp_path):
    agent, store = _two_branch_session(tmp_path)
    cmd = BranchCommand()
    ctx = UserCommandContext(agent=agent)
    result = await cmd._execute("1 99", ctx)
    assert result.error is not None
    assert "no branch 99" in result.error
    store.close(update_status=False)


@pytest.mark.asyncio
async def test_branch_latest_resets_view(tmp_path):
    agent, store = _two_branch_session(tmp_path)
    agent._branch_view = {1: 1}
    cmd = BranchCommand()
    ctx = UserCommandContext(agent=agent)
    result = await cmd._execute("latest", ctx)
    assert result.error is None
    assert agent._branch_view == {}
    msgs = agent.controller.conversation.get_messages()
    assert any(m.role == "assistant" and "second reply" in m.content for m in msgs)
    store.close(update_status=False)
