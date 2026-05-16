"""Laboratory layer configuration dataclasses.

Configuration is consumed by :class:`HostEngine` and
:class:`ClientConnector`. Values are immutable (``frozen=True``); to
change config, construct a new instance.

The defaults reflect the locked Laboratory design decisions:

- WebSocket-only transport
- Shared-token authentication
- Static config-based discovery
- Bounded-buffer backpressure (default 1000 envelopes)
- 5-second heartbeat interval
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class HostConfig:
    """Configuration for a laboratory host.

    The host listens for incoming client WebSocket connections,
    authenticates them via a shared token, and routes envelopes between
    connected clients.

    Attributes:
        bind_host: Interface to bind the host's WebSocket listener.
            Use ``"0.0.0.0"`` to expose externally; ``"127.0.0.1"`` for
            loopback only.
        bind_port: Port for the host's WebSocket listener.
        token: Shared token clients must present to authenticate.
            Empty string disables auth (only safe for in-process tests).
        heartbeat_interval_seconds: How often each side sends a
            heartbeat envelope.
        heartbeat_timeout_seconds: Mark a peer as lost after this many
            seconds without a heartbeat.
        backpressure_buffer_size: Max envelopes queued for outbound
            delivery to a single peer before backpressure kicks in.
    """

    bind_host: str = "127.0.0.1"
    bind_port: int = 8100
    token: str = ""
    heartbeat_interval_seconds: float = 5.0
    heartbeat_timeout_seconds: float = 15.0
    backpressure_buffer_size: int = 1000


@dataclass(frozen=True)
class ClientConfig:
    """Configuration for a laboratory client.

    The client connects outbound to a host, advertises its capabilities,
    and serves placements (creatures the user has chosen to run on this
    client) routed via the host.

    Attributes:
        client_name: Human-readable name; surfaced in the host's UI.
            Must be unique within a cluster.
        host_url: WebSocket URL of the host's Lab listener.
            Typically ``"ws://host:8100/_lab"`` for loopback or
            ``"wss://host.example.com/_lab"`` for production.
        token: Shared token (must match the host's).
        capabilities: Strings the client advertises to the host for
            placement decisions (e.g. ``("gpu", "cuda")``).
        heartbeat_interval_seconds: How often the client sends a
            heartbeat envelope to the host.
        reconnect_initial_delay_seconds: Backoff start when reconnecting.
        reconnect_max_delay_seconds: Backoff cap when reconnecting.
        backpressure_buffer_size: Max envelopes queued outbound before
            backpressure kicks in.
    """

    client_name: str
    host_url: str
    token: str
    capabilities: tuple[str, ...] = field(default_factory=tuple)
    heartbeat_interval_seconds: float = 5.0
    reconnect_initial_delay_seconds: float = 1.0
    reconnect_max_delay_seconds: float = 30.0
    backpressure_buffer_size: int = 1000
