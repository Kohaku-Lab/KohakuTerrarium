from types import SimpleNamespace

from kohakuterrarium.session.output import SessionOutput
from kohakuterrarium.session.store import SessionStore


def test_session_output_persists_interrupted_tool_result(tmp_path):
    store = SessionStore(tmp_path / "session.kohakutr")
    try:
        agent = SimpleNamespace(controller=None, session=None)
        output = SessionOutput("agent", store, agent)

        output.on_activity_with_metadata(
            "tool_start",
            "[bash] command=sleep 10",
            {"job_id": "bash_123", "args": {"command": "sleep 10"}},
        )
        output.on_activity_with_metadata(
            "tool_error",
            "[bash] INTERRUPTED: User manually interrupted this job.",
            {
                "job_id": "bash_123",
                "result": "User manually interrupted this job.",
                "error": "User manually interrupted this job.",
                "interrupted": True,
                "final_state": "interrupted",
            },
        )

        events = store.get_events("agent")
        assert events[0]["type"] == "tool_call"
        assert events[1]["type"] == "tool_result"
        assert events[1]["call_id"] == "bash_123"
        assert events[1]["output"] == "User manually interrupted this job."
        assert events[1]["error"] == "User manually interrupted this job."
        assert events[1]["interrupted"] is True
        assert events[1]["final_state"] == "interrupted"
    finally:
        store.close()


def test_session_output_persists_interrupted_subagent_result(tmp_path):
    store = SessionStore(tmp_path / "session.kohakutr")
    try:
        agent = SimpleNamespace(controller=None, session=None)
        output = SessionOutput("agent", store, agent)

        output.on_activity_with_metadata(
            "subagent_start",
            "[explore] find auth",
            {"job_id": "agent_explore_123", "task": "find auth", "background": False},
        )
        output.on_activity_with_metadata(
            "subagent_error",
            "[explore] INTERRUPTED: User manually interrupted this job.",
            {
                "job_id": "agent_explore_123",
                "result": "User manually interrupted this job.",
                "error": "User manually interrupted this job.",
                "interrupted": True,
                "final_state": "interrupted",
                "tools_used": ["grep"],
                "turns": 2,
                "duration": 1.5,
                "total_tokens": 50,
                "prompt_tokens": 30,
                "completion_tokens": 20,
            },
        )

        events = store.get_events("agent")
        assert events[0]["type"] == "subagent_call"
        assert events[1]["type"] == "subagent_result"
        assert events[1]["job_id"] == "agent_explore_123"
        assert events[1]["output"] == "User manually interrupted this job."
        assert events[1]["error"] == "User manually interrupted this job."
        assert events[1]["interrupted"] is True
        assert events[1]["final_state"] == "interrupted"
        assert events[1]["tools_used"] == ["grep"]
    finally:
        store.close()
