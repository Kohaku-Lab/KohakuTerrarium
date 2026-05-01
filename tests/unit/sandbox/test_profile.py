from kohakuterrarium.modules.sandbox.presets import READ_ONLY, SHELL, WORKSPACE
from kohakuterrarium.modules.sandbox.profile import profile_intersection
from kohakuterrarium.modules.sandbox.violations import ProfileViolation


def test_profile_intersection_narrows_capabilities() -> None:
    effective = profile_intersection(WORKSPACE, READ_ONLY)

    assert effective.fs_read == "broad"
    assert effective.fs_write == "deny"
    assert effective.network == "deny"


def test_workspace_default_allows_network() -> None:
    assert WORKSPACE.network == "allow"
    assert SHELL.network == "allow"


def test_profile_intersection_unions_fs_deny() -> None:
    left = WORKSPACE.with_overrides(fs_deny=["/secret"])
    right = READ_ONLY.with_overrides(fs_deny=["/tmp/private"])

    effective = profile_intersection(left, right)

    assert effective.fs_deny == ("/secret", "/tmp/private")


def test_profile_violation_serializes() -> None:
    violation = ProfileViolation(
        axis="fs_write",
        operation="write",
        requested="/secret/file",
        profile=WORKSPACE,
    )

    payload = violation.to_dict()

    assert payload["type"] == "profile_violation"
    assert payload["axis"] == "fs_write"
    assert payload["profile"]["name"] == "WORKSPACE"
