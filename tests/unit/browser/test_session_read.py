"""Unit tests for ``BrowserSession.read_page``, ``async with`` and the one-shot ``read_page``.

Against the scripted driver and the session's real filtering proxy: a read
returns plain data, goes through the same URL check, proxy and policy as
``navigate``, raises typed errors, and leaves no trace an agent could see.
"""

import asyncio

import pytest

from kohakuterrarium.browser.errors import (
    ActionSpecError,
    BrowserClosedError,
    BrowserTimeoutError,
    ContextDestroyedError,
    DriverError,
    NavigationBlockedError,
    NavigationError,
)
from kohakuterrarium.browser.options import BrowserOptions
from kohakuterrarium.browser.protocol import NavResponse, PageContent
from kohakuterrarium.browser.read import read_page
from kohakuterrarium.browser.session import BrowserSession
from kohakuterrarium.browser.session.reading import READ_MAX_CHARS, PageRead
from tests.helpers.browser_driver import (
    ScriptedDialog,
    ScriptedDownload,
    ScriptedDriver,
)
from tests.unit.browser.test_session import make, shop

NEWS = "http://news.test/a"
NEWS_HTML = (
    "<!DOCTYPE html><html><head><title>News</title></head>"
    "<body><h1>Headline</h1><p>Body text</p></body></html>"
)
SITE = {
    NEWS: {
        "title_text": "News",
        "html_text": NEWS_HTML,
        "body_text": "Headline\nBody text",
        "snapshot_text": '- heading "Headline" [level=1] [ref=e1]',
    },
}
SECRET = "Sx9f2kq-hunter-77"


def site(browser) -> None:
    shop(browser)
    browser.site.update(SITE)


def reader(tmp_path, page_setup=None, **options):
    """A session whose browser serves ``SITE``; ``page_setup`` prepares read pages."""

    def setup(browser):
        site(browser)
        browser.page_setup = page_setup

    return make(tmp_path, setup=setup, **options)


def page_content(page, max_chars: int) -> PageContent:
    """What the scripted page's ``content`` returns when no handler steps in."""
    html, text = page.html_text, page.body_text
    return PageContent(html[:max_chars], text[:max_chars], len(html), len(text))


def read_pages(driver) -> list:
    """Every page the session opened with ``new_page``, oldest first."""
    return [p for b in driver.browsers for p in b.page_list[1:]]


def assert_no_read_left(session, driver) -> None:
    assert all(page.closed for page in read_pages(driver))
    assert [t.id for t in session._tabs] in ([], ["t1"])
    assert not session._read_owned


async def proxied_get(session, host: str) -> None:
    proxy = session._stack.proxy
    reader_, writer = await asyncio.open_connection("127.0.0.1", proxy.port)
    writer.write(f"GET http://{host}/ HTTP/1.1\r\nHost: {host}\r\n\r\n".encode())
    await writer.drain()
    await reader_.read()
    writer.close()


def slices(value: str, size: int = 6) -> list[str]:
    return [value[i : i + size] for i in range(len(value) - size + 1)]


class TestRead:
    async def test_returns_status_title_final_url_html_and_text(self, tmp_path):
        def setup(page):
            async def goto(url, timeout):
                page.load(NEWS)
                return NavResponse(NEWS, 203)

            page.handlers["goto"] = goto

        session, driver = reader(tmp_path, setup)
        async with session:
            got = await session.read_page("http://short.test/x")
        assert got == PageRead(
            url=NEWS,
            status=203,
            title="News",
            html=NEWS_HTML,
            text="Headline\nBody text",
        )
        assert not got.html_truncated and not got.text_truncated
        assert driver.alive() == []

    async def test_the_read_tab_is_invisible_to_the_agent(self, tmp_path):
        session, driver = reader(tmp_path)
        first = await session.navigate("http://shop.test/")
        refs = repr(vars(session._refs))
        last = session._last["t1"]
        before = [(t.id, t.page) for t in session._tabs]
        got = await session.read_page(NEWS)
        assert got.title == "News"
        assert [(t.id, t.page) for t in session._tabs] == before
        assert session._tabs.active.id == "t1"
        assert repr(vars(session._refs)) == refs
        assert session._last == {"t1": last}
        assert_no_read_left(session, driver)
        listing = await session.tabs_list()
        assert listing.meta["tabs"] == [{"tab": "t1", "url": "http://shop.test/"}]
        again = await session.snapshot()
        assert "news.test" not in again.text and "Headline" not in again.text
        assert again.meta == first.meta
        opened = await session.tab_new()
        assert opened.meta["active"] == "t2"
        await session.close()

    async def test_reading_needs_no_agent_tab_and_leaves_the_launch_tab(self, tmp_path):
        session, driver = reader(tmp_path)
        got = await session.read_page(NEWS)
        assert got.url == NEWS and len(driver.plans) == 1
        assert [t.id for t in session._tabs] == ["t1"]
        assert session._tabs.active.page.url == "http://shop.test/"
        await session.close()

    @pytest.mark.parametrize(
        "url",
        [
            "file:///etc/passwd",
            "javascript:alert(1)",
            "http://user:pw@news.test/",
            "http://evil.test/",
            "http://10.0.0.5/",
            "http://127.0.0.1:8000/",
        ],
    )
    async def test_a_refused_url_raises_before_anything_runs(self, tmp_path, url):
        session, driver = reader(tmp_path, block_hosts=["evil.test"])
        with pytest.raises(NavigationBlockedError):
            await session.read_page(url)
        assert driver.plans == [] and session.state == "new"
        await session.close()

    async def test_a_refused_url_on_a_running_browser_opens_no_page(self, tmp_path):
        session, driver = reader(tmp_path, block_hosts=["evil.test"])
        await session.snapshot()
        with pytest.raises(NavigationBlockedError, match="evil.test is in block_hosts"):
            await session.read_page("http://evil.test/")
        assert read_pages(driver) == []
        await session.close()

    @pytest.mark.parametrize("bad", [None, "", "   ", 7])
    async def test_a_missing_url_is_an_argument_error(self, tmp_path, bad):
        session, driver = reader(tmp_path)
        with pytest.raises(ActionSpecError):
            await session.read_page(bad)
        assert driver.plans == []
        await session.close()

    async def test_the_external_check_is_consulted(self, tmp_path):
        asked = []

        def check(host, port):
            asked.append((host, port))
            return "sandbox says no" if host == "news.test" else None

        session = BrowserSession(
            BrowserOptions(),
            downloads_dir=tmp_path,
            driver=ScriptedDriver(site),
            external_check=check,
        )
        with pytest.raises(NavigationBlockedError, match="sandbox says no"):
            await session.read_page(NEWS)
        assert asked == [("news.test", 80)]
        await session.close()

    async def test_a_redirect_the_proxy_blocked_raises_and_closes_the_tab(
        self, tmp_path
    ):
        session, driver = make(tmp_path, block_hosts=["b.test"])
        await session.snapshot()

        def setup(page):
            async def goto(url, timeout):
                driver.browser.sink.navigation_started(page, "http://b.test/landing")
                await proxied_get(session, "b.test")
                return NavResponse("http://b.test/landing", 403, "block_hosts")

            page.handlers["goto"] = goto

        driver.browser.page_setup = setup
        with pytest.raises(NavigationBlockedError) as info:
            await session.read_page("http://a.test/")
        assert "(it led to b.test)" in str(info.value)
        assert_no_read_left(session, driver)
        later = await session.snapshot()
        assert "b.test" not in later.text
        await session.close()

    async def test_a_script_navigation_to_a_blocked_host_is_caught(self, tmp_path):
        session, driver = make(tmp_path, block_hosts=["b.test"])
        await session.snapshot()

        def setup(page):
            async def content(max_chars, timeout):
                driver.browser.sink.navigation_started(page, "http://b.test/")
                await proxied_get(session, "b.test")
                page._url = "http://b.test/"
                return page_content(page, max_chars)

            page.handlers["content"] = content

        driver.browser.page_setup = setup
        with pytest.raises(NavigationBlockedError, match="it led to b.test"):
            await session.read_page("http://a.test/")
        assert_no_read_left(session, driver)
        await session.close()

    async def test_an_unreachable_host_is_a_navigation_error(self, tmp_path):
        session, driver = reader(tmp_path)
        await session.snapshot()

        def setup(page):
            async def goto(url, timeout):
                await proxied_get(session, "nowhere.invalid")
                page.load(url)
                return NavResponse(url, 502, "resolve_failed")

            page.handlers["goto"] = goto

        driver.browser.page_setup = setup
        with pytest.raises(NavigationError, match="could not resolve nowhere.invalid"):
            await session.read_page("http://nowhere.invalid/")
        assert_no_read_left(session, driver)
        await session.close()

    async def test_a_failed_load_is_a_navigation_error(self, tmp_path):
        def setup(page):
            page.raises["goto"] = DriverError(
                "net::ERR_CONNECTION_RESET", detail="reset by the page"
            )

        session, driver = reader(tmp_path, setup)
        with pytest.raises(NavigationError) as info:
            await session.read_page(NEWS)
        assert "net::ERR_CONNECTION_RESET" in str(info.value)
        assert info.value.detail == "reset by the page"
        assert_no_read_left(session, driver)
        await session.close()

    async def test_a_load_that_hangs_times_out(self, tmp_path):
        session, driver = reader(tmp_path, lambda page: page.hang.add("goto"))
        with pytest.raises(BrowserTimeoutError, match="did not finish within 0.5 s"):
            await session.read_page(NEWS)
        assert_no_read_left(session, driver)
        await session.close()

    async def test_a_cancelled_read_closes_its_tab_and_frees_the_session(
        self, tmp_path
    ):
        session, driver = reader(tmp_path, lambda page: page.hang.add("goto"))
        await session.snapshot()
        task = asyncio.create_task(session.read_page(NEWS))
        for _ in range(50):
            if read_pages(driver):
                break
            await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.sleep(0.05)
        assert_no_read_left(session, driver)
        assert (await session.snapshot()).meta["tab"] == "t1"
        await session.close()

    async def test_a_page_that_navigates_while_read_is_retried_once(self, tmp_path):
        def setup(page):
            outcomes = [ContextDestroyedError("navigated"), None]

            async def content(max_chars, timeout):
                outcome = outcomes.pop(0)
                if outcome is not None:
                    raise outcome
                return page_content(page, max_chars)

            page.handlers["content"] = content

        session, _ = reader(tmp_path, setup)
        got = await session.read_page(NEWS)
        assert got.text == "Headline\nBody text"
        await session.close()

    async def test_the_html_and_text_bounds_hold(self, tmp_path):
        def setup(browser):
            site(browser)
            browser.site[NEWS] = {
                "html_text": "<p>" + "x" * 500,
                "body_text": "y" * 500,
            }

        session, _ = make(tmp_path, setup=setup)
        got = await session.read_page(NEWS, max_chars=100)
        assert len(got.html) == 100 and got.html_truncated
        assert len(got.text) == 100 and got.text_truncated
        assert READ_MAX_CHARS >= 1_000_000
        await session.close()

    @pytest.mark.parametrize("bad", [0, -1, True, 1.5, "100", 10**9])
    async def test_an_invalid_bound_is_an_argument_error(self, tmp_path, bad):
        session, driver = reader(tmp_path)
        with pytest.raises(ActionSpecError, match="max_chars"):
            await session.read_page(NEWS, max_chars=bad)
        assert driver.plans == []
        await session.close()


class TestSecrets:
    @pytest.fixture
    def secret_env(self, monkeypatch):
        monkeypatch.setenv("KT_T_READ_PW", SECRET)
        return {"secrets": {"pw": "env:KT_T_READ_PW"}}

    async def test_secrets_are_redacted_from_every_field(self, tmp_path, secret_env):
        url = f"http://news.test/?token={SECRET}"

        def setup(browser):
            site(browser)
            browser.site[url] = {
                "title_text": f"Hi {SECRET}",
                "html_text": f"<p data-k='{SECRET}'>{SECRET}</p>",
                "body_text": f"your key {SECRET}",
            }

        session, _ = make(tmp_path, setup=setup, **secret_env)
        got = await session.read_page(url)
        blob = repr(got)
        assert not [s for s in slices(SECRET) if s in blob], blob
        assert "[secret:pw]" in got.url and "[secret:pw]" in got.html
        await session.close()

    @pytest.mark.parametrize("cut", range(1, len(SECRET)))
    async def test_a_cut_never_leaves_part_of_a_secret(self, tmp_path, secret_env, cut):
        limit = 40
        html = "a" * (limit - cut) + SECRET + "tail"

        def setup(browser):
            site(browser)
            browser.site[NEWS] = {"html_text": html, "body_text": html}

        session, _ = make(tmp_path, setup=setup, **secret_env)
        got = await session.read_page(NEWS, max_chars=limit)
        for field in (got.html, got.text):
            assert got.html_truncated and len(field) <= limit
            assert not any(field.endswith(SECRET[:k]) for k in range(1, cut + 1))
            assert field.startswith("a" * (limit - cut))
        await session.close()

    async def test_an_error_message_never_carries_a_secret(self, tmp_path, secret_env):
        session, _ = reader(tmp_path, block_hosts=["evil.test"], **secret_env)
        with pytest.raises(NavigationBlockedError) as info:
            await session.read_page(f"http://evil.test/?k={SECRET}")
        assert SECRET not in str(info.value) and "[secret:pw]" in str(info.value)
        await session.close()


class TestNoTrace:
    async def test_dialogs_of_the_read_page_are_dismissed_and_never_pending(
        self, tmp_path
    ):
        dialogs = []

        def setup(page):
            async def goto(url, timeout):
                page.load(url)
                for kind in ("confirm", "alert", "prompt", "beforeunload"):
                    dialogs.append(page.open_dialog(ScriptedDialog(kind, "Sure?")))
                return NavResponse(url, 200)

            page.handlers["goto"] = goto

        session, _ = reader(tmp_path, setup)
        await session.snapshot()
        got = await session.read_page(NEWS)
        await asyncio.sleep(0.05)
        assert got.title == "News"
        assert [d.answer for d in dialogs] == [("dismiss", None)] * 4
        assert session._dialogs.pending is None
        after = await session.snapshot()
        assert "Sure?" not in after.text and "dialog" not in after.text.lower()
        await session.close()

    async def test_downloads_of_the_read_page_are_cancelled_never_saved(self, tmp_path):
        downloads = []

        def setup(page):
            async def goto(url, timeout):
                page.load(url)
                download = ScriptedDownload("report.csv")
                downloads.append(download)
                page.browser.sink.download_started(page, download)
                return NavResponse(url, 200)

            page.handlers["goto"] = goto

        session, _ = reader(tmp_path, setup)
        await session.snapshot()
        got = await session.read_page(NEWS)
        await asyncio.sleep(0.05)
        assert got.title == "News"
        assert downloads[0].cancelled and downloads[0].saved_to is None
        assert not (tmp_path / "downloads").exists() or not any(
            (tmp_path / "downloads").iterdir()
        )
        assert session._downloads.started == 0
        after = await session.snapshot()
        assert "report.csv" not in after.text
        await session.close()

    @pytest.mark.parametrize("announced", ["by the driver", "by the event"])
    async def test_a_url_that_only_downloads_is_a_navigation_error(
        self, tmp_path, announced
    ):
        download = ScriptedDownload("a.zip")

        def setup(page):
            async def goto(url, timeout):
                if announced == "by the driver":
                    return NavResponse("about:blank", download=True)
                page.browser.sink.download_started(page, download)
                return NavResponse("about:blank")

            page.handlers["goto"] = goto

        session, driver = reader(tmp_path, setup)
        with pytest.raises(NavigationError, match="started a download"):
            await session.read_page("http://news.test/a.zip")
        assert_no_read_left(session, driver)
        if announced == "by the event":
            await asyncio.sleep(0.01)
            assert download.cancelled
        await session.close()

    async def test_windows_the_read_page_opens_are_closed_never_tabs(self, tmp_path):
        popups = []

        def setup(page):
            async def goto(url, timeout):
                page.load(url)
                browser = page.browser
                popups.append(browser.open_popup("http://pop.test/now"))
                browser.sink.window_opening(page)
                browser.sink.window_opening(page)

                def later():
                    popup = browser.open_popup("http://pop.test/later")
                    popups.append(popup)
                    popup.open_dialog(ScriptedDialog("confirm", "Stay?"))

                asyncio.get_running_loop().call_later(0.1, later)
                return NavResponse(url, 200)

            page.handlers["goto"] = goto

        session, driver = reader(tmp_path, setup)
        await session.snapshot()
        await session.read_page(NEWS)
        await asyncio.sleep(0.2)
        assert len(popups) == 2 and all(p.closed for p in popups)
        assert [t.id for t in session._tabs] == ["t1"]
        assert session._dialogs.pending is None
        after = await session.snapshot()
        assert "pop.test" not in after.text and "t2" not in after.text
        await session.close()

    async def test_blocked_requests_of_the_read_are_not_reported_to_the_agent(
        self, tmp_path
    ):
        session, driver = make(tmp_path, block_hosts=["ads.test", "old.test"])
        await session.snapshot()
        await proxied_get(session, "old.test")

        def setup(page):
            async def goto(url, timeout):
                page.load(url)
                await proxied_get(session, "ads.test")
                return NavResponse(url, 200)

            page.handlers["goto"] = goto

        driver.browser.page_setup = setup
        await session.read_page("http://a.test/")
        after = await session.snapshot()
        assert "old.test" in after.text
        assert "ads.test" not in after.text
        await session.close()

    async def test_reads_and_agent_calls_never_interleave(self, tmp_path):
        log = []
        release = asyncio.Event()

        def setup(page):
            async def goto(url, timeout):
                log.append("read starts")
                await release.wait()
                page.load(url)
                log.append("read loaded")
                return NavResponse(url, 200)

            page.handlers["goto"] = goto

        session, driver = reader(tmp_path, setup)
        await session.snapshot()
        agent = driver.browser.page
        agent.handlers["snapshot"] = lambda **_: log.append("agent reads") or (
            agent.snapshot_text
        )
        read = asyncio.create_task(session.read_page(NEWS))
        while "read starts" not in log:
            await asyncio.sleep(0.01)
        snap = asyncio.create_task(session.snapshot())
        second = asyncio.create_task(session.read_page(NEWS))
        await asyncio.sleep(0.1)
        assert log == ["read starts"] and not snap.done()
        release.set()
        await asyncio.gather(read, snap, second)
        assert log[:3] == ["read starts", "read loaded", "agent reads"]
        assert log[3:] == ["read starts", "read loaded"]
        await session.close()

    async def test_a_lost_browser_is_still_reported_to_the_agent_after_a_read(
        self, tmp_path
    ):
        session, driver = reader(tmp_path)
        await session.navigate("http://shop.test/")
        driver.browser.crash()
        await asyncio.sleep(0)
        await session.read_page(NEWS)
        result = await session.snapshot()
        assert "Browser state was lost" in result.text
        later = await session.snapshot()
        assert "Browser state was lost" not in later.text
        await session.close()


class TestLifecycle:
    async def test_reading_a_closed_session_raises_the_closed_error(self, tmp_path):
        session, driver = reader(tmp_path)
        await session.close()
        with pytest.raises(BrowserClosedError):
            await session.read_page(NEWS)
        assert driver.plans == []

    async def test_closing_during_a_read_raises_the_closed_error(self, tmp_path):
        session, driver = reader(tmp_path, lambda page: page.hang.add("goto"))
        await session.snapshot()
        task = asyncio.create_task(session.read_page(NEWS))
        while not read_pages(driver):
            await asyncio.sleep(0.01)
        await session.close()
        with pytest.raises(BrowserClosedError):
            await task
        assert driver.alive() == []

    async def test_a_read_launches_lazily_and_the_idle_close_follows(self, tmp_path):
        session, driver = reader(tmp_path, idle_close_s=0.2)
        await session.read_page(NEWS)
        assert len(driver.plans) == 1 and session.state == "ready"
        for _ in range(100):
            if session.state == "new" and driver.alive() == []:
                break
            await asyncio.sleep(0.02)
        assert session.state == "new" and driver.alive() == []
        await session.close()

    async def test_async_with_closes_on_exit_and_on_error(self, tmp_path):
        session, driver = reader(tmp_path)
        with pytest.raises(RuntimeError, match="boom"):
            async with session as entered:
                assert entered is session
                await session.read_page(NEWS)
                raise RuntimeError("boom")
        assert session.state == "closed" and driver.alive() == []


class TestNoDownloadsDirectory:
    async def test_downloads_are_refused_and_nothing_is_written(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.chdir(tmp_path)
        driver = ScriptedDriver(shop)
        session = BrowserSession(
            BrowserOptions(action_timeout_s=0.3, nav_timeout_s=0.5),
            driver=driver,
            settle_grace=0.001,
        )
        await session.snapshot()
        page = driver.browser.page
        download = ScriptedDownload("report.csv")

        async def click(**_):
            page.browser.sink.download_started(page, download)

        page.handlers["click"] = click
        result = await session.act([{"kind": "click", "ref": "e2"}])
        await asyncio.sleep(0.05)
        assert download.cancelled and download.saved_to is None
        assert "Download failed" in result.text
        assert "keeps no downloads" in result.text
        assert list(tmp_path.iterdir()) == []
        await session.close()


class TestOneShot:
    async def test_launches_once_reads_and_leaves_nothing_running(self, tmp_path):
        driver = ScriptedDriver(site)
        got = await read_page(NEWS, driver=driver)
        assert got.title == "News" and got.status == 200
        assert len(driver.plans) == 1 and driver.alive() == []

    async def test_a_refusal_raises_and_launches_nothing(self):
        driver = ScriptedDriver(site)
        with pytest.raises(NavigationBlockedError, match="not today"):
            await read_page(
                NEWS, driver=driver, external_check=lambda host, port: "not today"
            )
        assert driver.plans == []

    async def test_a_failure_after_launch_leaves_nothing_running(self):
        def setup(browser):
            site(browser)
            browser.page_setup = lambda page: page.raises.__setitem__(
                "goto", DriverError("net::ERR_FAILED")
            )

        driver = ScriptedDriver(setup)
        with pytest.raises(NavigationError):
            await read_page(
                NEWS,
                BrowserOptions(action_timeout_s=0.3, nav_timeout_s=0.5),
                driver=driver,
            )
        assert len(driver.plans) == 1 and driver.alive() == []

    async def test_options_reach_the_session(self):
        driver = ScriptedDriver(site)
        with pytest.raises(NavigationBlockedError, match="loopback"):
            await read_page("http://127.0.0.1:9/", BrowserOptions(), driver=driver)
        got = await read_page(
            NEWS,
            BrowserOptions(allow_hosts=("news.test",)),
            driver=driver,
            max_chars=5,
        )
        assert got.html == NEWS_HTML[:5] and got.html_truncated
