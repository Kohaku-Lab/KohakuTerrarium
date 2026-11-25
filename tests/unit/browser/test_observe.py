"""Unit tests for ``kohakuterrarium.browser.observe``: origins, the pipeline, blocked reasons."""

import pytest

from kohakuterrarium.browser import snapshot as snap
from kohakuterrarium.browser.errors import (
    BrowserTimeoutError,
    DialogOpenedError,
    DriverError,
)
from kohakuterrarium.browser.observe import (
    MAX_FRAME_ORIGINS,
    blocked_reason,
    cap_text,
    format_blocked_records,
    observe,
    origin_of,
    read_fresh,
)
from kohakuterrarium.browser.policy import NetworkPolicy
from kohakuterrarium.browser.protocol import FrameView, ProbeFrames
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
    long_host = EgressRecord(
        "a" * 50 + ".tok-value-1." + "b" * 70, 80, "GET", True, "x"
    )
    hide = lambda text: text.replace("tok-value-1", "[secret:t]")  # noqa: E731
    listed = format_blocked_records([long_host], policy, hide)
    assert "tok-" not in listed and "[secr" in listed


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
    assert obs.cut_from == 70 and obs.raw_chars == 52
    assert obs.fresh == {"e1": ("button", "Go")}
    rendered = "\n".join(snap.line_of(n) for n in obs.nodes)
    assert "[secret:x]" in rendered and "SECRET" not in rendered
    assert "tail" not in rendered
    fresh = await read_fresh(Secretive(), run, 1.0, max_chars=30)
    assert fresh == {"e1": ("button", "Go")}


class Straddling(Secretive):
    async def snapshot(self, timeout):
        return "- paragraph: " + "x" * 37 + "SECRET-1234 tail"


async def test_a_cut_without_a_line_break_never_splits_a_secret():
    redact = lambda text: text.replace("SECRET-1234", "[secret:x]")  # noqa: E731
    obs = await observe(Straddling(), run, 1.0, redact=redact, max_chars=55)
    rendered = "\n".join(snap.line_of(n) for n in obs.raw)
    assert obs.cut_from and "SECRE" not in rendered and "[secr" in rendered


class Framed(Page):
    url = "https://shop.test/checkout"

    def __init__(
        self,
        frames: dict[str, str] | Exception | None = None,
        count=0,
        docs: dict[str, float] | None = None,
    ):
        self.frames = frames if frames is not None else {}
        self.count = count
        self.docs = docs or {}
        self.asked: list[list[str]] = []

    async def snapshot(self, timeout):
        lines = [
            "- iframe [ref=e2]:",
            '  - textbox "Card" [ref=f1e1]',
            "- iframe [ref=e3]:",
            '  - button "Help" [ref=f2e1]',
            "- iframe [ref=e4]:",
            "  - text: inline",
            "- generic [ref=e5]:",
            "  - iframe [ref=e6]:",
            '    - button "Ad" [ref=f3e1]',
        ]
        lines += [f"- iframe [ref=e{100 + i}]" for i in range(self.count)]
        return "\n".join(lines)

    async def probe(self, script, timeout):
        return ProbeFrames([])

    async def frame_views(self, refs, timeout):
        self.asked.append(list(refs))
        if isinstance(self.frames, Exception):
            raise self.frames
        known = {**dict.fromkeys(self.docs, ""), **self.frames}
        return {
            ref: FrameView(url, self.docs.get(ref))
            for ref, url in known.items()
            if ref in refs
        }


def iframe_lines(obs) -> list[str]:
    return [snap.line_of(n) for n, _, _ in snap.walk(obs.nodes) if n.role == "iframe"]


async def test_a_frame_from_another_origin_names_its_origin():
    page = Framed(
        {
            "e2": "https://pay.test:8443/card?session=abc",
            "e3": "https://shop.test/help",
            "e4": "about:srcdoc",
            "e6": "https://ads.test/x",
        }
    )
    obs = await observe(page, run, 1.0)
    assert page.asked == [["e2", "e3", "e4", "e6"]]
    assert iframe_lines(obs) == [
        "iframe [ref=e2] [origin=https://pay.test:8443]:",
        "iframe [ref=e3]:",
        "iframe [ref=e4]:",
        "iframe [ref=e6] [origin=https://ads.test]:",
    ]
    assert obs.fresh["e2"] == ("iframe", "")
    bare = await observe(
        Framed(
            {
                "e2": "HTTPS://user:pw@pay.test/x",
                "e3": "https://other.test",
                "e4": "chrome-error://chromewebdata/",
                "e6": "http://[::1]:9000/ad",
            }
        ),
        run,
        1.0,
    )
    assert iframe_lines(bare) == [
        "iframe [ref=e2] [origin=https://pay.test]:",
        "iframe [ref=e3] [origin=https://other.test]:",
        "iframe [ref=e4]:",
        "iframe [ref=e6] [origin=http://[::1]:9000]:",
    ]


async def test_frame_documents_are_keyed_by_the_ref_prefix_of_their_content():
    page = Framed({"e2": "https://pay.test/x"}, docs={"e2": 11.5, "e4": 12.5, "e6": 13})
    obs = await observe(page, run, 1.0)
    assert obs.frame_docs == {"f1": 11.5, "f3": 13}
    assert (await observe(Framed(DriverError("gone")), run, 1.0)).frame_docs == {}


async def test_frame_origins_are_redacted():
    page = Framed({"e2": "https://tok-SECRET.pay.test/x"})
    obs = await observe(
        page, run, 1.0, redact=lambda text: text.replace("SECRET", "[secret:k]")
    )
    assert (
        iframe_lines(obs)[0]
        == "iframe [ref=e2] [origin=https://tok-[secret:k].pay.test]:"
    )


@pytest.mark.parametrize(
    "failure", [BrowserTimeoutError("slow"), DriverError("frame detached")]
)
async def test_a_failed_frame_read_leaves_the_lines_plain(failure):
    obs = await observe(Framed(failure), run, 1.0)
    assert all("origin=" not in line for line in iframe_lines(obs))
    assert len(iframe_lines(obs)) == 4


async def test_a_dialog_during_the_frame_read_is_not_swallowed():
    with pytest.raises(DialogOpenedError):
        await observe(Framed(DialogOpenedError(None, "an alert opened")), run, 1.0)


async def test_frame_reads_are_capped_and_skipped_without_frames():
    page = Framed(count=MAX_FRAME_ORIGINS * 2)
    await observe(page, run, 1.0)
    assert len(page.asked[0]) == MAX_FRAME_ORIGINS
    plain = Framed()
    plain.snapshot = Page().snapshot
    await observe(plain, run, 1.0)
    assert plain.asked == []
