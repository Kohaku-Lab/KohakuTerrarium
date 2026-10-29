"""Owned tunnel child with parent-pipe lifetime."""

import argparse
import os
import subprocess
import sys
import threading
from pathlib import Path
from urllib.parse import urlsplit

from kohakuterrarium.mcp_server.endpoint import EndpointStore
from kohakuterrarium.mcp_server.records import write_json


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


def cloudflare_environment(env: dict[str, str]) -> dict[str, str]:
    excluded = {
        "TUNNEL_TOKEN",
        "TUNNEL_TOKEN_FILE",
        "TUNNEL_CRED_CONTENTS",
        "TUNNEL_CRED_FILE",
    }
    return {key: value for key, value in env.items() if key.upper() not in excluded}


def cloudflare_command(record, config_path: Path) -> list[str]:
    write_json(
        config_path,
        {
            "tunnel": record.cloudflare_tunnel_id,
            "credentials-file": record.cloudflare_credentials,
            "ingress": [
                {
                    "hostname": urlsplit(record.public_origin).hostname,
                    "service": f"http://127.0.0.1:{record.port}",
                },
                {"service": "http_status:404"},
            ],
        },
    )
    return [
        record.cloudflared_bin,
        "tunnel",
        "--no-autoupdate",
        "--config",
        str(config_path),
        "run",
        record.cloudflare_tunnel_id,
    ]


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
    parser.add_argument("--home-dir", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    store = EndpointStore(args.home_dir)
    record = store.load_active(args.run_id)
    if record.tunnel == "external":
        raise ValueError("External entry points have no owned tunnel process")
    env = dict(os.environ)
    stage = "configuration"
    try:
        if record.tunnel == "ngrok":
            for key in list(env):
                if key.upper() in {"HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"}:
                    env.pop(key)
            command = ngrok_command(record)
        else:
            env = cloudflare_environment(env)
            command = cloudflare_command(record, store.directory / "cloudflared.json")
        stage = "process"
        return run_owned(command, sys.stdin.buffer, env=env)
    except OSError as exc:
        sys.stderr.write(
            f"Owned {record.tunnel} tunnel {stage} failed: {type(exc).__name__} "
            f"(errno={exc.errno})\n"
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
