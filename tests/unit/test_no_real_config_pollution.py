"""Regression guard: no test ever writes to the operator's real config.

The whole suite runs with ``KT_CONFIG_DIR`` redirected to a per-test
``tmp_path`` via the autouse fixture in ``tests/conftest.py``.  This
file is a separate, last-resort check: snapshot the *content* of
``~/.kohakuterrarium/`` at module import and fail the test if any
file's content changed during the test run.

A failure here usually means a new test introduced a save / write
path that resolves through an absolute ``Path.home()`` lookup instead
of ``config_dir()`` — for example the deprecated
``monkeypatch.setattr(mod, "PROFILES_PATH", …)`` seam that stopped
working when the live read/write path moved to ``_profiles_path()``.

**Why we compare content hashes, not mtime/size**: when the operator
has KohakuTerrarium running locally (browser tab, desktop app) it may
re-write identical content into files like ``ui_prefs.json`` every
few seconds (frontend polling save_prefs with no changes). That
bumps the mtime but doesn't change the bytes — and it's not a test
leak. A real test leak shows up as a content drift (added key,
modified value, new file). We compare SHA-256 of each file's bytes
to keep the guard tight while ignoring no-op external rewrites.
"""

import hashlib
from pathlib import Path

_REAL_CONFIG_DIR = Path.home() / ".kohakuterrarium"

# Subtrees populated by a *running* KohakuTerrarium daemon, not by
# any test path. The operator may have ``kt serve`` / a browser tab
# open while running pytest — those processes legitimately write
# under these subtrees concurrently and a content drift there is
# NOT a test leak. Tests never touch these paths.
_RUNTIME_STATE_SUBDIRS: tuple[str, ...] = (
    "run",  # web daemon PID / state / log files
    "logs",  # framework log files
    "sessions/mirror",  # SessionMirrorWriter rewrites the .kohakutr
    # mirror as the live KT daemon receives events
)


def _is_runtime_state(p: Path) -> bool:
    try:
        rel = p.relative_to(_REAL_CONFIG_DIR)
    except ValueError:
        return False
    parts = rel.parts
    for sub in _RUNTIME_STATE_SUBDIRS:
        sub_parts = tuple(sub.split("/"))
        if parts[: len(sub_parts)] == sub_parts:
            return True
    return False


def _snapshot() -> dict[str, str]:
    """Map every test-touchable file under the real config dir to its
    content SHA-256.

    Files under runtime-state subtrees (``run/``, ``logs/``,
    ``sessions/mirror/``) are excluded — those belong to whatever
    KT process the operator may have running concurrently.

    Files that disappear or become unreadable between snapshot and
    test are silently dropped — only new content / changed content
    in surviving files is treated as a leak.
    """
    if not _REAL_CONFIG_DIR.exists():
        return {}
    out: dict[str, str] = {}
    for p in _REAL_CONFIG_DIR.rglob("*"):
        if not p.is_file():
            continue
        if _is_runtime_state(p):
            continue
        try:
            out[str(p)] = hashlib.sha256(p.read_bytes()).hexdigest()
        except OSError:
            # File may have been deleted / locked / become inaccessible
            # between iglob and read — that's an external race, not a
            # test leak.  Skip it.
            continue
    return out


_BEFORE: dict[str, str] = _snapshot()


def test_no_writes_to_real_kohakuterrarium_dir():
    """Compare real-config snapshots; content drift fails loudly.

    Tolerates files that disappeared (operator-side cleanup is fine)
    and tolerates mtime-only rewrites with identical content (the
    operator's running KohakuTerrarium frontend may poll-save prefs
    with no changes). Only NEW files and CONTENT-changed files are
    flagged as test leaks.
    """
    after = _snapshot()
    leaks: list[str] = []
    for path, digest in after.items():
        before = _BEFORE.get(path)
        if before is None:
            leaks.append(f"NEW: {path}")
        elif before != digest:
            leaks.append(f"CONTENT-CHANGED: {path}")
    assert not leaks, (
        "tests wrote to the operator's real ~/.kohakuterrarium/ — every "
        "save path must resolve through KT_CONFIG_DIR (the conftest "
        "autouse fixture redirects to tmp_path).  Leaks:\n" + "\n".join(leaks)
    )
