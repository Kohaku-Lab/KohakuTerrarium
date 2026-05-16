"""Unit tests for :mod:`kohakuterrarium.laboratory.config` and
:mod:`kohakuterrarium.laboratory._internal.transport_base`."""

import pytest

from kohakuterrarium.laboratory.config import ClientConfig, HostConfig
from kohakuterrarium.laboratory._internal.transport_base import (
    AddressInUse,
    ConnectionClosed,
    ConnectionRefused,
)

# ── HostConfig / ClientConfig ────────────────────────────────────


class TestHostConfig:
    def test_defaults(self):
        cfg = HostConfig()
        assert cfg.bind_host == "127.0.0.1"
        assert cfg.bind_port == 8100
        assert cfg.token == ""
        assert cfg.heartbeat_interval_seconds == 5.0
        assert cfg.heartbeat_timeout_seconds == 15.0
        assert cfg.backpressure_buffer_size == 1000

    def test_frozen(self):
        cfg = HostConfig()
        with pytest.raises(Exception):
            cfg.bind_port = 9000  # type: ignore

    def test_custom_values(self):
        cfg = HostConfig(bind_host="0.0.0.0", bind_port=9000, token="sec")
        assert cfg.bind_host == "0.0.0.0"
        assert cfg.token == "sec"


class TestClientConfig:
    def test_required_fields(self):
        cfg = ClientConfig(
            client_name="worker-1",
            host_url="ws://h:8100",
            token="sec",
        )
        assert cfg.client_name == "worker-1"
        assert cfg.capabilities == ()
        assert cfg.heartbeat_interval_seconds == 5.0

    def test_with_capabilities(self):
        cfg = ClientConfig(
            client_name="w",
            host_url="ws://h",
            token="t",
            capabilities=("gpu", "cuda"),
        )
        assert "gpu" in cfg.capabilities


# ── Transport exceptions ────────────────────────────────────────


class TestExceptions:
    def test_connection_closed(self):
        assert issubclass(ConnectionClosed, Exception)

    def test_connection_refused(self):
        assert issubclass(ConnectionRefused, Exception)

    def test_address_in_use(self):
        assert issubclass(AddressInUse, Exception)
