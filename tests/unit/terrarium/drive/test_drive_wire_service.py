"""Unit tests for :mod:`terrarium.drive.wire_service`.

The service-level result DTOs (:class:`DriveView` / :class:`DriveRuntimeStatus`)
and their versioned wire packers: exact round trips, ``to_dict`` JSON-safety, and
rejection of unknown schema versions / malformed payloads.
"""

import json

import pytest

from kohakuterrarium.terrarium.drive.errors import DriveValidationError
from kohakuterrarium.terrarium.drive.wire_service import (
    DriveRuntimeStatus,
    DriveView,
    pack_drive_view,
    pack_runtime_status,
    pack_settings_status,
    unpack_drive_view,
    unpack_runtime_status,
    unpack_settings_status,
)
from tests.unit.terrarium.drive.test_drive_wire import make_record


def _view(**over) -> DriveView:
    base = dict(
        record=make_record(),
        assignee_creature_id="worker",
        assignment_state="assigned",
        availability="available",
        durability="persistent",
        allowed_actions=("read", "update", "transition"),
    )
    base.update(over)
    return DriveView(**base)


class TestDriveView:
    def test_round_trip_preserves_all_fields(self):
        view = _view()
        back = unpack_drive_view(pack_drive_view(view))
        assert back == view

    def test_round_trip_with_no_assignment(self):
        view = _view(
            assignee_creature_id=None, assignment_state=None, allowed_actions=()
        )
        back = unpack_drive_view(pack_drive_view(view))
        assert back.assignee_creature_id is None
        assert back.allowed_actions == ()

    def test_to_dict_is_json_safe_and_bounded(self):
        d = _view().to_dict()
        json.dumps(d)  # must not raise
        assert d["owner"] == make_record().owner.format()
        assert d["allowed_actions"] == ["read", "update", "transition"]
        assert d["assignee_creature_id"] == "worker"

    def test_unknown_schema_version_rejected(self):
        payload = pack_drive_view(_view())
        payload["wire_schema"] = 999
        with pytest.raises(DriveValidationError):
            unpack_drive_view(payload)

    def test_wrong_wire_type_rejected(self):
        with pytest.raises(DriveValidationError):
            unpack_drive_view(pack_runtime_status(DriveRuntimeStatus(enabled=True)))


class TestRuntimeStatus:
    def test_round_trip(self):
        status = DriveRuntimeStatus(
            enabled=True,
            durability="ephemeral",
            registrations=({"name": "generic", "kind": "generic", "available": True},),
            counts={"active": 2, "waiting": 1},
            running_revision="abc123",
        )
        back = unpack_runtime_status(pack_runtime_status(status))
        assert back == status

    def test_bad_counts_rejected(self):
        payload = pack_runtime_status(DriveRuntimeStatus(enabled=True))
        payload["data"]["counts"] = ["not", "a", "dict"]
        with pytest.raises(DriveValidationError):
            unpack_runtime_status(payload)


class TestSettingsStatus:
    def test_round_trip_preserves_dict(self):
        status = {"node": "_host", "settings_revision": "r1", "registrations": []}
        back = unpack_settings_status(pack_settings_status(status))
        assert back == status

    def test_non_dict_rejected(self):
        with pytest.raises(DriveValidationError):
            pack_settings_status(["not", "a", "dict"])
