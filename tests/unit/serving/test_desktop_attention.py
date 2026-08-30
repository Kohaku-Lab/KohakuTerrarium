from types import SimpleNamespace

import kohakuterrarium.serving.desktop_attention as attention


class Window:
    def __init__(self, native=None):
        self.native = native
        self.exposed = []

    def expose(self, *functions):
        self.exposed.extend(functions)


def test_expose_registers_named_pywebview_functions():
    window = Window()

    attention.expose_desktop_attention(window)

    assert [function.__name__ for function in window.exposed] == [
        "get_desktop_capabilities",
        "request_desktop_attention",
    ]
    assert window.exposed[0]()["surface"] == "desktop"


def test_windows_attention_uses_native_intptr_handle(monkeypatch):
    calls = []

    class Handle:
        def ToInt64(self):
            return 4242

    user32 = SimpleNamespace(
        GetForegroundWindow=lambda: 7,
        FlashWindowEx=lambda info: calls.append(info) or 1,
    )
    monkeypatch.setattr(attention.sys, "platform", "win32")
    monkeypatch.setattr(
        attention.ctypes, "windll", SimpleNamespace(user32=user32), raising=False
    )
    window = Window(SimpleNamespace(Handle=Handle()))

    assert attention.request_desktop_attention(window) is True
    assert len(calls) == 1


def test_active_window_does_not_request_attention(monkeypatch):
    class Handle:
        def ToInt64(self):
            return 4242

    user32 = SimpleNamespace(
        GetForegroundWindow=lambda: 4242,
        FlashWindowEx=lambda info: (_ for _ in ()).throw(
            AssertionError("must not flash")
        ),
    )
    monkeypatch.setattr(attention.sys, "platform", "win32")
    monkeypatch.setattr(
        attention.ctypes, "windll", SimpleNamespace(user32=user32), raising=False
    )

    assert (
        attention.request_desktop_attention(Window(SimpleNamespace(Handle=Handle())))
        is False
    )


def test_missing_native_capability_degrades_safely(monkeypatch):
    monkeypatch.setattr(attention.sys, "platform", "linux")
    window = Window()

    assert attention.get_desktop_capabilities(window) == {
        "surface": "desktop",
        "protocol": 1,
        "nativeAttention": False,
    }
    assert attention.request_desktop_attention(window) is False
