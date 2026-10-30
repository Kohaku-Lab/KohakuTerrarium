"""Native desktop frame regression checks."""

import ctypes
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from kohakuterrarium.serving.desktop.frame import NativeFrame, Rect


def test_size_transitions_refresh_dwm_after_maximized_focus_changes():
    frame = NativeFrame.__new__(NativeFrame)
    frame.hwnd = 1
    state = {"maximized": False}
    top_margins = []
    frame._user = SimpleNamespace(IsZoomed=lambda _: state["maximized"])
    frame._dwm = SimpleNamespace(
        DwmExtendFrameIntoClientArea=lambda _hwnd, margins: top_margins.append(
            ctypes.cast(margins, ctypes.POINTER(ctypes.c_int * 4)).contents[2]
        ),
        DwmSetWindowAttribute=lambda *_: 0,
    )
    frame._comctl = SimpleNamespace(DefSubclassProc=lambda *_: 0)
    frame._extend_frame()
    assert top_margins[-1] == 1
    state["maximized"] = True
    frame._dispatch(1, 0x5, 2, 0, 1, 0)
    frame._dispatch(1, 0x6, 0, 0, 1, 0)
    frame._dispatch(1, 0x6, 1, 0, 1, 0)
    assert top_margins[-1] == 0
    state["maximized"] = False
    frame._dispatch(1, 0x5, 0, 0, 1, 0)
    assert top_margins[-1] == 1

    state["maximized"] = True
    frame._dispatch(1, 0x5, 2, 0, 1, 0)
    assert top_margins[-1] == 0
    state["maximized"] = False
    frame._dispatch(1, 0x5, 1, 0, 1, 0)
    assert top_margins[-1] == 0
    state["maximized"] = True
    frame._dispatch(1, 0x5, 2, 0, 1, 0)
    assert top_margins[-1] == 0


def test_top_edge_uses_compositor_alpha_in_both_activation_states():
    frame = NativeFrame.__new__(NativeFrame)
    frame.hwnd = 1
    frame._user = SimpleNamespace(IsZoomed=lambda _: False)
    captured = []

    def draw(
        _dc, _x, _y, width, _height, _sx, _sy, _start, _lines, pixels, _header, _usage
    ):
        captured.append(
            list(ctypes.cast(pixels, ctypes.POINTER(ctypes.c_uint32 * width)).contents)
        )
        return 1

    frame._gdi = SimpleNamespace(SetDIBitsToDevice=draw)
    frame.active = True
    frame.paint_top_edge(1, 4)
    frame.active = False
    frame.paint_top_edge(1, 4)
    frame._user.IsZoomed = lambda _: True
    frame.paint_top_edge(1, 4)
    assert captured == [[0] * 4, [0x802B2B2B] * 4]


@pytest.mark.parametrize("maximized", [False, True])
def test_caption_controls_and_negative_monitor_coordinates(maximized):
    frame = NativeFrame.__new__(NativeFrame)
    frame.hwnd = 1
    frame.caption_height = 32
    frame.controls_width = 138

    def bounds(_hwnd, target):
        rect = ctypes.cast(target, ctypes.POINTER(Rect)).contents
        rect.left, rect.top, rect.right, rect.bottom = -1000, -700, -100, -100
        return 1

    frame._user = SimpleNamespace(
        GetWindowRect=bounds,
        GetDpiForWindow=lambda _: 96,
        GetSystemMetricsForDpi=lambda metric, _: 4,
        IsZoomed=lambda _: maximized,
    )

    def hit(x, y):
        return frame._hit_test((x & 0xFFFF) | ((y & 0xFFFF) << 16))

    assert hit(-150, -680) == 1
    assert hit(-800, -680) == 2
    assert hit(-800, -400) == 1
    assert hit(-1001, -680) is None
    assert hit(-999, -699) == (1 if maximized else 13)


@pytest.mark.skipif(
    sys.platform != "win32" or os.environ.get("KT_TEST_NATIVE_CHROME") != "1",
    reason="Requires an interactive Windows desktop and pywebview/WebView2",
)
def test_real_native_frame_lifecycle(tmp_path):
    result = tmp_path / "native-frame.json"
    probe = Path(__file__).with_name("desktop_chrome_probe.py")
    process = subprocess.run(
        [sys.executable, str(probe), str(result)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )
    assert process.returncode == 0, process.stderr
    report = json.loads(result.read_text(encoding="utf-8"))
    assert report.get("ok"), report
    assert "Desktop non-client message failed" not in process.stderr
