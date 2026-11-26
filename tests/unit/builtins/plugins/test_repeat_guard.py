"""Unit tests for :mod:`kohakuterrarium.builtins.plugins.repeat_guard.plugin`."""

import pytest

from kohakuterrarium.builtins.plugins.repeat_guard.plugin import (
    RepeatGuardPlugin,
    call_key,
)
from kohakuterrarium.core.events import (
    EventType,
    TriggerEvent,
    create_tool_complete_event,
    create_user_input_event,
)
from kohakuterrarium.modules.plugin.base import PluginBlockError
from kohakuterrarium.modules.plugin.option_validation import PluginOptionError
from kohakuterrarium.modules.tool.base import ToolResult

_ERR = "the conversation holds only 0 image(s); 1 requested"
_ARGS = {"prompt": "a lake", "num_last_images_to_include": 1}


async def _call(
    plugin: RepeatGuardPlugin,
    args: dict,
    result: ToolResult,
    *,
    tool: str = "image_gen",
    job: str = "job",
) -> None:
    """Drive one call through the pre and post hooks as the executor does."""
    await plugin.pre_tool_execute(args, tool_name=tool, job_id=job)
    await plugin.post_tool_execute(result, args=args, tool_name=tool, job_id=job)


async def _fail(plugin, args=_ARGS, *, tool="image_gen", error=_ERR, n=1):
    for i in range(n):
        await _call(plugin, args, ToolResult(error=error), tool=tool, job=f"j{i}")


class TestCallKey:
    def test_argument_order_does_not_change_the_key(self):
        assert call_key("t", {"a": 1, "b": {"y": 2, "x": 1}}) == call_key(
            "t", {"b": {"x": 1, "y": 2}, "a": 1}
        )

    def test_control_keys_are_ignored(self):
        bare = call_key("t", {"a": 1})
        assert call_key("t", {"a": 1, "run_in_background": True}) == bare
        assert call_key("t", {"allow_concurrent": "true", "a": 1}) == bare

    def test_tool_name_and_values_are_part_of_the_key(self):
        assert call_key("t", {"a": 1}) != call_key("u", {"a": 1})
        assert call_key("t", {"a": 1}) != call_key("t", {"a": 2})
        assert call_key("t", {"a": 1}) != call_key("t", {"a": "1"})

    def test_non_json_values_still_key(self):
        class Opaque:
            def __str__(self) -> str:
                return "opaque"

        assert call_key("t", {"a": Opaque()}) == call_key("t", {"a": Opaque()})
        assert call_key("t", None) == call_key("t", {})


class TestBlocking:
    async def test_blocks_the_call_after_k_identical_failures(self):
        plugin = RepeatGuardPlugin()
        await _fail(plugin, n=3)
        with pytest.raises(PluginBlockError) as exc:
            await plugin.pre_tool_execute(dict(_ARGS), tool_name="image_gen")
        assert str(exc.value) == (
            "This exact call to image_gen failed 3 times in a row with the same "
            f"arguments: {_ERR}. Change the arguments or take another approach; "
            "repeating it will fail the same way."
        )

    async def test_k_minus_one_failures_still_pass(self):
        plugin = RepeatGuardPlugin()
        await _fail(plugin, n=2)
        assert await plugin.pre_tool_execute(_ARGS, tool_name="image_gen") is None

    async def test_blocked_calls_keep_blocking_without_growing_the_count(self):
        plugin = RepeatGuardPlugin(repeat_failures=2)
        await _fail(plugin, n=2)
        for _ in range(5):
            with pytest.raises(PluginBlockError, match="failed 2 times"):
                await plugin.pre_tool_execute(_ARGS, tool_name="image_gen")
        assert plugin.failure_count("image_gen", _ARGS) == 2

    async def test_changed_argument_proceeds(self):
        plugin = RepeatGuardPlugin()
        await _fail(plugin, n=3)
        changed = {"prompt": "a lake"}
        assert await plugin.pre_tool_execute(changed, tool_name="image_gen") is None
        other_tool = await plugin.pre_tool_execute(_ARGS, tool_name="video_gen")
        assert other_tool is None

    async def test_control_key_change_does_not_dodge_the_block(self):
        plugin = RepeatGuardPlugin()
        await _fail(plugin, n=3)
        with pytest.raises(PluginBlockError):
            await plugin.pre_tool_execute(
                {**_ARGS, "run_in_background": True}, tool_name="image_gen"
            )

    async def test_long_error_is_shortened_to_its_first_line(self):
        plugin = RepeatGuardPlugin(repeat_failures=1)
        error = "first line\n" + "x" * 1000
        await _fail(plugin, error=error)
        with pytest.raises(PluginBlockError) as exc:
            await plugin.pre_tool_execute(_ARGS, tool_name="image_gen")
        assert "first line." in str(exc.value)
        assert "xxx" not in str(exc.value)

        plugin = RepeatGuardPlugin(repeat_failures=1)
        await _fail(plugin, error="y" * 1000)
        with pytest.raises(PluginBlockError) as exc:
            await plugin.pre_tool_execute(_ARGS, tool_name="image_gen")
        assert "y" * 200 + "..." in str(exc.value)
        assert "y" * 201 not in str(exc.value)

    async def test_exit_code_failure_without_error_text_counts(self):
        plugin = RepeatGuardPlugin(repeat_failures=1)
        await _call(plugin, {"command": "false"}, ToolResult(exit_code=1), tool="bash")
        with pytest.raises(PluginBlockError, match="exit code 1"):
            await plugin.pre_tool_execute({"command": "false"}, tool_name="bash")


class TestCounting:
    async def test_success_resets_that_key(self):
        plugin = RepeatGuardPlugin()
        await _fail(plugin, n=2)
        await _call(plugin, _ARGS, ToolResult(output="ok"))
        assert plugin.failure_count("image_gen", _ARGS) == 0
        await _fail(plugin, n=2)
        assert await plugin.pre_tool_execute(_ARGS, tool_name="image_gen") is None

    async def test_other_calls_do_not_reset_a_streak(self):
        plugin = RepeatGuardPlugin()
        await _fail(plugin, n=2)
        await _call(plugin, {"path": "a"}, ToolResult(output="ok"), tool="read")
        await _fail(plugin, {"prompt": "other"})
        await _fail(plugin)
        with pytest.raises(PluginBlockError):
            await plugin.pre_tool_execute(_ARGS, tool_name="image_gen")

    async def test_a_different_failure_restarts_the_streak(self):
        plugin = RepeatGuardPlugin()
        await _fail(plugin, n=2)
        await _fail(plugin, error="a different error")
        assert plugin.failure_count("image_gen", _ARGS) == 1
        await _fail(plugin, error="a different error", n=2)
        with pytest.raises(PluginBlockError, match="a different error"):
            await plugin.pre_tool_execute(_ARGS, tool_name="image_gen")

    async def test_different_output_with_the_same_error_restarts_the_streak(self):
        plugin = RepeatGuardPlugin(repeat_failures=2)
        args = {"command": "pytest"}
        for run in range(3):
            result = ToolResult(
                output=f"1 failed in 0.{run}s", exit_code=1, error="exit 1"
            )
            await _call(plugin, args, result, tool="bash", job=f"r{run}")
        assert plugin.failure_count("bash", args) == 1
        assert await plugin.pre_tool_execute(args, tool_name="bash") is None

    async def test_post_uses_the_key_seen_by_pre(self):
        plugin = RepeatGuardPlugin(repeat_failures=1)
        await plugin.pre_tool_execute(_ARGS, tool_name="image_gen", job_id="j")
        rewritten = {**_ARGS, "prompt": "rewritten by a later plugin"}
        await plugin.post_tool_execute(
            ToolResult(error=_ERR), args=rewritten, tool_name="image_gen", job_id="j"
        )
        with pytest.raises(PluginBlockError):
            await plugin.pre_tool_execute(_ARGS, tool_name="image_gen")

    async def test_post_without_pre_keys_from_its_own_args(self):
        plugin = RepeatGuardPlugin(repeat_failures=1)
        await plugin.post_tool_execute(
            ToolResult(error=_ERR), args=_ARGS, tool_name="image_gen"
        )
        with pytest.raises(PluginBlockError):
            await plugin.pre_tool_execute(_ARGS, tool_name="image_gen")

    async def test_tool_name_falls_back_to_the_tool_object(self):
        class Tool:
            tool_name = "image_gen"

        plugin = RepeatGuardPlugin(repeat_failures=1)
        await plugin.post_tool_execute(ToolResult(error=_ERR), args=_ARGS, tool=Tool())
        with pytest.raises(PluginBlockError):
            await plugin.pre_tool_execute(_ARGS, tool=Tool())

    async def test_unnamed_call_is_still_guarded(self):
        plugin = RepeatGuardPlugin(repeat_failures=1)
        await plugin.post_tool_execute(ToolResult(error=_ERR), args=_ARGS)
        with pytest.raises(PluginBlockError, match="call to tool failed"):
            await plugin.pre_tool_execute(_ARGS)

    async def test_pending_key_is_dropped_when_another_plugin_blocks(self):
        plugin = RepeatGuardPlugin()
        await plugin.pre_tool_execute(_ARGS, tool_name="image_gen", job_id="j")
        await plugin.on_event(create_user_input_event("next"))
        await plugin.post_tool_execute(
            ToolResult(error=_ERR), args={"x": 1}, tool_name="image_gen", job_id="j"
        )
        assert plugin.failure_count("image_gen", {"x": 1}) == 1
        assert plugin.failure_count("image_gen", _ARGS) == 0

    async def test_non_tool_result_is_ignored(self):
        plugin = RepeatGuardPlugin(repeat_failures=1)
        await plugin.post_tool_execute("raw text", args=_ARGS, tool_name="image_gen")
        assert await plugin.pre_tool_execute(_ARGS, tool_name="image_gen") is None


class TestTurnReset:
    async def test_new_user_input_clears_every_streak(self):
        plugin = RepeatGuardPlugin()
        await _fail(plugin, n=3)
        await plugin.on_event(create_user_input_event("try again"))
        assert plugin.failure_count("image_gen", _ARGS) == 0
        assert await plugin.pre_tool_execute(_ARGS, tool_name="image_gen") is None

    @pytest.mark.parametrize("kind", [EventType.CHANNEL_MESSAGE, EventType.TIMER])
    async def test_other_outside_stimuli_also_open_a_turn(self, kind):
        plugin = RepeatGuardPlugin()
        await _fail(plugin, n=3)
        await plugin.on_event(TriggerEvent(type=kind, content="x"))
        assert plugin.failure_count("image_gen", _ARGS) == 0

    async def test_background_completions_keep_the_turn(self):
        plugin = RepeatGuardPlugin()
        await _fail(plugin, n=3)
        await plugin.on_event(create_tool_complete_event("job", "done"))
        await plugin.on_event(TriggerEvent(type=EventType.SUBAGENT_OUTPUT))
        await plugin.on_event(None)
        with pytest.raises(PluginBlockError):
            await plugin.pre_tool_execute(_ARGS, tool_name="image_gen")


class TestOptions:
    def test_schema_declares_the_option(self):
        spec = RepeatGuardPlugin.option_schema()["repeat_failures"]
        assert spec["type"] == "int"
        assert spec["default"] == 3
        assert spec["min"] == 0
        assert RepeatGuardPlugin().get_options() == {"repeat_failures": 3}

    async def test_zero_disables_the_guard(self):
        plugin = RepeatGuardPlugin(repeat_failures=0)
        await _fail(plugin, n=10)
        assert plugin.failure_count("image_gen", _ARGS) == 0
        assert await plugin.pre_tool_execute(_ARGS, tool_name="image_gen") is None

    async def test_options_dict_form_is_accepted(self):
        plugin = RepeatGuardPlugin(options={"repeat_failures": 1})
        await _fail(plugin)
        with pytest.raises(PluginBlockError, match="failed 1 times"):
            await plugin.pre_tool_execute(_ARGS, tool_name="image_gen")

    async def test_runtime_zero_drops_state_and_reenable_starts_clean(self):
        plugin = RepeatGuardPlugin()
        await _fail(plugin, n=3)
        plugin.set_options({"repeat_failures": 0})
        assert plugin.failure_count("image_gen", _ARGS) == 0
        plugin.set_options({"repeat_failures": 3})
        assert await plugin.pre_tool_execute(_ARGS, tool_name="image_gen") is None

    async def test_runtime_lower_limit_applies_to_existing_streaks(self):
        plugin = RepeatGuardPlugin()
        await _fail(plugin, n=2)
        plugin.set_options({"repeat_failures": 2})
        with pytest.raises(PluginBlockError, match="failed 2 times"):
            await plugin.pre_tool_execute(_ARGS, tool_name="image_gen")

    @pytest.mark.parametrize("bad", [-1, "many", True])
    def test_invalid_values_are_rejected(self, bad):
        with pytest.raises(PluginOptionError):
            RepeatGuardPlugin(repeat_failures=bad)
        plugin = RepeatGuardPlugin()
        with pytest.raises(PluginOptionError):
            plugin.set_options({"repeat_failures": bad})
        assert plugin.get_options() == {"repeat_failures": 3}

    def test_none_restores_the_default(self):
        plugin = RepeatGuardPlugin(repeat_failures=5)
        plugin.set_options({"repeat_failures": None})
        assert plugin.get_options() == {"repeat_failures": 3}


class TestIsolation:
    async def test_two_instances_keep_separate_streaks(self):
        parent = RepeatGuardPlugin()
        child = RepeatGuardPlugin()
        await _fail(parent, n=3)
        assert await child.pre_tool_execute(_ARGS, tool_name="image_gen") is None
        await _fail(child, n=2)
        assert parent.failure_count("image_gen", _ARGS) == 3
        assert child.failure_count("image_gen", _ARGS) == 2
        await parent.on_event(create_user_input_event("hi"))
        assert child.failure_count("image_gen", _ARGS) == 2

    def test_adds_no_prompt_text(self):
        assert RepeatGuardPlugin().get_prompt_content(None) is None
