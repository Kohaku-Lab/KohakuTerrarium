"""Owned ngrok child with parent-pipe lifetime; external tunnels never enter here."""

import argparse
import os
import subprocess
import sys
import threading
from pathlib import Path

from kohakuterrarium.mcp_server.connection import ConnectionStore


def ngrok_command(record) -> list[str]:
    command = [
        record.ngrok_bin,
        "http",
        f"http://127.0.0.1:{record.port}",
        "--url",
        record.public_origin,
        "--inspect=false",
        "--log=false",
    ]
    if record.ngrok_config:
        command.extend(["--config", record.ngrok_config])
    return command


def run_owned(command: list[str], parent_input, *, env=None) -> int:
    """EOF means the supervisor exited (including crashes); reap only our child."""
    stopped = threading.Event()

    def watch_parent():
        try:
            parent_input.read()
        finally:
            stopped.set()

    threading.Thread(target=watch_parent, daemon=True).start()
    child = subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        env=env,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        close_fds=True,
    )
    try:
        while child.poll() is None and not stopped.wait(0.2):
            pass
        return child.returncode if child.returncode is not None else 0
    finally:
        if child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--state-dir", type=Path, required=True)
    args = parser.parse_args()
    record = ConnectionStore(args.workspace, args.state_dir).load()
    if record.tunnel != "ngrok":
        raise ValueError("External entry points have no owned tunnel process")
    env = dict(os.environ)
    # The previously verified ngrok setup uses its own configured connectivity,
    # independent of proxy variables injected into the hosting tool process.
    for key in list(env):
        if key.upper() in {"HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"}:
            env.pop(key)
    try:
        return run_owned(ngrok_command(record), sys.stdin.buffer, env=env)
    except OSError:
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
