"""Unit tests for kohakuterrarium.browser.policy."""

import pytest

from kohakuterrarium.browser.policy import (
    ALLOWED,
    Decision,
    HostPattern,
    NetworkPolicy,
    classify_address,
    normalize_host,
    parse_ip,
)

# ---------------------------------------------------------------------------
# Address classes
# ---------------------------------------------------------------------------

ADDRESS_CLASSES = [
    # public
    ("93.184.216.34", None),
    ("8.8.8.8", None),
    ("192.0.2.10", None),
    ("198.51.100.7", None),
    ("203.0.113.9", None),
    ("2606:4700:4700::1111", None),
    ("2001:db8::5", None),
    # loopback
    ("127.0.0.1", "loopback"),
    ("127.255.255.254", "loopback"),
    ("::1", "loopback"),
    # private
    ("10.0.0.1", "private"),
    ("10.255.255.255", "private"),
    ("172.16.0.1", "private"),
    ("172.31.255.255", "private"),
    ("192.168.1.1", "private"),
    ("fec0::1", "private"),
    # just outside the private ranges
    ("172.15.255.255", None),
    ("172.32.0.0", None),
    ("11.0.0.1", None),
    ("192.169.0.1", None),
    # carrier-grade NAT
    ("100.64.0.1", "cgnat"),
    ("100.127.255.255", "cgnat"),
    ("100.63.255.255", None),
    ("100.128.0.0", None),
    # unique-local
    ("fc00::1", "unique_local"),
    ("fd12:3456::1", "unique_local"),
    # link-local (cloud metadata lives here)
    ("169.254.169.254", "link_local"),
    ("169.254.0.1", "link_local"),
    ("fe80::1", "link_local"),
    ("fe80::1%3", "link_local"),
    ("febf::1", "link_local"),
    # unspecified / this-network
    ("0.0.0.0", "unspecified"),
    ("0.1.2.3", "unspecified"),
    ("::", "unspecified"),
    # multicast
    ("224.0.0.1", "multicast"),
    ("239.255.255.250", "multicast"),
    ("ff02::1", "multicast"),
    # reserved
    ("240.0.0.1", "reserved"),
    ("255.255.255.255", "reserved"),
    ("192.0.0.8", "reserved"),
    ("100::1", "reserved"),
    ("2001::1", "reserved"),
    ("64:ff9b:1::1", "reserved"),
    ("4000::1", "reserved"),
    # embedded IPv4 is judged by the IPv4 address
    ("::ffff:127.0.0.1", "loopback"),
    ("::ffff:10.1.2.3", "private"),
    ("::ffff:169.254.169.254", "link_local"),
    ("::ffff:93.184.216.34", None),
    ("::127.0.0.1", "loopback"),
    ("::10.0.0.1", "private"),
    ("64:ff9b::7f00:1", "loopback"),
    ("64:ff9b::a9fe:a9fe", "link_local"),
    ("64:ff9b::5db8:d822", None),
    ("2002:7f00:1::1", "loopback"),
    ("2002:c0a8:101::1", "private"),
    ("2002:5db8:d822::1", None),
]


@pytest.mark.parametrize(("address", "expected"), ADDRESS_CLASSES)
def test_classify_address(address, expected):
    assert classify_address(address) == expected


ALWAYS_BLOCKED = [
    "169.254.169.254",
    "fe80::1",
    "::ffff:169.254.169.254",
    "64:ff9b::a9fe:a9fe",
    "0.0.0.0",
    "::",
    "224.0.0.1",
    "240.0.0.1",
]


@pytest.mark.parametrize("address", ALWAYS_BLOCKED)
def test_some_classes_are_blocked_whatever_the_flags(address):
    policy = NetworkPolicy(allow_loopback=True, allow_private=True)
    decision = policy.check_address(address)
    assert not decision.allowed
    assert decision.code == classify_address(address)


FLAG_CASES = [
    # address, allow_loopback, allow_private, expected code (None = allowed)
    ("127.0.0.1", False, False, "loopback"),
    ("127.0.0.1", True, False, None),
    ("127.0.0.1", False, True, "loopback"),
    ("::1", True, False, None),
    ("::ffff:127.0.0.1", True, False, None),
    ("::ffff:127.0.0.1", False, True, "loopback"),
    ("10.0.0.1", False, False, "private"),
    ("10.0.0.1", True, False, "private"),
    ("10.0.0.1", False, True, None),
    ("100.64.0.1", False, False, "cgnat"),
    ("100.64.0.1", False, True, None),
    ("fd00::1", False, False, "unique_local"),
    ("fd00::1", False, True, None),
    ("93.184.216.34", False, False, None),
]


@pytest.mark.parametrize(("address", "loopback", "private", "code"), FLAG_CASES)
def test_check_address_flags(address, loopback, private, code):
    policy = NetworkPolicy(allow_loopback=loopback, allow_private=private)
    decision = policy.check_address(address)
    if code is None:
        assert decision == ALLOWED
    else:
        assert not decision.allowed
        assert decision.code == code
        assert address.split("%")[0] in decision.reason


def test_check_address_rejects_garbage():
    decision = NetworkPolicy().check_address("not-an-ip")
    assert (decision.allowed, decision.code) == (False, "malformed")


def test_parse_ip_strips_brackets_and_rejects_names():
    assert str(parse_ip("[::1]")) == "::1"
    assert str(parse_ip("127.0.0.1")) == "127.0.0.1"
    assert parse_ip("example.com") is None
    # decimal and hex spellings are names until a resolver says otherwise
    assert parse_ip("2130706433") is None
    assert parse_ip("0x7f000001") is None


# ---------------------------------------------------------------------------
# Host normalisation and patterns
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Example.COM", "example.com"),
        ("example.com.", "example.com"),
        ("bücher.example", "xn--bcher-kva.example"),
        ("BÜCHER.example.", "xn--bcher-kva.example"),
        ("xn--bcher-kva.example", "xn--bcher-kva.example"),
        ("[::1]", "::1"),
        ("::FFFF:7F00:1", "::ffff:127.0.0.1"),
        ("::ffff:127.0.0.1", "::ffff:127.0.0.1"),
        ("127.0.0.1", "127.0.0.1"),
        ("2130706433", "2130706433"),
    ],
)
def test_normalize_host(raw, expected):
    assert normalize_host(raw) == expected


@pytest.mark.parametrize(
    "raw", ["", ".", "a..b", "exa mple.com", "a/b", "a@b", "-" * 64 + ".com", "a\x00b"]
)
def test_normalize_host_rejects(raw):
    with pytest.raises(ValueError):
        normalize_host(raw)


PATTERN_CASES = [
    # pattern, host, port, matches
    ("example.com", "example.com", 443, True),
    ("example.com", "EXAMPLE.com.", 80, True),
    ("example.com", "www.example.com", 443, False),
    ("example.com", "badexample.com", 443, False),
    ("*.example.com", "www.example.com", 443, True),
    ("*.example.com", "a.b.example.com", 443, True),
    ("*.example.com", "example.com", 443, False),
    ("*.example.com", "badexample.com", 443, False),
    ("example.com:8080", "example.com", 8080, True),
    ("example.com:8080", "example.com", 443, False),
    ("*.example.com:443", "api.example.com", 443, True),
    ("*.example.com:443", "api.example.com", 80, False),
    ("bücher.example", "xn--bcher-kva.example", 443, True),
    ("xn--bcher-kva.example", "BÜCHER.example", 443, True),
    ("*.bücher.example", "shop.xn--bcher-kva.example", 443, True),
    ("Example.com.", "example.com", 443, True),
    ("10.0.0.5", "10.0.0.5", 80, True),
    ("10.0.0.5", "10.0.0.6", 80, False),
    ("[::1]:3000", "::1", 3000, True),
    ("[::1]:3000", "[::1]", 3001, False),
    ("::1", "0:0:0:0:0:0:0:1", 80, True),
]


@pytest.mark.parametrize(("pattern", "host", "port", "matches"), PATTERN_CASES)
def test_host_pattern_matching(pattern, host, port, matches):
    assert HostPattern.parse(pattern).matches(host, port) is matches


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "*",
        "*.",
        "**.example.com",
        "a.*.example.com",
        "example.com:0",
        "example.com:70000",
        "example.com:http",
        "[::1",
        "exa mple.com",
        "*.10.0.0.1",
    ],
)
def test_host_pattern_rejects(raw):
    with pytest.raises(ValueError):
        HostPattern.parse(raw)


def test_host_pattern_text_round_trips_normalised_form():
    assert HostPattern.parse("*.BÜCHER.example.:8443").text == (
        "*.xn--bcher-kva.example:8443"
    )
    assert HostPattern.parse("[::1]:80").text == "[::1]:80"
    assert HostPattern.parse("::1").text == "::1"


def test_host_pattern_matches_nothing_for_an_invalid_host():
    assert HostPattern.parse("example.com").matches("exa mple.com", 80) is False


# ---------------------------------------------------------------------------
# Host checks
# ---------------------------------------------------------------------------


def test_block_hosts_beat_allow_hosts():
    policy = NetworkPolicy(
        allow_hosts=("*.example.com",), block_hosts=("evil.example.com",)
    )
    assert policy.check_host("good.example.com", 443) == ALLOWED
    blocked = policy.check_host("evil.example.com", 443)
    assert (blocked.allowed, blocked.code) == (False, "blocked_host")
    assert "evil.example.com" in blocked.reason


def test_allow_hosts_restricts_when_non_empty():
    policy = NetworkPolicy(allow_hosts=("example.com",))
    assert policy.check_host("example.com", 443).allowed
    other = policy.check_host("other.com", 443)
    assert (other.allowed, other.code) == (False, "not_allowed_host")


def test_empty_allow_hosts_allows_any_host():
    assert NetworkPolicy().check_host("anything.test", 80) == ALLOWED


def test_block_hosts_port_restricted():
    policy = NetworkPolicy(block_hosts=("example.com:8080",))
    assert policy.check_host("example.com", 443).allowed
    assert policy.check_host("example.com", 8080).code == "blocked_host"


def test_numeric_hostnames_are_not_judged_by_string():
    # 2130706433 == 127.0.0.1; only the resolved address may decide.
    policy = NetworkPolicy()
    assert policy.check_host("2130706433", 80) == ALLOWED
    assert policy.check_host("0x7f000001", 80) == ALLOWED
    assert policy.check_address("127.0.0.1").code == "loopback"


def test_check_host_rejects_malformed():
    decision = NetworkPolicy().check_host("exa mple.com", 80)
    assert (decision.allowed, decision.code) == (False, "malformed")


def test_external_check_is_consulted_after_lists():
    calls = []

    def external(host, port):
        calls.append((host, port))
        return "sandbox says no" if host == "deny.test" else None

    policy = NetworkPolicy(block_hosts=("blocked.test",), external_check=external)
    assert policy.check_host("Allow.TEST.", 443) == ALLOWED
    denied = policy.check_host("deny.test", 80)
    assert (denied.allowed, denied.code) == (False, "external")
    assert "sandbox says no" in denied.reason
    assert policy.check_host("blocked.test", 443).code == "blocked_host"
    # the external check sees normalised hosts and never the blocked one
    assert calls == [("allow.test", 443), ("deny.test", 80)]


def test_external_check_failure_denies():
    def external(host, port):
        raise RuntimeError("boom")

    decision = NetworkPolicy(external_check=external).check_host("a.test", 443)
    assert (decision.allowed, decision.code) == (False, "external")


def test_policy_parses_and_validates_patterns():
    with pytest.raises(ValueError):
        NetworkPolicy(allow_hosts=("bad host",))
    policy = NetworkPolicy(allow_hosts=["Example.com"])
    assert policy.allow_hosts == (HostPattern.parse("example.com"),)


def test_with_external_check_returns_a_copy():
    base = NetworkPolicy(allow_loopback=True, allow_hosts=("a.test",))
    swapped = base.with_external_check(lambda host, port: "no")
    assert base.check_host("a.test", 1).allowed
    assert swapped.check_host("a.test", 1).code == "external"
    assert swapped.allow_loopback is True
    assert swapped.allow_hosts == base.allow_hosts


# ---------------------------------------------------------------------------
# URL checks
# ---------------------------------------------------------------------------


URL_CASES = [
    ("https://example.com/path?q=1", True, "ok"),
    ("http://example.com:8080/", True, "ok"),
    ("HTTPS://EXAMPLE.COM", True, "ok"),
    ("about:blank", True, "ok"),
    ("ABOUT:BLANK", True, "ok"),
    ("about:config", False, "scheme"),
    ("file:///etc/passwd", False, "scheme"),
    ("javascript:alert(1)", False, "scheme"),
    ("data:text/html,hi", False, "scheme"),
    ("chrome://settings", False, "scheme"),
    ("ftp://example.com/", False, "scheme"),
    ("example.com", False, "scheme"),
    ("http:///path", False, "malformed"),
    ("http://", False, "malformed"),
    ("http://user:pass@example.com/", False, "credentials"),
    ("http://user@example.com/", False, "credentials"),
    ("http://example.com:99999/", False, "malformed"),
    ("http://example.com:abc/", False, "malformed"),
    ("http://exa\tmple.com/", False, "control_char"),
    ("http://example.com/\r\nX: y", False, "control_char"),
    ("http://example.com/\x7f", False, "control_char"),
    ("http://exa mple.com/", False, "malformed"),
    ("http://[::1/", False, "malformed"),
    ("http://example.com:0/", False, "malformed"),
    ("http://[not-v6]/", False, "malformed"),
    ("http://127.0.0.1/", False, "loopback"),
    ("http://[::1]:8080/", False, "loopback"),
    ("http://169.254.169.254/latest/meta-data", False, "link_local"),
    ("http://2130706433/", True, "ok"),
]


@pytest.mark.parametrize(("url", "allowed", "code"), URL_CASES)
def test_check_url(url, allowed, code):
    decision = NetworkPolicy().check_url(url)
    assert (decision.allowed, decision.code) == (allowed, code)
    assert decision.reason


def test_check_url_applies_host_lists_with_default_ports():
    policy = NetworkPolicy(allow_hosts=("example.com:443",))
    assert policy.check_url("https://example.com/").allowed
    assert policy.check_url("http://example.com/").code == "not_allowed_host"
    assert policy.check_url("http://example.com:443/").allowed


def test_check_url_honours_loopback_flag():
    assert NetworkPolicy(allow_loopback=True).check_url("http://127.0.0.1:5173/")


def test_decision_truthiness_and_text():
    denied = Decision(False, "private", "address 10.0.0.1 is private")
    assert not denied
    assert ALLOWED
    assert str(denied) == "private: address 10.0.0.1 is private"


class TestAddressBlockPatterns:
    def policy(self, *block):
        return NetworkPolicy(block_hosts=block, allow_loopback=True, allow_private=True)

    @pytest.mark.parametrize(
        "pattern,address,port,blocked",
        [
            ("127.0.0.1:8001", "127.0.0.1", 8001, True),
            ("127.0.0.1:8001", "127.0.0.1", 8002, False),
            ("127.0.0.1", "127.0.0.1", 9, True),
            ("127.0.0.1", "::ffff:127.0.0.1", 9, True),
            ("[::ffff:127.0.0.1]:80", "127.0.0.1", 80, True),
            ("[::1]", "::1", 443, True),
            ("10.0.0.5", "10.0.0.6", 80, False),
            ("example.test", "127.0.0.1", 80, False),
        ],
    )
    def test_ip_literal_patterns_apply_to_resolved_addresses(
        self, pattern, address, port, blocked
    ):
        decision = self.policy(pattern).check_address(address, port)
        assert decision.allowed is not blocked
        if blocked:
            assert decision.code == "blocked_host"
            assert "block_hosts" in decision.reason

    def test_without_a_port_only_portless_patterns_apply(self):
        assert self.policy("127.0.0.1:8001").check_address("127.0.0.1").allowed
        assert not self.policy("127.0.0.1").check_address("127.0.0.1").allowed

    def test_check_url_with_an_ip_literal_honours_the_port(self):
        policy = self.policy("[::ffff:127.0.0.1]:8001")
        assert policy.check_url("http://127.0.0.1:8001/").code == "blocked_host"
        assert policy.check_url("http://127.0.0.1:8002/")
