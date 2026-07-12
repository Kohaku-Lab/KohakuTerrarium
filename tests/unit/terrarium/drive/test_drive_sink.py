"""Contract for the Phase E delivery seam types (design §5.2).

``DeliveryOutcome`` separates physical admission from turn settlement, and
``emit_observation`` is fail-open — a broken observer never propagates.
"""

from kohakuterrarium.terrarium.drive.sink import (
    DeliveryOutcome,
    DriveObservation,
    Settlement,
    SettlementStatus,
    emit_observation,
)


class TestDeliveryOutcome:
    def test_rejected_stopped_is_not_admitted(self):
        outcome = DeliveryOutcome.rejected_stopped()
        assert outcome.admitted is False
        assert outcome.settlement is None

    def test_accepted_carries_settlement_source(self):
        async def settle() -> Settlement:
            return Settlement(SettlementStatus.OK, {"turns": 1})

        outcome = DeliveryOutcome.accepted(settle)
        assert outcome.admitted is True
        assert outcome.settlement is settle

    def test_accepted_without_settlement_is_fire_and_forget(self):
        outcome = DeliveryOutcome.accepted()
        assert outcome.admitted is True and outcome.settlement is None


class TestSettlement:
    def test_settlement_defaults_empty_detail(self):
        settlement = Settlement(SettlementStatus.ERROR)
        assert settlement.status is SettlementStatus.ERROR
        assert settlement.detail == {}


class TestEmitObservation:
    def test_none_observer_is_noop(self):
        emit_observation(None, "drive_created", "d1", {"x": 1})  # must not raise

    def test_records_observation(self):
        seen: list[DriveObservation] = []
        emit_observation(seen.append, "drive_ready", "d1", {"reason": "activated"})
        assert seen == [DriveObservation("drive_ready", "d1", {"reason": "activated"})]

    def test_broken_observer_is_swallowed(self):
        def boom(_obs):
            raise RuntimeError("observer boom")

        # fail-open: the exception must not escape (design §8.8)
        emit_observation(boom, "drive_status_changed", "d1", {"status": "paused"})
