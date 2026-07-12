"""Unit tests for :mod:`kohakuterrarium.terrarium.drive.config`.

Behavior asserts on the explicit runtime-config DTOs: construction-time
validation of nonsense values, §7.4 retention defaults, the enabled-without-
registrations rule (§8.3), and the in-process :class:`DriveRuntimeSpec` grouping.
"""

import pytest

from kohakuterrarium.terrarium.drive.config import (
    DriveRetentionConfig,
    DriveRetryConfig,
    DriveRuntimeConfig,
    DriveRuntimeSpec,
    default_registrations,
    validate_runtime_selection,
)
from kohakuterrarium.terrarium.drive.errors import DriveValidationError
from kohakuterrarium.terrarium.drive.registration import GenericDriveRegistration


class TestRuntimeConfig:
    def test_defaults_are_disabled(self):
        cfg = DriveRuntimeConfig()
        assert cfg.enabled is False
        assert cfg.max_active_per_creature == 8
        assert cfg.max_consecutive_drive_turns == 3
        assert cfg.spec_max_bytes == 16384

    def test_enabled_true_is_valid(self):
        assert DriveRuntimeConfig(enabled=True).enabled is True

    def test_zero_limit_rejected(self):
        with pytest.raises(DriveValidationError, match="max_active_per_creature"):
            DriveRuntimeConfig(max_active_per_creature=0)

    def test_bool_limit_rejected(self):
        # bool is an int subclass; a flag must not masquerade as a count.
        with pytest.raises(DriveValidationError, match="must be an int"):
            DriveRuntimeConfig(max_pending_per_graph=True)

    def test_zero_byte_limit_rejected(self):
        with pytest.raises(DriveValidationError, match="spec_max_bytes"):
            DriveRuntimeConfig(spec_max_bytes=0)

    def test_wrong_retry_type_rejected(self):
        with pytest.raises(DriveValidationError, match="retry must be"):
            DriveRuntimeConfig(retry={"max_attempts": 3})


class TestRetryConfig:
    def test_defaults(self):
        r = DriveRetryConfig()
        assert r.max_attempts == 5
        assert r.max_backoff_s == 300.0

    def test_zero_attempts_rejected(self):
        with pytest.raises(DriveValidationError, match="max_attempts"):
            DriveRetryConfig(max_attempts=0)

    def test_max_backoff_below_initial_rejected(self):
        with pytest.raises(DriveValidationError, match="max_backoff_s must be >="):
            DriveRetryConfig(initial_backoff_s=10, max_backoff_s=5)

    def test_jitter_over_one_rejected(self):
        with pytest.raises(DriveValidationError, match="jitter must be within"):
            DriveRetryConfig(jitter=1.5)

    def test_negative_jitter_rejected(self):
        with pytest.raises(DriveValidationError, match="jitter"):
            DriveRetryConfig(jitter=-0.1)


class TestRetentionConfig:
    def test_defaults_match_design_7_4(self):
        r = DriveRetentionConfig()
        assert r.terminal_days == 90
        assert r.acknowledged_delivery_days == 30
        assert r.superseded_delivery_days == 7
        assert r.dead_letter_days == 90

    def test_negative_day_rejected(self):
        with pytest.raises(DriveValidationError, match="terminal_days"):
            DriveRetentionConfig(terminal_days=-1)

    def test_zero_progress_count_rejected(self):
        with pytest.raises(DriveValidationError, match="progress_max_count"):
            DriveRetentionConfig(progress_max_count=0)


class TestRuntimeSelection:
    def test_default_registrations_is_generic(self):
        regs = default_registrations()
        assert [r.name for r in regs] == ["generic"]

    def test_enabled_with_no_registrations_rejected(self):
        with pytest.raises(DriveValidationError, match="no registrations"):
            validate_runtime_selection(DriveRuntimeConfig(enabled=True), [])

    def test_disabled_with_no_registrations_ok(self):
        validate_runtime_selection(DriveRuntimeConfig(enabled=False), [])

    def test_enabled_with_registrations_ok(self):
        validate_runtime_selection(
            DriveRuntimeConfig(enabled=True), [GenericDriveRegistration()]
        )


class TestRuntimeSpec:
    def test_valid_spec_groups_pieces(self):
        spec = DriveRuntimeSpec(
            config=DriveRuntimeConfig(enabled=True),
            registrations=(GenericDriveRegistration(),),
            persistence="persistent",
            source_revision="rev-7",
            target_node="worker-1",
        )
        assert spec.persistence == "persistent"
        assert spec.source_revision == "rev-7"
        assert [r.name for r in spec.registrations] == ["generic"]

    def test_enabled_without_registrations_rejected(self):
        with pytest.raises(DriveValidationError, match="no registrations"):
            DriveRuntimeSpec(config=DriveRuntimeConfig(enabled=True), registrations=())

    def test_registrations_must_be_tuple(self):
        with pytest.raises(DriveValidationError, match="registrations must be a tuple"):
            DriveRuntimeSpec(
                config=DriveRuntimeConfig(enabled=True),
                registrations=[GenericDriveRegistration()],
            )

    def test_bad_persistence_rejected(self):
        with pytest.raises(DriveValidationError, match="persistence must be one of"):
            DriveRuntimeSpec(config=DriveRuntimeConfig(), persistence="maybe")

    def test_wrong_config_type_rejected(self):
        with pytest.raises(DriveValidationError, match="config must be"):
            DriveRuntimeSpec(config={"enabled": True})
