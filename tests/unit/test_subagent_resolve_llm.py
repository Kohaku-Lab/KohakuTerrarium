"""``resolve_llm`` honors inherit-parent sentinels and provider rejections.

Without this, a sub-agent config that records ``model="subagent-default"``
sends that string to the provider's ``with_model``, which sends it
straight to the API. Strict-whitelist providers (Codex/ChatGPT) reject
it as an unknown model and the sub-agent run fails with a 400.
"""

from kohakuterrarium.modules.subagent.config import SubAgentConfig
from kohakuterrarium.modules.subagent.runtime_builders import resolve_llm


class _TrackingLLM:
    """Minimal LLM stand-in: records every ``with_model`` call so the
    test can assert whether the resolver dispatched to the provider.
    """

    def __init__(self, name: str = "parent-model"):
        self.name = name
        self.with_model_calls: list[str] = []

    def with_model(self, name: str) -> "_TrackingLLM":
        self.with_model_calls.append(name)
        return _TrackingLLM(name)


class _RejectingLLM(_TrackingLLM):
    """``with_model`` always raises — emulates Codex/ChatGPT 400 path."""

    def with_model(self, name: str) -> "_RejectingLLM":
        self.with_model_calls.append(name)
        raise ValueError(f"unsupported model: {name}")


def test_empty_model_inherits_parent():
    parent = _TrackingLLM()
    cfg = SubAgentConfig(name="x")
    assert resolve_llm(parent, cfg) is parent
    assert parent.with_model_calls == []


def test_subagent_default_sentinel_inherits_parent_without_calling_with_model():
    """The fix: ``subagent-default`` must NOT reach the provider — it's
    a sentinel that means "use whatever the parent uses".
    """
    parent = _TrackingLLM()
    cfg = SubAgentConfig(name="x", model="subagent-default")
    assert resolve_llm(parent, cfg) is parent
    assert parent.with_model_calls == []


def test_other_sentinels_inherit_parent():
    parent = _TrackingLLM()
    for sentinel in ("default", "inherit", "parent", "SUBAGENT-DEFAULT"):
        cfg = SubAgentConfig(name="x", model=sentinel)
        assert resolve_llm(parent, cfg) is parent
    assert parent.with_model_calls == []


def test_real_model_name_dispatches_to_with_model():
    parent = _TrackingLLM()
    cfg = SubAgentConfig(name="x", model="gpt-4o-mini")
    out = resolve_llm(parent, cfg)
    assert out is not parent
    assert parent.with_model_calls == ["gpt-4o-mini"]
    assert out.name == "gpt-4o-mini"


def test_provider_rejection_falls_back_to_parent():
    """If the provider rejects the model id (e.g. Codex unknown model),
    resolve_llm logs and inherits the parent rather than crashing the
    sub-agent run.
    """
    parent = _RejectingLLM()
    cfg = SubAgentConfig(name="x", model="some-unknown-id")
    out = resolve_llm(parent, cfg)
    assert out is parent
    assert parent.with_model_calls == ["some-unknown-id"]
