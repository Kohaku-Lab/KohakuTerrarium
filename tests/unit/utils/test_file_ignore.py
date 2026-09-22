"""Behavior of per-search .gitignore scope, ordering and caching."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from kohakuterrarium.utils.file_ignore import GitIgnoreFilter


def test_nested_rules_override_only_inside_their_directory(tmp_path):
    (tmp_path / ".gitignore").write_text("*.log\n!keep.log\n", encoding="utf-8")
    sub = tmp_path / "src"
    sub.mkdir()
    (sub / ".gitignore").write_text("!drop.log\nkeep.log\n", encoding="utf-8")
    ignore = GitIgnoreFilter(tmp_path)
    assert ignore.is_ignored(tmp_path / "drop.log", False)
    assert not ignore.is_ignored(tmp_path / "keep.log", False)
    assert not ignore.is_ignored(sub / "drop.log", False)
    assert ignore.is_ignored(sub / "keep.log", False)
    assert ignore.is_ignored(tmp_path / "other/drop.log", False)


@pytest.mark.parametrize("marker", ["directory", "file"])
def test_repository_boundary_limits_ancestor_rules(tmp_path, marker):
    (tmp_path / ".gitignore").write_text("*.txt\n", encoding="utf-8")
    repo = tmp_path / "repo"
    sub = repo / "src"
    sub.mkdir(parents=True)
    if marker == "directory":
        (repo / ".git").mkdir()
    else:
        (repo / ".git").write_text("gitdir: elsewhere\n", encoding="utf-8")
    (repo / ".gitignore").write_text("*.log\n", encoding="utf-8")
    ignore = GitIgnoreFilter(sub)
    assert ignore.is_ignored(sub / "drop.log", False)
    assert not ignore.is_ignored(sub / "keep.txt", False)
    assert not ignore.is_ignored(tmp_path / "outside.log", False)


def test_non_repository_starts_at_search_root(tmp_path):
    (tmp_path / ".gitignore").write_text("*.log\n", encoding="utf-8")
    sub = tmp_path / "src"
    sub.mkdir()
    assert not GitIgnoreFilter(sub).is_ignored(sub / "keep.log", False)


def test_escaped_patterns_whitespace_and_directory_only(tmp_path):
    (tmp_path / ".gitignore").write_text(
        "# comment\n\\#literal\n\\!literal\n leading\ncache/\n*.log\n!keep.log\n",
        encoding="utf-8",
    )
    ignore = GitIgnoreFilter(tmp_path)
    for name in ["#literal", "!literal", " leading", "drop.log"]:
        assert ignore.is_ignored(tmp_path / name, False)
    for name in ["leading", "cache", "keep.log", "comment"]:
        assert not ignore.is_ignored(tmp_path / name, False)
    assert ignore.is_ignored(tmp_path / "cache", True)


def test_rules_are_read_once_per_search_and_refreshed_next_search(
    tmp_path, monkeypatch
):
    rule = tmp_path / ".gitignore"
    rule.write_text("*.log\n", encoding="utf-8")
    original = Path.read_text
    reads = []

    def read(path, *args, **kwargs):
        reads.append(path)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read)
    ignore = GitIgnoreFilter(tmp_path)
    for index in range(100):
        assert ignore.is_ignored(tmp_path / f"{index}.log", False)
    assert reads.count(rule) == 1
    rule.write_text("!*.log\n", encoding="utf-8")
    assert not GitIgnoreFilter(tmp_path).is_ignored(tmp_path / "0.log", False)
    assert reads.count(rule) == 2


def test_excluded_parent_prevents_reading_child_rules(tmp_path, monkeypatch):
    (tmp_path / ".gitignore").write_text(
        "blocked/\n!blocked/keep.log\n", encoding="utf-8"
    )
    blocked = tmp_path / "blocked"
    blocked.mkdir()
    (blocked / ".gitignore").write_text("!keep.log\n", encoding="utf-8")
    original = Path.read_text

    def read(path, *args, **kwargs):
        assert path.parent != blocked
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read)
    assert GitIgnoreFilter(tmp_path).is_ignored(blocked / "keep.log", False)


def test_unreadable_rules_do_not_abort_search(tmp_path, monkeypatch):
    def denied(*args, **kwargs):
        raise PermissionError("denied")

    monkeypatch.setattr(Path, "read_text", denied)
    assert not GitIgnoreFilter(tmp_path).is_ignored(tmp_path / "keep.txt", False)


def test_invalid_rule_does_not_discard_valid_rules(tmp_path):
    (tmp_path / ".gitignore").write_text("bad\\\n*.log\n", encoding="utf-8")
    ignore = GitIgnoreFilter(tmp_path)
    assert ignore.is_ignored(tmp_path / "drop.log", False)
    assert not ignore.is_ignored(tmp_path / "keep.txt", False)


def test_reincluded_directory_does_not_reinclude_all_descendants(tmp_path):
    (tmp_path / ".gitignore").write_text(
        "*\n!src/\nsrc/*\n!src/keep.log\n", encoding="utf-8"
    )
    (tmp_path / "src").mkdir()
    ignore = GitIgnoreFilter(tmp_path)
    assert not ignore.is_ignored(tmp_path / "src", True)
    assert not ignore.is_ignored(tmp_path / "src/keep.log", False)
    assert ignore.is_ignored(tmp_path / "src/drop.log", False)
    assert ignore.is_ignored(tmp_path / "src/deep", True)


def test_case_handling_preserves_platform_fnmatch_convention(tmp_path):
    (tmp_path / ".gitignore").write_text("*.LOG\n", encoding="utf-8")
    ignore = GitIgnoreFilter(tmp_path)
    assert ignore.is_ignored(tmp_path / "test.LOG", False)
    assert ignore.is_ignored(tmp_path / "test.log", False) == (os.name == "nt")


def test_rule_combinations_agree_with_git(tmp_path):
    git = shutil.which("git")
    if git is None:
        pytest.skip("Git is only needed as a test oracle")
    subprocess.run(
        [git, "-c", "init.templateDir=", "init", "-q", str(tmp_path)], check=True
    )
    files = [
        "temp/a.py",
        "artifacts/out/b.py",
        "src/temp/a.py",
        "src/cache/a.py",
        "drop.log",
        "keep.log",
        "blocked/keep.log",
        "blocked/drop.log",
        "src/keep.log",
        "src/drop.log",
        "src/deep/drop.log",
        "src/deep/keep.log",
        "#literal",
        "!literal",
        " leading",
        "a1.log",
        "aX.log",
        "ok.txt",
    ]
    for relative in files:
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x", encoding="utf-8")
    (tmp_path / "src/.gitignore").write_text(
        "!drop.log\n/deep/keep.log\n", encoding="utf-8"
    )
    rulesets = [
        "/temp/\nartifacts/out/\n",
        "*.log\n!keep.log\n",
        "blocked/\n!blocked/keep.log\n",
        "blocked/*\n!blocked/keep.log\n",
        "*\n!src/\nsrc/*\n!src/keep.log\n",
        "**/cache/\n",
        "\\#literal\n\\!literal\n leading\n",
        "bad\\\n*.log\n",
        "/a[0-9].log\n",
        "src/\n!src/\n*.log\n!keep.log\n",
        "src/**\n",
        "src/**/keep.log\n",
        "src/*/\n",
        "*.log\n!*/\n",
        "src/**/\n",
        "**/src/**/\n",
        "**/keep.log\n",
        "src/**\n!src/*\n",
        "*\n!*/\n!keep.log\n",
        "src/**\n!src/deep/\n!src/deep/keep.log\n",
    ]
    for rules in rulesets:
        (tmp_path / ".gitignore").write_text(rules, encoding="utf-8")
        result = subprocess.run(
            [
                git,
                "-C",
                str(tmp_path),
                "-c",
                "core.excludesFile=",
                "check-ignore",
                "--no-index",
                "-z",
                "--stdin",
            ],
            input=("\0".join(files) + "\0").encode(),
            capture_output=True,
            check=False,
        )
        assert result.returncode in (0, 1), result.stderr
        expected = (
            set(result.stdout.decode().rstrip("\0").split("\0"))
            if result.stdout
            else set()
        )
        ignore = GitIgnoreFilter(tmp_path)
        actual = {p for p in files if ignore.is_ignored(tmp_path / p, False)}
        assert actual == expected, (rules, actual ^ expected)


def _reference_match(lines, relative, is_dir):
    """Verbatim port of the pre-optimization _matches inner loops.

    Kept as the oracle for the anchored fast path: last-match-wins
    scanning with the finditer/lastgroup/end acceptance predicate.
    """
    from pathspec.patterns.gitignore.spec import GitIgnoreSpecPattern

    patterns = []
    for line in lines:
        if os.name == "nt":
            line = line.lower()
        if line.rstrip().endswith("/**/"):
            line = line.rstrip()[:-1] + "/*/"
        try:
            patterns.append(GitIgnoreSpecPattern(line))
        except GitIgnorePatternError:
            continue
    ignored = False
    for pattern in reversed(patterns):
        candidate = relative
        if is_dir and pattern.pattern.rstrip().endswith("/"):
            candidate += "/"
        if pattern.regex and any(
            match.lastgroup is None or match.end() == len(candidate)
            for match in pattern.regex.finditer(candidate)
        ):
            ignored = bool(pattern.include)
            break
    return ignored


@pytest.mark.parametrize("is_dir", [False, True])
def test_matches_agrees_with_reference_scan_semantics(tmp_path, is_dir):
    # Wide pattern corpus: wildcards, negation, anchoring, scoping,
    # doublestar, dir-only, classes, escapes, comments, blanks.
    lines = [
        "*.log",
        "!keep.log",
        "build/",
        "/temp/",
        "artifacts/out/",
        "**/*.py",
        "src/**/*.ts",
        "foo/**/bar",
        "docs",
        "a?c",
        "build/output",
        "*.tmp/",
        "**/deep",
        "x/**/",
        "[abc].py",
        "*.py[cod]",
        "sub/dir/leaf.txt",
        "**",
        "/",
        "weird/**/",
        "root*end",
        "#comment",
        "",
        "!/rootonly",
        "/rootonly",
        "MiXeDCase",
    ]
    candidates = [
        "a.log",
        "sub/x.log",
        "keep.log",
        "src/keep.log",
        "build",
        "build/",
        "build/out.o",
        "temp",
        "temp/x",
        "artifacts/out",
        "artifacts/out/z",
        "src/mod.ts",
        "other/mod.ts",
        "foo/bar",
        "foo/mid/bar",
        "docs",
        "docs/a",
        "abc",
        "axc",
        "a.py",
        "m.pyc",
        "x.pycod",
        "sub/dir/leaf.txt",
        "deep",
        "x/deep",
        "deep/y",
        "x/deep/y",
        "weird",
        "weird/a",
        "root*end",
        "rootMIDDLEend",
        "rootonly",
        "sub/rootonly",
        "mixedcase",
        "MIXEDCASE",
    ]
    (tmp_path / ".gitignore").write_text("\n".join(lines) + "\n", encoding="utf-8")
    ignore = GitIgnoreFilter(tmp_path)
    for relative in candidates:
        path = tmp_path / relative
        assert ignore._matches(path, is_dir) == _reference_match(
            lines, relative.lower() if os.name == "nt" else relative, is_dir
        ), (relative, is_dir)


@pytest.mark.parametrize("is_dir", [False, True])
def test_prepare_dir_matches_is_ignored_and_reference(tmp_path, is_dir):
    # Same corpus as the scan-semantics test, but driven through the
    # walker-facing prepare_dir fast path: hoisted prefixes per parent
    # directory, name-only matching per entry.
    lines = [
        "*.log",
        "!keep.log",
        "build/",
        "/temp/",
        "artifacts/out/",
        "**/*.py",
        "src/**/*.ts",
        "foo/**/bar",
        "docs",
        "a?c",
        "build/output",
        "*.tmp/",
        "**/deep",
        "x/**/",
        "[abc].py",
        "*.py[cod]",
        "sub/dir/leaf.txt",
        "**",
        "/",
        "weird/**/",
        "root*end",
        "#comment",
        "",
        "!/rootonly",
        "/rootonly",
        "MiXeDCase",
    ]
    candidates = [
        "a.log",
        "sub/x.log",
        "keep.log",
        "src/keep.log",
        "build/out.o",
        "temp/x",
        "artifacts/out/z",
        "src/mod.ts",
        "other/mod.ts",
        "foo/bar",
        "foo/mid/bar",
        "docs/a",
        "abc",
        "axc",
        "a.py",
        "m.pyc",
        "x.pycod",
        "sub/dir/leaf.txt",
        "deep",
        "x/deep",
        "deep/y",
        "x/deep/y",
        "weird/a",
        "root*end",
        "rootMIDDLEend",
        "rootonly",
        "sub/rootonly",
        "mixedcase",
        "MIXEDCASE",
    ]
    (tmp_path / ".gitignore").write_text("\n".join(lines) + "\n", encoding="utf-8")
    ignore = GitIgnoreFilter(tmp_path)

    checkers = {}

    def checker_for(parent: Path):
        if parent not in checkers:
            checkers[parent] = ignore.prepare_dir(parent)
        return checkers[parent]

    for relative in candidates:
        path = tmp_path / relative
        name = Path(relative).name
        parent = path.parent
        expected = _reference_match(
            lines, relative.lower() if os.name == "nt" else relative, is_dir
        )
        assert ignore.is_ignored(path, is_dir) == expected, (relative, is_dir)
        assert checker_for(parent)(name, is_dir) == expected, (relative, is_dir)


def test_prepare_dir_outside_boundary_checks_as_not_ignored(tmp_path):
    # Regression: prepare_dir used to climb the exclusion chain from
    # out-of-boundary directories, where the boundary is never an
    # ancestor — a deterministic hang (glob/grep patterns like
    # "../**/*.py" reach this). is_ignored always checked such paths
    # as not ignored; the fast path must agree.
    (tmp_path / ".git").mkdir()
    ignore = GitIgnoreFilter(tmp_path)
    outside = tmp_path.parent
    check = ignore.prepare_dir(outside)
    assert check("whatever.py", False) is False
    assert check("whatever", True) is False
    assert ignore.is_ignored(outside / "whatever.py", False) is False
    assert ignore.is_ignored(outside / "whatever", True) is False


def test_walk_terminates_when_pattern_prefix_escapes_the_boundary(tmp_path):
    # Companion to test_prepare_dir_outside_boundary_checks_as_not_ignored,
    # driven through iter_matching_files like the glob/grep tools reach
    # it: pattern prefixes may escape the search base (".."), putting
    # walk directories outside the gitignore boundary (proj, which
    # holds .git). The walk must terminate; the out-of-base paths
    # leaking into the result matches baseline (pre-existing) behavior.
    from kohakuterrarium.utils.file_walk import iter_matching_files

    (tmp_path / ".git").mkdir()
    proj = tmp_path / "proj"
    work = proj / "work"
    work.mkdir(parents=True)
    (work / "in.py").write_text("")
    (proj / "lib.py").write_text("")
    (tmp_path / "outer.py").write_text("")

    out = list(iter_matching_files(work, "../../**/*.py"))
    assert {p.name for p in out} == {"outer.py", "lib.py", "in.py"}


def test_entry_prefix_normalizes_separators_only_on_windows(monkeypatch):
    # A "\" is a legal filename character on POSIX: prefix building must
    # leave it alone there (mirroring relative_to().as_posix()) while
    # Windows separators are normalized to "/".
    from kohakuterrarium.utils.file_ignore import _entry_prefix

    monkeypatch.setattr(os, "name", "posix")
    assert _entry_prefix("/r/sub", "/r") == "sub/"
    assert _entry_prefix("/r/a\\b", "/r") == "a\\b/"
    assert _entry_prefix("/r", "/r") == ""
    assert _entry_prefix("/elsewhere", "/r") is None

    monkeypatch.setattr(os, "name", "nt")
    assert _entry_prefix("C:\\r\\a\\b", "C:\\r") == "a/b/"
    assert _entry_prefix("C:\\r\\a\\b", "C:\\r") != "a\\b/"
