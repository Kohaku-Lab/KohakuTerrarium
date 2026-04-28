"""Trigger codegen tests."""

from kohakuterrarium.studio.editors import codegen_trigger as trig_cg

SAMPLE = '''\
"""Sample trigger."""

from typing import Any, ClassVar

from kohakuterrarium.core.events import TriggerEvent
from kohakuterrarium.modules.trigger.base import BaseTrigger


class PingTrigger(BaseTrigger):
    universal: ClassVar[bool] = True
    setup_tool_name: ClassVar[str] = "add_ping"
    setup_description: ClassVar[str] = "Ping every N seconds"

    async def wait_for_trigger(self) -> TriggerEvent | None:
        return None
'''


def test_render_new_compiles():
    source = trig_cg.render_new(
        {
            "name": "my_trig",
            "class_name": "MyTrigger",
            "universal": False,
            "wait_for_trigger_body": "return None",
        }
    )
    compile(source, "<rendered>", "exec")
    assert "class MyTrigger(BaseTrigger)" in source


def test_render_new_universal_emits_metadata():
    source = trig_cg.render_new(
        {
            "name": "ping",
            "class_name": "PingTrigger",
            "universal": True,
            "setup_tool_name": "add_ping",
            "setup_description": "Ping",
            "wait_for_trigger_body": "return None",
        }
    )
    compile(source, "<rendered>", "exec")
    assert "universal: ClassVar[bool] = True" in source
    assert '"add_ping"' in source


def test_parse_back_extracts_form():
    env = trig_cg.parse_back(SAMPLE)
    assert env["mode"] == "simple"
    form = env["form"]
    assert form["class_name"] == "PingTrigger"
    assert form["universal"] is True
    assert form["setup_tool_name"] == "add_ping"
    assert "return None" in env["execute_body"]


def test_update_existing_rewrites_body():
    new_src = trig_cg.update_existing(
        SAMPLE,
        {"class_name": "PingTrigger"},
        "return TriggerEvent(type='ping', content='')",
    )
    compile(new_src, "<updated>", "exec")
    assert "TriggerEvent(type='ping'" in new_src
