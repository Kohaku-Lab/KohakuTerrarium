"""Pin: session-route path resolution + slash-command alias hygiene.

Two collisions caught at review time:

1. ``/fork`` previously declared ``aliases = ["branch"]`` so the
   resolver shadowed the new ``/branch`` command — typing ``/branch``
   in the prompt invoked Fork, not the regen / edit navigator.
2. ``api/routes/sessions.py`` listed ``*.kohakutr.v*`` files but
   ``delete_session`` / ``resume_session`` / ``_resolve_session_path``
   only checked ``.kohakutr`` and ``.kt`` extensions and used
   ``Path.stem`` (which returns ``"foo.kohakutr"`` for
   ``foo.kohakutr.v2``). With Wave D auto-migration in place the
   v2 file is the live one — every operation other than listing
   was broken.
"""

from pathlib import Path

import pytest

# ── /branch resolves to BranchCommand, not ForkCommand ────────────────


def test_branch_command_resolves_to_branch_not_fork():
    from kohakuterrarium.builtins.user_commands import (
        get_builtin_user_command,
    )
    from kohakuterrarium.builtins.user_commands.branch import BranchCommand
    from kohakuterrarium.builtins.user_commands.fork import ForkCommand

    cmd = get_builtin_user_command("branch")
    assert cmd is not None
    assert isinstance(cmd, BranchCommand)
    assert not isinstance(cmd, ForkCommand)


def test_fork_command_does_not_alias_branch():
    from kohakuterrarium.builtins.user_commands.fork import ForkCommand

    assert "branch" not in ForkCommand.aliases


def test_branch_alias_br_still_resolves():
    from kohakuterrarium.builtins.user_commands import (
        get_builtin_user_command,
    )
    from kohakuterrarium.builtins.user_commands.branch import BranchCommand

    cmd = get_builtin_user_command("br")
    assert isinstance(cmd, BranchCommand)


# ── sessions.py path resolution ───────────────────────────────────────


@pytest.fixture
def session_dir(tmp_path, monkeypatch):
    """Repoint ``_SESSION_DIR`` to a tmp path so the tests don't touch
    the user's real session library."""
    from kohakuterrarium.api.routes import sessions as sessions_module

    monkeypatch.setattr(sessions_module, "_SESSION_DIR", tmp_path)
    return tmp_path


def _touch_session_file(path: Path) -> None:
    """Create a real (parseable) SessionStore at ``path``."""
    from kohakuterrarium.session.store import SessionStore

    path.parent.mkdir(parents=True, exist_ok=True)
    store = SessionStore(str(path))
    try:
        store.init_meta(
            session_id=path.name,
            config_type="agent",
            config_path="x",
            pwd=str(path.parent),
            agents=["alice"],
        )
    finally:
        store.close(update_status=False)


def test_normalize_session_stem_handles_v2_suffix(session_dir):
    from kohakuterrarium.api.routes.sessions import _normalize_session_stem

    p = session_dir / "foo.kohakutr.v2"
    p.write_bytes(b"")
    assert _normalize_session_stem(p) == "foo"


def test_normalize_session_stem_handles_bare_kohakutr(session_dir):
    from kohakuterrarium.api.routes.sessions import _normalize_session_stem

    p = session_dir / "foo.kohakutr"
    p.write_bytes(b"")
    assert _normalize_session_stem(p) == "foo"


def test_normalize_session_stem_handles_kt(session_dir):
    from kohakuterrarium.api.routes.sessions import _normalize_session_stem

    p = session_dir / "foo.kt"
    p.write_bytes(b"")
    assert _normalize_session_stem(p) == "foo"


def test_resolve_session_path_prefers_v2_over_v1(session_dir):
    from kohakuterrarium.api.routes.sessions import _resolve_session_path

    _touch_session_file(session_dir / "foo.kohakutr")
    _touch_session_file(session_dir / "foo.kohakutr.v2")

    resolved = _resolve_session_path("foo")
    assert resolved is not None
    assert resolved.name == "foo.kohakutr.v2"


def test_resolve_session_path_falls_back_to_v1(session_dir):
    from kohakuterrarium.api.routes.sessions import _resolve_session_path

    _touch_session_file(session_dir / "foo.kohakutr")
    resolved = _resolve_session_path("foo")
    assert resolved is not None
    assert resolved.name == "foo.kohakutr"


def test_resolve_session_path_v2_only_works(session_dir):
    """Wave-D-migrated session: only v2 file present (no v1 rollback)."""
    from kohakuterrarium.api.routes.sessions import _resolve_session_path

    _touch_session_file(session_dir / "foo.kohakutr.v2")
    resolved = _resolve_session_path("foo")
    assert resolved is not None
    assert resolved.name == "foo.kohakutr.v2"


def test_resolve_session_path_unknown_returns_none(session_dir):
    from kohakuterrarium.api.routes.sessions import _resolve_session_path

    assert _resolve_session_path("missing") is None


def test_all_versions_returns_v1_and_v2(session_dir):
    from kohakuterrarium.api.routes.sessions import _all_versions_for_session

    _touch_session_file(session_dir / "foo.kohakutr")
    _touch_session_file(session_dir / "foo.kohakutr.v2")
    paths = _all_versions_for_session("foo")
    names = {p.name for p in paths}
    assert names == {"foo.kohakutr", "foo.kohakutr.v2"}


# ── delete + listing round-trips ──────────────────────────────────────


def test_listing_dedupes_v1_v2_under_same_canonical_name(session_dir):
    from kohakuterrarium.api.routes.sessions import _build_session_index

    _touch_session_file(session_dir / "foo.kohakutr")
    _touch_session_file(session_dir / "foo.kohakutr.v2")
    _touch_session_file(session_dir / "bar.kohakutr.v2")

    index = _build_session_index()
    names = sorted(s["name"] for s in index)
    assert names == ["bar", "foo"]
    foo = next(s for s in index if s["name"] == "foo")
    # Listing surfaces the v2 file (not v1) when both exist.
    assert foo["filename"] == "foo.kohakutr.v2"


@pytest.mark.asyncio
async def test_delete_session_removes_both_v1_and_v2(session_dir):
    from kohakuterrarium.api.routes.sessions import delete_session

    v1 = session_dir / "foo.kohakutr"
    v2 = session_dir / "foo.kohakutr.v2"
    _touch_session_file(v1)
    _touch_session_file(v2)

    result = await delete_session("foo")
    assert result["status"] == "deleted"
    assert sorted(result["files"]) == ["foo.kohakutr", "foo.kohakutr.v2"]
    assert not v1.exists()
    assert not v2.exists()


@pytest.mark.asyncio
async def test_delete_session_404_when_missing(session_dir):
    from fastapi import HTTPException

    from kohakuterrarium.api.routes.sessions import delete_session

    with pytest.raises(HTTPException) as exc:
        await delete_session("nope")
    assert exc.value.status_code == 404
