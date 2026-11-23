"""Unit tests for ``kohakuterrarium.browser.launch``: executables, args, proxy, loop check."""

import asyncio

import pytest

from kohakuterrarium.browser.errors import BrowserUnavailableError
from kohakuterrarium.browser.launch import (
    LOOPBACK_BYPASS_ARG,
    Candidate,
    check_event_loop,
    plan_launch,
)
from kohakuterrarium.browser.options import BrowserOptions

PROXY = "http://127.0.0.1:4567"


def plan(**options):
    return plan_launch(BrowserOptions.from_mapping(options), PROXY)


def test_default_order_is_bundled_then_system_chrome_and_edge():
    assert plan().candidates == (
        Candidate("bundled chromium"),
        Candidate("system chrome", channel="chrome"),
        Candidate("system msedge", channel="msedge"),
    )


def test_explicit_executable_or_channel_is_used_alone():
    assert plan(executable_path="/opt/chrome", channel="chrome").candidates == (
        Candidate("executable_path", executable_path="/opt/chrome"),
    )
    assert plan(channel="msedge").candidates == (
        Candidate("channel msedge", channel="msedge"),
    )


def test_launch_kwargs_route_everything_through_the_proxy():
    p = plan(headless=False)
    kwargs = p.launch_kwargs(p.candidates[0])
    assert kwargs["proxy"] == {"server": PROXY}
    assert "bypass" not in kwargs["proxy"]
    assert kwargs["headless"] is False and kwargs["handle_sigint"] is False
    assert kwargs["timeout"] == p.timeout_s * 1000
    assert "executable_path" not in kwargs and "channel" not in kwargs
    assert LOOPBACK_BYPASS_ARG == "--proxy-bypass-list=<-loopback>"
    assert kwargs["args"] == [
        "--disable-quic",
        "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
        LOOPBACK_BYPASS_ARG,
    ]
    assert not any(a.startswith("--disable-features") for a in kwargs["args"])
    chrome = p.launch_kwargs(p.candidates[1])
    assert chrome["channel"] == "chrome"
    exe = plan(executable_path="/x/chrome").launch_kwargs(
        Candidate("executable_path", executable_path="/x/chrome")
    )
    assert exe["executable_path"] == "/x/chrome"


def test_context_kwargs_fix_viewport_and_scale():
    assert plan(viewport="1024x700").context_kwargs() == {
        "viewport": {"width": 1024, "height": 700},
        "device_scale_factor": 1.0,
        "accept_downloads": True,
        "service_workers": "allow",
    }


def test_profile_choice():
    assert plan().profile_dir is None and not plan().persistent
    persistent = plan(profile_dir="/tmp/profile")
    assert persistent.persistent and persistent.profile_dir == "/tmp/profile"


class FakeSelectorLoop:
    pass


def test_non_proactor_loop_is_rejected_on_windows(monkeypatch):
    with pytest.raises(BrowserUnavailableError, match="Proactor event loop"):
        check_event_loop(FakeSelectorLoop(), platform="win32")
    check_event_loop(FakeSelectorLoop(), platform="linux")
    monkeypatch.delattr(asyncio, "ProactorEventLoop", raising=False)
    with pytest.raises(BrowserUnavailableError, match="Proactor event loop"):
        check_event_loop(FakeSelectorLoop(), platform="win32")


@pytest.mark.skipif(
    not hasattr(asyncio, "ProactorEventLoop"), reason="Windows-only loop class"
)
def test_proactor_loop_is_accepted():
    loop = asyncio.ProactorEventLoop()
    try:
        check_event_loop(loop, platform="win32")
    finally:
        loop.close()


async def test_running_loop_is_checked_by_default():
    check_event_loop(platform="linux")
