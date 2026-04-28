"""Input / output codegen tests."""

from kohakuterrarium.studio.editors import codegen_io as io_mod

# Canonical framework shape — BaseInputModule.get_input() / BaseOutputModule.write().
SAMPLE_INPUT = '''\
"""Sample input."""

from kohakuterrarium.modules.input.base import BaseInputModule


class MyInput(BaseInputModule):
    async def get_input(self):
        return "hello"
'''


SAMPLE_OUTPUT = '''\
"""Sample output."""

from kohakuterrarium.modules.output.base import BaseOutputModule


class MyOutput(BaseOutputModule):
    async def write(self, content: str) -> None:
        print(content)
'''


# Legacy-style shape for back-compat with any scaffolds authored under the
# original (wrong) method names.
LEGACY_OUTPUT = '''\
"""Sample legacy output."""


class MyOutput:
    async def write_output(self, data):
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
    assert "async def get_input" in source


def test_render_new_output_compiles():
    source = io_mod.render_new(
        {
            "kind": "output",
            "name": "my_output",
            "class_name": "MyOutput",
            "description": "x",
            "body": "print(content)",
        }
    )
    compile(source, "<rendered>", "exec")
    assert "async def write(" in source


def test_parse_back_input():
    env = io_mod.parse_back(SAMPLE_INPUT)
    assert env["mode"] == "simple"
    assert env["form"]["class_name"] == "MyInput"
    assert env["form"]["method_name"] == "get_input"
    assert 'return "hello"' in env["execute_body"]


def test_parse_back_output():
    env = io_mod.parse_back(SAMPLE_OUTPUT)
    assert env["mode"] == "simple"
    assert env["form"]["class_name"] == "MyOutput"
    assert env["form"]["method_name"] == "write"
    assert "print(content)" in env["execute_body"]


def test_parse_back_legacy_write_output():
    """Files that still use the old write_output name are still parseable."""
    env = io_mod.parse_back(LEGACY_OUTPUT)
    assert env["mode"] == "simple"
    assert env["form"]["method_name"] == "write_output"


def test_update_existing_replaces_body():
    new_src = io_mod.update_existing(
        SAMPLE_INPUT,
        {"class_name": "MyInput"},
        'return "new body"',
    )
    compile(new_src, "<updated>", "exec")
    assert "new body" in new_src
