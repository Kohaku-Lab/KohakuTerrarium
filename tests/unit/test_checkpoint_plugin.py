"""Tests for kt_biome.plugins.checkpoint.CheckpointPlugin.

The plugin snapshots the workspace with ``git stash`` before destructive
tools run. Tests that need a real git repo are skipped if git is not on
PATH — matching the plugin's own "silently no-op" contract.
"""

import json
import shutil
import subprocess
import types
from pathlib import Path

import pytest

from kohakuterrarium.core.scratchpad import Scratchpad
from kohakuterrarium.modules.plugin.base import PluginContext

pytestmark = pytest.mark.asyncio


# ────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────


GIT_AVAILABLE = shutil.which("git") is not None


def _fake_agent(scratchpad: Scratchpad) -> types.SimpleNamespace:
    """Build the smallest agent-like object the plugin pokes at.

    The plugin reads ``agent.scratchpad`` (falling back to
    ``agent.session.scratchpad``) and nothing else off the agent handle
    during checkpointing.
    """
    session = types.SimpleNamespace(scratchpad=scratchpad)
    return types.SimpleNamespace(scratchpad=scratchpad, session=session)


def _make_context(working_dir: Path, agent_name: str = "test") -> PluginContext:
    scratchpad = Scratchpad()
    ctx = PluginContext(
        agent_name=agent_name,
        working_dir=working_dir,
        session_id="sess-test",
        model="test-model",
        _agent=_fake_agent(scratchpad),
        _plugin_name="checkpoint",
    )
    return ctx


def _init_git_repo(path: Path) -> None:
    """Initialise a minimal git repo at ``path`` with one tracked file."""
    env = {"GIT_TERMINAL_PROMPT": "0"}
    # `git init` — quiet, explicit branch name to avoid hint noise.
    subprocess.run(
        ["git", "init", "-b", "main"],
        cwd=str(path),
        check=True,
        capture_output=True,
        timeout=15,
    )
    # Minimal identity so `git commit` works without a global config.
    for key, value in (
        ("user.email", "checkpoint-test@example.invalid"),
        ("user.name", "Checkpoint Test"),
        ("commit.gpgsign", "false"),
    ):
        subprocess.run(
            ["git", "config", key, value],
            cwd=str(path),
            check=True,
            capture_output=True,
            timeout=15,
        )
    # Seed with one commit so `git stash` has something to diff against.
    seed = path / "README.md"
    seed.write_text("seed\n", encoding="utf-8")
    subprocess.run(
        ["git", "add", "README.md"],
        cwd=str(path),
        check=True,
        capture_output=True,
        timeout=15,
        env={**env},
    )
    subprocess.run(
        ["git", "commit", "-m", "seed"],
        cwd=str(path),
        check=True,
        capture_output=True,
        timeout=15,
        env={**env},
    )


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    """Create a disposable git repo with one tracked file and dirty state."""
    if not GIT_AVAILABLE:
        pytest.skip("git not installed — checkpoint plugin is a no-op")
    _init_git_repo(tmp_path)
    # Make the workspace dirty so `git stash push` has something to save.
    (tmp_path / "dirty.txt").write_text("dirty\n", encoding="utf-8")
    return tmp_path


# ────────────────────────────────────────────────────────────
# Tests
# ────────────────────────────────────────────────────────────


async def test_plugin_loads_with_defaults(tmp_path: Path) -> None:
    """Plugin instantiates with no options and reports a sane default state."""
    from kt_biome.plugins.checkpoint import CheckpointPlugin

    plugin = CheckpointPlugin()
    ctx = _make_context(tmp_path)
    await plugin.on_load(ctx)

    info = plugin.info()
    assert info["enabled"] is True
    assert info["backend"] == "git"
    assert set(info["tools"]) == {"write", "edit", "multi_edit"}
    # Default bash patterns include the classic rm -rf.
    assert any("rm" in pat for pat in info["bash_patterns"])
    assert info["checkpoints"] == []


async def test_non_destructive_tool_not_checkpointed(tmp_path: Path) -> None:
    """A ``read`` call must NOT create a checkpoint entry."""
    if not GIT_AVAILABLE:
        pytest.skip("git not installed — checkpoint plugin is a no-op")

    from kt_biome.plugins.checkpoint import CheckpointPlugin, SCRATCHPAD_KEY

    _init_git_repo(tmp_path)
    (tmp_path / "dirty.txt").write_text("dirty\n", encoding="utf-8")

    plugin = CheckpointPlugin()
    ctx = _make_context(tmp_path)
    await plugin.on_load(ctx)

    result = await plugin.pre_tool_execute(
        {"path": "README.md"}, tool_name="read", job_id="j1"
    )

    assert result is None  # Never modify tool args.
    assert plugin.list_checkpoints() == []
    # Scratchpad untouched.
    assert ctx.scratchpad.get(SCRATCHPAD_KEY) is None


async def test_write_tool_triggers_git_stash(git_repo: Path) -> None:
    """A ``write`` call in a git repo produces a stash + scratchpad entry."""
    from kt_biome.plugins.checkpoint import CheckpointPlugin, SCRATCHPAD_KEY

    plugin = CheckpointPlugin()
    ctx = _make_context(git_repo, agent_name="swe")
    await plugin.on_load(ctx)

    await plugin.pre_tool_execute(
        {"path": "foo.txt", "content": "x"},
        tool_name="write",
        job_id="j1",
    )

    checkpoints = plugin.list_checkpoints()
    assert len(checkpoints) == 1
    entry = checkpoints[0]
    assert entry["tool"] == "write"
    assert entry["stash_ref"].startswith("stash@")
    assert "kt-checkpoint write@" in entry["message"]
    assert entry["cwd"] == str(git_repo)

    # Scratchpad holds the same data JSON-encoded.
    raw = ctx.scratchpad.get(SCRATCHPAD_KEY)
    assert raw is not None
    decoded = json.loads(raw)
    assert decoded == checkpoints

    # The stash actually exists in the repo.
    stash_list = subprocess.run(
        ["git", "stash", "list"],
        cwd=str(git_repo),
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert stash_list.returncode == 0
    assert "kt-checkpoint" in stash_list.stdout


async def test_destructive_bash_triggers_checkpoint(git_repo: Path) -> None:
    """``bash`` with a matching destructive pattern is snapshotted."""
    from kt_biome.plugins.checkpoint import CheckpointPlugin

    plugin = CheckpointPlugin()
    ctx = _make_context(git_repo)
    await plugin.on_load(ctx)

    # Benign bash: no checkpoint.
    await plugin.pre_tool_execute({"command": "ls -la"}, tool_name="bash", job_id="j1")
    assert plugin.list_checkpoints() == []

    # Destructive bash: checkpoint expected.
    await plugin.pre_tool_execute(
        {"command": "rm -rf foo"}, tool_name="bash", job_id="j2"
    )
    checkpoints = plugin.list_checkpoints()
    assert len(checkpoints) == 1
    assert checkpoints[0]["tool"] == "bash"


async def test_non_repo_cwd_is_silent_noop(tmp_path: Path) -> None:
    """When cwd is not a git repo, the plugin logs at DEBUG and returns None."""
    if not GIT_AVAILABLE:
        pytest.skip("git not installed — checkpoint plugin is a no-op")

    from kt_biome.plugins.checkpoint import CheckpointPlugin

    plugin = CheckpointPlugin()
    ctx = _make_context(tmp_path)
    await plugin.on_load(ctx)

    # Tool matches the deny-list but cwd isn't a git repo.
    result = await plugin.pre_tool_execute(
        {"path": "foo.txt", "content": "x"},
        tool_name="write",
        job_id="j1",
    )
    assert result is None
    assert plugin.list_checkpoints() == []


async def test_disabled_backend_short_circuits(git_repo: Path) -> None:
    """``backend: disabled`` prevents any subprocess or log mutation."""
    from kt_biome.plugins.checkpoint import CheckpointPlugin

    plugin = CheckpointPlugin(options={"backend": "disabled"})
    ctx = _make_context(git_repo)
    await plugin.on_load(ctx)

    await plugin.pre_tool_execute(
        {"path": "foo.txt", "content": "x"},
        tool_name="write",
        job_id="j1",
    )
    assert plugin.list_checkpoints() == []


async def test_agent_name_scoping(git_repo: Path) -> None:
    """``agent_names`` limits the plugin to the listed creatures."""
    from kt_biome.plugins.checkpoint import CheckpointPlugin

    plugin = CheckpointPlugin(options={"agent_names": ["only-this-one"]})
    ctx = _make_context(git_repo, agent_name="someone-else")
    await plugin.on_load(ctx)

    await plugin.pre_tool_execute(
        {"path": "foo.txt", "content": "x"},
        tool_name="write",
        job_id="j1",
    )
    assert plugin.list_checkpoints() == []


async def test_list_checkpoints_for_session_helper(git_repo: Path) -> None:
    """The classmethod reads entries directly off a Session scratchpad."""
    from kt_biome.plugins.checkpoint import CheckpointPlugin

    plugin = CheckpointPlugin()
    ctx = _make_context(git_repo)
    await plugin.on_load(ctx)
    await plugin.pre_tool_execute(
        {"path": "foo.txt", "content": "x"},
        tool_name="write",
        job_id="j1",
    )

    # Build a minimal session-like object for the classmethod helper.
    fake_session = types.SimpleNamespace(scratchpad=ctx.scratchpad)
    entries = CheckpointPlugin.list_checkpoints_for_session(fake_session)
    assert len(entries) == 1
    assert entries[0]["tool"] == "write"
