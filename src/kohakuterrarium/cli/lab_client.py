"""Foreground worker process — connects to a lab-host as a client.

``kt lab-client --host wss://host.lan:8100 --token T --name worker-1``
runs until Ctrl+C, exposing the local :class:`Terrarium` engine to the
host via four APP adapters (runtime, events, files, deploy).

This command does not start a web UI — workers don't serve their own
Studio surface in 1.5.x.  The controller's Studio reaches into the
worker through Lab.
"""

import argparse
import asyncio
import os
import signal
import sys

from kohakuterrarium.laboratory import ClientConfig
from kohakuterrarium.laboratory._internal.client import ClientConnector
from kohakuterrarium.laboratory._internal.transport_ws import WebSocketTransport
from kohakuterrarium.laboratory.adapters import (
    StudioCatalogAdapter,
    StudioDeployAdapter,
    StudioIdentityAdapter,
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
from kohakuterrarium.terrarium import Terrarium
from kohakuterrarium.utils.logging import (
    configure_utf8_stdio,
    enable_stderr_logging,
    get_logger,
    set_level,
)

logger = get_logger(__name__)


def _build_parser(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--host",
        required=True,
        help="lab-host WebSocket URL, e.g. ws://127.0.0.1:8100",
    )
    parser.add_argument(
        "--token",
        required=True,
        help="shared token (must match the lab-host's --lab-token)",
    )
    parser.add_argument(
        "--name",
        required=True,
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
    # Always a foreground process: route logs to stderr so the user
    # can actually see them.  Setting KT_LOG_STDERR alone isn't
    # enough — ``get_logger`` may have already initialised the
    # framework's root handler at module-import time (KT_LOG_STDERR
    # unset back then), and its one-shot ``if _handler is None``
    # guard never re-runs.  Call ``enable_stderr_logging`` explicitly
    # so the stderr handler is wired regardless of import order.
    os.environ.setdefault("KT_LOG_STDERR", "1")
    # ``--home-dir`` re-homes every config_dir() consumer for this
    # process: api_keys.yaml, llm_profiles.json, codex tokens, mcp
    # servers, sessions.  Set BEFORE configure_utf8_stdio / logger init
    # so any first-touch of the config dir lands at the right place.
    if getattr(args, "home_dir", ""):
        os.environ["KT_CONFIG_DIR"] = args.home_dir
    configure_utf8_stdio(log=True)
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
    # Test-only seam: when ``KT_TEST_LLM_SCRIPT`` is set, route every
    # LLM factory call to a deterministic ``ScriptedLLM`` reading that
    # file.  Production runs never set the env var; the import (and
    # therefore the ``testing`` package) stays out of the call graph.
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
    engine = Terrarium()
    # Auto-attach SessionStore + SessionEventTee per spawned creature
    # so events persist on this worker AND mirror to the controller.
    # Constructed BEFORE the runtime adapter so the adapter can hand
    # creatures off as they spawn.
    session_attacher = WorkerSessionAttacher(
        engine,
        client,
        session_dir=args.session_dir or None,
    )
    # IdentityCache backed by the controller's StudioIdentityAdapter.
    # The runtime adapter pre-warms it per spawn; a sync resolver
    # registered into llm.api_keys ensures the engine's LLM builder
    # finds keys without ever leaving the loop.
    identity_cache = IdentityCache(client)
    register_api_key_resolver(identity_cache.sync_api_key)
    # Codex resolver: mirrors the api-key path so the worker's
    # ``CodexOAuthProvider`` build picks up the host's Codex tokens
    # via ``studio.identity.get_codex_token`` instead of falling back
    # to the worker-local ``codex-auth.json`` (which is intentionally
    # isolated under the worker's ``KT_CONFIG_DIR``).
    register_codex_resolver(identity_cache.sync_codex_tokens)
    TerrariumRuntimeAdapter(
        engine,
        client,
        session_attacher=session_attacher,
        identity_cache=identity_cache,
    )
    TerrariumEventsAdapter(engine, client)
    # Attach-WS proxy — the controller's chat WebSocket opens a
    # ``terrarium.attach.start_attach`` stream against this adapter so
    # tool calls, sub-agent events, channel messages, and interactive
    # UI events all reach the frontend with the same shape they have
    # for a host-local creature.
    TerrariumAttachAdapter(engine, client)
    # PTY-WS proxy — spawn a shell in the creature's working directory
    # on this worker and bridge stdin/stdout to the controller's WS.
    TerrariumPtyAdapter(engine, client)
    # Cross-node channel forwarder.  Per-node state: which peers want
    # local sends on (graph, channel) forwarded to them, plus the
    # subscriptions we hold on peers.  Stashes itself on the engine
    # under ``_broadcast_adapter`` so the channel persistence hook
    # in ``terrarium/channels.py`` finds it without an import cycle.
    TerrariumBroadcastAdapter(engine, client)
    # Cross-node output-wiring forwarder.  Workers only ever RECEIVE
    # forwarded events (they have no cluster-wide target resolver);
    # the controller drives outbound forwarding via the multi-node
    # service.  Stashes itself on the engine under
    # ``_output_wire_adapter`` so :class:`TerrariumOutputWiringResolver`
    # finds it without an import cycle.
    TerrariumOutputWireAdapter(engine, client)
    # StudioDeployAdapter shares the files adapter — installing both
    # without sharing would double-register the `terrarium.files`
    # namespace and crash on startup.
    files_adapter = TerrariumFilesAdapter(engine, client)
    StudioDeployAdapter(engine, client, files_adapter=files_adapter)
    # Read-side session ops the controller's mirror uses to rehydrate.
    TerrariumSessionAdapter(engine, client)
    # Per-node catalog so the controller's aggregator can see this
    # worker's installed packages.
    StudioCatalogAdapter(client)
    # ``studio.identity`` adapter on the WORKER side too — exposes the
    # worker's local identity store (api_keys.yaml / codex-auth.json /
    # llm_profiles.json / mcp servers) so the controller's per-node
    # Settings > Providers UI can manage credentials per worker.
    # Codex login on a worker runs OAuth on the worker's machine, so
    # the resulting token is process-local and Codex calls from this
    # worker actually succeed (host's token would mismatch).
    StudioIdentityAdapter(client)

    stop_event = asyncio.Event()
    loop = asyncio.get_event_loop()

    if sys.platform == "win32":
        # Windows: ``loop.add_signal_handler`` is not implemented.  Use
        # ``signal.signal`` (synchronous) and bounce into the loop via
        # ``call_soon_threadsafe`` so the wait below resolves cleanly
        # instead of asyncio.run raising KeyboardInterrupt into the
        # finally block (which then can't ``await``).
        def _on_sigint_win(_signum, _frame):
            loop.call_soon_threadsafe(stop_event.set)

        signal.signal(signal.SIGINT, _on_sigint_win)
    else:

        def _on_signal_posix():
            stop_event.set()

        loop.add_signal_handler(signal.SIGTERM, _on_signal_posix)
        loop.add_signal_handler(signal.SIGINT, _on_signal_posix)

    await client.start()
    # NB: ``name`` is a reserved LogRecord attribute — passing it via
    # ``extra`` raises KeyError.  Rename to ``client_name``.
    logger.info("lab-client connected", client_name=args.name, host=args.host)
    print(f"lab-client {args.name!r} connected to {args.host}")
    try:
        await stop_event.wait()
    except (asyncio.CancelledError, KeyboardInterrupt):
        # If we still slip past the signal handler (e.g. on platforms
        # where Ctrl+C arrives as KeyboardInterrupt despite our hook),
        # swallow it here so the finally cleanup can await cleanly.
        pass
    finally:
        # Release every Tee BEFORE stopping the client so the pump
        # tasks can flush whatever is queued instead of being
        # cancelled mid-publish.
        try:
            session_attacher.close_all()
        except Exception:  # pragma: no cover - defensive
            pass
        # Clear the resolver so the next ``kt`` invocation in the
        # same process (tests, embedded uses) starts from a clean
        # slate.
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
