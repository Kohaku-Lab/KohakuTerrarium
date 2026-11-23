"""Unit tests for ``kohakuterrarium.browser.observe``: origins, the pipeline, blocked reasons."""

import pytest

from kohakuterrarium.browser import snapshot as snap
from kohakuterrarium.browser.errors import DialogOpenedError
from kohakuterrarium.browser.observe import (
    blocked_reason,
    cap_text,
    format_blocked_records,
    observe,
    origin_of,
    read_fresh,
)
from kohakuterrarium.browser.policy import NetworkPolicy
from kohakuterrarium.browser.protocol import ProbeFrames
from kohakuterrarium.browser.proxy import EgressRecord


@pytest.mark.parametrize(
    "url,origin",
    [
        ("https://a.test:8443/x?y#z", "https://a.test:8443"),
        ("about:blank", "about:blank"),
        ("http://[::1/", "http://[::1/"),
        ("not a url", "not a url"),
    ],
)
def test_origin_of(url, origin):
    assert origin_of(url) == origin


def test_blocked_reason_by_address_host_or_code():
    policy = NetworkPolicy(block_hosts=("bad.test",))
    by_address = EgressRecord("x.test", 80, "CONNECT", True, "loopback", "127.0.0.1")
    by_literal = EgressRecord("10.1.2.3", 80, "GET", True, "private")
    by_host = EgressRecord("bad.test", 443, "CONNECT", True, "blocked_host")
    stale = EgressRecord("fine.test", 443, "CONNECT", True, "external")
    assert "loopback (set allow_loopback to permit)" in blocked_reason(
        by_address, policy
    )
    assert "private (set allow_private to permit)" in blocked_reason(by_literal, policy)
    assert blocked_reason(by_host, policy) == "bad.test is in block_hosts"
    assert blocked_reason(stale, policy) == "external"
    assert format_blocked_records([by_host], policy) == (
        "Blocked 1 request:\n  bad.test:443 (bad.test is in block_hosts)"
    )


class Page:
    url = "http://a.test/"

    async def evaluate(self, expression, arg=None, *, ref=None, timeout):
        return 7

    async def snapshot(self, timeout):
        return '- button "Go" [ref=e1]\n- paragraph: hidden words here'

    async def probe(self, script, timeout):
        raise DialogOpenedError(None, "a confirm opened")

    async def title(self, timeout):
        return "A"


async def run(operation, timeout, what):
    return await operation()


async def test_a_dialog_during_the_probe_is_not_swallowed():
    with pytest.raises(DialogOpenedError):
        await observe(Page(), run, 1.0)


def test_cap_text_cuts_at_a_line_boundary():
    assert cap_text("abc", 10) == ("abc", 0)
    assert cap_text("one\ntwo\nthree", 9) == ("one\ntwo", 13)
    assert cap_text("x" * 20, 5) == ("xxxxx", 20)


class Secretive(Page):
    async def snapshot(self, timeout):
        return (
            '- button "Go" [ref=e1]\n- paragraph: token SECRET-1234\n- paragraph: tail'
        )

    async def probe(self, script, timeout):
        return ProbeFrames([{"hidden": [], "visible": [], "passwords": []}])


async def test_observe_redacts_before_parsing_and_reports_the_cut():
    obs = await observe(
        Secretive(),
        run,
        1.0,
        redact=lambda text: text.replace("SECRET-1234", "[secret:x]"),
        max_chars=60,
    )
    assert obs.cut_from == 71 and obs.raw_chars == 53
    assert obs.fresh == {"e1": ("button", "Go")}
    rendered = "\n".join(snap.line_of(n) for n in obs.nodes)
    assert "[secret:x]" in rendered and "SECRET" not in rendered
    assert "tail" not in rendered
    fresh = await read_fresh(Secretive(), run, 1.0, max_chars=30)
    assert fresh == {"e1": ("button", "Go")}
