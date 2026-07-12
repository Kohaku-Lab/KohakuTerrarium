"""Unit tests for the rich-CLI Drive record overlay + its pure format helpers.

Deterministic: the overlay is driven against a scripted service (no engine, no
real terrarium) and the async methods are awaited directly. Covers list/detail
loading, capability-filtered actions, action wiring, the server-still-rejects
path for a hidden/served action, live-event reload, and no-runtime degradation.
"""

from datetime import datetime, timedelta, timezone

import pytest

from kohakuterrarium.builtins.cli_rich.dialogs import drive_format as fmt
from kohakuterrarium.builtins.cli_rich.dialogs.drive_overlay import DriveOverlay
from kohakuterrarium.terrarium.drive.errors import DrivePermissionError
from kohakuterrarium.terrarium.drive.models import (
    ActorRef,
    DriveProgress,
    DriveRecord,
    DriveStatus,
)
from kohakuterrarium.terrarium.drive.wire_service import DriveView

NOW = datetime(2026, 7, 12, tzinfo=timezone.utc)
USER = ActorRef("user", "local")


def make_record(drive_id, status, *, priority=0, not_before=None, reason=None):
    return DriveRecord(
        drive_id=drive_id,
        kind="generic",
        schema_version=1,
        revision=4,
        title=f"Watch {drive_id}",
        spec={},
        presentation={},
        metadata={},
        scope_type="graph",
        scope_id="g1",
        origin_scope_id="g1",
        status=status,
        status_reason=reason,
        priority=priority,
        policy_name="generic",
        created_by=USER,
        owner=USER,
        owner_scope="actor",
        created_at=NOW,
        updated_by=USER,
        updated_at=NOW,
        lifecycle_epoch=0,
        not_before=not_before,
    )


def make_view(drive_id, status, *, actions=("transition", "report_progress"), **kw):
    return DriveView(
        record=make_record(drive_id, status, **kw),
        assignee_creature_id="c1",
        assignment_state="assigned",
        availability="available",
        durability="persistent",
        allowed_actions=actions,
    )


class ScriptedService:
    """Minimal in-memory TerrariumService surface the overlay uses."""

    def __init__(self, views, *, reject=False):
        self._views = {v.record.drive_id: v for v in views}
        self.reject = reject
        self.calls = []

    async def get_creature_info(self, creature_id):
        return type("Info", (), {"graph_id": "g1"})()

    async def list_drives(self, **kwargs):
        self.calls.append(("list_drives", kwargs))
        views = list(self._views.values())
        assignee = kwargs.get("assignee_creature_id")
        if assignee:
            views = [v for v in views if v.assignee_creature_id == assignee]
        statuses = kwargs.get("statuses")
        if statuses:
            views = [v for v in views if v.record.status in statuses]
        return tuple(views)

    async def get_drive(self, drive_id, **kwargs):
        return self._views.get(drive_id)

    async def list_drive_progress(self, drive_id, *, actor, is_privileged=False):
        # Mirrors the post-R1-02 service signature: progress reads are
        # actor/privilege-authorized, so ``actor`` is required (no default).
        self.calls.append(("list_drive_progress", drive_id, actor, is_privileged))
        return (DriveProgress("p1", drive_id, USER, "did a thing", NOW),)

    async def transition_drive(self, drive_id, target, **kwargs):
        self.calls.append(("transition_drive", drive_id, target, kwargs))
        if self.reject:
            raise DrivePermissionError("not allowed")
        self._views[drive_id] = make_view(drive_id, target)
        return self._views[drive_id]

    async def wake_drive(self, drive_id, **kwargs):
        self.calls.append(("wake_drive", drive_id, kwargs))
        if self.reject:
            raise DrivePermissionError("not allowed")
        return self._views[drive_id]

    async def report_drive_progress(self, drive_id, *, summary, **kwargs):
        self.calls.append(("report_drive_progress", drive_id, summary))
        if self.reject:
            raise DrivePermissionError("not allowed")
        return DriveProgress("p2", drive_id, USER, summary, NOW)


class Harness:
    """Wraps a DriveOverlay + a coroutine collector for deterministic drives."""

    def __init__(self, service, *, engine=object()):
        self.service = service
        self.pending = []
        self.invalidated = 0
        self.closed = 0
        self.overlay = DriveOverlay(
            get_engine=lambda: engine,
            get_creature_id=lambda: "c1",
            schedule=self.pending.append,
            on_invalidate=self._invalidate,
            on_close=self._close,
        )
        # Inject the scripted service by overriding the resolver.
        self.overlay._service = lambda: service
        self.overlay.visible = True

    def _invalidate(self):
        self.invalidated += 1

    def _close(self):
        self.closed += 1

    async def drain(self):
        while self.pending:
            coro = self.pending.pop(0)
            await coro


# ── pure format helpers ─────────────────────────────────────────


def test_project_view_pulls_not_before_and_fields():
    wake = NOW + timedelta(hours=2)
    view = make_view("d1", DriveStatus.WAITING, not_before=wake)
    row = fmt.project_view(view)
    assert row["drive_id"] == "d1"
    assert row["status"] == "waiting"
    assert row["owner"] == "user:local"
    assert row["assignee_creature_id"] == "c1"
    assert row["not_before"] == wake.isoformat()
    assert row["allowed_actions"] == ("transition", "report_progress")


def test_status_label_and_badges_have_text_not_only_colour():
    icon, label, style = fmt.status_meta("blocked")
    assert label == "blocked" and icon and style
    row = {
        "status": "blocked",
        "availability": "registration_disabled",
        "assignment_state": "orphaned",
    }
    badges = [text for text, _ in fmt.warning_badges(row)]
    assert "registration disabled" in badges
    assert "orphaned" in badges
    assert "needs attention" in badges


def test_capability_filter_hides_actions_missing_gate():
    # report_progress only -> no pause/resume/cancel/wake, just progress.
    row = {"status": "active", "allowed_actions": ("report_progress",)}
    assert [a["id"] for a in fmt.enabled_actions(row)] == ["progress"]
    # transition present -> pause + cancel offered for an active drive.
    row2 = {"status": "active", "allowed_actions": ("transition", "report_progress")}
    ids = {a["id"] for a in fmt.enabled_actions(row2)}
    assert ids == {"pause", "cancel", "progress"}


# ── overlay behaviour ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_reload_populates_and_orders_rows():
    svc = ScriptedService(
        [
            make_view("low", DriveStatus.ACTIVE, priority=1),
            make_view("done", DriveStatus.COMPLETED),
            make_view("high", DriveStatus.ACTIVE, priority=9),
        ]
    )
    h = Harness(svc)
    await h.overlay.reload()
    ids = [r["drive_id"] for r in h.overlay._rows]
    # Non-terminal (by priority desc) before terminal.
    assert ids == ["high", "low", "done"]
    assert h.invalidated >= 1


@pytest.mark.asyncio
async def test_detail_load_and_action_confirm_calls_service():
    svc = ScriptedService([make_view("d1", DriveStatus.ACTIVE)])
    h = Harness(svc)
    await h.overlay.reload()
    await h.overlay.load_detail("d1")
    assert h.overlay.mode == "detail"
    assert h.overlay._detail_row["drive_id"] == "d1"
    # 'p' opens a confirm for pause; 'y' schedules the transition.
    h.overlay.handle_text("p")
    assert h.overlay.mode == "confirm"
    h.overlay.handle_text("y")
    await h.drain()
    kinds = [c for c in svc.calls if c[0] == "transition_drive"]
    assert kinds and kinds[0][2] == DriveStatus.PAUSED


@pytest.mark.asyncio
async def test_detail_load_forwards_actor_to_progress_post_r1_02():
    # R1-02 made ``list_drive_progress`` require actor/privilege. The overlay's
    # detail load must forward its local-operator context, not omit it — else the
    # call raises TypeError the moment a user opens a Drive's detail.
    svc = ScriptedService([make_view("d1", DriveStatus.ACTIVE)])
    h = Harness(svc)
    await h.overlay.reload()
    await h.overlay.load_detail("d1")
    assert h.overlay.mode == "detail"
    # Progress actually loaded (empty/raised if the actor kwarg were dropped).
    assert [p.progress_id for p in h.overlay._detail_progress] == ["p1"]
    progress_calls = [c for c in svc.calls if c[0] == "list_drive_progress"]
    assert progress_calls, "detail load must query progress"
    _, drive_id, actor, is_priv = progress_calls[0]
    assert drive_id == "d1"
    assert isinstance(actor, ActorRef)
    assert is_priv is True  # the local rich console is the trusted operator


@pytest.mark.asyncio
async def test_hidden_action_is_not_offered_but_server_still_rejects_safely():
    # allowed_actions lacks "transition": pause must not be offered.
    svc = ScriptedService(
        [make_view("d1", DriveStatus.ACTIVE, actions=("report_progress",))],
        reject=True,
    )
    h = Harness(svc)
    await h.overlay.reload()
    await h.overlay.load_detail("d1")
    assert "pause" not in {a["id"] for a in h.overlay.enabled_actions()}
    # Pressing 'p' anyway is a no-op flash, not a crash, and never calls the server.
    h.overlay.handle_text("p")
    assert h.overlay.mode == "detail"
    assert not any(c[0] == "transition_drive" for c in svc.calls)
    # And if a served action is reached, a server rejection surfaces as an error.
    h.overlay.handle_text("g")  # progress -> input mode
    assert h.overlay.mode == "progress"
    h.overlay._progress_text = "note"
    await h.overlay._submit_progress()
    assert "permission denied" in h.overlay._error


@pytest.mark.asyncio
async def test_list_navigation_filter_and_scope():
    svc = ScriptedService([make_view("d1", DriveStatus.ACTIVE)])
    h = Harness(svc)
    await h.overlay.reload()
    assert h.overlay.status_filter_label == "all"
    # 's' cycles the status filter (a printable char -> handle_text).
    h.overlay.handle_text("s")
    await h.drain()
    assert h.overlay.status_filter_label == "active"
    # 'tab' cycles the scope; a stray letter must NOT scramble it.
    assert h.overlay.scope == "mine"
    h.overlay.handle_key("tab")
    await h.drain()
    assert h.overlay.scope == "graph"
    h.overlay.handle_text("g")  # no list-mode meaning: scope stays put
    assert h.overlay.scope == "graph"


@pytest.mark.asyncio
async def test_note_event_reloads_while_open():
    svc = ScriptedService([make_view("d1", DriveStatus.ACTIVE)])
    h = Harness(svc)
    h.overlay.visible = True
    h.overlay.note_event()
    await h.drain()
    assert any(c[0] == "list_drives" for c in svc.calls)


@pytest.mark.asyncio
async def test_no_engine_shows_unavailable_message():
    h = Harness(ScriptedService([]), engine=None)
    # Restore the real resolver so a None engine yields no service.
    h.overlay._service = DriveOverlay._service.__get__(h.overlay)
    await h.overlay.reload()
    assert "not available" in h.overlay._error
    assert h.overlay.render(80)  # renders without raising
