"""Deterministic tests for the TUI Drive panel's pure/async helpers.

Per ``tests/README.md`` the Textual widgets are a manual surface; here we pin the
non-widget logic — service-backed row loading, scope handling, detail rendering,
capability filtering, and the settings collect step — against a scripted service
without mounting the app.
"""

from datetime import datetime, timezone

from kohakuterrarium.builtins.tui.widgets.drive_panel import (
    DriveScreen,
    detail_lines,
    graph_members,
    load_drive_rows,
    next_member,
)
from kohakuterrarium.builtins.tui.widgets.drive_settings_pane import (
    build_settings_from_values,
)
from kohakuterrarium.builtins.cli_rich.dialogs.drive_format import (
    enabled_actions,
    project_view,
)
from kohakuterrarium.studio.identity.drive_settings import DriveSettings
from kohakuterrarium.terrarium.drive.models import (
    ActorRef,
    DriveProgress,
    DriveRecord,
    DriveStatus,
)
from kohakuterrarium.terrarium.drive.wire_service import DriveView

NOW = datetime(2026, 7, 12, tzinfo=timezone.utc)
USER = ActorRef("user", "local")


def make_view(drive_id, status, *, assignee="c1", priority=0, actions=("transition",)):
    record = DriveRecord(
        drive_id=drive_id,
        kind="generic",
        schema_version=1,
        revision=2,
        title=f"Watch {drive_id}",
        spec={},
        presentation={},
        metadata={},
        scope_type="graph",
        scope_id="g1",
        origin_scope_id="g1",
        status=status,
        status_reason=None,
        priority=priority,
        policy_name="generic",
        created_by=USER,
        owner=USER,
        owner_scope="actor",
        created_at=NOW,
        updated_by=USER,
        updated_at=NOW,
        lifecycle_epoch=0,
    )
    return DriveView(
        record=record,
        assignee_creature_id=assignee,
        assignment_state="assigned",
        availability="available",
        durability="persistent",
        allowed_actions=actions,
    )


class ScriptedService:
    def __init__(self, views):
        self._views = list(views)
        self.calls = []

    async def get_creature_info(self, creature_id):
        return type("Info", (), {"graph_id": "g1"})()

    async def list_drives(self, **kwargs):
        self.calls.append(kwargs)
        views = self._views
        assignee = kwargs.get("assignee_creature_id")
        if assignee:
            views = [v for v in views if v.assignee_creature_id == assignee]
        return tuple(views)

    async def get_drive(self, drive_id, **kwargs):
        return next((v for v in self._views if v.record.drive_id == drive_id), None)

    async def list_drive_progress(self, drive_id):
        return (DriveProgress("p1", drive_id, USER, "checkpoint", NOW),)


async def test_load_drive_rows_scope_mine_filters_by_assignee():
    svc = ScriptedService(
        [
            make_view("d1", DriveStatus.ACTIVE, assignee="c1"),
            make_view("d2", DriveStatus.ACTIVE, assignee="other"),
        ]
    )
    _, rows = await load_drive_rows(
        svc, USER, "c1", scope="mine", statuses=None, is_operator=True
    )
    assert [r["drive_id"] for r in rows] == ["d1"]
    assert svc.calls[-1]["assignee_creature_id"] == "c1"
    assert svc.calls[-1]["graph_id"] is None


async def test_load_drive_rows_scope_graph_uses_graph_id_and_orders():
    svc = ScriptedService(
        [
            make_view("low", DriveStatus.ACTIVE, priority=1),
            make_view("done", DriveStatus.COMPLETED),
            make_view("high", DriveStatus.ACTIVE, priority=9),
        ]
    )
    _, rows = await load_drive_rows(
        svc, USER, "c1", scope="graph", statuses=None, is_operator=True
    )
    assert [r["drive_id"] for r in rows] == ["high", "low", "done"]
    assert svc.calls[-1]["graph_id"] == "g1"
    assert svc.calls[-1]["assignee_creature_id"] is None


def test_detail_lines_render_labels_and_progress():
    view = make_view("d1", DriveStatus.BLOCKED, actions=("transition",))
    row = project_view(view)
    progress = [DriveProgress("p1", "d1", USER, "looked into it", NOW)]
    lines = detail_lines(row, progress)
    text = "\n".join(lines)
    assert "id: d1" in text
    assert "blocked" in text
    assert "user:local -> c1" in text
    assert "looked into it" in text


def test_detail_lines_escape_user_markup():
    # A title with brackets must not be treated as Textual markup.
    from rich.text import Text

    view = make_view("d1", DriveStatus.ACTIVE)
    row = project_view(view)
    row["title"] = "Fix [urgent] bug"
    row["status_reason"] = "blocked on [PR-9]"
    lines = detail_lines(row, [])
    joined = "\n".join(lines)
    assert "Fix \\[urgent] bug" in joined
    # Every line parses as Textual/Rich markup without raising.
    for line in lines:
        Text.from_markup(line)


def test_capability_filter_matches_shared_gating():
    row = {"status": "paused", "allowed_actions": ("transition",)}
    assert {a["id"] for a in enabled_actions(row)} == {"resume", "cancel"}
    row_none = {"status": "active", "allowed_actions": ()}
    assert enabled_actions(row_none) == []


def test_build_settings_preserves_unexposed_fields():
    base = DriveSettings()
    new = build_settings_from_values(
        base,
        {
            "enabled": True,
            "max_active_per_creature": 12,
            "retry.max_attempts": 9,
            "registrations": {"generic": True},
        },
    )
    assert new.runtime.enabled is True
    assert new.runtime.max_active_per_creature == 12
    assert new.runtime.retry.max_attempts == 9
    assert new.registrations["generic"].enabled is True
    # Fields the pane does not expose survive untouched.
    assert new.runtime.retry.initial_backoff_s == base.runtime.retry.initial_backoff_s
    assert new.runtime.spec_max_bytes == base.runtime.spec_max_bytes


def test_drive_screen_consumes_injected_service():
    # The panel consumes an injected duck-typed service handle (never imports
    # terrarium.service); a None handle degrades to a clean "unavailable" notice.
    svc = object()
    with_service = DriveScreen(svc, object(), creature_id="c1")
    assert with_service._service is svc
    without = DriveScreen(None, None, creature_id="c1")
    assert without._service is None


async def test_show_drive_panel_reuses_app_action():
    # The /drives slash intercept shares the F4 path via the app action, so both
    # entries resolve the injected service the same way.
    from kohakuterrarium.builtins.tui.session import TUISession

    scheduled = []

    class FakeApp:
        is_running = True

        def call_later(self, fn):
            scheduled.append(fn)

        def action_open_drives(self):
            scheduled.append("opened")

    sess = TUISession()
    sess._app = FakeApp()
    await sess.show_drive_panel()
    assert len(scheduled) == 1
    assert getattr(scheduled[0], "__name__", "") == "action_open_drives"


async def test_drive_screen_reload_does_not_duplicate_action_buttons():
    # Regression: action buttons were re-mounted with fixed ids on every detail
    # reload, so a rapid second reload raised DuplicateIds / accumulated buttons.
    from textual.app import App
    from textual.widgets import Button

    svc = ScriptedService(
        [make_view("d1", DriveStatus.ACTIVE, actions=("transition", "report_progress"))]
    )

    class Host(App):
        def on_mount(self) -> None:
            self.push_screen(DriveScreen(svc, object(), creature_id="c1"))

    app = Host()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
        screen = app.screen
        await screen._reload()
        await screen._reload()
        # Bounded settle: slow runners need several message-pump passes
        # before all three buttons finish mounting.
        names: list[str] = []
        for _ in range(40):
            await pilot.pause()
            names = [
                b.name for b in screen.query("#drive-actions Button").results(Button)
            ]
            if len(names) >= 3:
                break
        assert names.count("pause") == 1
        assert set(names) == {"pause", "cancel", "progress"}


class _Engine:
    def __init__(self, members):
        self._members = [
            type("C", (), {"creature_id": cid, "name": name, "graph_id": gid})()
            for cid, name, gid in members
        ]

    def list_creatures(self):
        return list(self._members)


def test_graph_members_stays_inside_the_focused_graph_and_sorts_by_name():
    engine = _Engine([("c2", "bob", "g1"), ("c1", "alice", "g1"), ("c9", "zed", "g2")])
    assert graph_members(engine, "c1") == [("c1", "alice"), ("c2", "bob")]
    assert graph_members(object(), "c1") == []
    assert graph_members(engine, "") == []


def test_next_member_wraps_and_needs_two_members():
    members = [("c1", "alice"), ("c2", "bob")]
    assert next_member(members, "c1") == "c2"
    assert next_member(members, "c2") == "c1"
    assert next_member(members, "unknown") == "c1"
    assert next_member([("c1", "alice")], "c1") is None


def test_drive_screen_creature_label_uses_graph_member_name():
    engine = _Engine([("c1", "alice", "g1")])
    screen = DriveScreen(object(), engine, creature_id="c1")
    assert screen.creature_label() == "alice"
    assert DriveScreen(object(), object(), creature_id="c7").creature_label() == "c7"
