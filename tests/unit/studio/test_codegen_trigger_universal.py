"""Unit: ``codegen_trigger.update_existing`` must persist the form's
``universal`` metadata class attribute.

Regression guard for B-iw2-1 (see ``temp/BUGS.md``): the codegen's
own module docstring promises it rewrites "the body of
``wait_for_trigger`` ... **+ universal metadata class attributes**",
but ``update_existing`` only ever replaced the method body — the
``universal`` toggle from the editor form was silently dropped.
"""

from kohakuterrarium.studio.editors import codegen_trigger

_SOURCE = """\
from kohakuterrarium.modules.trigger.base import BaseTrigger


class AlarmTrigger(BaseTrigger):
    universal = False
    setup_tool_name = ""
    setup_description = ""

    async def wait_for_trigger(self):
        return None
"""


def test_update_existing_rewrites_universal_attribute():
    """Contract: a form with ``universal=True`` flips the class-level
    ``universal`` assignment in the rewritten source, and ``parse_back``
    of that source reports ``universal=True``.

    Regression guard for B-iw2-1 (FIXED): ``update_existing`` only
    replaced the ``wait_for_trigger`` method body and silently dropped
    the ``universal`` toggle, despite the module docstring promising it
    rewrites "universal metadata class attributes". The fix routes the
    form's ``universal`` value through ``replace_class_attr_bool``."""
    patched = codegen_trigger.update_existing(
        _SOURCE,
        {"class_name": "AlarmTrigger", "universal": True},
        "return None",
    )
    envelope = codegen_trigger.parse_back(patched)
    assert envelope["form"]["universal"] is True


def test_update_existing_rewrites_setup_string_attributes():
    """Regression guard for B-iw2-1 (FIXED): ``update_existing`` also
    persists the ``setup_tool_name`` / ``setup_description`` string
    class attributes from the form — the same metadata round-trip the
    module docstring promises, previously dropped alongside
    ``universal``."""
    patched = codegen_trigger.update_existing(
        _SOURCE,
        {
            "class_name": "AlarmTrigger",
            "setup_tool_name": "set_alarm",
            "setup_description": "Schedule an alarm.",
        },
        "return None",
    )
    envelope = codegen_trigger.parse_back(patched)
    assert envelope["form"]["setup_tool_name"] == "set_alarm"
    assert envelope["form"]["setup_description"] == "Schedule an alarm."


def test_update_existing_replaces_method_body():
    """Contract (passing baseline): ``update_existing`` does correctly
    replace the ``wait_for_trigger`` body."""
    patched = codegen_trigger.update_existing(
        _SOURCE,
        {"class_name": "AlarmTrigger"},
        "return 'fired'",
    )
    envelope = codegen_trigger.parse_back(patched)
    assert "fired" in envelope["execute_body"]
