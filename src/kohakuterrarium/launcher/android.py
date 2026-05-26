"""Android launcher entry — boots the embedded host on loopback +
reports the **actually-bound** port via a file the Java foreground
service polls.

Called via JNI from
``packaging/android/template/.../PythonHost.java::start`` after the
Java side has set the env vars and (best-effort) extracted the
bundled sandbox.  Blocks for the lifetime of the app — when this
function returns, the host process is gone and the Java
foreground service treats that as a fatal error.

Flow:

    1. ``ensure_extracted()`` — Python-side fallback that runs even
       when Java extraction failed (rare; usually a no-op because
       Java already populated ``KT_SANDBOX_BIN_DIR``).
    2. Build the FastAPI app via ``create_app`` (standalone mode).
    3. Hand off to ``start_uvicorn_with_port_fallback`` which binds
       on a daemon thread + waits for ``server.started`` to flip
       true.  Critically: the function only returns AFTER the
       socket is actually bound, so we know the port is real.
    4. Write the verified-bound port to ``KT_PORT_FILE`` atomically.
    5. Block on the uvicorn thread; return when uvicorn shuts down.

This replaces the earlier "pre-bind then hand uvicorn the port"
pattern which had a TOCTOU race: if uvicorn failed to grab the
pre-bound port, the launcher had already written a stale port to
``KT_PORT_FILE`` and Java would poll it forever.

Designed to be importable + testable from non-Android shells —
no Android API imports; the only Android-specific behaviour is
reading env vars Java set.
"""

import os
import sys
import time
from pathlib import Path

from kohakuterrarium.utils.logging import get_logger
from kohakuterrarium.utils.mobile_sandbox import default_workdir, ensure_extracted

logger = get_logger(__name__)


def main() -> int:
    """Top-level entry called by the foreground service.

    Returns an exit code (0 = clean shutdown, non-zero = fatal).
    Android's Java side logs the failure if non-zero; the
    foreground service then sits with a "host died" notification
    until the user force-closes.
    """
    # Best-effort sandbox extraction.  Java does this too — if it
    # already ran, this is a no-op idempotent skip.  If Java
    # failed (couldn't find assets, permission issue), we may
    # still recover here if the operator sideloaded binaries.
    ensure_extracted()

    # Briefcase boots Python with ``cwd = /`` on Android.  The app
    # has no permission to read or write there, so every relative-
    # path tool call (read, write, bash, glob, …) would
    # PermissionError before this chdir.  Defense in depth — the
    # executor's own default also resolves through ``default_workdir``,
    # but subprocess children (busybox, future bundled binaries)
    # inherit our cwd, so we set it explicitly here too.
    try:
        os.chdir(default_workdir())
    except OSError as exc:  # pragma: no cover - defensive
        logger.warning(
            "android launcher: chdir to default workdir failed", error=str(exc)
        )

    try:
        return _serve_and_report()
    except KeyboardInterrupt:
        return 0
    except Exception:
        logger.exception("android launcher: host boot failed")
        return 1


def _serve_and_report() -> int:
    """Build the FastAPI app, start uvicorn, write KT_PORT_FILE
    after the real bind, then block on the uvicorn thread.

    Lazy import of the framework's heavy modules — keeps module
    load fast (the foreground service blocks app startup until
    Python boots; cold-start latency directly affects the splash
    screen duration).
    """
    from kohakuterrarium.api.app import create_app
    from kohakuterrarium.serving.web import (
        WEB_DIST_DIR,
        start_uvicorn_with_port_fallback,
    )

    # The WebView loads the host's index.html — if we don't pass
    # ``static_dir`` to ``create_app`` the SPA never mounts and
    # GET / returns 404, leaving the WebView showing a blank
    # screen + status-text-stuck-on-"Starting…" forever.  Audit
    # caught this: ``serving/web.py`` does the same wiring for
    # ``kt serve`` but the Android launcher bypassed that helper.
    static_dir = WEB_DIST_DIR if WEB_DIST_DIR.is_dir() else None
    if static_dir is None:
        logger.error(
            "android launcher: web_dist missing at boot; the WebView "
            "will see 404 on GET /.  This is a packaging bug — the "
            "Briefcase build should have copied web_dist/ into the APK.",
            expected_path=str(WEB_DIST_DIR),
        )
    app = create_app(static_dir=static_dir)

    # Requested port — env override for tests, default ephemeral
    # band (uvicorn fallback scans from here).  Android always
    # uses 8001 as the requested start since it's the framework's
    # default and avoids stomping on common dev ports.
    requested_port = int(os.environ.get("KT_SERVE_PORT", "8001") or "8001")
    if requested_port <= 0:
        requested_port = 8001

    server, bound_port = start_uvicorn_with_port_fallback(
        app,
        requested_port=requested_port,
        host="127.0.0.1",
        log_level="warning",
    )
    # ``start_uvicorn_with_port_fallback`` returns AFTER the
    # socket is bound + ``server.started`` is true, so this is the
    # real port the kernel handed us — not a pre-bind guess.
    _write_port_file(bound_port)
    logger.info("android launcher: bound", port=bound_port)

    # Watch the shutdown sentinel file so Java's
    # ``MainActivity.onDestroy`` can request a clean uvicorn exit
    # without trying to ``Thread.interrupt()`` Python's GIL-holding
    # interpreter thread (which doesn't actually stop uvicorn).
    # The contract: Java touches ``<configDir>/shutdown`` to ask
    # the host to drain; the loop below sees it and flips
    # ``server.should_exit``, which makes uvicorn finish its
    # current requests and close cleanly.
    shutdown_marker = _shutdown_marker_path()
    try:
        while not server.should_exit:
            if shutdown_marker is not None and shutdown_marker.exists():
                logger.info(
                    "android launcher: shutdown marker present; "
                    "asking uvicorn to drain"
                )
                server.should_exit = True
                break
            time.sleep(0.5)
    except KeyboardInterrupt:
        server.should_exit = True
    return 0


def _shutdown_marker_path() -> Path | None:
    config_dir = os.environ.get("KT_CONFIG_DIR", "").strip()
    if not config_dir:
        return None
    return Path(config_dir) / "shutdown"


def _write_port_file(port: int) -> None:
    """Atomic port-file write.

    Java's :meth:`KohakuHostService.getBoundPort` polls the file
    every 250ms.  Writing atomically (temp + rename) avoids the
    race where Java reads a half-written value.
    """
    path = os.environ.get("KT_PORT_FILE", "").strip()
    if not path:
        logger.warning("android launcher: KT_PORT_FILE not set; Java will poll forever")
        return
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".part")
    tmp.write_text(f"{port}\n", encoding="utf-8")
    tmp.replace(target)


if __name__ == "__main__":
    sys.exit(main())
