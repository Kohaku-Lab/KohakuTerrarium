"""Foreground worker process — connects to a lab-host as a client.

``kt lab-client --host wss://host.lan:8100 --token T --name worker-1``
runs until Ctrl+C, exposing the local :class:`Terrarium` engine to the
host via four APP adapters (runtime, events, files, deploy).

This command does not start a web UI. The controller's Studio reaches
worker services through Lab.
"""

import argparse
import asyncio
import os
import signal
import sys

from kohakuterrarium.cli._config_layers import load_layered_config
from kohakuterrarium.laboratory import ClientConfig
from kohakuterrarium.laboratory._internal.client import ClientConnector
from kohakuterrarium.laboratory._internal.transport_ws import WebSocketTransport
from kohakuterrarium.laboratory.adapters import (
    StudioCatalogAdapter,
    StudioDeployAdapter,
    StudioIdentityAdapter,
    StudioSettingsAdapter,
    TerrariumAttachAdapter,
    TerrariumBroadcastAdapter,
    TerrariumEventsAdapter,
    TerrariumFilesAdapter,
    TerrariumOutputWireAdapter,
    TerrariumPtyAdapter,
    TerrariumRuntimeAdapter,
    TerrariumSessionAdapter,
)
from kohakuterrarium.laboratory.adapters._worker_session import (
    WorkerSessionAttacher,
)
from kohakuterrarium.laboratory.identity_cache import IdentityCache
from kohakuterrarium.llm.api_keys import (
    clear_api_key_resolver,
    register_api_key_resolver,
)
from kohakuterrarium.llm.codex_auth import (
    clear_codex_resolver,
    register_codex_resolver,
)
from kohakuterrarium.studio.hooks import register_group_hooks
from kohakuterrarium.studio.identity import drive_settings as _drive_settings
from kohakuterrarium.terrarium import Terrarium
from kohakuterrarium.utils.fd_limit import raise_fd_limit
from kohakuterrarium.utils.logging import (
    configure_utf8_stdio,
    enable_stderr_logging,
    get_logger,
    set_level,
)

logger = get_logger(__name__)


def _build_parser(parser: argparse.ArgumentParser) -> None:
    """Register Lab worker options on a parser."""
    # Connection fields remain optional here because environment or YAML layers
    # may supply them; validation runs after those layers are merged.
    parser.add_argument(
        "--host",
        default="",
        help="lab-host WebSocket URL, e.g. ws://127.0.0.1:8100",
    )
    parser.add_argument(
        "--token",
        default="",
        help="shared token (must match the lab-host's --lab-token)",
    )
    parser.add_argument(
        "--name",
        default="",
        help="this worker's client name (must be unique on the host)",
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
        help=(
            "the worker's config home (api keys, OAuth tokens, LLM "
            "profiles, MCP servers, sessions). Sets KT_CONFIG_DIR for "
            "this worker process. Default: ~/.kohakuterrarium. Use "
            "this to give a worker its OWN set of provider credentials "
            "(important for Codex / OAuth which are process-scoped)."
        ),
    )
    parser.add_argument(
        "--session-dir",
        default="",
        help=("override where session files live; defaults to " "<home-dir>/sessions"),
    )


def add_lab_client_subparser(subparsers) -> None:
    parser = subparsers.add_parser(
        "lab-client",
        help="Run as a Lab worker (foreground process)",
    )
    _build_parser(parser)


def lab_client_cli(args: argparse.Namespace) -> int:
    """Resolve worker configuration and run the foreground Lab client."""
    cfg = load_layered_config("client")
    # Explicit CLI values take precedence over environment, YAML, and defaults.
    if not args.host:
        args.host = cfg.get("host_url") or ""
    if not args.token:
        args.token = cfg.get("host_token") or ""
    if not args.name:
        args.name = cfg.get("client_name") or ""
    if not getattr(args, "home_dir", ""):
        args.home_dir = cfg.get("home_dir") or ""
    if not getattr(args, "session_dir", ""):
        args.session_dir = cfg.get("session_dir") or ""
    if args.heartbeat_interval == 5.0 and cfg.get("heartbeat_interval"):
        args.heartbeat_interval = float(cfg["heartbeat_interval"])
    if args.log_level == "INFO" and cfg.get("log_level"):
        args.log_level = cfg["log_level"]

    missing = [
        flag
        for flag, value in (
            ("--host (or KT_HOST_URL)", args.host),
            ("--token (or KT_HOST_TOKEN)", args.token),
            ("--name (or KT_CLIENT_NAME)", args.name),
        )
        if not value
    ]
    if missing:
        print(
            "lab-client: missing required configuration: " + ", ".join(missing),
            file=sys.stderr,
        )
        return 2

    # The logger may initialize before this command sets the environment, so
    # install stderr logging explicitly rather than relying on KT_LOG_STDERR.
    os.environ.setdefault("KT_LOG_STDERR", "1")
    # Set the config home before any configuration consumer first resolves it.
    if getattr(args, "home_dir", ""):
        os.environ["KT_CONFIG_DIR"] = args.home_dir
    configure_utf8_stdio(log=True)
    raise_fd_limit(log=True)
    set_level(args.log_level)
    enable_stderr_logging(args.log_level)
    try:
        asyncio.run(_run_worker(args))
        return 0
    except KeyboardInterrupt:
        print("\nworker interrupted, exiting.")
        return 0
    except Exception as e:
        print(f"lab-client failed: {e}", file=sys.stderr)
        return 1


async def _run_worker(args: argparse.Namespace) -> None:
    """Worker process body — start client, attach adapters, wait."""
    # Keep deterministic test dependencies outside production startup unless
    # the subprocess test seam is explicitly enabled.
    if os.environ.get("KT_TEST_LLM_SCRIPT"):
        from kohakuterrarium.testing.subprocess_seam import (
            maybe_install_test_llm_seam,
        )

        maybe_install_test_llm_seam()

    logger.info(
        "lab-client boot",
        client_name=args.name,
        host_url=args.host,
        token_present=bool(args.token),
        heartbeat_interval=args.heartbeat_interval,
        session_dir=args.session_dir or "(default)",
    )
    client = ClientConnector(
        ClientConfig(
            client_name=args.name,
            host_url=args.host,
            token=args.token,
            heartbeat_interval_seconds=args.heartbeat_interval,
        ),
        WebSocketTransport(),
    )
    # Drive settings are node-local; invalid or disabled settings resolve to a
    # Drive-disabled engine rather than preventing worker startup.
    register_group_hooks()
    drive_kwargs = _drive_settings.resolve_drive_kwargs()
    engine = Terrarium(**drive_kwargs)
    # The runtime adapter needs the attacher at construction time so newly
    # spawned creatures persist locally and mirror events to the controller.
    session_attacher = WorkerSessionAttacher(
        engine,
        client,
        session_dir=args.session_dir or None,
    )
    # Pre-warmed synchronous resolvers let LLM construction use controller-held
    # credentials without performing network I/O from the synchronous lookup.
    identity_cache = IdentityCache(client)
    register_api_key_resolver(identity_cache.sync_api_key)
    # Codex uses the same cache path to avoid falling back to the worker-local,
    # intentionally isolated OAuth store.
    register_codex_resolver(identity_cache.sync_codex_tokens)
    TerrariumRuntimeAdapter(
        engine,
        client,
        session_attacher=session_attacher,
        identity_cache=identity_cache,
    )
    TerrariumEventsAdapter(engine, client)
    # Preserve the host-local attach event shape across the Lab boundary.
    TerrariumAttachAdapter(engine, client)
    TerrariumPtyAdapter(engine, client)
    # The engine reference lets channel persistence find the cross-node
    # forwarder without importing Laboratory code into Terrarium channels.
    TerrariumBroadcastAdapter(engine, client)
    # Workers receive forwarded output events; the controller owns cluster-wide
    # target resolution. The engine reference avoids an import cycle.
    TerrariumOutputWireAdapter(engine, client)
    # Deploy and file operations must share one adapter to avoid registering the
    # ``terrarium.files`` namespace twice.
    files_adapter = TerrariumFilesAdapter(engine, client)
    StudioDeployAdapter(engine, client, files_adapter=files_adapter)
    TerrariumSessionAdapter(engine, client)
    StudioCatalogAdapter(client)
    # Identity remains node-local because OAuth tokens and provider credentials
    # may be process- or machine-specific.
    StudioIdentityAdapter(client)
    # Settings operations target this worker's config home; authorization is
    # enforced by the host before requests reach the authenticated Lab peer.
    StudioSettingsAdapter(engine, client)

    stop_event = asyncio.Event()
    loop = asyncio.get_event_loop()

    if sys.platform == "win32":
        # Windows lacks ``loop.add_signal_handler``; bridge the synchronous
        # handler into the event loop so asynchronous cleanup can still run.
        def _on_sigint_win(_signum, _frame):
            """Schedule worker shutdown from the Windows signal handler."""
            loop.call_soon_threadsafe(stop_event.set)

        signal.signal(signal.SIGINT, _on_sigint_win)
    else:

        def _on_signal_posix():
            """Request worker shutdown from a POSIX signal handler."""
            stop_event.set()

        loop.add_signal_handler(signal.SIGTERM, _on_signal_posix)
        loop.add_signal_handler(signal.SIGINT, _on_signal_posix)

    await client.start()
    # ``name`` is reserved by LogRecord, so structured context uses client_name.
    logger.info("lab-client connected", client_name=args.name, host=args.host)
    print(f"lab-client {args.name!r} connected to {args.host}")
    try:
        await stop_event.wait()
    except (asyncio.CancelledError, KeyboardInterrupt):
        # Some platforms still surface Ctrl+C directly; defer it so cleanup can await.
        pass
    finally:
        # Close session tees before the client so queued events can finish publishing.
        try:
            session_attacher.close_all()
        except Exception:  # pragma: no cover - defensive
            pass
        # Resolver registration is process-global and must not leak into later runs.
        try:
            clear_api_key_resolver()
        except Exception:  # pragma: no cover - defensive
            pass
        try:
            clear_codex_resolver()
        except Exception:  # pragma: no cover - defensive
            pass
        await client.stop()
        await engine.shutdown()


__all__ = ["add_lab_client_subparser", "lab_client_cli"]
