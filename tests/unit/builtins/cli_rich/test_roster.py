"""Tests for :mod:`builtins.cli_rich.roster`.

Uses Rich's ``Console(record=True)`` to render the widget to plain
text at a known width and asserts substrings / shape. This sidesteps
prompt_toolkit's Layout entirely — the widget only returns a Rich
``Text``; rendering is the caller's concern.
"""

from io import StringIO

from rich.console import Console

from kohakuterrarium.builtins.cli_rich.creature_status import CreatureStatus
from kohakuterrarium.builtins.cli_rich.roster import RosterWidget


def _render(widget: RosterWidget, term_width: int) -> str:
    """Render the widget's Text to plain ASCII at the given width."""
    buf = StringIO()
    console = Console(
        file=buf,
        force_terminal=False,
        color_system=None,
        width=term_width,
        legacy_windows=False,
    )
    text = widget.render(term_width)
    console.print(text)
    return buf.getvalue().rstrip("\n")


def _mk(cid: str, name: str, state: str, activity: str = "") -> CreatureStatus:
    return CreatureStatus(
        creature_id=cid,
        name=name,
        state=state,  # type: ignore[arg-type]
        activity=activity or state,
    )


class TestSingleCreatureIsInvisible:
    def test_one_creature_returns_empty(self):
        widget = RosterWidget(
            get_statuses=lambda: [_mk("c1", "alice", "idle")],
            get_focus_id=lambda: "c1",
        )
        out = _render(widget, 80)
        assert out.strip() == ""


class TestThreeCreaturesAllFit:
    def test_renders_three_slots(self):
        statuses = [
            _mk("c1", "alice", "working", "running tests"),
            _mk("c2", "bob", "idle", "idle 2m"),
            _mk("c3", "carol", "waiting", "needs: confirm"),
        ]
        widget = RosterWidget(
            get_statuses=lambda: statuses,
            get_focus_id=lambda: "c1",
        )
        out = _render(widget, 120)
        # All names present.
        assert "alice" in out
        assert "bob" in out
        assert "carol" in out
        # Focus marker present exactly once.
        assert out.count("▸") == 1
        # No collapsed tail at this width.
        assert "+1 idle" not in out
        assert "+1 stopped" not in out

    def test_focus_marker_on_focus_only(self):
        statuses = [
            _mk("c1", "alice", "idle"),
            _mk("c2", "bob", "idle"),
        ]
        widget = RosterWidget(get_statuses=lambda: statuses, get_focus_id=lambda: "c2")
        out = _render(widget, 100)
        # The focus marker should be next to bob.
        assert "▸bob" in out
        assert "▸alice" not in out


class TestCompression:
    def test_idle_creatures_collapse_at_narrow_width(self):
        # 7 creatures: 1 working + 6 idle, narrow terminal.
        statuses = [_mk("c0", "working", "working", "in flight")] + [
            _mk(f"c{i}", f"idle{i}", "idle", "idle") for i in range(1, 7)
        ]
        widget = RosterWidget(get_statuses=lambda: statuses, get_focus_id=lambda: "c0")
        out = _render(widget, 50)
        # Working always visible.
        assert "working" in out
        # At least some idle creatures collapsed to tail count.
        assert "idle" in out  # state/activity present
        assert "+" in out  # tail count rendered

    def test_waiting_creatures_always_visible(self):
        statuses = [_mk("c0", "alpha", "idle", "idle")] * 5 + [  # noqa: WPS435
            _mk("cw", "waiter", "waiting", "needs: yes/no")
        ]
        widget = RosterWidget(get_statuses=lambda: statuses, get_focus_id=lambda: "c0")
        out = _render(widget, 60)
        assert "waiter" in out


class TestSnapshotShape:
    def test_focus_marker_count_at_various_widths(self):
        statuses = [
            _mk("c1", "a", "working"),
            _mk("c2", "b", "idle"),
            _mk("c3", "c", "stopped"),
        ]
        widget = RosterWidget(get_statuses=lambda: statuses, get_focus_id=lambda: "c1")
        for w in (40, 80, 120):
            out = _render(widget, w)
            # Exactly one focus marker at every width that renders > 0 slots
            assert out.count("▸") == 1, f"width={w}: {out!r}"

    def test_state_glyphs_present(self):
        statuses = [
            _mk("c1", "w", "working"),
            _mk("c2", "i", "idle"),
            _mk("c3", "x", "stopped"),
            _mk("c4", "f", "failed"),
            _mk("c5", "p", "waiting"),
        ]
        widget = RosterWidget(get_statuses=lambda: statuses, get_focus_id=lambda: "c1")
        out = _render(widget, 200)
        for glyph in ("●", "○", "■", "✗", "⚠"):
            assert glyph in out, f"missing {glyph} in {out!r}"


class TestEdgeCases:
    def test_no_creatures_returns_empty(self):
        widget = RosterWidget(get_statuses=lambda: [], get_focus_id=lambda: "")
        out = _render(widget, 80)
        assert out.strip() == ""

    def test_focus_not_in_list_renders_no_marker(self):
        statuses = [_mk("c1", "alpha", "idle"), _mk("c2", "beta", "idle")]
        widget = RosterWidget(
            get_statuses=lambda: statuses, get_focus_id=lambda: "nope"
        )
        out = _render(widget, 100)
        assert "▸" not in out


class TestUnreadBadge:
    def test_non_focused_with_unread_shows_badge(self):
        s = CreatureStatus(
            creature_id="c2",
            name="bob",
            state="working",
            activity="bash",
            unread=3,
        )
        focus = _mk("c1", "alice", "idle")
        widget = RosterWidget(
            get_statuses=lambda: [focus, s], get_focus_id=lambda: "c1"
        )
        out = _render(widget, 120)
        assert "●3" in out

    def test_focused_creature_never_shows_unread_badge(self):
        # Even if unread>0 (which shouldn't happen in practice — the
        # app clears it on focus), the focused slot suppresses it.
        s = CreatureStatus(
            creature_id="c1",
            name="alice",
            state="working",
            activity="bash",
            unread=42,
        )
        other = _mk("c2", "bob", "idle")
        widget = RosterWidget(
            get_statuses=lambda: [s, other], get_focus_id=lambda: "c1"
        )
        out = _render(widget, 120)
        assert "●42" not in out

    def test_unread_caps_at_99_plus(self):
        s = CreatureStatus(
            creature_id="c2",
            name="bob",
            state="idle",
            activity="bash",
            unread=500,
        )
        focus = _mk("c1", "alice", "idle")
        widget = RosterWidget(
            get_statuses=lambda: [focus, s], get_focus_id=lambda: "c1"
        )
        out = _render(widget, 120)
        assert "●99+" in out
