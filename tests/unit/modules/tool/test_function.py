"""Unit tests for :mod:`kohakuterrarium.modules.tool.function`."""

from kohakuterrarium.modules.tool.base import ExecutionMode, ToolContext
from kohakuterrarium.modules.tool.function import FunctionTool, tool


def _plain(text: str, count: int = 1) -> str:
    """Repeat text."""
    return text * count


class TestFunctionTool:
    def test_signature_derives_name_description_and_schema(self):
        wrapped = tool(_plain)
        assert wrapped.tool_name == "_plain"
        assert wrapped.description == "Repeat text."
        assert wrapped.execution_mode is ExecutionMode.DIRECT
        assert wrapped.get_parameters_schema() == {
            "type": "object",
            "properties": {"text": {"type": "string"}, "count": {"type": "integer"}},
            "required": ["text"],
        }

    async def test_execute_calls_the_function(self):
        result = await tool(_plain).execute({"text": "ab", "count": 2})
        assert result.output == "abab"

    async def test_missing_required_argument_is_an_error(self):
        result = await tool(_plain).execute({})
        assert result.error == "missing required argument: text"

    async def test_context_parameter_receives_the_tool_context(self, tmp_path):
        @tool
        async def where(context) -> str:
            return str(context.working_dir)

        ctx = ToolContext("a", None, tmp_path)
        assert (await where.execute({}, context=ctx)).output == str(tmp_path)

    def test_network_declarations_default_to_none(self):
        wrapped = tool(_plain)
        assert wrapped.url_arguments == ()
        assert wrapped.network_hosts == ()
        assert wrapped.network_access is False

    def test_decorator_declares_network_use(self):
        @tool(
            url_arguments=["url"],
            network_hosts=["api.example.com"],
            network_access=True,
        )
        def fetch(url: str) -> str:
            return url

        assert isinstance(fetch, FunctionTool)
        assert fetch.url_arguments == ("url",)
        assert fetch.network_hosts == ("api.example.com",)
        assert fetch.network_access is True

    def test_a_single_name_is_not_split_into_characters(self):
        wrapped = FunctionTool(_plain, url_arguments="text", network_hosts="a.test")
        assert wrapped.url_arguments == ("text",)
        assert wrapped.network_hosts == ("a.test",)

    def test_declarations_stay_on_their_instance(self):
        declared = FunctionTool(_plain, url_arguments=("text",))
        assert declared.url_arguments == ("text",)
        assert FunctionTool(_plain).url_arguments == ()
