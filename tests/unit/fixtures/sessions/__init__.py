"""Programmatic session fixtures used by migration + resume tests.

These helpers build a realistic v1 ``.kohakutr`` on disk without
committing a binary fixture — keeps the SQLite format free to evolve
with KohakuVault while still exercising the migration code path.
"""

from pathlib import Path

from kohakuterrarium.session.store import SessionStore


def build_v1_basic_session(path: Path, *, agent: str = "alice") -> Path:
    """Create a v1 ``.kohakutr`` file at ``path`` with a realistic history.

    The session mimics what the pre-Wave-D framework would have
    written: a conversation snapshot is the source of truth, events
    exist for audit, ``meta["format_version"]`` is explicitly 1.

    Returns ``path`` for convenience.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    store = SessionStore(path)
    try:
        store.init_meta(
            session_id="v1-fixture",
            config_type="agent",
            config_path="examples/agent-apps/swe_agent",
            pwd=str(path.parent),
            agents=[agent],
        )
        # Force the v1 marker — SessionStore now defaults to v2.
        store.meta["format_version"] = 1

        # Realistic event stream: user input → streamed text → two
        # tool calls → one tool result → another assistant reply.
        store.append_event(
            agent, "user_input", {"content": "read README and list files"}
        )
        store.append_event(agent, "processing_start", {})
        store.append_event(
            agent,
            "text",
            {"content": "Sure, let me inspect the repo."},
        )
        store.append_event(
            agent,
            "tool_call",
            {
                "name": "read",
                "call_id": "tc_readme",
                "args": {"path": "README.md"},
            },
        )
        store.append_event(
            agent,
            "tool_call",
            {
                "name": "glob",
                "call_id": "tc_glob",
                "args": {"pattern": "*.py"},
            },
        )
        store.append_event(
            agent,
            "tool_result",
            {
                "name": "read",
                "call_id": "tc_readme",
                "output": "# Project\nHello world\n",
                "exit_code": 0,
            },
        )
        store.append_event(
            agent,
            "text",
            {"content": "The README says hello world."},
        )
        store.append_event(agent, "processing_end", {})

        # v1 source of truth: the conversation snapshot.
        store.save_conversation(
            agent,
            [
                {"role": "system", "content": "You are a helpful SWE agent."},
                {"role": "user", "content": "read README and list files"},
                {
                    "role": "assistant",
                    "content": "Sure, let me inspect the repo.",
                    "tool_calls": [
                        {
                            "id": "tc_readme",
                            "type": "function",
                            "function": {
                                "name": "read",
                                "arguments": '{"path": "README.md"}',
                            },
                        },
                        {
                            "id": "tc_glob",
                            "type": "function",
                            "function": {
                                "name": "glob",
                                "arguments": '{"pattern": "*.py"}',
                            },
                        },
                    ],
                },
                {
                    "role": "tool",
                    "content": "# Project\nHello world\n",
                    "tool_call_id": "tc_readme",
                    "name": "read",
                },
                {
                    "role": "assistant",
                    "content": "The README says hello world.",
                },
            ],
        )

        # Scratchpad, triggers — realistic auxiliary state.
        store.save_state(
            agent,
            scratchpad={"plan": "inspect repo", "status": "done"},
            turn_count=1,
            token_usage={
                "total_input_tokens": 500,
                "total_output_tokens": 50,
                "total_cached_tokens": 0,
            },
        )
    finally:
        store.close(update_status=False)

    return path
