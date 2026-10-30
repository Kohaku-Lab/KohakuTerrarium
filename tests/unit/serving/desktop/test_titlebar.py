"""Managed caption layout checks without creating a desktop window."""

import importlib.util
import subprocess
import sys

import pytest


@pytest.mark.skipif(
    sys.platform != "win32" or importlib.util.find_spec("clr") is None,
    reason="Requires Windows and the optional pythonnet dependency",
)
def test_minimum_size_tracks_dpi_without_creating_handles():
    code = """
import clr
clr.AddReference('System.Windows.Forms')
clr.AddReference('System.Drawing')
from System.Drawing import Rectangle, Size
from System.Windows.Forms import Button, Form, FormWindowState, Screen
from types import SimpleNamespace
from kohakuterrarium.serving.desktop.titlebar import DesktopTitleBar

form = Form()
caption = DesktopTitleBar.__new__(DesktopTitleBar)
caption.form = form
caption.frame = SimpleNamespace(dpi=96, maximized=False)
caption.buttons = [Button() for _ in range(3)]
caption._state = FormWindowState.Normal
try:
    for screen in Screen.AllScreens:
        work = screen.WorkingArea
        form.MinimumSize = Size.Empty
        form.Bounds = Rectangle(
            work.Left + work.Width//4, work.Top + work.Height//4,
            max(1, work.Width//4), max(1, work.Height//4),
        )
        logical = (max(1, min(200, work.Width//8)), max(1, min(150, work.Height//8)))
        caption._logical_minimum = logical
        form.MinimumSize = Size(*(round(value*1.5) for value in logical))
        for dpi in (96, 144, 192, 96):
            caption.frame.dpi = dpi
            caption._layout()
            expected = Size(*(round(value*dpi/96) for value in logical))
            assert form.MinimumSize == expected, (work, dpi, form.MinimumSize, expected)
            assert not form.IsHandleCreated
            assert all(not button.IsHandleCreated for button in caption.buttons)

        reference = Form()
        try:
            reference.Bounds = form.Bounds
            oversized = (work.Width*2, work.Height*2)
            reference.MinimumSize = Size(*oversized)
            constrained = reference.MinimumSize
            assert constrained.Width <= work.Width and constrained.Height <= work.Height
            assert constrained != Size(*oversized)
            assert not reference.IsHandleCreated
        finally:
            reference.Dispose()
        caption._logical_minimum = oversized
        caption.frame.dpi = 96
        caption._layout()
        assert form.MinimumSize == constrained, (work, form.MinimumSize, constrained)
        assert not form.IsHandleCreated
        assert all(not button.IsHandleCreated for button in caption.buttons)
finally:
    for button in caption.buttons:
        button.Dispose()
    form.Dispose()
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=20,
    )
    assert result.returncode == 0, result.stderr
