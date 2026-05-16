"""``kt service`` — install / uninstall / status / edit systemd units.

Linux only. Reads unit-file templates from ``packaging/systemd/``
shipped inside the wheel, fills in placeholders (path of ``kt`` /
``kt-aio``, default working dir, default ports), writes the result to
``/etc/systemd/system/``, and runs ``systemctl daemon-reload``.

Three roles:

- ``--host``   → ``kohakuterrarium-host.service``
- ``--client`` → ``kohakuterrarium-client@.service`` (instance template;
                 ``--name`` becomes the instance suffix when starting)
- ``--all``    → ``kohakuterrarium-all.service`` (one-process AIO)

Use ``--no-install`` to render the rendered files into the CWD without
touching ``/etc/systemd/system/`` — useful for review or for shipping
the rendered units via a configuration-management tool (Ansible,
puppet) instead of letting the installer touch root paths.
"""

import argparse
import os
import secrets
import shutil
import subprocess
import sys
from pathlib import Path

from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

# ``/etc`` paths the installer touches. Constants so tests can pin
# them and so the same names appear in the generated journalctl
# instructions.
SYSTEMD_UNIT_DIR = Path("/etc/systemd/system")
SYSTEMD_ENV_DIR = Path("/etc/kohakuterrarium")
UNIT_HOST = "kohakuterrarium-host.service"
UNIT_CLIENT_TEMPLATE = "kohakuterrarium-client@.service"
UNIT_ALL = "kohakuterrarium-all.service"
ENV_HOST = "host.env"
ENV_CLIENT = "client.env"
ENV_ALL = "all.env"
DEFAULT_HOME_HOST = "/var/lib/kohakuterrarium-host"
DEFAULT_HOME_CLIENT_BASE = "/var/lib/kohakuterrarium-client"
DEFAULT_HOME_ALL = "/var/lib/kohakuterrarium"

_PACKAGING_DIR = Path(__file__).resolve().parents[1] / "packaging" / "systemd"
# Templates ship inside the wheel under
# ``kohakuterrarium/packaging/systemd/*.template`` via package-data
# (see pyproject ``[tool.setuptools.package-data]``).  Editable
# installs also have a copy at the repo's top-level
# ``packaging/systemd/`` — ``_read_template`` tries the package
# location first so dev and prod always resolve to the same content.


def _is_supported_platform() -> bool:
    return sys.platform.startswith("linux")


def _read_template(name: str) -> str:
    """Load a packaging/systemd template by filename.

    Resolution order: package-data alongside the installed wheel
    (``kohakuterrarium/packaging/systemd/``), then the repo-relative
    ``packaging/systemd/`` (for editable installs).
    """
    candidates = [
        _PACKAGING_DIR / name,
        Path(__file__).resolve().parents[3] / "packaging" / "systemd" / name,
    ]
    for path in candidates:
        if path.is_file():
            return path.read_text(encoding="utf-8")
    raise FileNotFoundError(
        f"systemd template {name!r} not found in any of: {candidates!r}"
    )


def _resolve_kt_executable() -> str:
    """Absolute path to the ``kt`` console script.

    Prefer ``shutil.which("kt")`` — that's what the user already has
    on PATH and what the unit will use after a reboot. Fall back to
    the running interpreter's ``Scripts/bin`` dir.
    """
    found = shutil.which("kt")
    if found:
        return found
    candidate = Path(sys.exec_prefix) / "bin" / "kt"
    if candidate.is_file():
        return str(candidate)
    raise FileNotFoundError(
        "Could not locate `kt` on PATH or in this interpreter's bin dir. "
        "Install KohakuTerrarium globally (or in a venv on PATH) before "
        "running `kt service install`."
    )


def _resolve_kt_aio_executable() -> str:
    """Absolute path to the ``kt-aio`` console script."""
    found = shutil.which("kt-aio")
    if found:
        return found
    candidate = Path(sys.exec_prefix) / "bin" / "kt-aio"
    if candidate.is_file():
        return str(candidate)
    raise FileNotFoundError(
        "Could not locate `kt-aio` on PATH. The `kt-aio` console script "
        "is shipped by KohakuTerrarium 1.5+; reinstall the package."
    )


def _render_host_unit(
    *, home_dir: str, http_host: str, http_port: int, lab_bind: str
) -> str:
    template = _read_template("kohakuterrarium-host.service.template")
    return template.format(
        kt_bin=_resolve_kt_executable(),
        home_dir=home_dir,
        http_host=http_host,
        http_port=http_port,
        lab_bind=lab_bind,
    )


def _render_client_unit(*, home_dir_base: str) -> str:
    template = _read_template("kohakuterrarium-client@.service.template")
    return template.format(
        kt_bin=_resolve_kt_executable(),
        home_dir_base=home_dir_base,
    )


def _render_all_unit(*, home_dir: str) -> str:
    template = _read_template("kohakuterrarium-all.service.template")
    return template.format(
        kt_aio_bin=_resolve_kt_aio_executable(),
        home_dir=home_dir,
    )


def _render_env(pairs: dict[str, str]) -> str:
    """Render KEY=VALUE lines for an EnvironmentFile=."""
    return "\n".join(f"{k}={v}" for k, v in pairs.items()) + "\n"


def _require_linux() -> None:
    if not _is_supported_platform():
        print(
            "ERROR: `kt service` only supports Linux. "
            "On Windows / macOS, use the Docker images "
            "(see docs/en/guides/deployment-docker.md) or run "
            "`kt serve start --mode lab-host` directly.",
            file=sys.stderr,
        )
        raise SystemExit(2)


def _require_root() -> None:
    if os.geteuid() != 0:
        print(
            "ERROR: `kt service install` writes to /etc/systemd/system/ "
            "and /etc/kohakuterrarium/ — please re-run with sudo.",
            file=sys.stderr,
        )
        raise SystemExit(2)


def _write_unit_and_env(
    *,
    unit_name: str,
    unit_content: str,
    env_name: str | None,
    env_content: str | None,
    install: bool,
    out_dir: Path,
) -> list[Path]:
    """Write unit + env to ``out_dir``. Returns the paths written.

    When ``install=True`` and ``out_dir`` is the real systemd dir, we
    copy through a temp file + ``os.replace`` so a half-written unit
    can never be picked up by ``daemon-reload``.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    unit_target = out_dir / unit_name
    _atomic_write(unit_target, unit_content, mode=0o644)
    written.append(unit_target)

    if env_name and env_content:
        env_dir = SYSTEMD_ENV_DIR if install else out_dir
        env_dir.mkdir(parents=True, exist_ok=True)
        env_target = env_dir / env_name
        _atomic_write(env_target, env_content, mode=0o600)
        written.append(env_target)

    return written


def _atomic_write(path: Path, content: str, *, mode: int) -> None:
    tmp = Path(str(path) + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.chmod(tmp, mode)
    os.replace(tmp, path)


def _systemctl(*args: str) -> int:
    cmd = ["systemctl", *args]
    print(f"[service] {' '.join(cmd)}", file=sys.stderr)
    return subprocess.call(cmd)


def _print_post_install(unit_full_name: str) -> None:
    print(f"\nUnit installed: {unit_full_name}\n")
    print("Enable on boot and start now:")
    print(f"  sudo systemctl enable --now {unit_full_name}")
    print("Tail logs:")
    print(f"  journalctl -fu {unit_full_name}")
    print("Status:")
    print(f"  systemctl status {unit_full_name}")
    print()


# ─────────────────────────────────────────────────────────────────────
# Subcommand handlers
# ─────────────────────────────────────────────────────────────────────


def _cmd_install(args: argparse.Namespace) -> int:
    _require_linux()
    if not args.no_install:
        _require_root()

    out_dir = SYSTEMD_UNIT_DIR if not args.no_install else Path.cwd()
    install = not args.no_install

    if args.role == "host":
        return _install_host(args, out_dir=out_dir, install=install)
    if args.role == "client":
        return _install_client(args, out_dir=out_dir, install=install)
    if args.role == "all":
        return _install_all(args, out_dir=out_dir, install=install)
    print("ERROR: one of --host, --client, --all is required", file=sys.stderr)
    return 2


def _install_host(args, *, out_dir: Path, install: bool) -> int:
    home_dir = args.home_dir or DEFAULT_HOME_HOST
    http_host = args.http_host or "0.0.0.0"
    http_port = args.http_port or 8001
    lab_bind = args.lab_bind or "0.0.0.0:8100"
    token = args.host_token or secrets.token_hex(24)

    unit = _render_host_unit(
        home_dir=home_dir,
        http_host=http_host,
        http_port=http_port,
        lab_bind=lab_bind,
    )
    env = _render_env({"KT_HOST_TOKEN": token})

    written = _write_unit_and_env(
        unit_name=UNIT_HOST,
        unit_content=unit,
        env_name=ENV_HOST,
        env_content=env,
        install=install,
        out_dir=out_dir,
    )

    if not install:
        print("Rendered (not installed):")
        for p in written:
            print(f"  {p}")
        print("\nTo install manually:")
        print(f"  sudo cp {written[0]} {SYSTEMD_UNIT_DIR}/")
        if len(written) > 1:
            print(f"  sudo cp {written[1]} {SYSTEMD_ENV_DIR}/")
        print("  sudo systemctl daemon-reload")
        return 0

    _systemctl("daemon-reload")
    print(f"\nLab token (write this down — workers need it):\n  {token}\n")
    _print_post_install("kohakuterrarium-host.service")
    return 0


def _install_client(args, *, out_dir: Path, install: bool) -> int:
    if not args.host_url:
        print("ERROR: --host-url is required for client install", file=sys.stderr)
        return 2
    if not args.host_token:
        print("ERROR: --host-token is required for client install", file=sys.stderr)
        return 2

    home_base = args.home_dir or DEFAULT_HOME_CLIENT_BASE
    unit = _render_client_unit(home_dir_base=home_base)
    env = _render_env({"KT_HOST_URL": args.host_url, "KT_HOST_TOKEN": args.host_token})

    written = _write_unit_and_env(
        unit_name=UNIT_CLIENT_TEMPLATE,
        unit_content=unit,
        env_name=ENV_CLIENT,
        env_content=env,
        install=install,
        out_dir=out_dir,
    )

    if not install:
        print("Rendered (not installed):")
        for p in written:
            print(f"  {p}")
        print("\nTo install manually:")
        print(f"  sudo cp {written[0]} {SYSTEMD_UNIT_DIR}/")
        if len(written) > 1:
            print(f"  sudo cp {written[1]} {SYSTEMD_ENV_DIR}/")
        print("  sudo systemctl daemon-reload")
        print(f"  sudo systemctl enable --now kohakuterrarium-client@{args.name}")
        return 0

    _systemctl("daemon-reload")
    print(f"\nWorker name: {args.name}\n")
    print("Enable + start this worker:")
    print(f"  sudo systemctl enable --now kohakuterrarium-client@{args.name}")
    print("Tail logs:")
    print(f"  journalctl -fu kohakuterrarium-client@{args.name}")
    print()
    return 0


def _install_all(args, *, out_dir: Path, install: bool) -> int:
    home_dir = args.home_dir or DEFAULT_HOME_ALL
    unit = _render_all_unit(home_dir=home_dir)

    env_pairs: dict[str, str] = {}
    if args.host_token:
        env_pairs["KT_HOST_TOKEN"] = args.host_token
    if args.lab_bind:
        env_pairs["KT_LAB_BIND"] = args.lab_bind

    written = _write_unit_and_env(
        unit_name=UNIT_ALL,
        unit_content=unit,
        env_name=ENV_ALL if env_pairs else None,
        env_content=_render_env(env_pairs) if env_pairs else None,
        install=install,
        out_dir=out_dir,
    )

    if not install:
        print("Rendered (not installed):")
        for p in written:
            print(f"  {p}")
        print("\nTo install manually:")
        for p in written:
            target = (
                SYSTEMD_UNIT_DIR if p.name.endswith(".service") else SYSTEMD_ENV_DIR
            )
            print(f"  sudo cp {p} {target}/")
        print("  sudo systemctl daemon-reload")
        return 0

    _systemctl("daemon-reload")
    _print_post_install("kohakuterrarium-all.service")
    print(
        "Token will be auto-generated on first boot and logged to "
        f"{home_dir}/host-token — find it via:\n"
        f"  sudo cat {home_dir}/host-token\n"
        "  # or:  journalctl -u kohakuterrarium-all | grep 'Lab token'\n"
    )
    return 0


def _cmd_uninstall(args: argparse.Namespace) -> int:
    _require_linux()
    _require_root()

    if args.role == "host":
        unit_full = "kohakuterrarium-host.service"
        unit_file = SYSTEMD_UNIT_DIR / UNIT_HOST
        state_dir = Path(DEFAULT_HOME_HOST)
    elif args.role == "client":
        unit_full = f"kohakuterrarium-client@{args.name}.service"
        unit_file = SYSTEMD_UNIT_DIR / UNIT_CLIENT_TEMPLATE
        state_dir = Path(DEFAULT_HOME_CLIENT_BASE) / args.name
    elif args.role == "all":
        unit_full = "kohakuterrarium-all.service"
        unit_file = SYSTEMD_UNIT_DIR / UNIT_ALL
        state_dir = Path(DEFAULT_HOME_ALL)
    else:
        print("ERROR: one of --host, --client, --all is required", file=sys.stderr)
        return 2

    _systemctl("disable", "--now", unit_full)

    # For --client we only remove the unit FILE when the LAST instance
    # is being uninstalled. Detect that by listing enabled instances.
    if args.role == "client":
        out = subprocess.run(
            ["systemctl", "list-units", "--all", "kohakuterrarium-client@*"],
            capture_output=True,
            text=True,
        )
        still_enabled = [
            line
            for line in out.stdout.splitlines()
            if "kohakuterrarium-client@" in line
        ]
        if still_enabled:
            print(
                "Other client instances are still installed; keeping the "
                "@ template unit file in place. To remove the template "
                "entirely, uninstall all instances first.",
                file=sys.stderr,
            )
        else:
            unit_file.unlink(missing_ok=True)
    else:
        unit_file.unlink(missing_ok=True)

    _systemctl("daemon-reload")

    # Ask before removing state.
    if state_dir.exists():
        ans = (
            input(
                f"\nAlso remove state directory {state_dir}? "
                "This includes session history. [y/N] "
            )
            .strip()
            .lower()
        )
        if ans == "y":
            shutil.rmtree(state_dir, ignore_errors=True)
            print(f"Removed: {state_dir}")
        else:
            print(f"Kept: {state_dir}")

    print(f"\nUninstalled: {unit_full}")
    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    _require_linux()
    if args.role == "host":
        unit_full = "kohakuterrarium-host.service"
    elif args.role == "client":
        unit_full = f"kohakuterrarium-client@{args.name}.service"
    elif args.role == "all":
        unit_full = "kohakuterrarium-all.service"
    else:
        print("ERROR: one of --host, --client, --all is required", file=sys.stderr)
        return 2
    return _systemctl("status", unit_full)


def _cmd_edit(args: argparse.Namespace) -> int:
    _require_linux()
    _require_root()
    if args.role == "host":
        unit_full = "kohakuterrarium-host.service"
    elif args.role == "client":
        unit_full = f"kohakuterrarium-client@{args.name}.service"
    elif args.role == "all":
        unit_full = "kohakuterrarium-all.service"
    else:
        print("ERROR: one of --host, --client, --all is required", file=sys.stderr)
        return 2
    return _systemctl("edit", unit_full)


# ─────────────────────────────────────────────────────────────────────
# CLI parser
# ─────────────────────────────────────────────────────────────────────


def add_service_subparser(subparsers) -> None:
    """Register ``kt service`` and its subcommands."""
    service = subparsers.add_parser(
        "service",
        help="Manage systemd units for the lab host / lab client",
        description=(
            "Install, uninstall, or inspect systemd units for the "
            "KohakuTerrarium lab host, lab clients, or the all-in-one "
            "setup.  Linux only — use the Docker images on Windows / macOS."
        ),
    )
    service_sub = service.add_subparsers(dest="service_command", required=True)

    # install
    inst = service_sub.add_parser("install", help="Render + install a unit")
    _add_role_flags(inst)
    inst.add_argument("--name", default="worker-1", help="for --client: instance name")
    inst.add_argument("--home-dir", default="", help="WorkingDirectory + KT_CONFIG_DIR")
    inst.add_argument("--host-url", default="", help="for --client: lab WS URL")
    inst.add_argument("--host-token", default="", help="shared token")
    inst.add_argument("--http-port", type=int, default=0, help="for --host: HTTP port")
    inst.add_argument(
        "--http-host", default="", help="for --host: HTTP bind (default 0.0.0.0)"
    )
    inst.add_argument("--lab-bind", default="", help="for --host / --all: lab WS bind")
    inst.add_argument(
        "--no-install",
        action="store_true",
        help="render to CWD without writing to /etc/systemd/system/",
    )

    # uninstall
    uninst = service_sub.add_parser("uninstall", help="Disable + remove a unit")
    _add_role_flags(uninst)
    uninst.add_argument(
        "--name", default="worker-1", help="for --client: instance name"
    )

    # status
    status = service_sub.add_parser("status", help="systemctl status wrapper")
    _add_role_flags(status)
    status.add_argument(
        "--name", default="worker-1", help="for --client: instance name"
    )

    # edit
    edit = service_sub.add_parser("edit", help="systemctl edit (overlay)")
    _add_role_flags(edit)
    edit.add_argument("--name", default="worker-1", help="for --client: instance name")


def _add_role_flags(parser: argparse.ArgumentParser) -> None:
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--host",
        dest="role",
        action="store_const",
        const="host",
        help="lab-host unit (kohakuterrarium-host.service)",
    )
    group.add_argument(
        "--client",
        dest="role",
        action="store_const",
        const="client",
        help="lab-client unit (kohakuterrarium-client@<name>.service)",
    )
    group.add_argument(
        "--all",
        dest="role",
        action="store_const",
        const="all",
        help="AIO unit (kohakuterrarium-all.service)",
    )


def service_cli(args: argparse.Namespace) -> int:
    """Top-level dispatch for ``kt service``."""
    sub = getattr(args, "service_command", None)
    if sub == "install":
        return _cmd_install(args)
    if sub == "uninstall":
        return _cmd_uninstall(args)
    if sub == "status":
        return _cmd_status(args)
    if sub == "edit":
        return _cmd_edit(args)
    print("ERROR: missing service subcommand", file=sys.stderr)
    return 2


__all__ = ["add_service_subparser", "service_cli"]
