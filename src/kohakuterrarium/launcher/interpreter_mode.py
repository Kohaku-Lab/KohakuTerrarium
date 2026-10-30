"""Run the embedded application stub as a ``python`` command line.

The Briefcase stub is not a Python interpreter: it cannot execute ``-c`` or
``-m`` on its own. Invoked as ``<stub> --kt-python ...`` it accepts the common
interpreter forms (``-c``, ``-m``, script path) and runs them against the active
release tree, so child processes can use the stub where they would otherwise use
``sys.executable``.
"""

import os
import runpy
import sys
import traceback

from kohakuterrarium.launcher.paths import site_packages_dir, version_dir
from kohakuterrarium.launcher.tree_ops import read_active_pointer

FLAG = "--kt-python"
STUB_ENV = "KT_STUB_EXECUTABLE"

_SWITCHES = frozenset({"-u", "-B", "-E", "-s", "-S", "-I", "-O", "-OO", "-q", "-b"})
_VALUED_SWITCHES = frozenset({"-X", "-W"})


def requested(argv: list[str]) -> bool:
    """Return whether ``argv`` asks for interpreter mode."""
    return len(argv) > 1 and argv[1] == FLAG


def activate_release(site_packages: str) -> None:
    """Redirect framework imports to a release tree, keeping launcher modules."""
    if site_packages not in sys.path:
        sys.path.insert(0, site_packages)
    for name in list(sys.modules):
        if name == "kohakuterrarium" or (
            name.startswith("kohakuterrarium.")
            and not name.startswith("kohakuterrarium.launcher")
        ):
            del sys.modules[name]


def _activate_active_release() -> None:
    pointer = read_active_pointer()
    if pointer is None:
        return
    site = site_packages_dir(version_dir(pointer.version))
    if site.is_dir():
        activate_release(str(site))


def _split(args: list[str]) -> tuple[str, str, list[str]] | None:
    """Return ``(kind, target, rest)`` for the first program argument."""
    index = 0
    while index < len(args):
        arg = args[index]
        if arg in _SWITCHES:
            index += 1
        elif arg in _VALUED_SWITCHES:
            index += 2
        elif arg == "-c":
            return (
                ("code", args[index + 1], args[index + 2 :])
                if index + 1 < len(args)
                else None
            )
        elif arg == "-m":
            return (
                ("module", args[index + 1], args[index + 2 :])
                if index + 1 < len(args)
                else None
            )
        elif arg == "-":
            return "stdin", "-", args[index + 1 :]
        else:
            return "script", arg, args[index + 1 :]
    return None


def _run_program(kind: str, target: str, rest: list[str]) -> None:
    if kind == "code":
        sys.argv = ["-c", *rest]
        exec(compile(target, "<string>", "exec"), {"__name__": "__main__"})
    elif kind == "module":
        sys.argv = [target, *rest]
        runpy.run_module(target, run_name="__main__", alter_sys=True)
    elif kind == "stdin":
        sys.argv = ["-", *rest]
        exec(compile(sys.stdin.read(), "<stdin>", "exec"), {"__name__": "__main__"})
    else:
        sys.argv = [target, *rest]
        runpy.run_path(target, run_name="__main__")


def run(argv: list[str]) -> int:
    """Run the program described by ``argv`` and return its exit status."""
    parsed = _split(argv[2:])
    if parsed is None:
        sys.stderr.write(f"usage: {FLAG} [-c code | -m module | script] [args...]\n")
        return 2
    os.environ["KT_LAUNCHER_EXEC"] = "1"
    sys.path.insert(0, "")
    _activate_active_release()
    try:
        _run_program(*parsed)
    except SystemExit as exc:
        code = exc.code
        if code is None:
            return 0
        if isinstance(code, int):
            return code
        sys.stderr.write(f"{code}\n")
        return 1
    except BaseException:
        traceback.print_exc()
        return 1
    return 0
