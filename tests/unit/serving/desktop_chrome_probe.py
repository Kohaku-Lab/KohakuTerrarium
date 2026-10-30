"""Opt-in, real WinForms/WebView2 desktop chrome workflow."""

import ctypes
import json
import sys
import threading
import time
import traceback
from pathlib import Path

import webview

from kohakuterrarium.serving.desktop.desktop import LOADING_HTML, _error_html
from kohakuterrarium.serving.desktop.chrome import configure_desktop_chrome
from kohakuterrarium.serving.desktop.frame import Rect
from kohakuterrarium.serving.desktop.palette import (
    BACKGROUND,
    DARK_BACKGROUND,
    DARK_FOREGROUND,
)

output = Path(sys.argv[1])
window = webview.create_window(
    "Kohaku native chrome regression",
    html=LOADING_HTML,
    background_color=BACKGROUND,
    width=900,
    height=600,
    x=220,
    y=160,
    min_size=(400, 300),
    on_top=True,
)
configure_desktop_chrome(window)


class Point(ctypes.Structure):
    _fields_ = [("x", ctypes.c_int32), ("y", ctypes.c_int32)]


class MonitorInfo(ctypes.Structure):
    _fields_ = [
        ("size", ctypes.c_uint32),
        ("monitor", Rect),
        ("work", Rect),
        ("flags", ctypes.c_uint32),
    ]


def values(rect):
    return [rect.left, rect.top, rect.right, rect.bottom]


def run():
    from System import Action
    from System.Drawing import Color, Rectangle, Size
    from System.Windows.Forms import Form, FormBorderStyle, FormStartPosition, Screen
    from PIL import ImageGrab

    result = {}
    try:
        assert window.events.loaded.wait(20), "WebView2 did not load"
        window.show()
        form = window.native
        frame = window._desktop_titlebar.frame
        hwnd = form.Handle.ToInt64()
        user = ctypes.WinDLL("user32", use_last_error=True)
        dwm = ctypes.WinDLL("dwmapi", use_last_error=True)
        user.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]
        user.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
        user.SendMessageW.argtypes = [
            ctypes.c_void_p,
            ctypes.c_uint,
            ctypes.c_size_t,
            ctypes.c_ssize_t,
        ]
        user.SendMessageW.restype = ctypes.c_ssize_t
        user.MonitorFromWindow.argtypes = [ctypes.c_void_p, ctypes.c_uint]
        user.MonitorFromWindow.restype = ctypes.c_void_p
        user.GetMonitorInfoW.argtypes = [ctypes.c_void_p, ctypes.POINTER(MonitorInfo)]
        user.WindowFromPoint.argtypes = [Point]
        user.WindowFromPoint.restype = ctypes.c_void_p

        def wait_for(predicate):
            for _ in range(100):
                if predicate():
                    form.Invoke(Action(lambda: None))
                    time.sleep(0.3)
                    return
                time.sleep(0.02)
            raise AssertionError("Native state transition did not finish")

        def snapshot():
            outer, client, visible = Rect(), Rect(), Rect()
            user.GetWindowRect(hwnd, ctypes.byref(outer))
            user.GetClientRect(hwnd, ctypes.byref(client))
            origin = Point()
            user.ClientToScreen(hwnd, ctypes.byref(origin))
            assert (
                dwm.DwmGetWindowAttribute(
                    hwnd, 9, ctypes.byref(visible), ctypes.sizeof(visible)
                )
                == 0
            )
            return {
                "outer": values(outer),
                "visible": values(visible),
                "client": [
                    origin.x,
                    origin.y,
                    origin.x + client.right,
                    origin.y + client.bottom,
                ],
            }

        def click(index):
            form.Invoke(
                Action(lambda: window._desktop_titlebar.buttons[index].PerformClick())
            )

        result["managed_before"] = str(form.RestoreBounds)
        original = snapshot()
        result["normal"] = original
        outer, client, visible = (
            original[key] for key in ("outer", "client", "visible")
        )
        assert client[1] == visible[1] == outer[1], original
        assert visible[0] == client[0] - 1, original
        assert visible[2:] == [client[2] + 1, client[3] + 1], original
        user.SetForegroundWindow(hwnd)
        time.sleep(0.3)
        bounds = original["outer"]
        capture = ImageGrab.grab(
            bbox=(bounds[0] - 20, bounds[1] - 20, bounds[2] + 20, bounds[3] + 20),
            all_screens=True,
        )
        capture.save(str(output.with_suffix(".png")))
        background = tuple(int(BACKGROUND[i : i + 2], 16) for i in (1, 3, 5))
        cx, cy = client[0] - bounds[0] + 20, client[1] - bounds[1] + 20
        assert capture.getpixel((cx, cy + 15))[:3] == background, "White caption seam"
        backdrop = []

        def create_backdrop():
            panel = Form()
            panel.FormBorderStyle = getattr(FormBorderStyle, "None")
            panel.StartPosition = FormStartPosition.Manual
            panel.ShowInTaskbar = False
            panel.TopMost = True
            panel.Bounds = Rectangle(
                bounds[0] - 30,
                bounds[1] - 30,
                bounds[2] - bounds[0] + 60,
                bounds[3] - bounds[1] + 60,
            )
            form.Owner = panel
            panel.Show()
            form.BringToFront()
            backdrop.append(panel)

        def verify_borders(stage, *, existing_backdrop=False):
            if not existing_backdrop:
                backdrop.clear()
                form.Invoke(Action(create_backdrop))
            result.setdefault("border_states", {})[stage] = []
            try:
                for backdrop_color in (Color.White, Color.Black):
                    for active in (False, True):

                        def redraw():
                            backdrop[0].BackColor = backdrop_color
                            backdrop[0].Refresh()
                            user.SendMessageW(hwnd, 0x86, int(active), 0)
                            form.Refresh()

                        form.Invoke(Action(redraw))
                        dwm.DwmFlush()
                        time.sleep(0.1)
                        shot = ImageGrab.grab(
                            bbox=(
                                bounds[0] - 20,
                                bounds[1] - 20,
                                bounds[2] + 20,
                                bounds[3] + 20,
                            ),
                            all_screens=True,
                        )
                        vx, vy, vr, vb = visible
                        points = [
                            ((vx + vr) // 2, vy),
                            (vx, (vy + vb) // 2),
                            (vr - 1, (vy + vb) // 2),
                            ((vx + vr) // 2, vb - 1),
                            (client[2] - 30, vy),
                        ]
                        colors = [
                            shot.getpixel((x - bounds[0] + 20, y - bounds[1] + 20))[:3]
                            for x, y in points
                        ]
                        assert all(
                            max(values) - min(values) <= 1 for values in zip(*colors)
                        ), (active, backdrop_color.Name, colors)
                        result["border_states"][stage].append(
                            [active, backdrop_color.Name, colors]
                        )
                        shot.save(
                            str(
                                output.with_name(
                                    f"{output.stem}-{stage}-border-{backdrop_color.Name}-{active}.png"
                                )
                            )
                        )
            finally:

                def close_backdrop():
                    form.Owner = None
                    backdrop[0].Dispose()

                form.Invoke(Action(close_backdrop))

        verify_borders("initial")
        css_background = "rgb(" + ", ".join(map(str, background)) + ")"
        assert (
            window.evaluate_js("getComputedStyle(document.body).backgroundColor")
            == css_background
        )
        window.load_html(_error_html("<startup failure>"))
        assert window.events.loaded.wait(20)
        assert (
            window.evaluate_js("getComputedStyle(document.body).backgroundColor")
            == css_background
        )
        window.load_html(
            '<body style="background:#f2eee8;font:24px Segoe UI">Native frame regression</body>'
        )
        assert window.events.loaded.wait(20)
        result["startup_palette_and_outline"] = True

        caption = window._desktop_titlebar
        guarded_on_ui_thread = []

        def stale():
            guarded_on_ui_thread.append(not form.InvokeRequired)
            return False

        assert caption.set_dark(True, is_current=stale) is False
        assert guarded_on_ui_thread == [True]
        assert caption.background.ToArgb() & 0xFFFFFF == int(BACKGROUND[1:], 16)
        for dark in (True, False, True, False):
            window.evaluate_js(
                f"document.documentElement.classList.toggle('dark', {str(dark).lower()})"
            )
            expected = int((DARK_BACKGROUND if dark else BACKGROUND)[1:], 16)
            wait_for(lambda: caption.background.ToArgb() & 0xFFFFFF == expected)
            assert all(
                button.BackColor == caption.background for button in caption.buttons
            )
            foreground = int((DARK_FOREGROUND if dark else "#2e2a26")[1:], 16)
            assert caption.foreground.ToArgb() & 0xFFFFFF == foreground
            assert snapshot() == original

        window.load_html(
            f'<html class="dark"><body style="margin:0;background:{DARK_BACKGROUND};color:{DARK_FOREGROUND};font:24px Segoe UI;padding:32px">Dark theme restored on navigation</body></html>'
        )
        assert window.events.loaded.wait(20)
        wait_for(
            lambda: caption.background.ToArgb() & 0xFFFFFF
            == int(DARK_BACKGROUND[1:], 16)
        )
        ImageGrab.grab(
            bbox=(bounds[0] - 20, bounds[1] - 20, bounds[2] + 20, bounds[3] + 20),
            all_screens=True,
        ).save(str(output.with_name(output.stem + "-dark.png")))
        window.evaluate_js("document.documentElement.classList.remove('dark')")
        wait_for(
            lambda: caption.background.ToArgb() & 0xFFFFFF == int(BACKGROUND[1:], 16)
        )
        result["live_theme_and_navigation"] = True

        l, t, r, b = bounds
        points = [
            (l + 1, t + 1, 13),
            (r - 2, t + 1, 14),
            (l + 1, b - 2, 16),
            (r - 2, b - 2, 17),
            (l + 1, t + 100, 10),
            (r - 2, t + 100, 11),
            (l + 100, t + 1, 12),
            (r - 60, t + 1, 12),
            (l + 100, b - 2, 15),
            (l + 100, t + 20, 2),
            (r - 60, t + 20, 1),
            (l + 100, t + 100, 1),
        ]
        for x, y, expected in points:
            hit = user.SendMessageW(hwnd, 0x84, 0, (x & 0xFFFF) | ((y & 0xFFFF) << 16))
            assert hit == expected, (x, y, hit, expected)
            if 10 <= expected <= 17:
                found = []
                form.Invoke(
                    Action(lambda: found.append(user.WindowFromPoint(Point(x, y))))
                )
                actual = found[0]
                assert actual == hwnd, (
                    "resize point covered by child",
                    expected,
                    actual,
                    hwnd,
                )
        result["hit_tests"] = len(points)

        click(1)
        wait_for(lambda: user.IsZoomed(hwnd))
        info = MonitorInfo(size=ctypes.sizeof(MonitorInfo))
        assert user.GetMonitorInfoW(user.MonitorFromWindow(hwnd, 2), ctypes.byref(info))
        result["managed_maximized"] = str(form.RestoreBounds)
        result["maximized"] = snapshot()
        assert result["maximized"]["client"] == values(info.work), (
            result,
            values(info.work),
        )
        top_pixel = ImageGrab.grab(
            bbox=(
                info.work.left + 5,
                info.work.top,
                info.work.left + 6,
                info.work.top + 1,
            ),
            all_screens=True,
        ).getpixel((0, 0))[:3]
        assert top_pixel == background, ("Maximized top seam", top_pixel, background)
        backdrop.clear()
        form.Invoke(Action(create_backdrop))
        form.Invoke(Action(form.Activate))
        user.SendMessageW(hwnd, 0x6, 0, 0)
        user.SendMessageW(hwnd, 0x6, 1, 0)
        click(1)
        wait_for(lambda: not user.IsZoomed(hwnd))
        assert snapshot() == original, (snapshot(), original)
        verify_borders("restored_after_maximized_focus", existing_backdrop=True)

        for maximize in (False, True):
            if maximize:
                click(1)
                wait_for(lambda: user.IsZoomed(hwnd))
            click(0)
            wait_for(lambda: user.IsIconic(hwnd))
            frame.command(0xF120)
            wait_for(lambda: not user.IsIconic(hwnd))
            assert bool(user.IsZoomed(hwnd)) == maximize
            if maximize:
                click(1)
                wait_for(lambda: not user.IsZoomed(hwnd))
            assert snapshot() == original, (snapshot(), original)
        result["minimize_restore"] = "normal and maximized"

        for maximized in (True, False):
            user.SendMessageW(hwnd, 0xA3, 2, (l + 100) | ((t + 20) << 16))
            wait_for(lambda: bool(user.IsZoomed(hwnd)) == maximized)
        assert snapshot() == original, (snapshot(), original)
        result["double_click"] = True

        entered, exited = threading.Event(), threading.Event()
        form.ResizeBegin += lambda *_: entered.set()
        form.ResizeEnd += lambda *_: exited.set()
        for direction in range(1, 9):
            entered.clear()
            exited.clear()
            frame.command(0xF000 | direction)
            assert entered.wait(2), ("sizing loop", direction)
            key = 0x27 if direction in (1, 2) else 0x28
            user.PostMessageW(hwnd, 0x100, key, 0)
            user.PostMessageW(hwnd, 0x101, key, 0)
            user.PostMessageW(hwnd, 0x100, 0x0D, 0)
            assert exited.wait(2), ("sizing loop exit", direction)
            assert snapshot()["outer"] != original["outer"], direction
            user.SetWindowPos(hwnd, None, l, t, r - l, b - t, 0x14)
        result["native_sizing_loops"] = 8

        result["monitors"] = []
        for screen in Screen.AllScreens:
            work = screen.WorkingArea
            user.SetWindowPos(hwnd, None, work.X + 80, work.Y + 80, 700, 450, 0x14)
            form.Invoke(Action(lambda: None))
            before = snapshot()
            dpi = frame.dpi
            minimum = tuple(round(value * dpi / 96) for value in window.min_size)
            assert (form.MinimumSize.Width, form.MinimumSize.Height) == minimum
            form.Invoke(Action(lambda: setattr(form, "Size", Size(1, 1))))
            smallest = snapshot()["outer"]
            assert (smallest[2] - smallest[0], smallest[3] - smallest[1]) == minimum
            x, y, right, bottom = before["outer"]
            user.SetWindowPos(hwnd, None, x, y, right - x, bottom - y, 0x14)
            form.Invoke(Action(lambda: None))
            assert frame.caption_height == round(32 * dpi / 96), (
                dpi,
                frame.caption_height,
            )
            click(1)
            wait_for(lambda: user.IsZoomed(hwnd))
            assert snapshot()["client"] == [
                work.Left,
                work.Top,
                work.Right,
                work.Bottom,
            ]
            click(1)
            wait_for(lambda: not user.IsZoomed(hwnd))
            assert snapshot() == before, (snapshot(), before)
            result["monitors"].append(
                {
                    "dpi": dpi,
                    "minimum": minimum,
                    "work": [work.Left, work.Top, work.Right, work.Bottom],
                }
            )
        result["ok"] = True
    except Exception:
        result["error"] = traceback.format_exc()
    finally:
        output.write_text(json.dumps(result, indent=2), encoding="utf-8")
        window.destroy()


webview.start(run)
