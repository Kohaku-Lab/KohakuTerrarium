"""Run the Tauri shell against the editable checkout and Vite."""

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "src" / "kohakuterrarium-frontend"
TAURI = ROOT / "packaging" / "desktop" / "src-tauri"


PRODUCTION_IDENTIFIER = "com.kohaku.terrarium"
DEVELOPMENT_IDENTIFIER = "com.kohaku.terrarium.development"


def interpreter() -> Path:
    """Return the interpreter running this script."""
    return Path(sys.executable)


def production_data() -> Path:
    """Return the data folder the installed app uses."""
    configured = os.environ.get("KT_CONFIG_DIR")
    return Path(configured) if configured else Path.home() / ".kohakuterrarium"


def check_source(python: Path, repository: Path) -> None:
    """Reject interpreters that import another checkout or an installed release."""
    if not python.is_file():
        raise ValueError(
            f"Python not found: {python}; create .venv and install -e '.[dev]'"
        )
    code = (
        "import json, pathlib, kohakuterrarium; "
        "print(json.dumps(str(pathlib.Path(kohakuterrarium.__file__).resolve())))"
    )
    env = os.environ.copy()
    for key in ("PYTHONPATH", "PYTHONHOME"):
        env.pop(key, None)
    result = subprocess.run(
        [str(python), "-c", code],
        cwd=repository,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    expected = (repository / "src/kohakuterrarium/__init__.py").resolve()
    if result.returncode:
        raise ValueError(
            "Cannot import kohakuterrarium; install -e '.[dev]' in the selected interpreter"
        )
    try:
        actual = Path(json.loads(result.stdout.strip())).resolve()
    except (ValueError, TypeError) as exc:
        raise ValueError("Python source check returned an invalid path") from exc
    if actual != expected:
        raise ValueError(
            f"Python imports {actual}, expected editable source {expected}"
        )


def check_ports(frontend: int, backend: int) -> None:
    """Check both ports without attaching to or terminating their current owners."""
    if frontend == backend or not all(
        1024 <= port <= 65535 for port in (frontend, backend)
    ):
        raise ValueError(
            "Choose distinct frontend/backend ports between 1024 and 65535"
        )
    for port in (frontend, backend):
        with socket.socket() as listener:
            if os.name == "nt":
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            else:
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                listener.bind(("127.0.0.1", port))
            except OSError as exc:
                raise ValueError(
                    f"Port {port} is in use; stop its owner or choose another port"
                ) from exc


def configuration(python: Path, data: Path, frontend: int, backend: int) -> dict:
    """Construct the shared source-mode configuration."""
    return {
        "python": str(python.absolute()),
        "repository": str(ROOT),
        "data": str(data.resolve()),
        "frontend": f"http://127.0.0.1:{frontend}/",
        "port": backend,
        "owner_pid": os.getpid(),
    }


def tauri_config(config: dict, production: bool = False) -> dict:
    """Return a development-only override for the installed Tauri CLI.

    Production keeps the installed app's identifier, so shell settings and
    WebView storage are shared with it.
    """
    identity = (
        {"identifier": PRODUCTION_IDENTIFIER, "productName": "KohakuTerrarium"}
        if production
        else {
            "identifier": DEVELOPMENT_IDENTIFIER,
            "productName": "KohakuTerrarium Development",
        }
    )
    return {
        **identity,
        "bundle": {"active": False, "resources": None},
        "build": {
            "devUrl": config["frontend"],
            "beforeDevCommand": {
                "script": "node scripts/desktop-vite.mjs",
                "cwd": str(FRONTEND),
                "wait": False,
            },
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--python",
        type=Path,
        default=interpreter(),
        help="Interpreter for the backend (default: the one running this script)",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        help="Data folder (default: .internal/desktop-dev, or the installed app's with --production)",
    )
    parser.add_argument(
        "--production",
        action="store_true",
        help="Use the installed app's data folder and app identity; quit the installed app first",
    )
    parser.add_argument("--frontend-port", type=int, default=15173)
    parser.add_argument("--backend-port", type=int, default=18001)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Check prerequisites without starting services",
    )
    parser.add_argument(
        "--no-watch", action="store_true", help="Disable Rust file watching"
    )
    args = parser.parse_args()
    if args.data_dir is None:
        args.data_dir = (
            production_data() if args.production else ROOT / ".internal/desktop-dev"
        )
    try:
        check_source(args.python.absolute(), ROOT)
        check_ports(args.frontend_port, args.backend_port)
        node = shutil.which("node")
        if not node:
            raise ValueError("Node.js is required")
        cargo = shutil.which("cargo")
        if not cargo:
            raise ValueError(
                "Rust/Cargo is required; install rustup and reopen the terminal"
            )
        cli = FRONTEND / "node_modules/@tauri-apps/cli/tauri.js"
        if not cli.is_file():
            raise ValueError(
                "Tauri CLI is missing; run npm ci in src/kohakuterrarium-frontend"
            )
        config = configuration(
            args.python, args.data_dir, args.frontend_port, args.backend_port
        )
        print(json.dumps(config, indent=2))
        if args.check:
            return 0
        args.data_dir.mkdir(parents=True, exist_ok=True)
        env = os.environ.copy()
        env["KT_DESKTOP_DEV_CONFIG"] = json.dumps(config)
        command = [
            node,
            str(cli),
            "dev",
            "--features",
            "source-dev",
            "--config",
            json.dumps(tauri_config(config, args.production)),
        ]
        if args.no_watch:
            command.append("--no-watch")
        return subprocess.call(command, cwd=TAURI, env=env)
    except (ValueError, OSError, subprocess.TimeoutExpired) as exc:
        print(f"Desktop development: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
