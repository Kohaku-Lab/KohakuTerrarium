"""Seatbelt profiles and the macOS-only rules they enforce (``sandbox-exec``)."""

import os
import socket
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from kohakuterrarium.builtins.plugins.sandbox import roots
from kohakuterrarium.builtins.plugins.sandbox import seatbelt
from kohakuterrarium.builtins.plugins.sandbox.plugin import SandboxPlugin
from kohakuterrarium.modules.sandbox.presets import WORKSPACE


def _seatbelt_usable() -> bool:
    if not seatbelt.seatbelt_available():
        return False
    probe = subprocess.run(
        [seatbelt.SANDBOX_EXEC, "-p", "(version 1)(allow default)", "/usr/bin/true"],
        capture_output=True,
    )
    return probe.returncode == 0


needs_seatbelt = pytest.mark.skipif(
    not _seatbelt_usable(), reason="sandbox-exec is unavailable or nested"
)
posix_paths = pytest.mark.skipif(
    sys.platform == "win32", reason="Seatbelt profiles use POSIX paths and uids"
)


@pytest.fixture
def dirs(tmp_path, monkeypatch):
    """A workspace and a sibling the policy must not let a command write."""
    workspace = tmp_path / "workspace"
    outside = tmp_path / "home"
    workspace.mkdir()
    outside.mkdir()
    (outside / ".zshrc").write_text("export KEEP=1\n", encoding="utf-8")
    # pytest's tmp_path sits under the system temp root, which the profile
    # allows; drop the temp roots so the sibling is genuinely outside.
    monkeypatch.setattr(roots, "temp_roots", lambda: [])
    return workspace, outside


@posix_paths
class TestBuildWriteProfile:
    def test_workspace_level_allows_only_listed_roots(self, tmp_path):
        text = seatbelt.build_write_profile(
            WORKSPACE, tmp_path, write_allow=["~/.cache"]
        )
        lines = text.splitlines()
        assert lines[:3] == ["(version 1)", "(allow default)", "(deny file-write*)"]
        allow, deny, exceptions, execs, lookups, sockets = lines[3:]
        assert allow.startswith("(allow file-write*")
        assert f'(subpath "{os.path.realpath(tmp_path)}")' in allow
        assert '(subpath "/dev")' in allow
        # User caches are ordinary allows, so the deny list still overrides them.
        assert f'(subpath "{Path("~/Library/Caches").expanduser()}")' in allow
        assert deny.startswith("(deny file-write*")
        assert f'(subpath "{Path("~/.ssh").expanduser()}")' in deny
        # The directory entry above a denied path cannot be renamed away.
        assert f'(literal "{Path("~").expanduser()}")' in deny
        # Only the explicitly named exceptions come after the deny list.
        assert exceptions == (
            f'(allow file-write* (subpath "{Path("~/.cache").expanduser()}"))'
        )
        assert execs.startswith("(deny process-exec")
        assert '(literal "/usr/bin/osascript")' in execs
        assert lookups.startswith("(deny mach-lookup")
        assert sockets.startswith("(deny network-outbound")
        assert f'(subpath "{Path("~/.docker/run").expanduser()}")' in sockets
        assert f'(subpath "{Path("~/.orbstack/run").expanduser()}")' in sockets

    def test_tmux_sockets_follow_tmux_tmpdir(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TMUX_TMPDIR", "/var/tmux-sockets")
        text = seatbelt.build_write_profile(WORKSPACE, tmp_path)
        assert f'(subpath "/var/tmux-sockets/tmux-{os.getuid()}")' in text
        assert f'(subpath "/tmp/tmux-{os.getuid()}")' in text

    def test_broad_level_keeps_only_the_deny_list(self, tmp_path):
        text = seatbelt.build_write_profile(
            WORKSPACE.with_overrides(fs_write="broad"), tmp_path, write_allow=[]
        )
        assert "(deny file-write*)\n" not in text
        assert "(allow file-write*" not in text
        assert f'(subpath "{Path("~/.zshrc").expanduser()}")' in text

    def test_deny_level_allows_devices_only(self, tmp_path):
        text = seatbelt.build_write_profile(
            WORKSPACE.with_overrides(fs_write="deny"),
            tmp_path,
            write_allow=["~/.cache"],
        )
        allows = [line for line in text.splitlines() if line.startswith("(allow ")]
        assert allows == ["(allow default)", '(allow file-write* (subpath "/dev"))']
        assert str(tmp_path) not in text

    def test_hosts_without_posix_uids_skip_the_tmux_rule(self, tmp_path, monkeypatch):
        monkeypatch.delattr(seatbelt.os, "getuid")
        text = seatbelt.build_write_profile(WORKSPACE, tmp_path)
        assert "tmux-" not in text
        assert "(deny network-outbound" in text

    def test_paths_are_quoted_for_the_profile_language(self, tmp_path):
        odd = tmp_path / 'we"ird'
        text = seatbelt.build_write_profile(WORKSPACE, odd, write_allow=[])
        assert '(subpath "' + str(odd).replace('"', '\\"') + '")' in text


@needs_seatbelt
class TestSeatbeltOnlyRules:
    @pytest.mark.parametrize(
        "argv",
        [["/usr/bin/osascript", "-e", "return 1"], ["/bin/launchctl", "list"]],
    )
    async def test_launchers_that_escape_the_sandbox_are_refused(self, dirs, argv):
        workspace, _ = dirs
        result = await SandboxPlugin().run_subprocess_exec(
            argv, cwd=workspace, timeout=30
        )
        assert result["returncode"] != 0
        assert b"Operation not permitted" in result["stderr"]

    async def test_control_sockets_cannot_be_reached(self, dirs, monkeypatch):
        workspace, _ = dirs
        # AF_UNIX paths are short; keep the socket near the filesystem root.
        sock_dir = Path(tempfile.mkdtemp(prefix="kts", dir="/tmp"))
        sock_path = sock_dir / "d.sock"
        monkeypatch.setattr(seatbelt, "_CONTROL_SOCKETS", (str(sock_dir),))
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(str(sock_path))
        server.listen(1)
        try:
            probe = (
                "import socket, sys\n"
                "s = socket.socket(socket.AF_UNIX)\n"
                "s.connect(sys.argv[1])\n"
            )
            result = await SandboxPlugin().run_subprocess_exec(
                [sys.executable, "-c", probe, str(sock_path)],
                cwd=workspace,
                timeout=30,
            )
        finally:
            server.close()
            sock_path.unlink()
            sock_dir.rmdir()
        assert result["returncode"] != 0
        assert b"Operation not permitted" in result["stderr"]
