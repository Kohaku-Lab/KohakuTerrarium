"""Tests for shared TUI session state."""

from collections.abc import Callable
from typing import Any

from kohakuterrarium.builtins.tui.session import TUISession


class _RunningApp:
    is_running = True

    def __init__(self, active: str) -> None:
        self.active = active
        self.reconciled: list[list[str]] = []

    def get_active_tab_name(self) -> str:
        return self.active

    def reconcile_terrarium_tabs(self, tabs: list[str]) -> None:
        self.reconciled.append(tabs)

    def call_later(self, callback: Callable[..., Any], *args: Any) -> bool:
        callback(*args)
        return True


def test_set_terrarium_tabs_reconciles_running_app_and_preserves_active() -> None:
    session = TUISession()
    session.set_terrarium_tabs(["root", "review-worker"])
    app = _RunningApp(active="review-worker")
    session._app = app  # type: ignore[assignment]

    session.set_terrarium_tabs(["root", "new_worker", "review-worker"])

    assert session._active_target == "review-worker"
    assert app.reconciled == [["root", "new_worker", "review-worker"]]

    app.active = "review-worker"
    session.set_terrarium_tabs(["root", "new_worker"])

    assert session._active_target == "root"
    assert app.reconciled[-1] == ["root", "new_worker"]
