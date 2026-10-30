"""Link opening and recovery when the desktop window leaves the app."""

import pytest

from kohakuterrarium.serving.desktop import links

HOME = "http://127.0.0.1:8001/"


class _Event:
    def __init__(self):
        self.handlers = []

    def __iadd__(self, handler):
        self.handlers.append(handler)
        return self


class _Window:
    def __init__(self, current, history_back=True):
        self.current = current
        self.history_back = history_back
        self.exposed = []
        self.loaded_urls = []
        self.scripts = []
        self.events = type("Events", (), {"loaded": _Event()})()

    def expose(self, *functions):
        self.exposed.extend(functions)

    def get_current_url(self):
        return self.current

    def evaluate_js(self, script):
        self.scripts.append(script)
        return self.history_back

    def load_url(self, url):
        self.loaded_urls.append(url)


@pytest.fixture
def browser(monkeypatch):
    opened = []
    monkeypatch.setattr(
        links.webbrowser, "open", lambda url: opened.append(url) or True
    )
    return opened


@pytest.mark.parametrize(
    "url", ["https://example.test/a", "http://example.test", "mailto:a@example.test"]
)
def test_web_and_mail_links_open_in_the_system_browser(browser, url):
    assert links.open_external(url) is True
    assert browser == [url]


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "javascript:alert(1)",
        "ms-msdt:/id PCWDiagnostic",
        "vscode://x",
        "relative/path",
        "",
        None,
    ],
)
def test_other_schemes_are_refused(browser, url):
    assert links.open_external(url) is False
    assert browser == []


def test_browser_failure_is_reported_not_raised(monkeypatch):
    def fail(_url):
        raise links.webbrowser.Error("no browser")

    monkeypatch.setattr(links.webbrowser, "open", fail)
    assert links.open_external("https://example.test") is False


@pytest.mark.parametrize(
    "current,expected",
    [
        ("https://example.test/", True),
        ("http://127.0.0.1:9999/", True),
        ("http://localhost:8001/", True),
        ("http://127.0.0.1:8001/sessions?x=1", False),
        ("about:blank", False),
        ("data:text/html,hi", False),
        (None, False),
        ("", False),
    ],
)
def test_foreign_detection_compares_origins(current, expected):
    assert links.is_foreign(current, HOME) is expected


def test_nothing_is_foreign_before_the_home_url_is_known():
    assert links.is_foreign("https://example.test/", None) is False


def test_attach_exposes_open_external_and_listens_for_loads():
    window = _Window(HOME)
    links.AppLinks(window).attach()
    assert window.exposed == [links.open_external]
    assert len(window.events.loaded.handlers) == 1


def test_escaped_page_opens_in_browser_and_history_goes_back(browser):
    window = _Window("https://example.test/page", history_back=True)
    app = links.AppLinks(window)
    app.attach()
    app.set_home(HOME)

    window.events.loaded.handlers[0]()

    assert browser == ["https://example.test/page"]
    assert "history.back" in window.scripts[0]
    assert window.loaded_urls == []


def test_escaped_page_without_history_reloads_the_app(browser):
    window = _Window("https://example.test/page", history_back=False)
    app = links.AppLinks(window)
    app.attach()
    app.set_home(HOME)

    window.events.loaded.handlers[0]()

    assert window.loaded_urls == [HOME]


def test_app_pages_are_left_alone(browser):
    window = _Window("http://127.0.0.1:8001/sessions/abc")
    app = links.AppLinks(window)
    app.attach()
    app.set_home(HOME)

    window.events.loaded.handlers[0]()

    assert browser == []
    assert window.scripts == [] and window.loaded_urls == []


def test_unreadable_current_url_is_ignored(browser):
    window = _Window(HOME)

    def broken():
        raise RuntimeError("window closed")

    window.get_current_url = broken
    app = links.AppLinks(window)
    app.attach()
    app.set_home(HOME)

    window.events.loaded.handlers[0]()

    assert browser == []
