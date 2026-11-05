"""Tool-call runs in a creature view: what reaches scrollback, and when a run ends."""

import time

import pytest
from kohakutui import App

from kohakuterrarium.builtins.ktui.view import CreatureView


@pytest.fixture
def app():
    app = App(backend="test", size=(80, 30), interrupt="event").start()
    yield app
    app.stop()


def _until(app: App, needle: str, budget: float = 5.0) -> str:
    deadline = time.monotonic() + budget
    while True:
        seen = "\n".join(app.history())
        if needle in seen:
            return seen
        if time.monotonic() > deadline:
            raise AssertionError(f"{needle!r} never reached scrollback:\n{seen}")
        time.sleep(0.02)


def _call(view: CreatureView, job_id: str, name: str = "echo") -> None:
    view.render("tool_start", job_id, name, job_id, False)
    view.render("tool_end", job_id, name, f"out {job_id}", "", 0.1)


class TestToolRuns:
    def test_a_divider_ends_the_run_so_nothing_is_held_back(self, app):
        view = CreatureView(app, "solo", "solo")
        _call(view, "a")
        _call(view, "b")
        view.render("divider", "resume complete")
        seen = _until(app, "resume complete")
        assert seen.index("echo  a") < seen.index("resume complete")

        # a later call starts a new run below the divider, not inside the old batch
        _call(view, "c")
        view.render("text", "done")
        view.render("assistant_end")
        seen = _until(app, "done")
        assert seen.index("resume complete") < seen.index("echo  c")

    def test_whitespace_between_calls_keeps_one_batch(self, app):
        view = CreatureView(app, "solo", "solo")
        for job in ("a", "b", "c"):
            _call(view, job)
            view.render("text", "\n")
        view.render("text", "all three")
        seen = _until(app, "all three")
        assert "3 tool calls  echo ×3" in seen, seen
        assert "out a" not in seen

    def test_batching_off_lists_every_call(self, app):
        view = CreatureView(app, "solo", "solo", batch_size=0)
        for job in ("a", "b", "c"):
            _call(view, job)
        view.render("text", "listed")
        seen = _until(app, "listed")
        assert "tool calls" not in seen
        assert all(f"echo  {job}" in seen for job in "abc")
