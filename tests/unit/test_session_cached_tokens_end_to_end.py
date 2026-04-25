"""cached_tokens flow from LLM → sub-agent → stored event.

Audit finding A in ``plans/session-system/implementation-plan.md``:

- ``modules/subagent/base.py:81-84`` declares ``_total_tokens``,
  ``_prompt_tokens``, ``_completion_tokens`` — but no
  ``_cached_tokens``.
- ``base.py:394-398`` accumulates only ``prompt_tokens``,
  ``completion_tokens``, ``total_tokens`` from the provider's
  ``last_usage`` dict. The ``cached_tokens`` key is silently dropped.
- ``SubAgentResult`` (``modules/subagent/result.py:81-84``) also has
  no ``cached_tokens`` field.
- ``SessionOutput._handle_subagent_done`` writes the event without a
  ``cached_tokens`` key even if metadata contained one.

Wave B fixes all four. Until then the end-to-end test below is
expected to ``xfail``. The assertions record the intended behaviour.
"""

from types import SimpleNamespace

import pytest

from kohakuterrarium.modules.subagent.result import SubAgentResult
from kohakuterrarium.session.output import SessionOutput
from kohakuterrarium.session.store import SessionStore


@pytest.fixture
def store(tmp_path):
    s = SessionStore(tmp_path / "cached.kohakutr")
    s.init_meta(
        session_id="cached",
        config_type="agent",
        config_path="/tmp",
        pwd=str(tmp_path),
        agents=["parent"],
    )
    yield s
    s.close()


class TestCachedTokensFlow:
    def test_subagent_result_has_cached_tokens_field(self):
        """``SubAgentResult`` should carry a ``cached_tokens`` field."""
        result = SubAgentResult(
            output="",
            success=True,
            turns=1,
            duration=0.1,
            total_tokens=100,
            prompt_tokens=80,
            completion_tokens=20,
        )
        # Post-Wave-B: cached_tokens is a first-class field.
        assert hasattr(result, "cached_tokens")
        assert result.cached_tokens == 0  # default

    def test_subagent_result_preserves_cached_from_provider(self):
        """Providing cached_tokens to SubAgentResult should round-trip."""
        result = SubAgentResult(
            output="done",
            success=True,
            turns=1,
            duration=0.1,
            total_tokens=100,
            prompt_tokens=80,
            completion_tokens=20,
            cached_tokens=55,
        )
        assert result.cached_tokens == 55

    def test_stored_subagent_event_carries_cached_tokens(self, store):
        """End-to-end: cached_tokens set in metadata should land in the event."""
        agent_stub = SimpleNamespace(controller=None, session=None)
        output = SessionOutput("parent", store, agent_stub)

        output.on_activity_with_metadata(
            "subagent_done",
            "[explore] finished",
            {
                "job_id": "explore_1",
                "result": "done",
                "turns": 2,
                "tools_used": ["grep"],
                "duration": 0.3,
                "total_tokens": 500,
                "prompt_tokens": 400,
                "completion_tokens": 100,
                "cached_tokens": 123,
            },
        )

        evts = [e for e in store.get_events("parent") if e["type"] == "subagent_result"]
        assert len(evts) == 1
        # Wave B: the handler must preserve cached_tokens in the event row.
        assert "cached_tokens" in evts[0]
        assert evts[0]["cached_tokens"] == 123


class TestParentCachedTokensNotAffected:
    """Parent's own cached_tokens plumbing already works — sanity check."""

    def test_parent_handle_token_usage_preserves_cached(self, store):
        """``_handle_token_usage`` already writes cached_tokens correctly."""
        agent_stub = SimpleNamespace(controller=None, session=None)
        output = SessionOutput("parent", store, agent_stub)

        output.on_activity_with_metadata(
            "token_usage",
            "",
            {
                "prompt_tokens": 100,
                "completion_tokens": 20,
                "total_tokens": 120,
                "cached_tokens": 44,
            },
        )

        usage_evt = [
            e for e in store.get_events("parent") if e["type"] == "token_usage"
        ][0]
        assert usage_evt["cached_tokens"] == 44
