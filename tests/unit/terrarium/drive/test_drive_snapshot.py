"""Unit tests for :mod:`kohakuterrarium.terrarium.drive.snapshot`.

Behavior asserts on the enabled-registry snapshot: name/kind collision (with
provenance), immutability, load-time prompt normalization + byte caps
(optional oversize omitted, required oversize -> unavailable, aggregate budget),
broken-required-role -> unavailable, and derived availability against a record.
"""

import dataclasses
from datetime import datetime, timezone

import pytest

from kohakuterrarium.terrarium.drive.errors import DriveValidationError
from kohakuterrarium.terrarium.drive.models import (
    ActorRef,
    DriveRecord,
    DriveStatus,
)
from kohakuterrarium.terrarium.drive.models import DriveAvailability as DA
from kohakuterrarium.terrarium.drive.registration import (
    DriveProjection,
    DriveRegistrationDescriptor,
    Readiness,
    VerificationResult,
)
from kohakuterrarium.terrarium.drive.snapshot import (
    EnabledRegistrySnapshot,
    availability_for_kind,
    derive_availability,
)

_NOW = datetime(2026, 7, 11, tzinfo=timezone.utc)


class _Reg:
    """Configurable in-process registration stub."""

    def __init__(
        self,
        name,
        kind,
        *,
        schema_version=1,
        min_schema_version=None,
        source_package=None,
        required=("spec",),
        optional=("prompt",),
        prompt=None,
    ):
        self.name = name
        self.kind = kind
        self.schema_version = schema_version
        self._min = min_schema_version
        self._src = source_package
        self._required = frozenset(required)
        self._optional = frozenset(optional)
        self._prompt = prompt

    def descriptor(self):
        return DriveRegistrationDescriptor(
            name=self.name,
            kind=self.kind,
            schema_version=self.schema_version,
            min_schema_version=self._min,
            source_package=self._src,
            required_roles=self._required,
            optional_roles=self._optional,
            prompt_contribution=self._prompt,
        )

    def validate_spec(self, spec):
        return None

    def validate_transition(self, before, proposal, context):
        return None

    def readiness(self, drive, deps, now):
        return Readiness(ready=True)

    def project_event(self, drive, assignment, reason):
        return DriveProjection(event_type="drive_ready")

    def verify_terminal(self, proposal, context):
        return VerificationResult(approved=True)

    def prompt_contribution(self):
        return self._prompt


class _MissingVerifier:
    """Declares a required 'verifier' role but does not implement it."""

    name = "broken"
    kind = "broken"
    schema_version = 1

    def descriptor(self):
        return DriveRegistrationDescriptor(
            name="broken",
            kind="broken",
            schema_version=1,
            required_roles=frozenset({"verifier"}),
        )


def _record(kind="generic", schema_version=1):
    return DriveRecord(
        drive_id="d1",
        kind=kind,
        schema_version=schema_version,
        revision=1,
        title="t",
        spec={},
        presentation={},
        metadata={},
        scope_type="creature",
        scope_id="c1",
        origin_scope_id="c1",
        status=DriveStatus.ACTIVE,
        status_reason=None,
        priority=0,
        policy_name="generic",
        created_by=ActorRef("creature", "c1"),
        owner=ActorRef("creature", "c1"),
        owner_scope="creature",
        created_at=_NOW,
        updated_by=ActorRef("creature", "c1"),
        updated_at=_NOW,
        lifecycle_epoch=0,
    )


class TestBuildAndLookup:
    def test_builds_available_entry(self):
        snap = EnabledRegistrySnapshot.build([_Reg("generic", "generic", prompt="p")])
        entry = snap.for_kind("generic")
        assert entry is not None
        assert entry.available is True
        assert snap.available_kinds() == frozenset({"generic"})
        assert snap.get("generic") is entry
        assert snap.get("missing") is None
        assert snap.for_kind("missing") is None

    def test_prompt_contributions_ordered_by_name(self):
        snap = EnabledRegistrySnapshot.build(
            [_Reg("b", "b", prompt="beta"), _Reg("a", "a", prompt="alpha")]
        )
        assert snap.prompt_contributions() == (("a", "alpha"), ("b", "beta"))

    def test_empty_snapshot(self):
        snap = EnabledRegistrySnapshot.build([])
        assert snap.is_empty is True
        assert snap.available_kinds() == frozenset()


class TestCollisions:
    def test_duplicate_name_is_hard_error_with_provenance(self):
        with pytest.raises(
            DriveValidationError, match="duplicate drive registration name 'x'"
        ):
            EnabledRegistrySnapshot.build(
                [
                    _Reg("x", "a", source_package="pkg-a"),
                    _Reg("x", "b", source_package="pkg-b"),
                ]
            )

    def test_duplicate_kind_is_hard_error_with_provenance(self):
        with pytest.raises(DriveValidationError) as exc:
            EnabledRegistrySnapshot.build(
                [
                    _Reg("first", "shared", source_package="pkg-a"),
                    _Reg("second", "shared", source_package="pkg-b"),
                ]
            )
        msg = str(exc.value)
        assert "kind 'shared'" in msg
        assert "pkg-a/first" in msg and "pkg-b/second" in msg


class TestImmutability:
    def test_snapshot_is_frozen_tuple(self):
        snap = EnabledRegistrySnapshot.build([_Reg("generic", "generic")])
        assert isinstance(snap.entries, tuple)
        with pytest.raises(dataclasses.FrozenInstanceError):
            snap.entries = ()  # type: ignore[misc]


class TestPromptNormalization:
    def test_optional_oversize_prompt_omitted_but_available(self):
        snap = EnabledRegistrySnapshot.build(
            [_Reg("a", "a", optional=("prompt",), prompt="y" * 5000)],
            per_prompt_max_bytes=2048,
        )
        entry = snap.get("a")
        assert entry.available is True
        assert entry.prompt_text is None  # omitted, not fatal
        assert snap.prompt_contributions() == ()

    def test_required_oversize_prompt_makes_registration_unavailable(self):
        snap = EnabledRegistrySnapshot.build(
            [_Reg("a", "a", required=("spec", "prompt"), prompt="y" * 5000)],
            per_prompt_max_bytes=2048,
        )
        entry = snap.get("a")
        assert entry.available is False
        assert "required prompt" in entry.unavailable_reason

    def test_aggregate_budget_omits_later_prose(self):
        snap = EnabledRegistrySnapshot.build(
            [_Reg("a", "a", prompt="x" * 1000), _Reg("b", "b", prompt="y" * 1000)],
            per_prompt_max_bytes=5000,
            aggregate_prompt_max_bytes=1500,
        )
        assert snap.get("a").prompt_text is not None  # first fits
        assert snap.get("b").prompt_text is None  # over aggregate budget
        assert snap.get("b").available is True  # prose omission is not fatal

    def test_required_prompt_over_aggregate_budget_is_fatal(self):
        # R1-27: a required prompt that fits the per-registration cap but does
        # not fit the AGGREGATE budget must fail closed (unavailable), not be
        # silently omitted while the registration stays available.
        snap = EnabledRegistrySnapshot.build(
            [
                _Reg("a", "a", required=("spec", "prompt"), prompt="x" * 1000),
                _Reg("b", "b", required=("spec", "prompt"), prompt="y" * 1000),
            ],
            per_prompt_max_bytes=5000,
            aggregate_prompt_max_bytes=1500,
        )
        # 'a' fits first (name-ordered); 'b' overflows the aggregate budget.
        assert snap.get("a").available is True
        assert snap.get("a").prompt_text is not None
        assert snap.get("b").available is False
        assert "aggregate" in (snap.get("b").unavailable_reason or "")

    def test_optional_prompt_over_aggregate_stays_available(self):
        # The optional counterpart to the above: omission remains non-fatal.
        snap = EnabledRegistrySnapshot.build(
            [
                _Reg("a", "a", required=("spec", "prompt"), prompt="x" * 1000),
                _Reg("b", "b", optional=("prompt",), prompt="y" * 1000),
            ],
            per_prompt_max_bytes=5000,
            aggregate_prompt_max_bytes=1500,
        )
        assert snap.get("b").available is True
        assert snap.get("b").prompt_text is None


class _NoneRequiredPrompt(_Reg):
    """Declares ``prompt`` required; its callable returns ``None``."""

    def __init__(self):
        super().__init__(
            "np", "np", required=("spec", "prompt"), optional=(), prompt=None
        )


class _RaisingRequiredPrompt(_Reg):
    """Declares ``prompt`` required; its callable raises."""

    def __init__(self):
        super().__init__(
            "rp", "rp", required=("spec", "prompt"), optional=(), prompt="ok"
        )

    def prompt_contribution(self):
        raise RuntimeError("prompt boom")


class _NonStringRequiredPrompt(_Reg):
    """Declares ``prompt`` required; its callable returns a non-string."""

    def __init__(self):
        super().__init__(
            "sp", "sp", required=("spec", "prompt"), optional=(), prompt="ok"
        )

    def prompt_contribution(self):
        return 123


class TestRequiredPromptFailClosed:
    """A REQUIRED prompt that raises / returns None / returns a non-string must
    fail closed (unavailable), same as a byte-budget overflow — never a silently
    prompt-less registration that still declares prompt required (R1-27 edge)."""

    @pytest.mark.parametrize(
        "reg",
        [_NoneRequiredPrompt(), _RaisingRequiredPrompt(), _NonStringRequiredPrompt()],
    )
    def test_broken_required_prompt_is_unavailable(self, reg):
        snap = EnabledRegistrySnapshot.build([reg])
        entry = snap.get(reg.name)
        assert entry.available is False
        assert "prompt" in (entry.unavailable_reason or "")
        assert snap.available_kinds() == frozenset()

    def test_optional_broken_prompt_stays_available(self):
        # The optional counterpart: a broken OPTIONAL prompt is omitted, not fatal.
        class _OptNone(_Reg):
            def __init__(self):
                super().__init__(
                    "on", "on", required=("spec",), optional=("prompt",), prompt=None
                )

        snap = EnabledRegistrySnapshot.build([_OptNone()])
        entry = snap.get("on")
        assert entry.available is True
        assert entry.prompt_text is None


class TestBrokenRequiredRole:
    def test_missing_required_role_makes_unavailable(self):
        snap = EnabledRegistrySnapshot.build([_MissingVerifier()])
        entry = snap.for_kind("broken")
        assert entry.available is False
        assert "verifier" in entry.unavailable_reason


class TestAvailabilityDerivation:
    def test_available_kind_and_version(self):
        snap = EnabledRegistrySnapshot.build(
            [_Reg("generic", "generic", schema_version=2, min_schema_version=1)]
        )
        assert availability_for_kind("generic", 1, snap) is DA.AVAILABLE
        assert availability_for_kind("generic", 2, snap) is DA.AVAILABLE

    def test_unknown_kind_is_disabled(self):
        snap = EnabledRegistrySnapshot.build([_Reg("generic", "generic")])
        assert availability_for_kind("goal", 1, snap) is DA.REGISTRATION_DISABLED

    def test_broken_registration_is_unavailable(self):
        snap = EnabledRegistrySnapshot.build([_MissingVerifier()])
        assert availability_for_kind("broken", 1, snap) is DA.REGISTRATION_UNAVAILABLE

    def test_unknown_schema_version_is_incompatible(self):
        snap = EnabledRegistrySnapshot.build(
            [_Reg("generic", "generic", schema_version=1)]
        )
        assert availability_for_kind("generic", 2, snap) is DA.REGISTRATION_INCOMPATIBLE

    def test_derive_availability_from_record_does_not_mutate(self):
        snap = EnabledRegistrySnapshot.build([_Reg("generic", "generic")])
        record = _record(kind="generic", schema_version=1)
        assert derive_availability(record, snap) is DA.AVAILABLE
        # Pure: the record is unchanged (frozen dataclass) and its kind is intact.
        assert record.kind == "generic"
        assert (
            derive_availability(_record(kind="goal"), snap) is DA.REGISTRATION_DISABLED
        )
