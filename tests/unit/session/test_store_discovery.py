"""Event-key namespace discovery: which agents and attached agents a session has."""

from kohakuterrarium.session.store_discovery import (
    discover_agents,
    discover_attached_agents,
)


class TestDiscoverAgents:
    def test_returns_agent_names_in_first_seen_order_without_duplicates(self):
        keys = ["bob:e000001", "alice:e000001", "bob:e000002", "carol:e000001"]
        assert discover_agents(keys) == ["bob", "alice", "carol"]

    def test_skips_framework_attached_and_malformed_namespaces(self):
        keys = [
            "terrarium:e000001",
            "alice:attached:helper:1:e000001",
            "no-sequence-marker",
            "alice:e000001",
        ]
        assert discover_agents(keys) == ["alice"]

    def test_agent_names_may_contain_colons(self):
        assert discover_agents(["group:alice:e000002"]) == ["group:alice"]


class TestDiscoverAttachedAgents:
    def test_reports_host_role_and_attach_sequence_once_per_namespace(self):
        keys = [
            "alice:attached:helper:1:e000001",
            "alice:attached:helper:1:e000002",
            "alice:attached:reviewer:x:2:e000001",
        ]
        assert discover_attached_agents(keys) == [
            {
                "host": "alice",
                "role": "helper",
                "attach_seq": 1,
                "namespace": "alice:attached:helper:1",
            },
            {
                "host": "alice",
                "role": "reviewer:x",
                "attach_seq": 2,
                "namespace": "alice:attached:reviewer:x:2",
            },
        ]

    def test_ignores_plain_and_unparseable_namespaces(self):
        keys = [
            "alice:e000001",
            "alice:attached:helper:not-a-number:e000001",
            "alice:attached:helper:e000001",
        ]
        assert discover_attached_agents(keys) == []
