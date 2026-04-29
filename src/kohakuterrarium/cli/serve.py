import argparse
import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from kohakuterrarium.cli.version import get_git_info, get_package_version
from kohakuterrarium.serving.web import run_web_server

RUN_DIR = Path.home() / ".kohakuterrarium" / "run"
PID_PATH = RUN_DIR / "web.pid"
STATE_PATH = RUN_DIR / "web.json"
LOG_PATH = RUN_DIR / "web.log"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes

            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            STILL_ACTIVE = 259
            handle = ctypes.windll.kernel32.OpenProcess(
                PROCESS_QUERY_LIMITED_INFORMATION, False, pid
            )
            if not handle:
                return False
            try:
                exit_code = wintypes.DWORD()
                ok = ctypes.windll.kernel32.GetExitCodeProcess(
                    handle, ctypes.byref(exit_code)
                )
                if not ok:
                    return False
                return exit_code.value == STILL_ACTIVE
            finally:
                ctypes.windll.kernel32.CloseHandle(handle)
        except Exception:
            return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _load_state() -> dict:
    if not STATE_PATH.exists():
        return {}
    try:
        with open(STATE_PATH, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_state(data: dict) -> None:
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)


def _remove_runtime_files() -> None:
    for path in (PID_PATH, STATE_PATH):
        try:
            path.unlink()
        except FileNotFoundError:
            pass


def _current_runtime() -> dict:
    state = _load_state()
    pid = int(state.get("pid", 0) or 0)
    alive = _is_pid_alive(pid)
    return {
        "state": state,
        "pid": pid,
        "alive": alive,
    }


def _write_started_state(
    *, pid: int, host: str, port: int, dev: bool, log_level: str
) -> None:
    git = get_git_info()
    _save_state(
        {
            "pid": pid,
            "host": host,
            "port": port,
            "url": f"http://{host}:{port}",
            "dev": dev,
            "log_level": log_level,
            "started_at": _utc_now_iso(),
            "cwd": str(Path.cwd()),
            "python": sys.executable,
            "log_path": str(LOG_PATH),
            "version": get_package_version(),
            "git_commit": git.get("short_commit", "") or git.get("summary", ""),
        }
    )
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    PID_PATH.write_text(str(pid), encoding="utf-8")


def _spawn_server_process(host: str, port: int, dev: bool, log_level: str) -> int:
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    log_file = open(LOG_PATH, "a", encoding="utf-8")  # noqa: SIM115
    cmd = [
        sys.executable,
        "-m",
        "kohakuterrarium",
        "__run-server",
        "--host",
        host,
        "--port",
        str(port),
        "--state-path",
        str(STATE_PATH),
    ]
    if dev:
        cmd.append("--dev")
    cmd.extend(["--log-level", str(log_level)])

    kwargs: dict[str, object] = {
        "stdin": subprocess.DEVNULL,
        "stdout": log_file,
        "stderr": log_file,
    }
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    else:
        kwargs["start_new_session"] = True

    proc = subprocess.Popen(cmd, **kwargs)
    return proc.pid


def _wait_until_alive(pid: int, timeout: float = 3.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _is_pid_alive(pid):
            return True
        time.sleep(0.1)
    return _is_pid_alive(pid)


def _wait_until_bound(pid: int, timeout: float = 3.0) -> bool:
    """Wait for the subprocess to publish its actual bound port to the state file."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not _is_pid_alive(pid):
            return False
        if _load_state().get("bound"):
            return True
        time.sleep(0.1)
    return _is_pid_alive(pid) and bool(_load_state().get("bound"))


def serve_start_cli(args: argparse.Namespace) -> int:
    runtime = _current_runtime()
    if runtime["alive"]:
        state = runtime["state"]
        print("KohakuTerrarium web daemon is already running")
        print(f"  pid:  {runtime['pid']}")
        print(f"  url:  {state.get('url', '')}")
        print(f"  log:  {state.get('log_path', LOG_PATH)}")
        return 0

    if runtime["pid"] and not runtime["alive"]:
        _remove_runtime_files()

    pid = _spawn_server_process(args.host, args.port, args.dev, args.log_level)
    _write_started_state(
        pid=pid,
        host=args.host,
        port=args.port,
        dev=args.dev,
        log_level=args.log_level,
    )
    if not _wait_until_bound(pid):
        print("Failed to start KohakuTerrarium web daemon.")
        print(f"Check log: {LOG_PATH}")
        return 1

    state = _load_state()
    print("KohakuTerrarium web daemon started")
    print(f"  pid:  {pid}")
    print(f"  url:  {state.get('url', '')}")
    print(f"  log:  {LOG_PATH}")
    return 0


def serve_stop_cli(args: argparse.Namespace) -> int:
    runtime = _current_runtime()
    pid = runtime["pid"]
    if not pid or not runtime["alive"]:
        _remove_runtime_files()
        print("KohakuTerrarium web daemon is not running.")
        return 0

    try:
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/F", "/PID", str(pid), "/T"],
                capture_output=True,
                text=True,
                check=False,
            )
        else:
            os.kill(pid, signal.SIGTERM)
    except OSError as e:
        print(f"Failed to stop daemon: {e}")
        return 1

    deadline = time.time() + float(args.timeout)
    while time.time() < deadline:
        if not _is_pid_alive(pid):
            _remove_runtime_files()
            print(f"Stopped daemon pid {pid}.")
            return 0
        time.sleep(0.1)

    try:
        if sys.platform != "win32":
            os.kill(pid, signal.SIGKILL)
    except OSError:
        pass

    time.sleep(0.2)
    if not _is_pid_alive(pid):
        _remove_runtime_files()
        print(f"Stopped daemon pid {pid}.")
        return 0

    print(f"Daemon pid {pid} did not stop within {args.timeout} seconds.")
    return 1


def serve_status_cli() -> int:
    runtime = _current_runtime()
    state = runtime["state"]
    pid = runtime["pid"]
    if not pid:
        print("KohakuTerrarium web daemon: stopped")
        print(f"  log: {LOG_PATH}")
        return 0

    if not runtime["alive"]:
        print("KohakuTerrarium web daemon: stale state")
        print(f"  previous pid: {pid}")
        if state.get("url"):
            print(f"  url:          {state['url']}")
        print(f"  log:          {state.get('log_path', LOG_PATH)}")
        return 1

    print("KohakuTerrarium web daemon: running")
    print(f"  pid:          {pid}")
    if state.get("url"):
        print(f"  url:          {state['url']}")
    if state.get("started_at"):
        print(f"  started at:   {state['started_at']}")
    if state.get("version"):
        print(f"  version:      {state['version']}")
    if state.get("git_commit"):
        print(f"  git commit:   {state['git_commit']}")
    print(f"  log:          {state.get('log_path', LOG_PATH)}")
    return 0


def serve_logs_cli(args: argparse.Namespace) -> int:
    if not LOG_PATH.exists():
        print(f"Log file not found: {LOG_PATH}")
        return 1
    print(f"Log file: {LOG_PATH}")
    with open(LOG_PATH, encoding="utf-8", errors="replace") as f:
        if args.follow:
            while True:
                line = f.readline()
                if line:
                    print(line, end="")
                    continue
                time.sleep(0.25)
        else:
            lines = f.readlines()
            tail = lines[-args.lines :] if args.lines > 0 else lines
            for line in tail:
                print(line, end="")
    return 0


def serve_restart_cli(args: argparse.Namespace) -> int:
    stop_args = argparse.Namespace(timeout=args.timeout)
    stop_code = serve_stop_cli(stop_args)
    if stop_code not in (0,):
        return stop_code
    start_args = argparse.Namespace(
        host=args.host,
        port=args.port,
        dev=args.dev,
        log_level=args.log_level,
    )
    return serve_start_cli(start_args)


def run_server_internal(args: argparse.Namespace) -> int:
    run_web_server(
        host=args.host,
        port=args.port,
        dev=args.dev,
        log_level=args.log_level,
        state_path=getattr(args, "state_path", None),
    )
    return 0


def serve_cli(args: argparse.Namespace) -> int:
    sub = getattr(args, "serve_command", None)
    if sub == "start" or sub is None:
        return serve_start_cli(args)
    if sub == "stop":
        return serve_stop_cli(args)
    if sub == "restart":
        return serve_restart_cli(args)
    if sub == "status":
        return serve_status_cli()
    if sub == "logs":
        return serve_logs_cli(args)
    if sub == "__run-server":
        return run_server_internal(args)
    print("Usage: kt serve [start|stop|restart|status|logs]")
    return 0


def add_serve_subparser(subparsers) -> None:
    serve_parser = subparsers.add_parser("serve", help="Manage the web UI daemon")
    serve_parser.set_defaults(
        serve_command="start",
        host="127.0.0.1",
        port=8001,
        dev=False,
        log_level="INFO",
    )
    serve_sub = serve_parser.add_subparsers(dest="serve_command")

    start_parser = serve_sub.add_parser("start", help="Start the web daemon")
    start_parser.add_argument("--host", default="127.0.0.1")
    start_parser.add_argument("--port", type=int, default=8001)
    start_parser.add_argument("--dev", action="store_true")
    start_parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO",
    )

    stop_parser = serve_sub.add_parser("stop", help="Stop the web daemon")
    stop_parser.add_argument("--timeout", type=float, default=5.0)

    restart_parser = serve_sub.add_parser("restart", help="Restart the web daemon")
    restart_parser.add_argument("--host", default="127.0.0.1")
    restart_parser.add_argument("--port", type=int, default=8001)
    restart_parser.add_argument("--dev", action="store_true")
    restart_parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO",
    )
    restart_parser.add_argument("--timeout", type=float, default=5.0)

    serve_sub.add_parser("status", help="Show daemon status")

    logs_parser = serve_sub.add_parser("logs", help="Show daemon logs")
    logs_parser.add_argument("--follow", action="store_true")
    logs_parser.add_argument("--lines", type=int, default=80)
