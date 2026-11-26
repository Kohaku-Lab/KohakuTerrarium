"""Unit tests for :mod:`kohakuterrarium.core.token_ledger`."""

from types import SimpleNamespace
from unittest.mock import MagicMock

from kohakuterrarium.core.token_ledger import (
    TokenLedger,
    context_window,
    empty_token_usage,
    ledger_for,
    model_context_window,
    reset_context_tokens,
    token_snapshot,
)


def _store(slot: dict | None, name: str = "alice") -> SimpleNamespace:
    state = {} if slot is None else {f"{name}:token_usage": slot}
    return SimpleNamespace(state=state)


def _agent(name: str = "alice", store=None) -> SimpleNamespace:
    return SimpleNamespace(config=SimpleNamespace(name=name), session_store=store)


class TestTokenLedger:
    def test_add_accumulates_and_tracks_last_prompt(self):
        ledger = TokenLedger()
        ledger.add({"prompt_tokens": 100, "completion_tokens": 10, "cached_tokens": 40})
        ledger.add({"prompt_tokens": 150, "completion_tokens": 20})
        assert ledger.totals() == {
            "prompt": 250,
            "completion": 30,
            "cached": 40,
            "total": 280,
        }
        assert ledger.context_tokens == 150

    def test_seed_reads_persisted_slot_once_per_store(self):
        ledger = TokenLedger()
        store = _store(
            {
                "total_input_tokens": 500,
                "total_output_tokens": 50,
                "total_cached_tokens": 5,
                "last_prompt_tokens": 120,
            }
        )
        ledger.seed(store, "alice")
        ledger.add({"prompt_tokens": 10, "completion_tokens": 1})
        ledger.seed(store, "alice")
        assert ledger.totals()["prompt"] == 510
        assert ledger.totals()["completion"] == 51
        assert ledger.context_tokens == 10

    def test_seed_accepts_event_shaped_slot(self):
        ledger = TokenLedger()
        ledger.seed(_store({"prompt_tokens": 7, "completion_tokens": 3}), "alice")
        assert ledger.totals() == {
            "prompt": 7,
            "completion": 3,
            "cached": 0,
            "total": 10,
        }

    def test_new_store_merges_field_wise_maximum(self):
        ledger = TokenLedger()
        ledger.seed(_store({"total_input_tokens": 300}), "alice")
        ledger.add({"prompt_tokens": 50, "completion_tokens": 5})
        ledger.seed(
            _store({"total_input_tokens": 200, "total_output_tokens": 90}), "alice"
        )
        assert ledger.totals()["prompt"] == 350
        assert ledger.totals()["completion"] == 90

    def test_reset_context_survives_reseed(self):
        ledger = TokenLedger()
        ledger.add({"prompt_tokens": 9000})
        ledger.reset_context()
        ledger.seed(_store({"last_prompt_tokens": 9000}), "alice")
        assert ledger.context_tokens == 0

    def test_seed_ignores_missing_slot_and_broken_store(self):
        ledger = TokenLedger()
        ledger.seed(_store(None), "alice")
        broken = SimpleNamespace(state=None)
        ledger.seed(broken, "alice")
        ledger.seed(None, "alice")
        assert ledger.totals() == empty_token_usage()


class TestAgentHelpers:
    def test_ledger_for_is_stable_and_seeded(self):
        agent = _agent(store=_store({"total_input_tokens": 40}))
        first = ledger_for(agent)
        assert first is ledger_for(agent)
        assert first.prompt == 40

    def test_token_snapshot_and_reset(self):
        agent = _agent()
        ledger_for(agent).add({"prompt_tokens": 30, "completion_tokens": 2})
        assert token_snapshot(agent) == {
            "token_usage": {"prompt": 30, "completion": 2, "cached": 0, "total": 32},
            "context_tokens": 30,
        }
        reset_context_tokens(agent)
        assert token_snapshot(agent)["context_tokens"] == 0
        assert token_snapshot(agent)["token_usage"]["total"] == 32

    def test_token_snapshot_without_agent(self):
        assert token_snapshot(None) == {
            "token_usage": empty_token_usage(),
            "context_tokens": 0,
        }
        reset_context_tokens(None)

    def test_mock_agent_reports_zero(self):
        snap = token_snapshot(MagicMock())
        assert snap == {"token_usage": empty_token_usage(), "context_tokens": 0}


class TestContextWindow:
    def test_chat_window_and_compaction_trigger(self):
        agent = SimpleNamespace(
            llm=SimpleNamespace(_profile_max_context=200_000),
            compact_manager=SimpleNamespace(
                config=SimpleNamespace(max_tokens=100_000, threshold=0.8)
            ),
        )
        assert context_window(agent) == (200_000, 80_000)

    def test_falls_back_to_compaction_window(self):
        agent = SimpleNamespace(
            llm=SimpleNamespace(),
            compact_manager=SimpleNamespace(
                config=SimpleNamespace(max_tokens=64_000, threshold=0.5)
            ),
        )
        assert context_window(agent) == (64_000, 32_000)

    def test_without_compaction(self):
        agent = SimpleNamespace(
            llm=SimpleNamespace(_profile_max_context=8000), compact_manager=None
        )
        assert context_window(agent) == (8000, 0)

    def test_mock_values_are_ignored(self):
        assert context_window(MagicMock()) == (0, 0)


class TestModelContextWindow:
    def test_reports_the_profile_window(self):
        agent = SimpleNamespace(llm=SimpleNamespace(_profile_context_window=1_000_000))
        assert model_context_window(agent) == 1_000_000

    def test_unknown_window_is_none(self):
        assert model_context_window(SimpleNamespace(llm=SimpleNamespace())) is None
        assert model_context_window(SimpleNamespace()) is None
        assert model_context_window(MagicMock()) is None
        agent = SimpleNamespace(llm=SimpleNamespace(_profile_context_window=0))
        assert model_context_window(agent) is None
