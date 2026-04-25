"""Sub-agent conversation survival across close + reopen.

Exercises the save path used by ``SubAgent._build_result`` (base.py:492
today). A sub-agent whose ``_session_store`` is wired by the manager
writes both metadata and its conversation; both should survive a
store close + reopen cycle.

The audit also flagged a gap: ``SessionOutput._handle_subagent_done``
records a ``subagent_result`` *event* but never calls
``save_subagent``. If a sub-agent is spawned without a
``SubAgentManager`` handing it a store reference — the
plugin-spawned-child-agent case in the audit — only the event row
survives; the full conversation is lost. That scenario is marked
``xfail`` here so the assertion records the intended behavior even
while the gap is open.
"""

import json
from types import SimpleNamespace

import pytest

from kohakuterrarium.session.output import SessionOutput
from kohakuterrarium.session.store import SessionStore


@pytest.fixture
def session_path(tmp_path):
    """Path to an initialized session file (meta set, store closed)."""
    path = tmp_path / "sub_roundtrip.kohakutr"
    store = SessionStore(path)
    store.init_meta(
        session_id="sub_roundtrip",
        config_type="agent",
        config_path="/tmp",
        pwd=str(tmp_path),
        agents=["parent"],
    )
    store.close()
    return path


def _simulate_subagent_save(
    store: SessionStore,
    parent: str,
    name: str,
    conversation: list[dict],
    tools_used: list[str] | None = None,
) -> int:
    """Mirror ``SubAgent._build_result``'s persistence call.

    Returns the run index used.
    """
    run = store.next_subagent_run(parent, name)
    store.save_subagent(
        parent=parent,
        name=name,
        run=run,
        meta={
            "task": (
                conversation[1].get("content", "") if len(conversation) > 1 else ""
            ),
            "turns": 1,
            "tools_used": tools_used or [],
            "success": True,
            "duration": 0.5,
            "output_preview": "preview",
        },
        conv_json=json.dumps(conversation),
    )
    return run


class TestSubAgentConversationRoundTrip:
    """Happy path: manager wires a store; full conversation persists."""

    def test_conversation_survives_close_and_reopen(self, session_path):
        conversation = [
            {"role": "system", "content": "You are the explore sub-agent."},
            {"role": "user", "content": "Find the token validator."},
            {"role": "assistant", "content": "[tool_call glob pattern='**/auth*.py']"},
            {"role": "user", "content": "[glob] src/auth.py\nsrc/auth_utils.py"},
            {"role": "assistant", "content": "Found it at src/auth.py."},
        ]

        store = SessionStore(session_path)
        run = _simulate_subagent_save(
            store, "parent", "explore", conversation, tools_used=["glob"]
        )
        store.close()

        # Reopen and confirm both meta and conversation survive.
        reopened = SessionStore(session_path)
        try:
            meta = reopened.load_subagent_meta("parent", "explore", run)
            assert meta is not None
            assert meta["turns"] == 1
            assert meta["success"] is True
            assert meta["tools_used"] == ["glob"]

            conv_json = reopened.load_subagent_conversation("parent", "explore", run)
            assert conv_json is not None
            restored = json.loads(conv_json)
            assert restored == conversation
            assert restored[1]["content"] == "Find the token validator."
            assert "Found it" in restored[-1]["content"]
        finally:
            reopened.close()

    def test_multi_run_roundtrip(self, session_path):
        """Several runs of the same sub-agent each round-trip."""
        store = SessionStore(session_path)
        runs = []
        for i in range(3):
            convo = [
                {"role": "system", "content": "You are explore."},
                {"role": "user", "content": f"Query #{i}"},
                {"role": "assistant", "content": f"Answer #{i}"},
            ]
            runs.append(_simulate_subagent_save(store, "parent", "explore", convo))
        store.close()

        reopened = SessionStore(session_path)
        try:
            for i, run in enumerate(runs):
                raw = reopened.load_subagent_conversation("parent", "explore", run)
                assert raw is not None
                convo = json.loads(raw)
                assert convo[1]["content"] == f"Query #{i}"
                assert convo[2]["content"] == f"Answer #{i}"
        finally:
            reopened.close()


class TestSubAgentConversationLossGap:
    """Documents the ``SessionOutput`` gap flagged by the audit.

    ``_handle_subagent_done`` records a ``subagent_result`` event only
    — it never calls ``save_subagent``. A framework path that routes
    purely through ``SessionOutput`` (e.g. a plugin that spawns its own
    Agent and hands it a SessionOutput but not a SubAgentManager loop)
    loses the child conversation once the parent closes the store.
    """

    def test_session_output_alone_persists_child_conversation(self, session_path):
        """When only SessionOutput is wired, the child convo should still land.

        Expected behaviour (post-Wave-C): the ``subagent_done`` activity
        either carries the child conversation or triggers a
        ``save_subagent`` call, so a reopen can reconstruct the child
        conversation keyed under the parent / child name / run.
        """
        store = SessionStore(session_path)
        agent_stub = SimpleNamespace(controller=None, session=None)
        output = SessionOutput("parent", store, agent_stub)

        # Plugin-style flow: the parent's SessionOutput sees a
        # subagent_start + subagent_done pair. The child ran entirely
        # outside the manager so there is no direct store.save_subagent
        # call.
        output.on_activity_with_metadata(
            "subagent_start",
            "[plugin_helper] do memory lookup",
            {"job_id": "ph_1", "task": "do memory lookup"},
        )
        output.on_activity_with_metadata(
            "subagent_done",
            "[plugin_helper] done",
            {
                "job_id": "ph_1",
                "result": "found: token_validator.py",
                "turns": 2,
                "tools_used": ["grep"],
                "duration": 0.2,
                "total_tokens": 123,
                "prompt_tokens": 80,
                "completion_tokens": 43,
            },
        )
        store.close()

        reopened = SessionStore(session_path)
        try:
            # A post-Wave-C implementation should persist the child
            # conversation under (parent, name, run=0).
            conv_json = reopened.load_subagent_conversation(
                "parent", "plugin_helper", 0
            )
            assert (
                conv_json is not None
            ), "SessionOutput dropped the sub-agent conversation"
        finally:
            reopened.close()
