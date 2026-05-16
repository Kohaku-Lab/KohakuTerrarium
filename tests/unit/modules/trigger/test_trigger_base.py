"""Unit tests for :mod:`kohakuterrarium.modules.trigger.base`.

Behavior-first: lifecycle flag transitions, context merge semantics,
event factory defaults, resume/setup classmethod round-trips.
"""

from kohakuterrarium.core.events import TriggerEvent
from kohakuterrarium.modules.trigger.base import BaseTrigger, TriggerModule


class _RecordingTrigger(BaseTrigger):
    """Concrete trigger that records lifecycle hook calls."""

    def __init__(self, prompt=None, **options):
        super().__init__(prompt=prompt, **options)
        self.calls: list[str] = []

    async def _on_start(self):
        self.calls.append("start")

    async def _on_stop(self):
        self.calls.append("stop")

    def _on_context_update(self, context):
        self.calls.append(f"ctx:{sorted(context)}")

    async def wait_for_trigger(self):
        return self._create_event("custom")


class TestLifecycle:
    async def test_start_sets_running_and_fires_hook(self):
        t = _RecordingTrigger()
        assert t.is_running is False
        await t.start()
        assert t.is_running is True
        assert t.calls == ["start"]

    async def test_stop_clears_running_and_fires_hook(self):
        t = _RecordingTrigger()
        await t.start()
        await t.stop()
        assert t.is_running is False
        assert t.calls == ["start", "stop"]


class TestContext:
    def test_set_context_merges_into_internal_dict(self):
        t = _RecordingTrigger()
        t.set_context({"a": 1})
        t.set_context({"b": 2})
        # Merge, not replace: both keys survive.
        assert t._context == {"a": 1, "b": 2}

    def test_set_context_invokes_update_hook(self):
        t = _RecordingTrigger()
        t.set_context({"x": 1})
        assert "ctx:['x']" in t.calls


class TestCreateEvent:
    def test_event_defaults_to_prompt_when_no_content(self):
        t = _RecordingTrigger(prompt="do the thing")
        ev = t._create_event("timer")
        assert isinstance(ev, TriggerEvent)
        assert ev.type == "timer"
        assert ev.content == "do the thing"
        assert ev.prompt_override == "do the thing"

    def test_explicit_content_overrides_prompt(self):
        t = _RecordingTrigger(prompt="default")
        ev = t._create_event("timer", content="specific")
        assert ev.content == "specific"
        # prompt_override still tracks the trigger's prompt, not content.
        assert ev.prompt_override == "default"

    def test_event_context_snapshots_internal_context(self):
        t = _RecordingTrigger()
        t.set_context({"k": "v"})
        ev = t._create_event("timer")
        assert ev.context == {"k": "v"}
        # It is a copy — mutating the event's context must not leak back.
        ev.context["new"] = 1
        assert "new" not in t._context

    def test_empty_content_and_no_prompt_yields_empty_string(self):
        t = _RecordingTrigger()
        ev = t._create_event("idle")
        assert ev.content == ""


class TestResumeAndSetup:
    def test_to_resume_dict_carries_prompt_and_options(self):
        t = _RecordingTrigger(prompt="p", interval=30, label="x")
        data = t.to_resume_dict()
        assert data["prompt"] == "p"
        assert data["interval"] == 30
        assert data["label"] == "x"

    def test_from_resume_dict_round_trips(self):
        original = _RecordingTrigger(prompt="hello", extra="meta")
        clone = _RecordingTrigger.from_resume_dict(original.to_resume_dict())
        assert clone.prompt == "hello"
        assert clone.options.get("extra") == "meta"

    def test_from_setup_args_delegates_to_resume_dict(self):
        # Default contract: from_setup_args == from_resume_dict.
        clone = _RecordingTrigger.from_setup_args({"prompt": "z"})
        assert clone.prompt == "z"

    def test_post_setup_is_a_noop_by_default(self):
        t = _RecordingTrigger()
        assert _RecordingTrigger.post_setup(t, context=None) is None

    def test_default_class_flags(self):
        assert BaseTrigger.resumable is False
        assert BaseTrigger.universal is False
        assert BaseTrigger.setup_tool_name == ""


class TestProtocol:
    async def test_concrete_trigger_satisfies_protocol_surface(self):
        # TriggerModule is runtime_checkable — _RecordingTrigger implements
        # start/stop/wait_for_trigger/set_context so it must pass.
        t = _RecordingTrigger()
        assert isinstance(t, TriggerModule)
