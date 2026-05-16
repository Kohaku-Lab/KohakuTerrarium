"""Unit tests for :mod:`kohakuterrarium.core.controller_metrics`."""

import types

import pytest

from kohakuterrarium.core import controller_metrics as cm
from kohakuterrarium.core.controller_metrics import (
    emit_token_metrics,
    llm_identity,
    time_llm_call,
)


class _FakeLLM:
    def __init__(self, provider="", model="", usage=None, config_model=""):
        self.provider_name = provider
        self.model = model
        if config_model:
            self.config = types.SimpleNamespace(model=config_model)
        if usage is not None:
            self.last_usage = usage


# ── llm_identity ─────────────────────────────────────────────────


class TestLLMIdentity:
    def test_explicit_provider_model(self):
        assert llm_identity(_FakeLLM("openai", "gpt-4")) == ("openai", "gpt-4")

    def test_empty_falls_back_to_unknown(self):
        assert llm_identity(_FakeLLM()) == ("unknown", "unknown")

    def test_falls_back_to_config_model(self):
        llm = _FakeLLM("openai", model="", config_model="gpt-4")
        assert llm_identity(llm) == ("openai", "gpt-4")

    def test_none_attrs_defaulted(self):
        class _Bare:
            pass

        # No attrs at all → both unknown.
        assert llm_identity(_Bare()) == ("unknown", "unknown")


# ── emit_token_metrics ───────────────────────────────────────────


class TestEmitTokenMetrics:
    def test_no_usage_no_op(self, monkeypatch):
        seen = []
        monkeypatch.setattr(
            cm.metrics,
            "observe_tokens",
            lambda *a, **k: seen.append((a, k)),
        )
        emit_token_metrics(_FakeLLM("openai", "m"), "openai", "m")
        assert seen == []

    def test_usage_emits(self, monkeypatch):
        seen = []
        monkeypatch.setattr(
            cm.metrics,
            "observe_tokens",
            lambda *a, **k: seen.append((a, k)),
        )
        llm = _FakeLLM(
            "openai",
            "m",
            usage={
                "prompt_tokens": 5,
                "completion_tokens": 7,
                "cached_tokens": 3,
                "cache_creation_input_tokens": 1,
            },
        )
        emit_token_metrics(llm, "openai", "m")
        assert seen[0][0] == ("openai", "m")
        assert seen[0][1] == {
            "prompt": 5,
            "completion": 7,
            "cache_read": 3,
            "cache_write": 1,
        }

    def test_cache_read_alt_key(self, monkeypatch):
        seen = []
        monkeypatch.setattr(
            cm.metrics, "observe_tokens", lambda *a, **k: seen.append(k)
        )
        # ``cache_read_input_tokens`` is the alternative spelling.
        llm = _FakeLLM("o", "m", usage={"cache_read_input_tokens": 8})
        emit_token_metrics(llm, "o", "m")
        assert seen[0]["cache_read"] == 8

    def test_underscored_last_usage(self, monkeypatch):
        seen = []
        monkeypatch.setattr(
            cm.metrics, "observe_tokens", lambda *a, **k: seen.append(k)
        )
        llm = types.SimpleNamespace(_last_usage={"prompt_tokens": 1})
        emit_token_metrics(llm, "o", "m")
        assert seen[0]["prompt"] == 1


# ── time_llm_call ────────────────────────────────────────────────


class TestTimeLLMCall:
    def test_ok_path(self, monkeypatch):
        observed = []

        def obs(provider, model, status, duration_ms):
            observed.append((provider, model, status, duration_ms))

        monkeypatch.setattr(cm.metrics, "observe_llm", obs)
        monkeypatch.setattr(cm.metrics, "observe_tokens", lambda *a, **k: None)
        with time_llm_call(_FakeLLM("openai", "m")) as t:
            assert t.status == "ok"
        assert observed[0][:3] == ("openai", "m", "ok")
        assert observed[0][3] >= 0.0

    def test_user_can_override_status(self, monkeypatch):
        observed = []
        monkeypatch.setattr(
            cm.metrics, "observe_llm", lambda *a, **k: observed.append(a)
        )
        monkeypatch.setattr(cm.metrics, "observe_tokens", lambda *a, **k: None)
        with time_llm_call(_FakeLLM("o", "m")) as t:
            t.status = "interrupted"
        assert observed[0][2] == "interrupted"

    def test_exception_flips_status_and_increments_errors(self, monkeypatch):
        llm_observed = []
        errors = []
        monkeypatch.setattr(
            cm.metrics, "observe_llm", lambda *a, **k: llm_observed.append(a)
        )
        monkeypatch.setattr(
            cm.metrics, "observe_error", lambda src, **k: errors.append(src)
        )
        monkeypatch.setattr(cm.metrics, "observe_tokens", lambda *a, **k: None)
        with pytest.raises(RuntimeError, match="boom"):
            with time_llm_call(_FakeLLM("o", "m")):
                raise RuntimeError("boom")
        # status flipped to "error"
        assert llm_observed[0][2] == "error"
        # error counted on the controller source
        assert errors == ["controller"]

    def test_timer_class_dataclass_like(self):
        # Verify _LLMCallTimer is the documented type.
        with time_llm_call(_FakeLLM()) as t:
            assert hasattr(t, "status")
