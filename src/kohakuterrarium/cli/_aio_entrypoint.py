"""``kt-aio`` — spawn a lab-host + an embedded lab-client in one process.

Python counterpart of ``docker/kt-aio-entrypoint.sh``. Used by:

- the ``kohakuterrarium-all.service`` systemd unit (no shell in PID 1)
- operators who run ``kt-aio`` directly outside a container

What it does, in order:

1. Resolve KT_HOST_TOKEN: file (KT_HOST_TOKEN_FILE) > env > generate.
2. Write the token to ``$KT_CONFIG_DIR/host-token`` (mode 0600) so
   later worker installers can pick it up.
3. Spawn ``kt serve start --mode lab-host --foreground …`` as a child.
4. Wait for the lab port to accept connections (max 30s).
5. Spawn ``kt lab-client --host ws://127.0.0.1:<lab-port> …`` as a child.
6. Forward SIGTERM/SIGINT to both children for clean shutdown.
7. ``wait()`` for either child; if one exits, terminate the other and
   exit with the same status (so systemd ``Restart=on-failure`` can
   kick in).
"""

import os
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

from kohakuterrarium.cli._config_layers import load_layered_config

PORT_READY_TIMEOUT_S = 30.0
PORT_READY_INTERVAL_S = 0.5


def _resolve_token(config_dir: Path) -> str:
    """Return KT_HOST_TOKEN: from file, env, or freshly generated.

    The generated token gets written to ``<config_dir>/host-token``
    (mode 0600) and logged to stderr so an operator using
    ``docker logs`` / ``journalctl -fu kohakuterrarium-all`` can
    pick it up for attaching external workers.
    """
    token_file = os.environ.get("KT_HOST_TOKEN_FILE", "")
    if token_file and Path(token_file).is_file():
        return Path(token_file).read_text(encoding="utf-8").strip()

    env_token = os.environ.get("KT_HOST_TOKEN", "")
    if env_token:
        return env_token

    token = secrets.token_hex(24)
    print(
        "[kt-aio] No KT_HOST_TOKEN provided — generated one.",
        file=sys.stderr,
        flush=True,
    )
    return token


def _persist_token(config_dir: Path, token: str) -> Path:
    """Write the token to ``<config_dir>/host-token`` mode 0600."""
    config_dir.mkdir(parents=True, exist_ok=True)
    path = config_dir / "host-token"
    path.write_text(token + "\n", encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:  # pragma: no cover - Windows: chmod is best-effort
        pass
    return path


def _wait_for_port(
    host: str, port: int, timeout_s: float = PORT_READY_TIMEOUT_S
) -> bool:
    """Return True once ``host:port`` accepts a TCP connection."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=1.0):
                return True
        except OSError:
            time.sleep(PORT_READY_INTERVAL_S)
    return False


def _spawn(cmd: list[str], env: dict[str, str]) -> subprocess.Popen:
    """Spawn ``cmd`` inheriting stdio; return the Popen handle."""
    print(f"[kt-aio] spawn: {' '.join(cmd)}", file=sys.stderr, flush=True)
    return subprocess.Popen(cmd, env=env)


def run() -> int:
    """Entry point — wired to ``kt-aio`` console script in pyproject."""
    cfg = load_layered_config("all")

    home_dir = (
        cfg.get("home_dir") or os.environ.get("KT_CONFIG_DIR") or "~/.kohakuterrarium"
    )
    config_dir = Path(home_dir).expanduser()
    config_dir.mkdir(parents=True, exist_ok=True)

    http_host = cfg["http"]["host"]
    http_port = int(cfg["http"]["port"])
    lab_bind = cfg["lab"]["bind"]
    client_name = cfg.get("client_name") or "local-1"
    yaml_token = cfg["lab"].get("token") or ""
    if yaml_token and not os.environ.get("KT_HOST_TOKEN"):
        os.environ["KT_HOST_TOKEN"] = yaml_token

    token = _resolve_token(config_dir)
    token_path = _persist_token(config_dir, token)

    # Strip the colon-port from lab_bind for the readiness check.
    lab_port_str = lab_bind.rsplit(":", 1)[-1]
    try:
        lab_port = int(lab_port_str)
    except ValueError:
        print(
            f"[kt-aio] KT_LAB_BIND={lab_bind!r} doesn't have a parseable port",
            file=sys.stderr,
            flush=True,
        )
        return 2

    print(f"[kt-aio] Lab token: {token}", file=sys.stderr, flush=True)
    print(f"[kt-aio] Token also written to: {token_path}", file=sys.stderr, flush=True)
    print(
        f"[kt-aio] To attach external workers: "
        f"KT_HOST_URL=ws://<this-host>:{lab_port} "
        f"KT_HOST_TOKEN={token}",
        file=sys.stderr,
        flush=True,
    )

    base_env = os.environ.copy()
    base_env["KT_HOST_TOKEN"] = token

    # 1. Spawn lab-host
    kt = _kt_executable()
    host_cmd = [
        kt,
        "serve",
        "start",
        "--mode",
        "lab-host",
        "--foreground",
        "--host",
        http_host,
        "--port",
        str(http_port),
        "--lab-bind",
        lab_bind,
        "--lab-token",
        token,
        "--home-dir",
        str(config_dir),
    ]
    host_proc = _spawn(host_cmd, base_env)

    # 2. Wait for lab port
    print(
        f"[kt-aio] Waiting for lab port {lab_port} to be ready…",
        file=sys.stderr,
        flush=True,
    )
    if not _wait_for_port("127.0.0.1", lab_port):
        print(
            f"[kt-aio] Lab port {lab_port} did NOT become ready within "
            f"{PORT_READY_TIMEOUT_S:.0f}s — aborting.",
            file=sys.stderr,
            flush=True,
        )
        host_proc.terminate()
        host_proc.wait()
        return 1
    print("[kt-aio] Lab port ready.", file=sys.stderr, flush=True)

    # 3. Spawn embedded worker
    worker_dir = config_dir / f"worker-{client_name}"
    client_cmd = [
        kt,
        "lab-client",
        "--host",
        f"ws://127.0.0.1:{lab_port}",
        "--token",
        token,
        "--name",
        client_name,
        "--home-dir",
        str(worker_dir),
    ]
    worker_proc = _spawn(client_cmd, base_env)

    # 4. Forward SIGTERM/SIGINT to both children
    def _term(_signo, _frame):
        print("[kt-aio] Stopping…", file=sys.stderr, flush=True)
        for p in (worker_proc, host_proc):
            try:
                p.terminate()
            except Exception:
                pass

    signal.signal(signal.SIGTERM, _term)
    signal.signal(signal.SIGINT, _term)

    # 5. Wait for either child to exit; on exit, take down the other.
    try:
        while True:
            for label, proc in (("host", host_proc), ("worker", worker_proc)):
                rc = proc.poll()
                if rc is not None:
                    print(
                        f"[kt-aio] {label} exited with status {rc} — "
                        "shutting down sibling.",
                        file=sys.stderr,
                        flush=True,
                    )
                    other = worker_proc if proc is host_proc else host_proc
                    other.terminate()
                    other.wait()
                    return rc
            time.sleep(0.5)
    finally:
        for p in (worker_proc, host_proc):
            try:
                p.terminate()
                p.wait(timeout=10)
            except Exception:
                pass


def _kt_executable() -> str:
    """Locate the `kt` console script for spawning host + client.

    Prefers the same prefix as the running interpreter so a venv-
    installed ``kt`` is used even when ``PATH`` would resolve a
    different one. Falls back to ``shutil.which("kt")``.
    """
    candidate = Path(sys.exec_prefix) / "bin" / "kt"
    if candidate.is_file():
        return str(candidate)
    candidate_win = Path(sys.exec_prefix) / "Scripts" / "kt.exe"
    if candidate_win.is_file():
        return str(candidate_win)
    found = shutil.which("kt")
    if found:
        return found
    # Last resort: hope it's on PATH at runtime.
    return "kt"


if __name__ == "__main__":
    sys.exit(run())
