"""Unit tests for :mod:`kohakuterrarium.studio.deploy`."""

import pytest

from kohakuterrarium.studio import deploy as deploy_mod


class _FakeSender:
    def __init__(self, response=None, raises=None):
        self._response = response or {"target_path": "/remote/path", "deployed": []}
        self._raises = raises
        self.calls = []

    async def request(self, *, to_node, namespace, type, body, timeout):
        self.calls.append(
            {
                "to": to_node,
                "namespace": namespace,
                "type": type,
                "body": body,
                "timeout": timeout,
            }
        )
        if self._raises:
            raise self._raises
        return self._response


# ── _walk_creature_files ─────────────────────────────────────


class TestWalkCreatureFiles:
    def test_missing_path(self, tmp_path):
        with pytest.raises(deploy_mod.DeployError, match="does not exist"):
            deploy_mod._walk_creature_files(tmp_path / "ghost")

    def test_not_a_directory(self, tmp_path):
        f = tmp_path / "file.txt"
        f.write_text("x")
        with pytest.raises(deploy_mod.DeployError, match="not a directory"):
            deploy_mod._walk_creature_files(f)

    def test_empty_directory(self, tmp_path):
        with pytest.raises(deploy_mod.DeployError, match="no files found"):
            deploy_mod._walk_creature_files(tmp_path)

    def test_walks_files(self, tmp_path):
        (tmp_path / "config.yaml").write_text("name: alice")
        (tmp_path / "system.md").write_text("prompt")
        out = deploy_mod._walk_creature_files(tmp_path)
        assert "config.yaml" in out
        assert "system.md" in out

    def test_skips_git_and_pycache(self, tmp_path):
        (tmp_path / "config.yaml").write_text("name: alice")
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        (git_dir / "HEAD").write_text("ref")
        cache_dir = tmp_path / "__pycache__"
        cache_dir.mkdir()
        (cache_dir / "x.pyc").write_text("x")
        out = deploy_mod._walk_creature_files(tmp_path)
        assert "config.yaml" in out
        assert ".git/HEAD" not in out
        assert "__pycache__/x.pyc" not in out

    def test_file_too_large(self, tmp_path, monkeypatch):
        monkeypatch.setattr(deploy_mod, "MAX_BUNDLE_FILE_BYTES", 10)
        big = tmp_path / "big.txt"
        big.write_text("a" * 100)
        with pytest.raises(deploy_mod.DeployError, match="exceeds"):
            deploy_mod._walk_creature_files(tmp_path)


# ── _hash ────────────────────────────────────────────────────


class TestHash:
    def test_hash_is_sha256(self):
        import hashlib

        h = deploy_mod._hash(b"data")
        assert h == hashlib.sha256(b"data").hexdigest()


# ── deploy_creature_to_node ──────────────────────────────────


class TestDeployCreatureToNode:
    async def test_success_returns_target_path(self, tmp_path):
        (tmp_path / "config.yaml").write_text("name: alice")
        sender = _FakeSender(
            response={
                "target_path": "/remote/creatures/alice",
                "deployed": ["config.yaml"],
            }
        )
        out = await deploy_mod.deploy_creature_to_node(sender, "worker-1", tmp_path)
        assert out == "/remote/creatures/alice"
        assert sender.calls[0]["namespace"] == "studio.deploy"
        assert sender.calls[0]["type"] == "push_creature_bundle"

    async def test_conflict_raises(self, tmp_path):
        (tmp_path / "config.yaml").write_text("x")
        sender = _FakeSender(
            response={"conflicts": ["config.yaml"], "target_path": "/x"}
        )
        with pytest.raises(deploy_mod.DeployError, match="conflicts"):
            await deploy_mod.deploy_creature_to_node(sender, "worker-1", tmp_path)

    async def test_remote_error_raises(self, tmp_path):
        (tmp_path / "config.yaml").write_text("x")
        sender = _FakeSender(
            response={"error": {"kind": "invalid", "message": "bad ref"}}
        )
        with pytest.raises(deploy_mod.DeployError, match="invalid"):
            await deploy_mod.deploy_creature_to_node(sender, "worker-1", tmp_path)

    async def test_partial_response_raises(self, tmp_path):
        (tmp_path / "config.yaml").write_text("x")
        sender = _FakeSender(
            response={
                "partial": True,
                "deployed": ["a"],
                "remaining": ["b"],
            }
        )
        with pytest.raises(deploy_mod.DeployError, match="partial"):
            await deploy_mod.deploy_creature_to_node(sender, "worker-1", tmp_path)

    async def test_missing_target_path_raises(self, tmp_path):
        (tmp_path / "config.yaml").write_text("x")
        sender = _FakeSender(response={"deployed": []})  # no target_path
        with pytest.raises(deploy_mod.DeployError, match="target_path"):
            await deploy_mod.deploy_creature_to_node(sender, "worker-1", tmp_path)

    async def test_empty_name_falls_back_to_basename(self, tmp_path):
        # Empty string falls back to ``local.name`` per ``name or local.name``.
        (tmp_path / "config.yaml").write_text("x")
        sender = _FakeSender()
        out = await deploy_mod.deploy_creature_to_node(
            sender, "worker-1", tmp_path, name=""
        )
        assert sender.calls[0]["body"]["name"] == tmp_path.name
        assert out == "/remote/path"

    async def test_uses_basename_as_default_name(self, tmp_path):
        (tmp_path / "config.yaml").write_text("x")
        sender = _FakeSender()
        await deploy_mod.deploy_creature_to_node(sender, "w", tmp_path)
        assert sender.calls[0]["body"]["name"] == tmp_path.name
