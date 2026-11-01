"""Run a command under a Linux Landlock write policy.

Usage: ``python -I -S landlock_exec.py '<json list of paths>' -- argv...``.
The listed paths (directories or files) stay writable; every other write is
refused by the kernel for the command and all of its descendants. Reads,
execution and network are left open. Any failure to apply the policy exits
126 instead of running the command unconfined.

Runs by file path before the command starts, so it imports only the stdlib.
"""

import ctypes
import json
import os
import stat
import struct
import sys

_CREATE_RULESET = 444
_ADD_RULE = 445
_RESTRICT_SELF = 446
_CREATE_RULESET_VERSION = 1
_RULE_PATH_BENEATH = 1
_PR_SET_NO_NEW_PRIVS = 38

WRITE_FILE = 1 << 1
REMOVE_DIR = 1 << 4
REMOVE_FILE = 1 << 5
MAKE_CHAR = 1 << 6
MAKE_DIR = 1 << 7
MAKE_REG = 1 << 8
MAKE_SOCK = 1 << 9
MAKE_FIFO = 1 << 10
MAKE_BLOCK = 1 << 11
MAKE_SYM = 1 << 12
REFER = 1 << 13
TRUNCATE = 1 << 14

_ABI1_WRITES = (
    WRITE_FILE
    | REMOVE_DIR
    | REMOVE_FILE
    | MAKE_CHAR
    | MAKE_DIR
    | MAKE_REG
    | MAKE_SOCK
    | MAKE_FIFO
    | MAKE_BLOCK
    | MAKE_SYM
)
_FILE_RIGHTS = WRITE_FILE | TRUNCATE


class _RulesetAttr(ctypes.Structure):
    _fields_ = [("handled_access_fs", ctypes.c_uint64)]


def _libc() -> ctypes.CDLL:
    libc = ctypes.CDLL(None, use_errno=True)
    libc.syscall.restype = ctypes.c_long
    return libc


def landlock_abi() -> int:
    """The kernel's Landlock ABI version, or 0 where Landlock is unusable."""
    if not sys.platform.startswith("linux"):
        return 0
    try:
        version = _libc().syscall(
            _CREATE_RULESET, None, ctypes.c_size_t(0), _CREATE_RULESET_VERSION
        )
    except (OSError, AttributeError):
        return 0
    return max(int(version), 0)


def handled_writes(abi: int) -> int:
    """Every write right the running kernel can restrict."""
    rights = _ABI1_WRITES
    if abi >= 2:
        rights |= REFER
    if abi >= 3:
        rights |= TRUNCATE
    return rights


def _check(result: int, what: str) -> int:
    if result < 0:
        err = ctypes.get_errno()
        raise OSError(err, f"{what}: {os.strerror(err)}")
    return result


def restrict_writes(paths: list[str]) -> None:
    """Confine this process and its future children to writing ``paths``."""
    abi = landlock_abi()
    if abi < 1:
        raise OSError("Landlock is not available on this kernel")
    libc = _libc()
    handled = handled_writes(abi)
    attr = _RulesetAttr(handled_access_fs=handled)
    ruleset = _check(
        libc.syscall(
            _CREATE_RULESET, ctypes.byref(attr), ctypes.c_size_t(ctypes.sizeof(attr)), 0
        ),
        "landlock_create_ruleset",
    )
    try:
        for path in paths:
            _add_path(libc, ruleset, path, handled)
        _check(
            libc.prctl(_PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0), "prctl(PR_SET_NO_NEW_PRIVS)"
        )
        _check(libc.syscall(_RESTRICT_SELF, ruleset, 0), "landlock_restrict_self")
    finally:
        os.close(ruleset)


def _add_path(libc: ctypes.CDLL, ruleset: int, path: str, handled: int) -> None:
    try:
        fd = os.open(path, os.O_PATH | os.O_CLOEXEC)
    except OSError:
        return
    try:
        is_dir = stat.S_ISDIR(os.fstat(fd).st_mode)
        allowed = handled if is_dir else handled & _FILE_RIGHTS
        # struct landlock_path_beneath_attr is packed: u64 access, s32 fd.
        rule = ctypes.create_string_buffer(struct.pack("=Qi", allowed, fd), 12)
        _check(
            libc.syscall(_ADD_RULE, ruleset, _RULE_PATH_BENEATH, rule, 0),
            f"landlock_add_rule({path})",
        )
    finally:
        os.close(fd)


def main(args: list[str]) -> int:
    """Apply the policy in ``args[0]``, then exec the command after ``--``."""
    if len(args) < 3 or args[1] != "--":
        sys.stderr.write("usage: landlock_exec.py PATHS_JSON -- argv...\n")
        return 2
    try:
        restrict_writes(json.loads(args[0]))
    except (OSError, ValueError, TypeError) as exc:
        sys.stderr.write(f"kt sandbox: cannot apply Landlock policy: {exc}\n")
        return 126
    command = args[2:]
    try:
        os.execvp(command[0], command)
    except OSError as exc:
        sys.stderr.write(f"kt sandbox: {command[0]}: {exc.strerror}\n")
        return 127


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
