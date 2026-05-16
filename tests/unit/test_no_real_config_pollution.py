"""Regression guard: no test ever writes to the operator's real config.

The whole suite runs with ``KT_CONFIG_DIR`` redirected to a per-test
``tmp_path`` via the autouse fixture in ``tests/conftest.py``.  This
file is a separate, last-resort check: snapshot the *real*
``~/.kohakuterrarium/`` mtime + size at module import and fail the
test if anything inside changed during the test run.

A failure here usually means a new test introduced a save / write
path that resolves through an absolute ``Path.home()`` lookup instead
of ``config_dir()`` — for example the deprecated
``monkeypatch.setattr(mod, "PROFILES_PATH", …)`` seam that stopped
working when the live read/write path moved to ``_profiles_path()``.
"""

from pathlib import Path

_REAL_CONFIG_DIR = Path.home() / ".kohakuterrarium"


def _snapshot() -> dict[str, tuple[float, int]]:
    """Map every file under the real config dir to (mtime, size)."""
    if not _REAL_CONFIG_DIR.exists():
        return {}
    out: dict[str, tuple[float, int]] = {}
    for p in _REAL_CONFIG_DIR.rglob("*"):
        if p.is_file():
            try:
                st = p.stat()
            except OSError:
                continue
            out[str(p)] = (st.st_mtime, st.st_size)
    return out


_BEFORE: dict[str, tuple[float, int]] = _snapshot()


def test_no_writes_to_real_kohakuterrarium_dir():
    """Compare real-config snapshots; any drift fails loudly.

    Tolerates files that disappeared (operator-side cleanup is fine)
    — only NEW writes / mutations are flagged, which is the leak
    surface tests can introduce.
    """
    after = _snapshot()
    leaks: list[str] = []
    for path, (mtime, size) in after.items():
        before = _BEFORE.get(path)
        if before is None:
            leaks.append(f"NEW: {path}")
        elif before != (mtime, size):
            leaks.append(f"MUTATED: {path} (was {before}, now {(mtime, size)})")
    assert not leaks, (
        "tests wrote to the operator's real ~/.kohakuterrarium/ — every "
        "save path must resolve through KT_CONFIG_DIR (the conftest "
        "autouse fixture redirects to tmp_path).  Leaks:\n" + "\n".join(leaks)
    )
