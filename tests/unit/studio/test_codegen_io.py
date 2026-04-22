"""Input / output codegen tests."""

from kohakuterrarium.api.studio.codegen import io_mod

SAMPLE_INPUT = '''\
"""Sample input."""

from typing import Any


class MyInput:
    async def read_input(self) -> Any:
        return "hello"
'''


SAMPLE_OUTPUT = '''\
"""Sample output."""

from typing import Any


class MyOutput:
    async def write_output(self, data: Any) -> None:
        print(data)
'''


def test_render_new_input_compiles():
    source = io_mod.render_new(
        {
            "kind": "input",
            "name": "my_input",
            "class_name": "MyInput",
            "description": "x",
            "body": 'return "hi"',
        }
    )
    compile(source, "<rendered>", "exec")
    assert "async def read_input" in source


def test_render_new_output_compiles():
    source = io_mod.render_new(
        {
            "kind": "output",
            "name": "my_output",
            "class_name": "MyOutput",
            "description": "x",
            "body": "print(data)",
        }
    )
    compile(source, "<rendered>", "exec")
    assert "async def write_output" in source


def test_parse_back_input():
    env = io_mod.parse_back(SAMPLE_INPUT)
    assert env["mode"] == "simple"
    assert env["form"]["class_name"] == "MyInput"
    assert 'return "hello"' in env["execute_body"]


def test_parse_back_output():
    env = io_mod.parse_back(SAMPLE_OUTPUT)
    assert env["mode"] == "simple"
    assert env["form"]["class_name"] == "MyOutput"
    assert "print(data)" in env["execute_body"]


def test_update_existing_replaces_body():
    new_src = io_mod.update_existing(
        SAMPLE_INPUT,
        {"class_name": "MyInput"},
        'return "new body"',
    )
    compile(new_src, "<updated>", "exec")
    assert "new body" in new_src
