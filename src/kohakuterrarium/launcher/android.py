"""Run the Android host and report its bound port to the Java service.

The module relies only on environment variables supplied by the Java host, so
it remains importable outside Android. The port file is written only after
uvicorn confirms its socket bind, preventing the service from observing a
stale preselected port.
"""

import os
import sys
import time
from pathlib import Path

from kohakuterrarium.utils.logging import get_logger
from kohakuterrarium.utils.mobile_sandbox import default_workdir, ensure_extracted

logger = get_logger(__name__)


def main() -> int:
    """Run the Android host until shutdown and return its process exit code."""
    # Extraction is idempotent and lets Python recover when Java did not
    # populate the sandbox but usable binaries are otherwise available.
    ensure_extracted()

    # Briefcase starts Python at an unwritable root directory. Set a usable
    # working directory here because subprocesses inherit it even when tools
    # resolve their own paths through ``default_workdir``.
    try:
        os.chdir(default_workdir())
    except OSError as exc:  # pragma: no cover - platform failure
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
    """Serve the Android app, publish the verified port, and await shutdown.

    Framework imports remain lazy because the foreground service blocks app
    startup until Python finishes booting.
    """
    from kohakuterrarium.api.app import create_app
    from kohakuterrarium.serving.web import (
        WEB_DIST_DIR,
        start_uvicorn_with_port_fallback,
    )

    # The WebView depends on the bundled SPA being mounted at the host root;
    # without ``static_dir``, startup succeeds but GET / returns 404.
    static_dir = WEB_DIST_DIR if WEB_DIST_DIR.is_dir() else None
    if static_dir is None:
        logger.error(
            "android launcher: web_dist missing at boot; the WebView "
            "will see 404 on GET /.  This is a packaging bug — the "
            "Briefcase build should have copied web_dist/ into the APK.",
            expected_path=str(WEB_DIST_DIR),
        )
    app = create_app(static_dir=static_dir)

    # The configured port is the first candidate; the server helper scans for
    # a fallback if it is unavailable.
    requested_port = int(os.environ.get("KT_SERVE_PORT", "8001") or "8001")
    if requested_port <= 0:
        requested_port = 8001

    server, bound_port = start_uvicorn_with_port_fallback(
        app,
        requested_port=requested_port,
        host="127.0.0.1",
        log_level="warning",
    )
    # Publish only the port whose socket uvicorn has confirmed as bound.
    _write_port_file(bound_port)
    logger.info("android launcher: bound", port=bound_port)

    # Java creates this marker to request uvicorn's cooperative shutdown;
    # interrupting the Python host thread would not reliably stop the server.
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
    """Atomically publish the bound port for the polling Java service."""
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
