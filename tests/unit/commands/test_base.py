"""Unit tests for :mod:`kohakuterrarium.commands.base`.

Contract:

- ``CommandResult.success`` is True iff ``error is None``.
- ``BaseCommand.execute`` wraps ``_execute`` so any raised exception
  becomes a failed ``CommandResult`` (error = str of the exception),
  never propagates.
- ``parse_command_args`` splits one positional arg from ``--flag``/``-f``
  kwargs: a flag with a following non-flag token takes that token as its
  value; a flag with nothing after it becomes ``"true"``; only the first
  bare token is the positional.
"""

import pytest

from kohakuterrarium.commands.base import (
    BaseCommand,
    CommandResult,
    parse_command_args,
)


class TestCommandResult:
    def test_success_true_when_no_error(self):
        assert CommandResult(content="ok").success is True

    def test_success_false_when_error_set(self):
        assert CommandResult(error="boom").success is False

    def test_defaults(self):
        r = CommandResult()
        assert r.content == ""
        assert r.error is None
        assert r.metadata is None
        assert r.success is True


class TestBaseCommandExecuteWrapping:
    async def test_execute_returns_result_from_subclass(self):
        class _OK(BaseCommand):
            @property
            def command_name(self) -> str:
                return "ok"

            @property
            def description(self) -> str:
                return "always ok"

            async def _execute(self, args, context):
                return CommandResult(content=f"got:{args}")

        result = await _OK().execute("hello", context=None)
        assert result.content == "got:hello"
        assert result.success is True

    async def test_execute_converts_raised_exception_to_failed_result(self):
        class _Boom(BaseCommand):
            @property
            def command_name(self) -> str:
                return "boom"

            @property
            def description(self) -> str:
                return "raises"

            async def _execute(self, args, context):
                raise ValueError("specific failure detail")

        result = await _Boom().execute("anything", context=None)
        assert result.success is False
        assert result.error == "specific failure detail"

    async def test_base_execute_without_override_raises_notimplemented_as_error(self):
        # BaseCommand._execute raises NotImplementedError; the wrapper must
        # catch it and surface it as a failed result, not propagate.
        class _Bare(BaseCommand):
            @property
            def command_name(self) -> str:
                return "bare"

            @property
            def description(self) -> str:
                return "no _execute override"

        result = await _Bare().execute("x", context=None)
        assert result.success is False

    def test_command_name_not_implemented_on_base(self):
        with pytest.raises(NotImplementedError):
            _ = BaseCommand().command_name

    def test_description_not_implemented_on_base(self):
        with pytest.raises(NotImplementedError):
            _ = BaseCommand().description


class TestParseCommandArgs:
    def test_empty_string_yields_empty_positional_and_kwargs(self):
        assert parse_command_args("") == ("", {})

    def test_whitespace_only_yields_empty(self):
        assert parse_command_args("   ") == ("", {})

    def test_single_positional(self):
        assert parse_command_args("job_123") == ("job_123", {})

    def test_positional_with_double_dash_kwarg(self):
        assert parse_command_args("job_123 --lines 50") == (
            "job_123",
            {"lines": "50"},
        )

    def test_multiple_double_dash_kwargs(self):
        assert parse_command_args("job_123 --lines 50 --offset 10") == (
            "job_123",
            {"lines": "50", "offset": "10"},
        )

    def test_double_dash_flag_with_no_value_becomes_true(self):
        assert parse_command_args("job_1 --verbose") == ("job_1", {"verbose": "true"})

    def test_double_dash_flag_followed_by_another_flag_becomes_true(self):
        assert parse_command_args("--a --b 2") == ("", {"a": "true", "b": "2"})

    def test_single_dash_kwarg(self):
        assert parse_command_args("job_1 -n 5") == ("job_1", {"n": "5"})

    def test_single_dash_flag_with_no_value_becomes_true(self):
        assert parse_command_args("job_1 -v") == ("job_1", {"v": "true"})

    def test_only_first_bare_token_is_positional(self):
        # Second bare token is dropped (not a kwarg, not the positional).
        pos, kwargs = parse_command_args("first second --k v")
        assert pos == "first"
        assert kwargs == {"k": "v"}
