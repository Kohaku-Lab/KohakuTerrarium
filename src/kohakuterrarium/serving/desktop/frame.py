"""Windows non-client sizing and hit testing for the desktop host (UI thread)."""

import ctypes

from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


class Rect(ctypes.Structure):
    _fields_ = [(name, ctypes.c_int32) for name in ("left", "top", "right", "bottom")]


class _BitmapHeader(ctypes.Structure):
    _fields_ = [
        ("size", ctypes.c_uint32),
        ("width", ctypes.c_int32),
        ("height", ctypes.c_int32),
        ("planes", ctypes.c_uint16),
        ("bits", ctypes.c_uint16),
        ("compression", ctypes.c_uint32),
        ("image_size", ctypes.c_uint32),
        ("x_resolution", ctypes.c_int32),
        ("y_resolution", ctypes.c_int32),
        ("colors", ctypes.c_uint32),
        ("important_colors", ctypes.c_uint32),
    ]


class NativeFrame:
    """Extend the client title into a standard resizable HWND's caption."""

    def __init__(self, hwnd: int):
        self.hwnd = hwnd
        self.caption_height = 32
        self.controls_width = 138
        self.dpi_changed = lambda: None
        self.appearance_changed = lambda: None
        self.active = False
        self._extended_maximized = None
        self._user = ctypes.WinDLL("user32", use_last_error=True)
        self._comctl = ctypes.WinDLL("comctl32", use_last_error=True)
        self._dwm = ctypes.WinDLL("dwmapi", use_last_error=True)
        self._gdi = ctypes.WinDLL("gdi32", use_last_error=True)
        pointer, uint, word, result = (
            ctypes.c_void_p,
            ctypes.c_uint,
            ctypes.c_size_t,
            ctypes.c_ssize_t,
        )
        self._callback_type = ctypes.WINFUNCTYPE(
            result, pointer, uint, word, result, word, word
        )
        signatures = [
            (self._user.GetWindowRect, [pointer, ctypes.POINTER(Rect)], ctypes.c_int),
            (self._user.GetDpiForWindow, [pointer], uint),
            (self._user.GetSystemMetricsForDpi, [ctypes.c_int, uint], ctypes.c_int),
            (self._user.IsZoomed, [pointer], ctypes.c_int),
            (self._user.GetForegroundWindow, [], pointer),
            (self._user.PostMessageW, [pointer, uint, word, result], ctypes.c_int),
            (
                self._user.SetWindowPos,
                [pointer, pointer] + [ctypes.c_int] * 4 + [uint],
                ctypes.c_int,
            ),
            (
                self._comctl.SetWindowSubclass,
                [pointer, self._callback_type, word, word],
                ctypes.c_int,
            ),
            (
                self._comctl.RemoveWindowSubclass,
                [pointer, self._callback_type, word],
                ctypes.c_int,
            ),
            (self._comctl.DefSubclassProc, [pointer, uint, word, result], result),
            (
                self._dwm.DwmExtendFrameIntoClientArea,
                [pointer, pointer],
                ctypes.c_int32,
            ),
            (
                self._dwm.DwmSetWindowAttribute,
                [pointer, uint, pointer, uint],
                ctypes.c_int32,
            ),
            (
                self._gdi.SetDIBitsToDevice,
                [
                    pointer,
                    ctypes.c_int,
                    ctypes.c_int,
                    uint,
                    uint,
                    ctypes.c_int,
                    ctypes.c_int,
                    uint,
                    uint,
                    pointer,
                    pointer,
                    uint,
                ],
                ctypes.c_int,
            ),
        ]
        for function, args, returns in signatures:
            function.argtypes, function.restype = args, returns
        self._callback = self._callback_type(self._dispatch)
        self._child_callback = self._callback_type(self._child_dispatch)
        self._children = set()
        self.installed = False

    @property
    def dpi(self) -> int:
        return self._user.GetDpiForWindow(self.hwnd) or 96

    @property
    def maximized(self) -> bool:
        return bool(self._user.IsZoomed(self.hwnd))

    def install(self) -> None:
        if not self._comctl.SetWindowSubclass(self.hwnd, self._callback, 1, 0):
            raise ctypes.WinError(ctypes.get_last_error())
        self.installed = True
        self.active = self._user.GetForegroundWindow() == self.hwnd
        self._extend_frame()
        self._user.SetWindowPos(self.hwnd, None, 0, 0, 0, 0, 0x37)

    def close(self) -> None:
        for child in tuple(self._children):
            self._comctl.RemoveWindowSubclass(child, self._child_callback, 1)
        self._children.clear()
        if self.installed:
            self._comctl.RemoveWindowSubclass(self.hwnd, self._callback, 1)
            self.installed = False

    def command(self, command: int) -> None:
        self._user.PostMessageW(self.hwnd, 0x112, command, 0)

    def _extend_frame(self) -> None:
        self._extended_maximized = self.maximized
        margins = (ctypes.c_int * 4)(0, 0, 0 if self._extended_maximized else 1, 0)
        self._dwm.DwmExtendFrameIntoClientArea(self.hwnd, ctypes.byref(margins))
        dark = ctypes.c_int(1)
        self._dwm.DwmSetWindowAttribute(self.hwnd, 20, ctypes.byref(dark), 4)

    def paint_top_edge(self, hdc: int, width: int) -> None:
        """Supply premultiplied top-edge pixels for DWM frame composition."""
        if self.maximized or width <= 0:
            return
        value = 0 if self.active else 0x802B2B2B
        pixels = (ctypes.c_uint32 * width)(*([value] * width))
        header = _BitmapHeader(
            size=ctypes.sizeof(_BitmapHeader),
            width=width,
            height=1,
            planes=1,
            bits=32,
            image_size=width * 4,
        )
        self._gdi.SetDIBitsToDevice(
            hdc, 0, 0, width, 1, 0, 0, 0, 1, pixels, ctypes.byref(header), 0
        )

    def attach_caption_control(self, hwnd: int) -> None:
        if not self._comctl.SetWindowSubclass(hwnd, self._child_callback, 1, 0):
            raise ctypes.WinError(ctypes.get_last_error())
        self._children.add(hwnd)

    def _child_dispatch(self, hwnd, message, wparam, lparam, _id, _data):
        try:
            if message == 0x82:
                self._comctl.RemoveWindowSubclass(hwnd, self._child_callback, 1)
                self._children.discard(hwnd)
            elif message == 0x84 and self._hit_test(lparam) in range(10, 18):
                return -1  # HTTRANSPARENT
        except Exception:
            logger.exception("Desktop caption hit test failed")
        return self._comctl.DefSubclassProc(hwnd, message, wparam, lparam)

    def _dispatch(self, hwnd, message, wparam, lparam, _id, _data):
        try:
            if message == 0x82:  # WM_NCDESTROY
                self.close()
            elif message == 0x83:  # WM_NCCALCSIZE
                rect = Rect.from_address(lparam)
                top, left = rect.top, rect.left
                self._comctl.DefSubclassProc(hwnd, message, wparam, lparam)
                rect.top = top + (rect.left - left if self.maximized else 0)
                return 0
            elif message == 0x84:  # WM_NCHITTEST
                result = self._hit_test(lparam)
                if result is not None:
                    return result
            elif message in (0x6, 0x31E):  # WM_ACTIVATE / WM_DWMCOMPOSITIONCHANGED
                self._extend_frame()
            elif message == 0x5 and wparam != 1:  # WM_SIZE, excluding SIZE_MINIMIZED
                if self.maximized != self._extended_maximized:
                    self._extend_frame()
            elif message == 0x86:  # WM_NCACTIVATE
                self.active = bool(wparam)
                result = self._comctl.DefSubclassProc(hwnd, message, wparam, lparam)
                self.appearance_changed()
                return result
            elif message == 0x2E0:  # WM_DPICHANGED
                result = self._comctl.DefSubclassProc(hwnd, message, wparam, lparam)
                self.dpi_changed()
                bounds = Rect.from_address(lparam)
                self._user.SetWindowPos(
                    hwnd,
                    None,
                    bounds.left,
                    bounds.top,
                    bounds.right - bounds.left,
                    bounds.bottom - bounds.top,
                    0x14,
                )
                return result
        except Exception:
            logger.exception("Desktop non-client message failed")
        return self._comctl.DefSubclassProc(hwnd, message, wparam, lparam)

    def _hit_test(self, position: int) -> int | None:
        bounds = Rect()
        if not self._user.GetWindowRect(self.hwnd, ctypes.byref(bounds)):
            return None
        x = ctypes.c_int16(position & 0xFFFF).value - bounds.left
        y = ctypes.c_int16((position >> 16) & 0xFFFF).value - bounds.top
        border = self._user.GetSystemMetricsForDpi(33, self.dpi)
        border += self._user.GetSystemMetricsForDpi(92, self.dpi)
        width = bounds.right - bounds.left
        height = bounds.bottom - bounds.top
        if not (0 <= x < width and 0 <= y < height):
            return None
        if not self.maximized:
            left, right = x < border, x >= width - border
            if y < border:
                return 13 if left else 14 if right else 12
            if y >= height - border:
                return 16 if left else 17 if right else 15
            if left or right:
                return 10 if left else 11
        top_inset = border if self.maximized else 0
        if top_inset <= y < top_inset + self.caption_height:
            if x >= width - border - self.controls_width:
                return 1  # HTCLIENT
            return 2  # HTCAPTION
        return 1
