"""Clean continuation point publication and conservative invalidation."""

from contextlib import closing
from types import SimpleNamespace

import pytest

from kohakuterrarium.core.config.types import AgentConfig
from kohakuterrarium.core.jobs.executor import Executor
from kohakuterrarium.core.jobs.job import JobState, JobStatus, JobType
from kohakuterrarium.modules.plugin.manager.manager import PluginManager
from kohakuterrarium.session.output.output import SessionOutput
from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.session.store.records.resume_point import (
    POINT_KEY,
    stage_point,
    text_messages,
)

MESSAGES = [
    {"role": "user", "content": "question"},
    {"role": "assistant", "content": "answer"},
]
BRANCH = {"turn_index": 1, "branch_id": 1, "parent_branch_path": []}
COMPACT = {"compact_count": 0, "last_compact_time": 0.0}


def staged_store(path):
    store = SessionStore(path)
    store.init_meta("point", "agent", "/unused", str(path.parent), ["plain"])
    assert store.max_branch_id("plain", 1) == 0
    store.append_event(
        "plain",
        "user_message",
        {"content": "question"},
        turn_index=1,
        branch_id=1,
        parent_branch_path=[],
    )
    store.append_event(
        "plain",
        "assistant_message",
        {"content": "answer"},
        turn_index=1,
        branch_id=1,
        parent_branch_path=[],
    )
    store.flush()
    store.save_branch_snapshot(
        "plain",
        MESSAGES,
        branch=BRANCH,
        watermark=store.global_max_event_id,
        compact_state=COMPACT,
    )
    stage_point(store, "plain", MESSAGES, BRANCH, COMPACT)
    assert store._resume_candidate is not None
    return store


@pytest.mark.parametrize(
    "operation", [None, "save_branch_snapshot", "save_state", "flush"]
)
def test_snapshot_publication_requires_success(tmp_path, monkeypatch, operation):
    path = tmp_path / "failed.kohakutr"
    store = staged_store(path)
    try:
        output = SessionOutput(
            "plain",
            store,
            SimpleNamespace(
                config=AgentConfig(name="plain"),
                plugins=PluginManager(),
                executor=Executor(),
                has_pending_mid_turn_inputs=False,
            ),
        )

        def fail_save(*args, **kwargs):
            raise OSError("snapshot write failed")

        if operation is not None:
            monkeypatch.setattr(store, operation, fail_save)
        output._persist_turn_snapshot(
            [{"role": "user", "content": "different unsaved context"}],
            BRANCH,
            {"turn_count": 1},
            COMPACT,
        )
        assert (store._resume_candidate is not None) == (operation is None)
    finally:
        monkeypatch.undo()
        store.close(update_status=False)
    with closing(SessionStore.open_readonly(path)) as check:
        assert (check.state.get(POINT_KEY) is not None) == (operation is None)


@pytest.mark.parametrize(
    "runtime", ["plugin", "running_job", "pending_job", "undelivered_result"]
)
def test_unsupported_runtime_does_not_publish(tmp_path, runtime):
    store = staged_store(tmp_path / "runtime.kohakutr")
    plugins = PluginManager()
    executor = Executor()
    if runtime == "plugin":
        plugins.register(SimpleNamespace(name="custom-runtime", priority=50))
    else:
        executor.job_store.register(
            JobStatus(
                job_id="unfinished",
                type_name="background",
                job_type=JobType.TOOL,
                state={
                    "running_job": JobState.RUNNING,
                    "pending_job": JobState.PENDING,
                    "undelivered_result": JobState.DONE,
                }[runtime],
            )
        )
    agent = SimpleNamespace(
        config=AgentConfig(name="plain"),
        plugins=plugins,
        executor=executor,
        has_pending_mid_turn_inputs=runtime == "undelivered_result",
    )
    output = SessionOutput("plain", store, agent)
    try:
        output._persist_turn_snapshot(MESSAGES, BRANCH, {}, COMPACT)
        assert store._resume_candidate is None
    finally:
        store.close(update_status=False)


@pytest.mark.parametrize("after_snapshot", [False, True])
@pytest.mark.parametrize(
    "failure", ["processing_error", "activity:interrupt", "record_failure"]
)
async def test_failed_or_interrupted_turn_cannot_be_certified(
    tmp_path, monkeypatch, failure, after_snapshot
):
    store = staged_store(tmp_path / "failed-turn.kohakutr")
    agent = SimpleNamespace(
        config=AgentConfig(name="plain"),
        plugins=PluginManager(),
        executor=Executor(),
        has_pending_mid_turn_inputs=False,
        _turn_index=1,
        _branch_id=1,
        _parent_branch_path=[],
    )
    output = SessionOutput("plain", store, agent)
    try:
        if after_snapshot:
            output._persist_turn_snapshot(MESSAGES, BRANCH, {}, COMPACT)
            assert store._resume_candidate is not None
        if failure == "record_failure":

            def fail_append(*args, **kwargs):
                raise OSError("event write failed")

            monkeypatch.setattr(store, "append_event", fail_append)
        output._record(failure, {})
        await output.drain()
        monkeypatch.undo()
        if not after_snapshot:
            output._persist_turn_snapshot(MESSAGES, BRANCH, {}, COMPACT)
        assert store._resume_candidate is None
    finally:
        monkeypatch.undo()
        store.close(update_status=False)


@pytest.mark.parametrize("kind", ["ordinary", "low_id", "other_namespace"])
def test_semantic_tail_invalidates_even_when_logical_max_does_not_change(
    tmp_path, kind
):
    path = tmp_path / "tail.kohakutr"
    store = staged_store(path)
    try:
        maximum = store.global_max_event_id
        store.append_event(
            "other" if kind == "other_namespace" else "plain",
            "tool_result",
            {"output": "late", **({"event_id": 1} if kind == "low_id" else {})},
            turn_index=1,
            branch_id=1,
        )
        assert store._resume_candidate is None
        if kind == "low_id":
            assert store.global_max_event_id == maximum
    finally:
        store.close(update_status=False)
    with closing(SessionStore.open_readonly(path)) as check:
        assert check.state.get(POINT_KEY) is None


def test_readonly_inspection_preserves_point_and_writer_open_consumes_it(tmp_path):
    path = tmp_path / "consumed.kohakutr"
    store = staged_store(path)
    store.close(update_status=False)
    with closing(SessionStore.open_readonly(path)) as reader:
        point = reader.state[POINT_KEY]
        assert point["path"] == [[1, 1]]
    writer = SessionStore(path)
    try:
        assert writer._resume_point == point
        assert writer.state.get(POINT_KEY) is None
        writer.events["plain:e000000"] = {
            "type": "user_message",
            "content": "raw mutation",
        }
    finally:
        writer.close(update_status=False)
    with closing(SessionStore.open_readonly(path)) as reader:
        assert reader.state.get(POINT_KEY) is None


@pytest.mark.parametrize("reset", ["index", "counters"])
def test_raw_history_reset_prevents_staged_publication(tmp_path, reset):
    path = tmp_path / "reset.kohakutr"
    store = staged_store(path)
    store.events["plain:e000000"] = {
        "type": "user_message",
        "content": "rewritten",
        "event_id": 1,
        "turn_index": 2,
        "branch_id": 7,
    }
    if reset == "index":
        store._branch_maxima.clear()
    else:
        store._restore_event_counters()
    store.close(update_status=False)
    with closing(SessionStore.open_readonly(path)) as reader:
        assert reader.state.get(POINT_KEY) is None


def test_hook_timing_is_non_conversation_tail(tmp_path):
    path = tmp_path / "timing.kohakutr"
    store = staged_store(path)
    old_watermark = store._resume_candidate["watermark"]
    store.append_event(
        "plain", "plugin_hook_timing", {"hook": "on_stop"}, turn_index=1, branch_id=1
    )
    assert store._resume_candidate["watermark"] == old_watermark
    assert store._resume_candidate["closed_event_id"] > old_watermark
    store.close(update_status=False)
    with closing(SessionStore.open_readonly(path)) as reader:
        assert reader.state[POINT_KEY]["watermark"] == old_watermark


def test_timing_on_future_turn_invalidates_branch_bound(tmp_path):
    store = staged_store(tmp_path / "future.kohakutr")
    try:
        store.append_event(
            "plain", "plugin_hook_timing", {"hook": "late"}, turn_index=2, branch_id=7
        )
        assert store._resume_candidate is None
        assert store.max_branch_id("plain", 2) == 7
    finally:
        store.close(update_status=False)


@pytest.mark.parametrize("failure", ["flush", "counter"])
def test_close_failure_does_not_publish_point(tmp_path, monkeypatch, failure):
    from_module = "kohakuterrarium.session.store.lifecycle.handle.persist_event_counter"
    path = tmp_path / "close-failure.kohakutr"
    store = staged_store(path)
    if failure == "flush":

        def fail_close():
            raise OSError("final flush failed")

        monkeypatch.setattr(store._flush, "close", fail_close)
    else:
        monkeypatch.setattr(from_module, lambda *args: False)
    store.close(update_status=False)
    with closing(SessionStore.open_readonly(path)) as reader:
        assert reader.state.get(POINT_KEY) is None


def test_native_tool_pair_is_preserved_and_unsettled_or_duplicate_calls_rejected():
    call = {
        "id": "call-1",
        "type": "function",
        "function": {"name": "echo", "arguments": "{}"},
    }
    assistant = {"role": "assistant", "content": None, "tool_calls": [call]}
    result = {"role": "tool", "content": "saved output", "tool_call_id": "call-1"}
    assert text_messages([assistant, result])
    assert not text_messages([assistant])
    assert not text_messages([assistant, result, assistant])


@pytest.mark.parametrize(
    "unsupported", ["compact", "cutoff", "empty_path", "unknown_upper"]
)
def test_staging_rejects_unsupported_checkpoint(tmp_path, unsupported):
    store = staged_store(tmp_path / "unsupported.kohakutr")
    branch, compact = dict(BRANCH), dict(COMPACT)
    if unsupported == "compact":
        compact["compact_count"] = 1
    elif unsupported == "cutoff":
        branch["context_cutoff_event_id"] = 1
    elif unsupported == "empty_path":
        branch = {}
    else:
        store._branch_maxima.clear()
    try:
        stage_point(store, "plain", MESSAGES, branch, compact)
        assert store._resume_candidate is None
    finally:
        store.close(update_status=False)


@pytest.mark.parametrize(
    "unsupported", ["multi_namespace", "graph", "open_text", "triggers", "snapshot_id"]
)
def test_close_does_not_publish_unsupported_final_state(tmp_path, unsupported):
    path = tmp_path / "unsupported-close.kohakutr"
    store = staged_store(path)
    if unsupported == "multi_namespace":
        store._event_seq["other"] = 1
        store._resume_candidate["event_seq"] = dict(store._event_seq)
    elif unsupported == "graph":
        store.meta["config_type"] = "terrarium"
    elif unsupported == "open_text":
        store.state["plain:open_text"] = "unfinished"
    elif unsupported == "triggers":
        store.state["plain:triggers"] = [{"type": "timer"}]
    else:
        store.state["plain:snapshot_event_id"] = 0
    store.close(update_status=False)
    with closing(SessionStore.open_readonly(path)) as reader:
        assert reader.state.get(POINT_KEY) is None


@pytest.mark.parametrize(
    "message",
    [
        None,
        {"role": "unknown", "content": "unsupported"},
        {"role": "assistant", "content": "reasoned", "reasoning_content": "chain"},
        {"role": "user", "content": "not assistant", "tool_calls": [{"id": "x"}]},
        {"role": "assistant", "content": None, "tool_calls": [1]},
        {"role": "assistant", "content": None, "tool_calls": [{"id": ""}]},
        {"role": "assistant", "content": [{"type": "text", "text": "structured"}]},
        {"role": "tool", "content": "unpaired", "tool_call_id": "missing"},
        {"role": "assistant", "content": "malformed", "tool_calls": 1},
        {
            "role": "assistant",
            "content": "pending",
            "metadata": {"tool_result_phase": "pending"},
        },
        {
            "role": "assistant",
            "content": "[Previous context summary (compact round 1)] summary",
        },
    ],
)
def test_unsupported_messages_are_not_certified(message):
    assert not text_messages([message])


def test_empty_or_missing_messages_are_not_certified():
    assert not text_messages([])
    assert not text_messages(None)
