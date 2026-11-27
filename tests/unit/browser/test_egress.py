"""Tests for ``kohakuterrarium.browser.egress``: navigation endpoints and refusal reasons."""

import pytest

from kohakuterrarium.browser.egress import (
    blocked_hops,
    blocked_reason,
    endpoint_of,
    hop_note,
    unreachable,
)
from kohakuterrarium.browser.policy import NetworkPolicy
from kohakuterrarium.browser.proxy import EgressRecord


def record(host, port=80, blocked=True, code="blocked_host", address=None):
    return EgressRecord(host, port, "GET", blocked, code, address)


@pytest.mark.parametrize(
    "url,expected",
    [
        ("http://Example.TEST/a", ("example.test", 80)),
        ("https://example.test/", ("example.test", 443)),
        ("http://example.test:8080/", ("example.test", 8080)),
        ("http://[::1]:9/", ("::1", 9)),
        ("about:blank", ("", 0)),
        ("http://example.test:99999/", ("", 0)),
        ("not a url", ("", 0)),
    ],
)
def test_endpoint_of(url, expected):
    assert endpoint_of(url) == expected


def test_blocked_hops_keep_only_blocked_records_on_the_chain():
    records = [
        record("a.test"),
        record("b.test", blocked=False, code="ok"),
        record("b.test"),
        record("b.test", port=81),
        record("c.test"),
    ]
    hops = blocked_hops(records, ["http://a.test/", "http://b.test/x", "about:blank"])
    assert hops == [records[0], records[2]]
    assert blocked_hops(records, []) == []


def test_hop_note_names_where_the_load_led():
    assert hop_note(record("a.test"), "http://a.test/") == ""
    assert hop_note(record("a.test", 81), "http://a.test/") == " (it led to a.test:81)"
    assert hop_note(record("b.test"), "http://a.test/") == " (it led to b.test)"


def test_unreachable_needs_a_matching_record_and_code():
    records = [record("a.test", blocked=False, code="upstream")]
    assert (
        unreachable(records, "http://a.test/", "upstream")
        == "could not connect to a.test"
    )
    assert unreachable(records, "http://a.test/", "resolve_failed") == ""
    assert unreachable(records, "http://b.test/", "upstream") == ""
    assert unreachable(records, "http://a.test/", None) == ""
    failed = [record("a.test", blocked=False, code="resolve_failed")]
    assert unreachable(failed, "http://a.test/", "resolve_failed") == (
        "could not resolve a.test"
    )
    weird = [record("a.test", blocked=False, code="blocked_host")]
    assert unreachable(weird, "http://a.test/", "blocked_host") == ""


class TestBlockedReason:
    def test_a_host_record_gets_the_host_rule(self):
        policy = NetworkPolicy(block_hosts=("a.test",))
        assert blocked_reason(record("a.test"), policy) == "a.test is in block_hosts"

    def test_an_address_record_gets_the_address_rule(self):
        policy = NetworkPolicy()
        reason = blocked_reason(record("a.test", address="10.0.0.5"), policy)
        assert reason == "address 10.0.0.5 is private (set allow_private to permit)"
        literal = blocked_reason(record("127.0.0.1", 8000, code="loopback"), policy)
        assert "is loopback" in literal

    def test_an_external_refusal_of_an_address_names_the_external_reason(self):
        policy = NetworkPolicy(
            allow_loopback=True,
            external_check=lambda host, port: "port closed" if port == 81 else None,
        )
        reason = blocked_reason(record("127.0.0.1", 81, code="external"), policy)
        assert reason == "127.0.0.1 denied: port closed"
        resolved = record("a.test", 81, code="external", address="127.0.0.1")
        assert blocked_reason(resolved, policy) == "a.test denied: port closed"

    def test_an_address_record_is_judged_by_its_address_first(self):
        asked = []

        def check(host, port):
            asked.append(host)
            return "nope"

        policy = NetworkPolicy(block_hosts=("a.test",), external_check=check)
        reason = blocked_reason(record("a.test", address="10.0.0.5"), policy)
        assert reason == "address 10.0.0.5 is private (set allow_private to permit)"
        assert asked == []

    def test_a_decision_that_no_longer_refuses_falls_back_to_the_code(self):
        assert blocked_reason(record("a.test", code="gone"), NetworkPolicy()) == "gone"
