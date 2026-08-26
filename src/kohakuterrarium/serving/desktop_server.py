"""Desktop server child entry point."""

import argparse
import ctypes
import json
import os
import sys
import tempfile
import time
from pathlib import Path

from kohakuterrarium.serving.web import (
    WEB_DIST_DIR,
    _resolve_config_dirs,
    start_uvicorn_with_port_fallback,
)
from kohakuterrarium.utils.logging import (
    configure_utf8_stdio,
    enable_file_logging,
    enable_stderr_logging,
    get_logger,
    set_level,
)
from kohakuterrarium.utils.startup_trace import mark as mark_startup

logger = get_logger(__name__)


def _publish_state(path: Path, **fields) -> None:
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(fields, stream, ensure_ascii=False)
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x00100000, False, pid)
        if not handle:
            return False
        try:
            return kernel32.WaitForSingleObject(handle, 0) == 0x00000102
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _join_server(server, timeout: float = 5.0) -> None:
    thread = getattr(server, "_kt_thread", None)
    if thread is not None:
        thread.join(timeout)


def create_app(**kwargs):
    from kohakuterrarium.api.app import create_app as factory

    return factory(**kwargs)


def start_desktop_server(port: int, log_level: str):
    """Start the desktop FastAPI server and return its server and bound port."""
    configure_utf8_stdio(log=True)
    os.environ["KT_STARTUP_SURFACE"] = "desktop"
    enable_file_logging()
    set_level(log_level)
    enable_stderr_logging(log_level)
    if not WEB_DIST_DIR.is_dir():
        raise RuntimeError(
            "web_dist not found — run 'npm run build --prefix "
            "src/kohakuterrarium-frontend' first"
        )
    creatures_dirs, terrariums_dirs = _resolve_config_dirs()
    mark_startup(
        "desktop_config_dirs_resolved",
        surface="desktop",
        creatures=len(creatures_dirs),
        terrariums=len(terrariums_dirs),
    )
    app = create_app(
        creatures_dirs=creatures_dirs,
        terrariums_dirs=terrariums_dirs,
        static_dir=WEB_DIST_DIR,
    )
    mark_startup("desktop_app_created", surface="desktop")
    return start_uvicorn_with_port_fallback(
        app,
        requested_port=port,
        host="127.0.0.1",
        log_level="warning",
    )


def run_server_child(
    port: int, log_level: str, state_path: Path, parent_pid: int = 0
) -> int:
    """Start a desktop server child and publish its ready or error state."""
    try:
        server, actual_port = start_desktop_server(port, log_level)
        _publish_state(state_path, status="ready", port=actual_port)
        mark_startup("desktop_server_ready", surface="desktop", port=actual_port)
        while not server.should_exit:
            if parent_pid and not _pid_alive(parent_pid):
                server.should_exit = True
                break
            thread = getattr(server, "_kt_thread", None)
            if thread is not None and not thread.is_alive():
                _publish_state(
                    state_path,
                    status="error",
                    error="Desktop server stopped unexpectedly.",
                )
                return 1
            try:
                state = json.loads(state_path.read_text(encoding="utf-8"))
            except (FileNotFoundError, json.JSONDecodeError, OSError):
                state = {}
            if state.get("status") == "shutdown":
                server.should_exit = True
                break
            time.sleep(0.2)
        _join_server(server)
        return 0
    except Exception as exc:
        logger.exception("Desktop server startup failed")
        try:
            _publish_state(state_path, status="error", error=str(exc))
        except OSError:
            pass
        mark_startup(
            "desktop_startup_failed",
            surface="desktop",
            stage="server",
            error=str(exc),
        )
        return 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8001)
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO",
    )
    parser.add_argument("--state-path", type=Path, required=True)
    parser.add_argument("--parent-pid", type=int, default=0)
    args = parser.parse_args()
    return run_server_child(
        args.port,
        args.log_level,
        args.state_path,
        parent_pid=args.parent_pid,
    )


if __name__ == "__main__":
    sys.exit(main())
