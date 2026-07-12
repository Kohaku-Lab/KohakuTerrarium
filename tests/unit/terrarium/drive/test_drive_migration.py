"""Unit tests for :mod:`terrarium.drive.migration` — legacy goal-state import.

Covers the design §14.2 rules: dry-run previews without creating; a re-run is
idempotent by source key; the original payload is preserved under migration
metadata; a legacy "completed" flag can never smuggle in a terminal Drive
(creation only mints non-terminal records).
"""

from types import SimpleNamespace

import pytest

from kohakuterrarium.terrarium.drive.errors import DriveValidationError
from kohakuterrarium.terrarium.drive.migration import (
    MIGRATION_METADATA_KEY,
    migrate_goal_state,
    migrated_source_keys,
)
from kohakuterrarium.terrarium.drive.models import ActorRef
from kohakuterrarium.terrarium.drive.requests import CreateDriveRequest

USER = ActorRef("user", "alice")

_LEGACY = [
    ("goal:1", {"objective": "fix the race", "status": "completed", "done": True}),
    ("goal:2", {"objective": "watch deploy", "status": "active"}),
]


def _mapper(payload):
    return CreateDriveRequest(
        kind="goal",
        title=payload["objective"][:80],
        scope_type="graph",
        scope_id="g1",
        owner=USER,
        owner_scope="actor",
        created_by=USER,
        assignee_creature_id="worker",
    )


class _RecordingSink:
    """Async create sink recording each enriched request; mints fake ids."""

    def __init__(self):
        self.requests = []

    async def __call__(self, request, *, graph_id, actor):
        self.requests.append((request, graph_id, actor))
        return SimpleNamespace(drive_id=f"drv{len(self.requests)}")


class TestDryRun:
    async def test_dry_run_creates_nothing(self):
        sink = _RecordingSink()
        report = await migrate_goal_state(
            None,
            graph_id="g1",
            mapper=_mapper,
            actor=USER,
            legacy=_LEGACY,
            create=sink,
            dry_run=True,
        )
        assert sink.requests == []
        assert len(report.planned) == 2
        assert len(report.created) == 0
        assert report.to_dict()["planned"] == 2

    async def test_dry_run_needs_no_sink(self):
        report = await migrate_goal_state(
            None,
            graph_id="g1",
            mapper=_mapper,
            actor=USER,
            legacy=_LEGACY,
            dry_run=True,
        )
        assert len(report.planned) == 2


class TestCommit:
    async def test_creates_and_preserves_payload(self):
        sink = _RecordingSink()
        report = await migrate_goal_state(
            None,
            graph_id="g1",
            mapper=_mapper,
            actor=USER,
            legacy=_LEGACY,
            create=sink,
        )
        assert len(report.created) == 2
        assert [e.drive_id for e in report.created] == ["drv1", "drv2"]
        # Original payload preserved verbatim under migration metadata.
        first_request = sink.requests[0][0]
        block = first_request.metadata[MIGRATION_METADATA_KEY]
        assert block["source_key"] == "goal:1"
        assert block["source"] == _LEGACY[0][1]
        assert block["migrated_from"] == "goal_state"

    async def test_never_infers_completed(self):
        # The legacy 'completed' flag must not become a terminal Drive: creation
        # only ever mints a non-terminal record, and CreateDriveRequest carries
        # no status field at all.
        sink = _RecordingSink()
        await migrate_goal_state(
            None,
            graph_id="g1",
            mapper=_mapper,
            actor=USER,
            legacy=[_LEGACY[0]],
            create=sink,
        )
        request = sink.requests[0][0]
        assert not hasattr(request, "status")

    async def test_idempotent_skips_already_migrated(self):
        sink = _RecordingSink()
        report = await migrate_goal_state(
            None,
            graph_id="g1",
            mapper=_mapper,
            actor=USER,
            legacy=_LEGACY,
            create=sink,
            already_migrated={"goal:1"},
        )
        assert len(report.skipped) == 1
        assert report.skipped[0].source_key == "goal:1"
        assert len(report.created) == 1
        assert report.created[0].source_key == "goal:2"
        assert len(sink.requests) == 1

    async def test_migrated_source_keys_reads_metadata(self):
        records = [
            SimpleNamespace(
                metadata={MIGRATION_METADATA_KEY: {"source_key": "goal:1"}}
            ),
            SimpleNamespace(metadata={"other": 1}),
            SimpleNamespace(metadata=None),
        ]
        assert migrated_source_keys(records) == {"goal:1"}


class TestValidation:
    async def test_missing_sink_rejected(self):
        with pytest.raises(DriveValidationError):
            await migrate_goal_state(
                None,
                graph_id="g1",
                mapper=_mapper,
                actor=USER,
                legacy=_LEGACY,
            )

    async def test_bad_mapper_return_rejected(self):
        async def sink(request, *, graph_id, actor):
            return SimpleNamespace(drive_id="x")

        with pytest.raises(DriveValidationError):
            await migrate_goal_state(
                None,
                graph_id="g1",
                mapper=lambda p: {"not": "a request"},
                actor=USER,
                legacy=_LEGACY,
                create=sink,
            )

    async def test_default_reader_returns_empty_without_hook(self):
        # A source store with no goal-state reader yields an empty (safe) report
        # rather than guessing record boundaries.
        report = await migrate_goal_state(
            object(),
            graph_id="g1",
            mapper=_mapper,
            actor=USER,
            dry_run=True,
        )
        assert report.entries == ()
