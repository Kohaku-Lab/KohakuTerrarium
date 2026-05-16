"""``kt host`` / ``kt client`` aliases — short names for the lab roles.

The framework's canonical commands are ``kt serve start --mode lab-host``
and ``kt lab-client``.  Operators and the deployment docs use the
shorter ``kt host`` / ``kt client`` forms; this module defines the
alias parsers and dispatches them onto the canonical CLIs.

Keeping the alias surface here (rather than inline in
:mod:`kohakuterrarium.cli.__init__`) keeps the package entry under the
600-line cap.
"""

import argparse

from kohakuterrarium.cli.lab_client import lab_client_cli
from kohakuterrarium.cli.serve import serve_cli


def add_host_alias(subparsers) -> None:
    """Register ``kt host`` as an alias for the lab-host serve flow.

    Adds the most common flags inline (so ``kt host --help`` is
    self-contained) and forwards to ``serve_cli`` with the equivalent
    Namespace.  Defaults match the systemd unit + the kohaku/host
    Docker image so the same operator commands work in either context.
    """
    parser = subparsers.add_parser(
        "host",
        help="Run the lab-host (alias for `kt serve start --mode lab-host --foreground`)",
        description=(
            "Start the lab-host in foreground. This is the single backend the "
            "frontend talks to; workers connect to it via `kt client` / "
            "`kt lab-client`.  For daemon-style start/stop/status, use "
            "`kt serve start/stop/status --mode lab-host` directly."
        ),
    )
    parser.add_argument(
        "--host", default="0.0.0.0", help="HTTP bind host (default 0.0.0.0)"
    )
    parser.add_argument(
        "--port", type=int, default=8001, help="HTTP port (default 8001)"
    )
    parser.add_argument("--dev", action="store_true", help="Enable dev mode")
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO",
    )
    parser.add_argument(
        "--lab-bind",
        default="0.0.0.0:8100",
        help="lab WebSocket bind (default 0.0.0.0:8100)",
    )
    parser.add_argument(
        "--lab-token",
        default="",
        help="lab shared token (or set KT_HOST_TOKEN env)",
    )
    parser.add_argument(
        "--home-dir",
        default="",
        help="config home (default ~/.kohakuterrarium; sets KT_CONFIG_DIR)",
    )


def add_client_alias(subparsers) -> None:
    """Register ``kt client`` as an alias for ``kt lab-client``.

    Mirrors lab_client's parser surface — duplicating the flag set keeps
    ``kt client --help`` self-contained without a second hop.
    """
    parser = subparsers.add_parser(
        "client",
        help="Run a lab-client worker (alias for `kt lab-client`)",
        description=(
            "Connect to a lab-host as a worker. Hosts creatures the controller "
            "schedules onto this node. Outbound-only — does NOT expose ports."
        ),
    )
    parser.add_argument(
        "--host",
        required=True,
        help="lab-host WebSocket URL, e.g. ws://127.0.0.1:8100",
    )
    parser.add_argument(
        "--token", required=True, help="shared token (must match the lab-host's)"
    )
    parser.add_argument(
        "--name", required=True, help="this worker's name (unique on the host)"
    )
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO",
    )
    parser.add_argument(
        "--heartbeat-interval",
        type=float,
        default=5.0,
        help="seconds between heartbeats (default 5.0)",
    )
    parser.add_argument(
        "--home-dir",
        default="",
        help="config home (default ~/.kohakuterrarium; sets KT_CONFIG_DIR)",
    )
    parser.add_argument(
        "--session-dir",
        default="",
        help="override session dir; default <home-dir>/sessions",
    )


def dispatch_host_alias(args: argparse.Namespace) -> int:
    """Translate ``kt host`` Namespace -> ``kt serve start --mode lab-host`` call."""
    serve_args = argparse.Namespace(
        serve_command="start",
        host=args.host,
        port=args.port,
        dev=getattr(args, "dev", False),
        log_level=args.log_level,
        mode="lab-host",
        lab_bind=args.lab_bind,
        lab_token=args.lab_token or "",
        home_dir=getattr(args, "home_dir", ""),
        foreground=True,
    )
    return serve_cli(serve_args)


def dispatch_client_alias(args: argparse.Namespace) -> int:
    """Translate ``kt client`` Namespace -> ``kt lab-client`` call."""
    client_args = argparse.Namespace(
        host=args.host,
        token=args.token,
        name=args.name,
        log_level=args.log_level,
        heartbeat_interval=args.heartbeat_interval,
        home_dir=getattr(args, "home_dir", ""),
        session_dir=getattr(args, "session_dir", ""),
    )
    return lab_client_cli(client_args)


__all__ = [
    "add_host_alias",
    "add_client_alias",
    "dispatch_host_alias",
    "dispatch_client_alias",
]
