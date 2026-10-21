"""Unit tests for the /branch slash command.

Every test drives the real command against a real :class:`Agent` bound to a real
:class:`SessionStore`. Branch state, compact counters and checkpoints are seeded
through the same store the command reads, so each assertion describes observable
session behavior instead of internal call shapes.
"""

import asyncio
import copy
from types import SimpleNamespace

import pytest

import kohakuterrarium.builtins.user_commands.branch as branch_command
import kohakuterrarium.core.agent_compact as agent_compact
from kohakuterrarium.builtins.user_commands.branch import BranchCommand
from kohakuterrarium.core.agent import Agent
from kohakuterrarium.core.branch_continuation import persist_branch_view
from kohakuterrarium.core.config_types import AgentConfig, InputConfig, OutputConfig
from kohakuterrarium.core.event_inbox import EventEnvelope
from kohakuterrarium.core.events import TriggerEvent
from kohakuterrarium.modules.user_command.base import UserCommandContext
from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.testing.llm import ScriptedLLM

NAME = "alice"
SYSTEM_MARKER = "SYSTEM_PROMPT_MARKER"


@pytest.fixture(autouse=True)
def offline_compact(monkeypatch):
    monkeypatch.setattr(
        agent_compact, "create_llm_from_profile_name", lambda _: ScriptedLLM([])
    )


@pytest.fixture
def store(tmp_path):
    store = SessionStore(str(tmp_path / "branch.kohakutr.v2"))
    store.init_meta(
        session_id="s1",
        config_type="agent",
        config_path="cfg.yaml",
        pwd=str(tmp_path),
        agents=[NAME],
    )
    try:
        yield store
    finally:
        store.close()


def _agent(tmp_path, store):
    config = AgentConfig(
        name=NAME,
        system_prompt=SYSTEM_MARKER,
        include_hints_in_prompt=False,
        agent_path=tmp_path,
        input=InputConfig(type="none"),
        output=OutputConfig(type="stdout"),
    )
    llm = ScriptedLLM(["never-requested"])
    agent = Agent(config, llm=llm)
    agent.session_store = store
    agent._context_cutoff_event_id = 0
    agent._init_compact_manager()
    return agent, llm


def _add(store, kind, turn, branch, content=None, path=(), **extra):
    """Append one persisted event and return its event id."""
    payload = dict(extra)
    if content is not None:
        payload["content"] = content
    _, event_id = store.append_event(
        NAME,
        kind,
        payload,
        turn_index=turn,
        branch_id=branch,
        parent_branch_path=[list(pair) for pair in path] or None,
    )
    return event_id


def _two_branch_session(store):
    """Turn 1 plus two regenerated responses for turn 2."""
    _add(store, "user_message", 1, 1, "U1_INPUT")
    _add(store, "text_chunk", 1, 1, "A1_REPLY")
    _add(store, "user_message", 2, 1, "U2_INPUT", path=((1, 1),))
    _add(store, "text_chunk", 2, 1, "A2_ORIGINAL", path=((1, 1),))
    _add(store, "user_message", 2, 2, "U2_INPUT", path=((1, 1),))
    _add(store, "text_chunk", 2, 2, "A2_SIBLING", path=((1, 1),))


@pytest.fixture
def session(tmp_path, store):
    _two_branch_session(store)
    agent, _ = _agent(tmp_path, store)
    return agent, store


async def _run(agent, args=""):
    return await BranchCommand().execute(args, UserCommandContext(agent=agent))


def _live(agent):
    return [
        (message.role, message.content)
        for message in agent.controller.conversation.get_messages()
        if message.role != "system"
    ]


def _text(agent):
    return "\n".join(
        str(message.content) for message in agent.controller.conversation.get_messages()
    )


def _fingerprint(agent, store):
    """Every piece of state a refused switch must leave untouched."""
    return {
        "messages": agent.controller.conversation.snapshot_messages(),
        "state": copy.deepcopy(dict(store.state)),
        "turn": agent._turn_index,
        "branch": agent._branch_id,
        "parents": list(agent._parent_branch_path),
        "cutoff": agent._context_cutoff_event_id,
        "compact_count": agent.compact_manager._compact_count,
        "compact_time": agent.compact_manager._last_compact_time,
    }


def _checkpoint(store, *, path, watermark, messages, compact_count=1, cutoff=0):
    """Install one persisted branch checkpoint exactly as the compact saver writes it."""
    store.state[f"{NAME}:branch_snapshots"] = [
        {
            "path": [list(pair) for pair in path],
            "messages": messages,
            "watermark": watermark,
            "compact_state": {
                "compact_count": compact_count,
                "last_compact_time": 123.0,
            },
            "context_cutoff_event_id": cutoff,
        }
    ]


def _checkpoint_session(store):
    """Turns 1 and 2 on branch 1 plus a regenerated turn 2 on branch 2."""
    alpha = _add(store, "user_message", 1, 1, "RAW_ALPHA_MARKER")
    alpha_reply = _add(store, "text_chunk", 1, 1, "ALPHA_REPLY")
    _add(store, "user_message", 2, 1, "RAW_BETA_ORIGINAL", path=((1, 1),))
    _add(store, "text_chunk", 2, 1, "BETA_ORIGINAL_REPLY", path=((1, 1),))
    _add(store, "user_message", 2, 2, "BETA_SIBLING_INPUT", path=((1, 1),))
    _add(store, "text_chunk", 2, 2, "BETA_SIBLING_REPLY", path=((1, 1),))
    return alpha, alpha_reply


async def test_switch_installs_selected_branch_and_persists_it(session):
    agent, store = session
    conversation = agent.controller.conversation
    event_count = len(store.get_events(NAME))

    result = await _run(agent, "2 2")

    assert result.error is None
    assert result.output == "Switched turn 2 → branch 2 (4 messages live)."
    assert _live(agent) == [
        ("user", "U1_INPUT"),
        ("assistant", "A1_REPLY"),
        ("user", "U2_INPUT"),
        ("assistant", "A2_SIBLING"),
    ]
    # The live conversation object, its config and the system prompts survive.
    assert agent.controller.conversation is conversation
    assert [m.content for m in conversation.get_messages() if m.role == "system"]
    assert conversation.get_messages()[0].role == "system"
    assert SYSTEM_MARKER in conversation.get_messages()[0].content
    # Turn identity survives the install so later edit targeting stays exact.
    users = [m.metadata for m in conversation.get_messages() if m.role == "user"]
    assert [(meta["turn_index"], meta["branch_id"]) for meta in users] == [
        (1, 1),
        (2, 2),
    ]
    assert (agent._turn_index, agent._branch_id) == (2, 2)
    assert agent._parent_branch_path == [(1, 1)]
    assert store.state[f"{NAME}:branch_view"] == {"1": 1, "2": 2}
    # No branch-view side channel and no generated turn.
    assert getattr(agent, "_branch_view", None) is None
    assert len(store.get_events(NAME)) == event_count
    assert agent.llm.call_count == 0
    assert agent.executor.get_running_jobs() == []
    # The persisted snapshot is the admitted conversation, byte for byte.
    records = store.state[f"{NAME}:branch_snapshots"]
    assert records[-1]["messages"] == conversation.snapshot_messages()
    assert records[-1]["path"] == [[1, 1], [2, 2]]


async def test_switch_between_recorded_branches(session):
    agent, store = session

    original = await _run(agent, "2 1")

    assert original.error is None
    assert original.output == "Switched turn 2 → branch 1 (4 messages live)."
    assert store.state[f"{NAME}:branch_view"] == {"1": 1, "2": 1}
    assert _live(agent)[-1] == ("assistant", "A2_ORIGINAL")

    space = await _run(agent, "2 2")

    assert space.error is None
    assert store.state[f"{NAME}:branch_view"] == {"1": 1, "2": 2}
    assert _live(agent)[-1] == ("assistant", "A2_SIBLING")


async def test_latest_resolves_one_coherent_subtree(tmp_path, store):
    _add(store, "user_message", 1, 1, "ANCESTOR_ORIGINAL")
    _add(store, "text_chunk", 1, 1, "ANCESTOR_REPLY")
    _add(store, "user_message", 2, 1, "CHILD_OF_ORIGINAL", path=((1, 1),))
    _add(store, "text_chunk", 2, 1, "CHILD_OF_ORIGINAL_REPLY", path=((1, 1),))
    _add(store, "user_message", 1, 2, "ANCESTOR_EDITED")
    _add(store, "text_chunk", 1, 2, "ANCESTOR_EDITED_REPLY")
    _add(store, "user_message", 2, 2, "CHILD_OF_EDITED", path=((1, 2),))
    _add(store, "text_chunk", 2, 2, "CHILD_OF_EDITED_REPLY", path=((1, 2),))
    _add(store, "user_message", 2, 3, "STALE_SIBLING", path=((1, 1),))
    _add(store, "text_chunk", 2, 3, "STALE_SIBLING_REPLY", path=((1, 1),))
    agent, _ = _agent(tmp_path, store)

    latest = await _run(agent, "latest")

    assert latest.error is None
    assert (
        latest.output == "Switched to the latest compatible branches (4 messages live)."
    )
    # The edited turn-1 branch wins, so turn 2 cannot pick its own maximum.
    assert _live(agent) == [
        ("user", "ANCESTOR_EDITED"),
        ("assistant", "ANCESTOR_EDITED_REPLY"),
        ("user", "CHILD_OF_EDITED"),
        ("assistant", "CHILD_OF_EDITED_REPLY"),
    ]
    assert store.state[f"{NAME}:branch_view"] == {"1": 2, "2": 2}


async def test_explicit_request_does_not_merge_stale_descendants(tmp_path, store):
    _add(store, "user_message", 1, 1, "ROOT_INPUT")
    _add(store, "user_message", 2, 1, "BRANCH_ONE_INPUT", path=((1, 1),))
    _add(store, "user_message", 2, 2, "BRANCH_TWO_INPUT", path=((1, 1),))
    _add(store, "user_message", 3, 1, "DESC_OF_BRANCH_ONE", path=((1, 1), (2, 1)))
    _add(store, "user_message", 3, 2, "DESC_OF_BRANCH_TWO", path=((1, 1), (2, 2)))
    agent, _ = _agent(tmp_path, store)
    stale = {1: 1, 2: 2, 3: 2}
    persist_branch_view(store, NAME, stale)
    agent._turn_index, agent._branch_id = 3, 2
    agent._parent_branch_path = [(1, 1), (2, 2)]

    result = await _run(agent, "2 1")

    assert result.error is None
    assert [content for _, content in _live(agent)] == [
        "ROOT_INPUT",
        "BRANCH_ONE_INPUT",
        "DESC_OF_BRANCH_ONE",
    ]
    assert store.state[f"{NAME}:branch_view"] == {"1": 1, "2": 1, "3": 1}
    assert (agent._turn_index, agent._branch_id) == (3, 1)
    assert agent._parent_branch_path == [(1, 1), (2, 1)]


async def test_listing_reports_the_selected_branch(session):
    agent, store = session
    agent._turn_index, agent._branch_id = 2, 1
    agent._parent_branch_path = [(1, 1)]
    before = _fingerprint(agent, store)

    listing = await _run(agent)

    assert listing.error is None
    assert "turn 2 regen 1..2: branches [1, 2] (selected: 1)" in listing.output
    assert _fingerprint(agent, store) == before

    persist_branch_view(store, NAME, {1: 1, 2: 2})

    assert "(selected: 2)" in (await _run(agent)).output


async def test_listing_and_latest_handle_an_empty_session(tmp_path, store):
    agent, _ = _agent(tmp_path, store)

    listing = await _run(agent)

    assert listing.error is None
    assert listing.output == "No branches recorded yet."

    latest = await _run(agent, "latest")

    assert latest.error is None
    assert (
        latest.output == "Switched to the latest compatible branches (0 messages live)."
    )
    assert _live(agent) == []
    assert f"{NAME}:branch_view" not in store.state


async def test_missing_agent_or_store_reports_the_existing_error():
    result = await BranchCommand().execute("", UserCommandContext(agent=None))

    assert result.error == "No agent / session store in context."


@pytest.mark.parametrize("cutoff", [0, 2])
async def test_checkpoint_summary_replaces_the_raw_compacted_payload(
    tmp_path, store, cutoff
):
    _, alpha_reply = _checkpoint_session(store)
    _checkpoint(
        store,
        path=[(1, 1)],
        watermark=alpha_reply,
        messages=[
            {
                "role": "assistant",
                "content": "SUMMARY_UNIQUE_MARKER",
                "metadata": {
                    "compact_summary": True,
                    "compact_source_path": [[1, 1]],
                },
            }
        ],
        compact_count=1,
        cutoff=cutoff,
    )
    agent, _ = _agent(tmp_path, store)

    result = await _run(agent, "2 2")

    assert result.error is None
    text = _text(agent)
    assert "SUMMARY_UNIQUE_MARKER" in text
    assert "RAW_ALPHA_MARKER" not in text
    assert "RAW_BETA_ORIGINAL" not in text
    assert "BETA_SIBLING_REPLY" in text
    assert [m.content for m in agent.controller.conversation.get_messages()][
        0
    ].startswith(SYSTEM_MARKER)
    # The checkpoint's own compact state and cutoff are installed verbatim.
    assert agent.compact_manager._compact_count == 1
    assert agent.compact_manager._last_compact_time == 123.0
    assert agent._context_cutoff_event_id == cutoff
    saved = store.state[f"{NAME}:branch_snapshots"][-1]
    assert saved["compact_state"] == {"compact_count": 1, "last_compact_time": 123.0}
    assert saved["messages"] == agent.controller.conversation.snapshot_messages()


async def test_checkpoint_message_dictionaries_roundtrip_exactly(tmp_path, store):
    _, alpha_reply = _checkpoint_session(store)
    checkpoint_messages = [
        {
            "role": "user",
            "content": "RAW_ALPHA_MARKER",
            "metadata": {"turn_index": 1, "branch_id": 1, "event_id": 1},
        },
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": "call-1", "name": "echo", "arguments": {"msg": "hi"}}
            ],
            "metadata": {"turn_index": 1, "branch_id": 1},
        },
        {
            "role": "tool",
            "content": "TOOL_OUTPUT_MARKER",
            "tool_call_id": "call-1",
            "name": "echo",
            "metadata": {"turn_index": 1, "branch_id": 1},
            "provider_extra_marker": {"nested": [1, 2]},
        },
        {
            "role": "assistant",
            "content": "SUMMARY_UNIQUE_MARKER",
            "metadata": {"compact_summary": True, "compact_source_path": [[1, 1]]},
        },
    ]
    _checkpoint(
        store, path=[(1, 1)], watermark=alpha_reply, messages=checkpoint_messages
    )
    agent, _ = _agent(tmp_path, store)

    result = await _run(agent, "2 2")

    assert result.error is None
    installed = agent.controller.conversation.snapshot_messages()
    system_count = sum(1 for message in installed if message["role"] == "system")
    assert installed[system_count : system_count + 4] == checkpoint_messages
    assert [message["role"] for message in installed[system_count + 4 :]] == [
        "user",
        "assistant",
    ]


async def test_persistence_failure_does_not_install_candidate(session, monkeypatch):
    agent, store = session
    before = _fingerprint(agent, store)

    def fail_persist(*args):
        raise OSError("branch view write failed")

    monkeypatch.setattr(branch_command, "persist_branch_view", fail_persist)
    result = await _run(agent, "2 1")
    assert result.error == "branch view write failed"
    assert _fingerprint(agent, store) == before


async def test_snapshot_failure_reports_the_completed_switch(session, monkeypatch):
    agent, store = session

    def fail_snapshot(*args):
        raise OSError("disk unavailable")

    monkeypatch.setattr(branch_command, "save_agent_snapshot", fail_snapshot)
    result = await _run(agent, "2 2")
    assert result.error == (
        "Branch switched, but saving its checkpoint failed: disk unavailable"
    )
    assert _live(agent)[-1] == ("assistant", "A2_SIBLING")
    assert (agent._turn_index, agent._branch_id) == (2, 2)
    assert agent._parent_branch_path == [(1, 1)]
    assert store.state[f"{NAME}:branch_view"] == {"1": 1, "2": 2}
    assert agent.llm.call_count == 0


async def test_drain_rechecks_pending_input_before_install(session):
    agent, store = session
    before = _fingerprint(agent, store)

    async def drain():
        agent._event_inbox.put(
            EventEnvelope(TriggerEvent(type="user_input", content="queued"))
        )

    agent._session_output = SimpleNamespace(drain=drain)
    result = await _run(agent, "2 1")
    assert "turn is active" in result.error
    assert _fingerprint(agent, store) == before


async def test_processing_turn_refuses_switch(session):
    agent, store = session
    before = _fingerprint(agent, store)
    async with agent._processing_lock:
        result = await asyncio.wait_for(_run(agent, "2 1"), timeout=1)
    assert "turn is active" in result.error
    assert _fingerprint(agent, store) == before


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        ("2", "Usage:"),
        ("invalid 2", "must be integers"),
        ("2 1 extra", "Usage:"),
        ("latest extra", "must be integers"),
        ("0 1", "must be positive"),
        ("2 -1", "must be positive"),
    ],
)
async def test_invalid_arguments_do_not_mutate(session, args, expected):
    agent, store = session
    before = _fingerprint(agent, store)
    result = await _run(agent, args)
    assert expected in (result.error or "")
    assert _fingerprint(agent, store) == before


def _refuse_retention(agent, store):
    agent.controller.conversation.config.max_messages = 2


def _refuse_budget(agent, store):
    agent.compact_manager.config.max_tokens = 5


def _refuse_compacting(agent, store):
    agent.compact_manager._dispatch._running = True


def _refuse_running_job(agent, store):
    agent._active_handles["bg-1"] = SimpleNamespace(promoted=True)


@pytest.mark.parametrize(
    ("break_state", "args", "expected"),
    [
        (None, "5 1", "branch 1 does not exist for turn 5"),
        (None, "2 9", "branch 9 does not exist for turn 2"),
        (
            _refuse_retention,
            "2 2",
            "discard prefix messages under the current retention limit",
        ),
        (_refuse_budget, "2 2", "exceeds the configured context budget"),
        (
            _refuse_compacting,
            "2 2",
            "Cannot change conversation history while compaction is running",
        ),
        (
            _refuse_running_job,
            "2 2",
            "Cannot continue a selected branch while a tool or background job "
            "is running",
        ),
    ],
    ids=[
        "missing-turn",
        "missing-branch",
        "retention",
        "budget",
        "compacting",
        "running-job",
    ],
)
async def test_refusal_leaves_every_piece_of_state_untouched(
    session, break_state, args, expected
):
    agent, store = session
    if break_state is not None:
        break_state(agent, store)
    before = _fingerprint(agent, store)
    event_count = len(store.get_events(NAME))

    result = await _run(agent, args)

    assert expected in (result.error or "")
    assert _fingerprint(agent, store) == before
    assert len(store.get_events(NAME)) == event_count
    assert agent.llm.call_count == 0
