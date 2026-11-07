"""Clean checkpoint selection uses persisted state and rejects unsupported cases."""

from contextlib import closing
from types import SimpleNamespace

import pytest

from kohakuterrarium.builtins.outputs.none import NoneOutput
from kohakuterrarium.builtins.outputs.stdout import StdoutOutput
from kohakuterrarium.core.config.types import AgentConfig
from kohakuterrarium.modules.output.router.router import OutputRouter
from kohakuterrarium.session.resume.checkpoint import prepare_clean_checkpoint
from kohakuterrarium.session.store import SessionStore
from tests.unit.session.store.records.test_resume_point import staged_store


@pytest.fixture
def clean_point(tmp_path):
    path = tmp_path / "point.kohakutr"
    store = staged_store(path)
    store.close(update_status=False)
    with closing(SessionStore(path)) as reopened:
        agent = SimpleNamespace(
            config=AgentConfig(name="plain"), output_router=OutputRouter(NoneOutput())
        )
        yield agent, reopened


def test_selects_and_seeds_new_turn_without_reading_history(clean_point, monkeypatch):
    agent, store = clean_point

    def forbidden(*args, **kwargs):
        raise AssertionError("historical body read")

    monkeypatch.setattr(store, "get_events", forbidden)
    prepared = prepare_clean_checkpoint(agent, store, "plain")
    assert prepared["messages"] == [
        {"role": "user", "content": "question"},
        {"role": "assistant", "content": "answer"},
    ]
    assert prepared["selected"] == {1: 1}
    assert store.max_branch_id("plain", 2) == 0


@pytest.mark.parametrize(
    "kind",
    [
        "missing",
        "unsupported_version",
        "bool_version",
        "namespace",
        "sequence",
        "watermark",
        "close_id",
        "invalid_watermark_type",
        "dirty_counter",
        "extra_namespace",
        "max_turn",
        "path",
        "branch_view",
        "invalid_branch_view",
        "snapshot_branch",
        "compact",
        "cutoff",
        "pending",
        "bad_calls",
        "open_text",
        "triggers",
        "graph",
        "two_agents",
        "output",
        "secondary_output",
        "named_output",
        "plugin",
    ],
)
def test_rejects_unsupported_state_before_installing(clean_point, kind):
    agent, store = clean_point
    point = store._resume_point
    match kind:
        case "missing":
            store._resume_point = None
        case "unsupported_version":
            point["version"] = 99
        case "bool_version":
            point["version"] = True
        case "namespace":
            point["namespace"] = "other"
        case "sequence":
            point["event_seq"]["plain"] += 1
        case "watermark":
            point["watermark"] = -1
        case "close_id":
            point["closed_event_id"] += 1
        case "invalid_watermark_type":
            point["watermark"] = "bad"
        case "dirty_counter":
            store.state["counters:exact_event_id"] = 0
        case "extra_namespace":
            store._event_seq["other"] = 1
            point["event_seq"] = dict(store._event_seq)
        case "max_turn":
            point["max_turn"] += 1
        case "path":
            point["path"] = []
        case "branch_view":
            store.state["plain:branch_view"] = {"1": 2}
        case "invalid_branch_view":
            store.state["plain:branch_view"] = {"1": True}
        case "snapshot_branch":
            store.state["plain:snapshot_branch"] = {}
        case "open_text":
            store.state["plain:open_text"] = "unfinished"
        case "triggers":
            store.state["plain:triggers"] = [{"type": "timer"}]
        case "graph":
            store.meta["config_type"] = "terrarium"
        case "two_agents":
            store.meta["agents"] = ["plain", "other"]
        case "output":
            agent.output_router.default_output = StdoutOutput()
        case "secondary_output":
            agent.output_router.add_secondary(StdoutOutput())
        case "named_output":
            agent.output_router.named_outputs["history"] = StdoutOutput()
        case "plugin":
            agent.config.plugins = [{"name": "custom"}]
        case "compact" | "cutoff" | "pending" | "bad_calls":
            records = store.state["plain:branch_snapshots"]
            if kind == "compact":
                records[0]["compact_state"]["compact_count"] = 1
            elif kind == "cutoff":
                records[0]["context_cutoff_event_id"] = 1
            elif kind == "bad_calls":
                records[0]["messages"][1]["tool_calls"] = 1
            else:
                records[0]["messages"][1]["metadata"] = {"tool_result_phase": "pending"}
            store.state["plain:branch_snapshots"] = records
    assert prepare_clean_checkpoint(agent, store, "plain") is None
    assert store._branch_maxima.upper_turn("plain") is None


def test_raw_reset_discards_incoming_authority(clean_point):
    agent, store = clean_point
    store.events["plain:e000000"] = {
        "type": "user_message",
        "content": "rewritten",
        "event_id": 1,
        "turn_index": 2,
        "branch_id": 7,
    }
    store._branch_maxima.clear()
    assert prepare_clean_checkpoint(agent, store, "plain") is None
    assert store.max_branch_id("plain", 2) == 7
