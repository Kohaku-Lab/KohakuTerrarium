"""Wave B observability: ``turn_token_usage`` + ``cache_stats`` events.

These two emitters were the last gap in the Wave B emitter set. The
session-output handlers existed but no call site ever produced the
events, so per-turn rollups and cache hit/write data were missing
from the persisted event stream. This test pins the contract.
"""

from kohakuterrarium.session.output import SessionOutput
from kohakuterrarium.session.store import SessionStore


class _FakeAgent:
    _turn_index = 1
    _branch_id = 1


class _FakeRouter:
    def __init__(self, output: SessionOutput) -> None:
        self.output = output

    def notify_activity(
        self, activity_type: str, detail: str, metadata: dict | None = None
    ) -> None:
        self.output.on_activity_with_metadata(activity_type, detail, metadata or {})


def _make_session_output(tmp_path) -> tuple[SessionOutput, SessionStore]:
    path = tmp_path / "s.kohakutr.v2"
    store = SessionStore(str(path))
    store.init_meta(
        session_id="s",
        config_type="agent",
        config_path="x",
        pwd=str(tmp_path),
        agents=["alice"],
    )
    output = SessionOutput("alice", store, _FakeAgent())
    return output, store


def test_turn_token_usage_persists(tmp_path):
    out, store = _make_session_output(tmp_path)
    router = _FakeRouter(out)
    router.notify_activity(
        "turn_token_usage",
        "turn 1: 100 in, 25 out",
        {
            "turn_index": 1,
            "prompt_tokens": 100,
            "completion_tokens": 25,
            "cached_tokens": 10,
            "total_tokens": 125,
        },
    )
    events = store.get_events("alice")
    rollup = [e for e in events if e.get("type") == "turn_token_usage"]
    assert len(rollup) == 1
    evt = rollup[0]
    assert evt["turn_index"] == 1
    assert evt["prompt_tokens"] == 100
    assert evt["completion_tokens"] == 25
    assert evt["cached_tokens"] == 10
    assert evt["total_tokens"] == 125
    store.close(update_status=False)


def test_cache_stats_persists(tmp_path):
    out, store = _make_session_output(tmp_path)
    router = _FakeRouter(out)
    router.notify_activity(
        "cache_stats",
        "cache: r=80 w=20",
        {
            "agent": "alice",
            "cache_write": 20,
            "cache_read": 80,
            "cache_hit_ratio": 0.4,
        },
    )
    events = store.get_events("alice")
    cache = [e for e in events if e.get("type") == "cache_stats"]
    assert len(cache) == 1
    evt = cache[0]
    assert evt["agent"] == "alice"
    assert evt["cache_write"] == 20
    assert evt["cache_read"] == 80
    assert abs(evt["cache_hit_ratio"] - 0.4) < 1e-6
    store.close(update_status=False)
