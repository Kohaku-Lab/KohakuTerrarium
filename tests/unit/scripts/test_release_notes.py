import subprocess
import sys
from pathlib import Path

import pytest


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def _commit(repo: Path, subject: str) -> str:
    (repo / "file.txt").write_text(subject, encoding="utf-8")
    _git(repo, "add", "file.txt")
    _git(repo, "commit", "-m", subject)
    return _git(repo, "rev-parse", "--short", "HEAD").strip()


@pytest.fixture
def repo(tmp_path):
    path = tmp_path / "repo"
    path.mkdir()
    _git(path, "init", "-b", "main")
    _git(path, "config", "user.email", "ci@example.test")
    _git(path, "config", "user.name", "CI")
    return path


@pytest.fixture
def render(scripts_dir, repo, tmp_path):
    """Run release_notes.py against the fixture repo and return the notes."""

    def _render(*args: str) -> str:
        out = tmp_path / "notes.md"
        subprocess.run(
            [
                sys.executable,
                str(scripts_dir / "release_notes.py"),
                "--repo-path",
                str(repo),
                "--out",
                str(out),
                *args,
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        return out.read_text(encoding="utf-8")

    return _render


def test_auto_since_lists_commits_after_last_stable_tag(repo, render):
    _commit(repo, "feat: before the release")
    _git(repo, "tag", "v2.1.0")
    sha = _commit(repo, "fix(session): preserve tool pairs")

    notes = render("--github-repo", "Kohaku-Lab/KohakuTerrarium")

    assert f"- `{sha}` fix(session): preserve tool pairs" in notes
    assert "feat: before the release" not in notes
    assert "1 commit since `v2.1.0`" in notes
    assert (
        "https://github.com/Kohaku-Lab/KohakuTerrarium/compare/v2.1.0...HEAD" in notes
    )


def test_prerelease_tags_do_not_anchor_the_range(repo, render):
    _commit(repo, "feat: shipped in 2.1.0")
    _git(repo, "tag", "v2.1.0")
    _commit(repo, "fix: after the release")
    _git(repo, "tag", "v2.2.0rc1")
    _commit(repo, "fix: after the rc")

    notes = render()

    assert "fix: after the release" in notes
    assert "fix: after the rc" in notes
    assert "2 commits since `v2.1.0`" in notes


def test_tag_at_head_is_skipped_as_the_lower_bound(repo, render):
    _commit(repo, "feat: shipped in 2.1.0")
    _git(repo, "tag", "v2.1.0")
    _commit(repo, "fix: patch material")
    _git(repo, "tag", "v2.1.1")

    notes = render("--until", "v2.1.1")

    assert "1 commit since `v2.1.0`" in notes
    assert "fix: patch material" in notes


def test_merge_commits_are_dropped_unless_requested(repo, render):
    _commit(repo, "feat: base")
    _git(repo, "tag", "v2.1.0")
    _git(repo, "checkout", "-b", "topic")
    _commit(repo, "fix: work on a branch")
    _git(repo, "checkout", "main")
    (repo / "other.txt").write_text("main", encoding="utf-8")
    _git(repo, "add", "other.txt")
    _git(repo, "commit", "-m", "chore: main side")
    _git(repo, "merge", "--no-ff", "topic", "-m", "Merge pull request #1 from topic")

    default_notes = render()
    with_merges = render("--include-merges")

    assert "Merge pull request #1" not in default_notes
    assert "fix: work on a branch" in default_notes
    assert "Merge pull request #1" in with_merges


def test_explicit_since_sha_bounds_the_range(repo, render):
    _commit(repo, "feat: first")
    anchor = _git(repo, "rev-parse", "HEAD").strip()
    _commit(repo, "feat: second")

    notes = render("--since", anchor)

    assert "feat: second" in notes
    assert "feat: first" not in notes


def test_unknown_since_falls_back_to_last_stable_tag(repo, render):
    _commit(repo, "feat: shipped")
    _git(repo, "tag", "v2.1.0")
    _commit(repo, "fix: unreleased")

    notes = render("--since", "0" * 40)

    assert "1 commit since `v2.1.0`" in notes
    assert "fix: unreleased" in notes


def test_empty_range_renders_a_placeholder_without_a_commit_list(repo, render):
    _commit(repo, "feat: shipped")
    _git(repo, "tag", "v2.1.0")

    notes = render("--since", "v2.1.0", "--github-repo", "Kohaku-Lab/KohakuTerrarium")

    assert "_No commits in this range._" in notes
    assert "- `" not in notes


def test_a_repo_with_no_earlier_tag_falls_back_to_the_full_history(repo, render):
    _commit(repo, "feat: first ever")
    _commit(repo, "feat: second")
    _git(repo, "tag", "v1.0.0")

    notes = render("--until", "v1.0.0")

    assert "feat: first ever" in notes
    assert "2 commits:" in notes
    assert "compare/" not in notes


def test_intro_and_truncation_are_rendered(repo, render):
    _commit(repo, "feat: base")
    _git(repo, "tag", "v2.1.0")
    for index in range(3):
        _commit(repo, f"fix: change {index}")

    notes = render("--intro", "Nightly build.", "--max-commits", "2")

    assert notes.startswith("Nightly build.")
    assert "_(list truncated)_" in notes
    assert notes.count("- `") == 2


def test_no_lower_bound_lists_the_whole_history(repo, render):
    _commit(repo, "feat: first")
    _git(repo, "tag", "v2.1.0")
    _commit(repo, "feat: second")

    notes = render("--since", "")

    assert "feat: first" in notes
    assert "feat: second" in notes
    assert "compare/" not in notes
