"""Tests for Windows Git Bash discovery."""

from kohakuterrarium.builtins.tools import bash as bash_tool
from kohakuterrarium.builtins.tools import bash_windows


def test_candidates_follow_active_git_installation(monkeypatch, tmp_path):
    git_root = tmp_path / "Git"
    git_executable = git_root / "cmd" / "git.exe"
    monkeypatch.setattr(
        bash_windows.shutil,
        "which",
        lambda name: str(git_executable) if name == "git" else None,
    )
    monkeypatch.setenv("ProgramW6432", str(tmp_path / "other"))
    monkeypatch.delenv("ProgramFiles", raising=False)
    monkeypatch.delenv("ProgramFiles(x86)", raising=False)
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.delenv("USERPROFILE", raising=False)

    candidates = bash_windows.windows_git_bash_candidates()

    assert candidates[:2] == [
        str(git_root / "bin" / "bash.exe"),
        str(git_root / "usr" / "bin" / "bash.exe"),
    ]


def test_resolver_uses_bash_next_to_active_git(monkeypatch, tmp_path):
    git_root = tmp_path / "Git"
    git_executable = git_root / "cmd" / "git.exe"
    bash_executable = git_root / "bin" / "bash.exe"
    bash_executable.parent.mkdir(parents=True)
    bash_executable.touch()
    monkeypatch.setattr(
        bash_windows.shutil,
        "which",
        lambda name: (
            str(git_executable) if name == "git" else r"C:\\Windows\\system32\\bash.exe"
        ),
    )
    monkeypatch.setattr(bash_tool.sys, "platform", "win32")
    monkeypatch.delenv("KT_BASH_PATH", raising=False)
    monkeypatch.delenv("KT_SHELL_PATH", raising=False)

    assert bash_tool._resolve_shell_executable("bash") == str(bash_executable)
