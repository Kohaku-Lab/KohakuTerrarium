"""Verify an installed desktop app end to end.

Runs the app headless, then uses the interpreter it ships to check the things users
reported broken: the app serves behind its launch token, ``kt`` runs from a
terminal, and the ``python`` tool executes code.

Usage::

    python packaging/desktop/installed_smoke.py --exe <app executable> \\
        --resources <directory holding python/ and bootstrap/> --config-dir <scratch>
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

PYTHON_TOOL_PROBE = """
import asyncio, json
from kohakuterrarium.builtins.tools.python import PythonTool

async def main():
    result = await PythonTool().execute({"code": "print(6 * 7)"})
    print(json.dumps({"output": result.output, "error": result.error}))

asyncio.run(main())
"""

SMOKE_TIMEOUT = 1800


class SmokeFailure(Exception):
    pass


def interpreter(resources: Path) -> Path:
    windows = resources / "python" / "python.exe"
    return windows if windows.is_file() else resources / "python" / "bin" / "python3"


def release_site_packages(config_dir: Path) -> Path:
    """The site-packages of the release tree the app installed on first run."""
    pointer = json.loads(
        (config_dir / "runtime" / "active").read_text(encoding="utf-8")
    )
    return config_dir / "runtime" / "versions" / pointer["version"] / "site-packages"


def child_environment(resources: Path, config_dir: Path) -> dict[str, str]:
    """Environment a terminal command sees: the shipped bootstrap, a scratch config."""
    return {
        **os.environ,
        "PYTHONPATH": str(resources / "bootstrap"),
        "PYTHONNOUSERSITE": "1",
        "KT_CONFIG_DIR": str(config_dir),
    }


def run_app(exe: Path, resources: Path, config_dir: Path, report: Path) -> dict:
    """Start the app headless and return the report it wrote."""
    env = {
        **os.environ,
        "KT_CONFIG_DIR": str(config_dir),
        "KT_DESKTOP_SMOKE_OUT": str(report),
    }
    if os.environ.get("KT_DESKTOP_RESOURCES") is None and resources != exe.parent:
        env["KT_DESKTOP_RESOURCES"] = str(resources)
    completed = subprocess.run(
        [str(exe)], env=env, timeout=SMOKE_TIMEOUT, capture_output=True, text=True
    )
    if not report.is_file():
        raise SmokeFailure(
            f"the app exited ({completed.returncode}) without a report\n{completed.stderr}"
        )
    return json.loads(report.read_text(encoding="utf-8"))


def check_report(data: dict) -> list[str]:
    """Return what is wrong with the headless report."""
    problems = []
    if data.get("ok") is not True:
        problems.append(f"the app did not report ok: {data}")
    if data.get("pageLoaded") is not True:
        problems.append("the window never loaded the app page")
    if data.get("ipcFromAppPage") is not True:
        problems.append("the app page could not call a native command")
    if data.get("unauthenticatedStatus") != 401:
        problems.append(
            f"a request without the launch cookie got {data.get('unauthenticatedStatus')}, expected 401"
        )
    if data.get("healthStatus") != 200:
        problems.append(f"/healthz answered {data.get('healthStatus')}, expected 200")
    return problems


def run_python(python: Path, args: list[str], env: dict[str, str]) -> str:
    completed = subprocess.run(
        [str(python), *args], env=env, capture_output=True, text=True, timeout=300
    )
    if completed.returncode != 0:
        raise SmokeFailure(
            f"{' '.join(args)} exited {completed.returncode}\n{completed.stderr}\n{completed.stdout}"
        )
    return completed.stdout


def check_kt_command(python: Path, env: dict[str, str]) -> str:
    """``kt --version`` through the launcher, as a terminal shim runs it."""
    output = run_python(python, ["-m", "kohakuterrarium.launcher", "--version"], env)
    if "kohakuterrarium" not in output.lower() and not output.strip():
        raise SmokeFailure(f"kt --version printed nothing useful: {output!r}")
    return output.strip()


def check_python_tool(python: Path, env: dict[str, str], site: Path) -> None:
    """Execute the python tool with the shipped interpreter and the installed release."""
    tool_env = {**env, "PYTHONPATH": os.pathsep.join([str(site), env["PYTHONPATH"]])}
    line = run_python(python, ["-c", PYTHON_TOOL_PROBE], tool_env).splitlines()[-1]
    result = json.loads(line)
    if result["error"] or "42" not in (result["output"] or ""):
        raise SmokeFailure(f"the python tool did not run code: {result}")


def check_shims(python: Path, env: dict[str, str], site: Path, home: Path) -> None:
    """Install the opt-in shims into a scratch home and run the written ``kt``."""
    shim_env = {
        **env,
        "KT_PYTHON": str(python),
        "KT_BOOTSTRAP_DIR": env["PYTHONPATH"],
        "HOME": str(home),
        "USERPROFILE": str(home),
        "LOCALAPPDATA": str(home / "local"),
        "PYTHONPATH": os.pathsep.join([str(site), env["PYTHONPATH"]]),
    }
    run_python(python, ["-m", "kohakuterrarium.cli", "shims", "install"], shim_env)
    status = run_python(
        python, ["-m", "kohakuterrarium.cli", "shims", "status"], shim_env
    )
    wrapper = next(
        (
            line.split("(", 1)[1].rstrip(")")
            for line in status.splitlines()
            if line.strip().startswith("kt ") and "installed" in line
        ),
        None,
    )
    if wrapper is None:
        raise SmokeFailure(f"the kt shim was not installed:\n{status}")
    completed = subprocess.run(
        [wrapper, "--version"],
        env={k: v for k, v in env.items() if k != "PYTHONPATH"},
        capture_output=True,
        text=True,
        timeout=300,
        shell=os.name == "nt",
    )
    if completed.returncode != 0:
        raise SmokeFailure(f"the kt shim failed: {completed.stderr}{completed.stdout}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--exe", type=Path, required=True)
    parser.add_argument("--resources", type=Path, required=True)
    parser.add_argument("--config-dir", type=Path)
    args = parser.parse_args(argv)

    config_dir = args.config_dir or Path(tempfile.mkdtemp(prefix="kt-smoke-"))
    config_dir.mkdir(parents=True, exist_ok=True)
    report = config_dir / "smoke-report.json"
    python = interpreter(args.resources)
    if not python.is_file():
        raise SmokeFailure(f"no interpreter at {python}")

    data = run_app(args.exe, args.resources, config_dir, report)
    print("app report:", json.dumps(data))
    problems = check_report(data)
    if problems:
        raise SmokeFailure("\n".join(problems))

    env = child_environment(args.resources, config_dir)
    site = release_site_packages(config_dir)
    print("kt --version:", check_kt_command(python, env))
    check_python_tool(python, env, site)
    print("python tool: ok")
    check_shims(python, env, site, config_dir / "home")
    print("kt shim: ok")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SmokeFailure as failure:
        print(f"SMOKE FAILED: {failure}", file=sys.stderr)
        sys.exit(1)
