"""Template helpers: import blocks, Python literals, YAML lists, strict context."""

import ast

import pytest
from jinja2 import UndefinedError

from kohakuterrarium.studio.editors.templates import (
    py_literal,
    _yaml_list,
    import_block,
    render,
    render_string,
)


class TestImportBlock:
    def test_groups_orders_and_merges_like_the_project(self):
        block = import_block(
            [
                "from typing import Any",
                "from kohakuterrarium.modules.tool.base import (\n    BaseTool,\n)",
                "from kohakuterrarium.utils.logging import get_logger",
            ],
            [
                "import httpx",
                "import asyncio",
                "from pathlib import Path",
                "from kohakuterrarium.modules.tool.base import resolve_tool_path",
                "import asyncio",
            ],
        )
        assert block == (
            "import asyncio\n"
            "from pathlib import Path\n"
            "from typing import Any\n"
            "\n"
            "import httpx\n"
            "\n"
            "from kohakuterrarium.utils.logging import get_logger\n"
            "from kohakuterrarium.modules.tool.base import BaseTool, resolve_tool_path"
        )

    def test_long_merged_imports_wrap_in_parentheses(self):
        names = [f"name_number_{i}" for i in range(8)]
        block = import_block([f"from kohakuterrarium.x import {', '.join(names)}"])
        assert block.startswith("from kohakuterrarium.x import (\n    name_number_0,\n")
        assert all(len(line) <= 88 for line in block.splitlines())
        ast.parse(block)

    def test_aliases_survive(self):
        assert import_block(["from a import b as c", "import numpy as np"]) == (
            "import numpy as np\nfrom a import b as c"
        )


class TestPyrepr:
    def test_short_values_stay_on_one_line_with_double_quotes(self):
        assert py_literal({"a": [1, True, None], "b": "it's"}) == (
            '{"a": [1, True, None], "b": "it\'s"}'
        )

    def test_long_values_wrap_black_style_within_the_width(self):
        value = {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "x" * 40}},
            "required": ["path"],
        }
        text = py_literal(value, 8, 7)
        lines = ("        return " + text).splitlines()
        assert all(len(line) <= 88 for line in lines)
        assert lines[-1] == "        }"
        assert ast.literal_eval(text) == value


class TestYamlList:
    def test_empty_is_inline_and_items_are_indented_under_the_key(self):
        assert _yaml_list([]) == " []"
        assert _yaml_list([{"name": "read"}, {"name": "x", "type": "custom"}]) == (
            "\n  - name: read\n  - name: x\n    type: custom"
        )


class TestStrictContext:
    def test_a_missing_template_variable_fails_loudly(self):
        with pytest.raises(UndefinedError):
            render("system_prompt.md.j2", name="x")
        with pytest.raises(UndefinedError):
            render_string("{{ purpose }}")

    def test_the_prompt_seed_uses_the_purpose_when_given(self):
        assert "You are the x creature." in render(
            "system_prompt.md.j2", name="x", purpose=""
        )
        assert render("system_prompt.md.j2", name="x", purpose="Sorts mail.") == (
            "# x\n\nSorts mail.\n"
        )
