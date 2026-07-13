"""Unit tests for :mod:`terrarium.drive.prompt` — the Drive prompt plugin.

Pins: bounded generic contract always present; enabled-registration prose in
stable name order; NO current-record dump; a disabled/empty snapshot
contributes only the generic contract; a snapshot swap refreshes without
duplicating.
"""

from kohakuterrarium.modules.plugin.base import PluginContext
from kohakuterrarium.terrarium.drive.prompt import DriveRuntimePromptPlugin
from kohakuterrarium.terrarium.drive.registration import (
    DriveRegistrationDescriptor,
    GenericDriveRegistration,
)
from kohakuterrarium.terrarium.drive.snapshot import EnabledRegistrySnapshot


class _AltRegistration:
    """A second enabled registration whose name sorts after ``generic``."""

    name = "zzz_alt"
    kind = "alt"
    schema_version = 1

    def descriptor(self) -> DriveRegistrationDescriptor:
        return DriveRegistrationDescriptor(
            name=self.name,
            kind=self.kind,
            schema_version=1,
            required_roles=frozenset({"spec"}),
            prompt_contribution="ALT-KIND-PROSE",
        )

    def validate_spec(self, spec):
        return None

    def prompt_contribution(self):
        return "ALT-KIND-PROSE"


def _ctx() -> PluginContext:
    return PluginContext(agent_name="c1")


def test_generic_contract_always_present():
    plugin = DriveRuntimePromptPlugin(EnabledRegistrySnapshot.build([]))
    text = plugin.get_prompt_content(_ctx())
    assert text is not None
    assert "Drive runtime" in text
    assert "do not begin by listing or inspecting Drives" in text
    # An empty snapshot contributes NO kind prose.
    assert "Drive kind:" not in text


def test_enabled_registration_prose_in_name_order():
    snapshot = EnabledRegistrySnapshot.build(
        [_AltRegistration(), GenericDriveRegistration()]
    )
    plugin = DriveRuntimePromptPlugin(snapshot)
    text = plugin.get_prompt_content(_ctx())
    assert "### Drive kind: generic" in text
    assert "### Drive kind: zzz_alt" in text
    assert "ALT-KIND-PROSE" in text
    # generic sorts before zzz_alt — deterministic, name-ordered.
    assert text.index("Drive kind: generic") < text.index("Drive kind: zzz_alt")


def test_no_current_record_dump():
    # The contributor takes only a PluginContext — it cannot dump records,
    # and its text never names a drive id.
    snapshot = EnabledRegistrySnapshot.build([GenericDriveRegistration()])
    plugin = DriveRuntimePromptPlugin(snapshot)
    text = plugin.get_prompt_content(_ctx())
    assert "drive_id" not in text
    assert "revision" not in text


def test_snapshot_swap_refreshes_without_duplication():
    plugin = DriveRuntimePromptPlugin(EnabledRegistrySnapshot.build([]))
    before = plugin.get_prompt_content(_ctx())
    assert "ALT-KIND-PROSE" not in before
    plugin.set_snapshot(EnabledRegistrySnapshot.build([_AltRegistration()]))
    after = plugin.get_prompt_content(_ctx())
    assert after.count("ALT-KIND-PROSE") == 1
    # The generic contract is still present exactly once.
    assert after.count("## Drive runtime") == 1


def test_none_snapshot_still_yields_generic_contract():
    plugin = DriveRuntimePromptPlugin(None)
    text = plugin.get_prompt_content(_ctx())
    assert text is not None and "Drive runtime" in text
    assert "Drive kind:" not in text
