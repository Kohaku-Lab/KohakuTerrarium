"""Framework wake settings must not require extension configuration hooks."""

from types import SimpleNamespace

import pytest

from kohakuterrarium.terrarium.drive.errors import DriveValidationError
from kohakuterrarium.terrarium.drive.registration import GenericDriveRegistration
from kohakuterrarium.terrarium.drive.registration_options import (
    apply_registration_wake_policy,
)
from kohakuterrarium.terrarium.drive.snapshot import EnabledRegistrySnapshot


class _SlottedRegistration:
    __slots__ = ("_kt_effective_options",)
    name = "slotted"
    kind = "custom"
    schema_version = 1


def test_default_policy_keeps_existing_slotted_registrations_usable():
    registration = _SlottedRegistration()
    apply_registration_wake_policy(registration)
    assert (
        not EnabledRegistrySnapshot.build([registration])
        .for_kind("custom")
        .unconditional_wake
    )
    with pytest.raises(DriveValidationError, match="unconditional_wake"):
        apply_registration_wake_policy(registration, unconditional_wake=True)


@pytest.mark.parametrize("value", [None, 0, 1, "false", {}, []])
def test_wake_policy_rejects_non_booleans(value):
    with pytest.raises(DriveValidationError, match="unconditional_wake"):
        apply_registration_wake_policy(SimpleNamespace(), unconditional_wake=value)


def test_policy_changes_take_effect_only_in_a_new_snapshot():
    registration = GenericDriveRegistration()
    before = EnabledRegistrySnapshot.build([registration])
    apply_registration_wake_policy(registration, unconditional_wake=True)
    enabled = EnabledRegistrySnapshot.build([registration])
    assert not before.for_kind("generic").unconditional_wake
    assert enabled.for_kind("generic").unconditional_wake
    apply_registration_wake_policy(registration, unconditional_wake=False)
    assert enabled.for_kind("generic").unconditional_wake
    assert (
        not EnabledRegistrySnapshot.build([registration])
        .for_kind("generic")
        .unconditional_wake
    )
