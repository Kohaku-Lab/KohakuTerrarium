"""Immutable configuration for Laboratory hosts and clients."""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class HostConfig:
    """Configure host binding, authentication, liveness, and outbound buffering."""

    bind_host: str = "127.0.0.1"
    bind_port: int = 8100
    token: str = ""
    heartbeat_interval_seconds: float = 5.0
    heartbeat_timeout_seconds: float = 15.0
    backpressure_buffer_size: int = 1000


@dataclass(frozen=True)
class ClientConfig:
    """Configure client identity, host connection, liveness, and reconnection."""

    client_name: str
    host_url: str
    token: str
    capabilities: tuple[str, ...] = field(default_factory=tuple)
    heartbeat_interval_seconds: float = 5.0
    reconnect_initial_delay_seconds: float = 1.0
    reconnect_max_delay_seconds: float = 30.0
    backpressure_buffer_size: int = 1000
