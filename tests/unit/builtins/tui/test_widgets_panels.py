"""Tests for TUI status panels."""

from textual.geometry import Offset

from kohakuterrarium.builtins.tui.widgets.panels import RunningPanel


class _Click:
    def __init__(self, content_offset: Offset | None) -> None:
        self._content_offset = content_offset

    def get_content_offset(self, _widget: RunningPanel) -> Offset | None:
        return self._content_offset


class TestRunningPanel:
    def test_click_targets_content_row_and_ignores_non_content(self) -> None:
        panel = RunningPanel()
        panel._items = {
            "first": ("First", 0.0, False),
            "second": ("Second", 0.0, False),
        }
        panel._ordered_ids = ["first", "second"]
        posted = []
        panel.post_message = posted.append  # type: ignore[method-assign]

        panel.on_click(_Click(None))
        panel.on_click(_Click(Offset(0, 2)))

        assert posted == []

        panel.on_click(_Click(Offset(0, 0)))

        assert len(posted) == 1
        assert isinstance(posted[0], RunningPanel.CancelRequested)
        assert posted[0].job_id == "first"
