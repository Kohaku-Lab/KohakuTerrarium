"""Unit tests for :mod:`kohakuterrarium.utils.file_walk`.

This module powers the tree / grep / glob built-in tools.  Bugs
here cause those tools to silently miss files or scan ignored
subtrees.  Every branch exercised against real filesystem fixtures.
"""

import os
from pathlib import Path

import pytest

from kohakuterrarium.utils.file_walk import (
    ALWAYS_SKIP_NAMES,
    _glob_match,
    _glob_to_regex,
    is_ignored,
    iter_matching_files,
    parse_gitignore,
    should_skip_dir,
    walk_dirs,
    walk_files,
)

# ── tree builder for assertions ──────────────────────────────────────


def _build_tree(root: Path, spec: dict) -> None:
    """Materialise a nested dict into a real filesystem tree.

    Leaves are strings (file contents); dict values build subdirs.
    """
    for name, value in spec.items():
        target = root / name
        if isinstance(value, dict):
            target.mkdir(parents=True, exist_ok=True)
            _build_tree(target, value)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(value, encoding="utf-8")


def _rel_set(root: Path, paths) -> set[str]:
    """Convert an iterable of Path → set of POSIX-style relpaths."""
    return {str(p.relative_to(root)).replace(os.sep, "/") for p in paths}


# ── should_skip_dir ──────────────────────────────────────────────────


class TestShouldSkipDir:
    @pytest.mark.parametrize("name", sorted(ALWAYS_SKIP_NAMES))
    def test_unconditional_names_skipped(self, name):
        assert should_skip_dir(name) is True

    def test_egg_info_suffix_skipped(self):
        assert should_skip_dir("kohakuterrarium.egg-info") is True

    def test_normal_dir_not_skipped(self):
        assert should_skip_dir("src") is False
        assert should_skip_dir("tests") is False

    def test_empty_string_not_skipped(self):
        assert should_skip_dir("") is False


# ── parse_gitignore ──────────────────────────────────────────────────


class TestParseGitignore:
    def test_missing_file_returns_empty(self, tmp_path):
        assert parse_gitignore(tmp_path / "nope") == []

    def test_strips_comments_and_blanks(self, tmp_path):
        gi = tmp_path / ".gitignore"
        gi.write_text("# comment\n\n*.log\n\nbuild/\n  # indented comment\n")
        patterns = parse_gitignore(gi)
        assert patterns == ["*.log", "build/"]

    def test_strips_inline_whitespace(self, tmp_path):
        gi = tmp_path / ".gitignore"
        gi.write_text("  *.log  \n")
        assert parse_gitignore(gi) == ["*.log"]

    def test_unreadable_returns_empty(self, tmp_path, monkeypatch):
        gi = tmp_path / ".gitignore"
        gi.write_text("*.log")

        original = Path.read_text

        def _boom(self, *a, **kw):
            if self == gi:
                raise PermissionError("denied")
            return original(self, *a, **kw)

        monkeypatch.setattr(Path, "read_text", _boom)
        assert parse_gitignore(gi) == []


# ── is_ignored ───────────────────────────────────────────────────────


class TestIsIgnored:
    def test_no_patterns_means_not_ignored(self):
        assert is_ignored("foo.py", False, []) is False

    def test_simple_glob_matches_file(self):
        assert is_ignored("a.log", False, ["*.log"]) is True

    def test_simple_glob_no_match(self):
        assert is_ignored("a.txt", False, ["*.log"]) is False

    def test_trailing_slash_dir_only_for_dir(self):
        # ``build/`` only matches directories.
        assert is_ignored("build", True, ["build/"]) is True
        assert is_ignored("build", False, ["build/"]) is False

    def test_negation_pattern_silently_skipped(self):
        # ``!keep.log`` does NOT make ``keep.log`` matching ``*.log``
        # be un-ignored — the simplified matcher just skips negation.
        # So ``keep.log`` still matches ``*.log`` → ignored.
        assert is_ignored("keep.log", False, ["*.log", "!keep.log"]) is True
        # ``!``-only patterns produce no match on their own.
        assert is_ignored("anything", False, ["!anything"]) is False


# ── walk_files ───────────────────────────────────────────────────────


class TestWalkFiles:
    def test_yields_all_files_no_filters(self, tmp_path):
        _build_tree(
            tmp_path,
            {
                "a.py": "x",
                "b.py": "y",
                "sub": {"c.py": "z"},
            },
        )
        rels = _rel_set(tmp_path, walk_files(tmp_path, gitignore=False))
        assert rels == {"a.py", "b.py", "sub/c.py"}

    def test_skips_always_skip_dirs(self, tmp_path):
        _build_tree(
            tmp_path,
            {
                "a.py": "x",
                "__pycache__": {"cache.pyc": "binary"},
                ".git": {"HEAD": "ref"},
                "node_modules": {"pkg": {"index.js": "code"}},
            },
        )
        rels = _rel_set(tmp_path, walk_files(tmp_path, gitignore=False))
        assert rels == {"a.py"}

    def test_skips_dot_files_by_default(self, tmp_path):
        _build_tree(tmp_path, {".secret": "x", "ok.py": "y"})
        rels = _rel_set(tmp_path, walk_files(tmp_path, gitignore=False))
        assert rels == {"ok.py"}

    def test_show_hidden_includes_dot_files(self, tmp_path):
        _build_tree(tmp_path, {".env": "x", "ok.py": "y"})
        rels = _rel_set(
            tmp_path, walk_files(tmp_path, gitignore=False, show_hidden=True)
        )
        assert rels == {".env", "ok.py"}

    def test_gitignore_filters_files(self, tmp_path):
        _build_tree(
            tmp_path,
            {
                ".gitignore": "*.log\n",
                "keep.py": "ok",
                "drop.log": "junk",
            },
        )
        # ``.gitignore`` is a dot-file → filtered by ``show_hidden=False``
        # default, so the walker won't surface it.  Result is just
        # ``keep.py`` (drop.log filtered by the gitignore pattern).
        rels = _rel_set(tmp_path, walk_files(tmp_path, gitignore=True))
        assert rels == {"keep.py"}

    def test_gitignore_filters_files_show_hidden(self, tmp_path):
        # Same content but show_hidden=True → .gitignore surfaces, log
        # is still filtered by the gitignore pattern.
        _build_tree(
            tmp_path,
            {".gitignore": "*.log\n", "keep.py": "ok", "drop.log": "junk"},
        )
        rels = _rel_set(
            tmp_path, walk_files(tmp_path, gitignore=True, show_hidden=True)
        )
        assert rels == {".gitignore", "keep.py"}

    def test_gitignore_inherits_to_subdirs(self, tmp_path):
        _build_tree(
            tmp_path,
            {
                ".gitignore": "*.log\n",
                "sub": {"drop.log": "junk", "keep.py": "ok"},
            },
        )
        rels = _rel_set(tmp_path, walk_files(tmp_path, gitignore=True))
        assert "sub/keep.py" in rels
        assert "sub/drop.log" not in rels

    def test_nested_gitignore_extends_parent(self, tmp_path):
        _build_tree(
            tmp_path,
            {
                ".gitignore": "*.log\n",
                "sub": {
                    ".gitignore": "*.tmp\n",
                    "drop.log": "x",
                    "also.tmp": "x",
                    "keep.py": "ok",
                },
            },
        )
        rels = _rel_set(tmp_path, walk_files(tmp_path, gitignore=True))
        assert "sub/keep.py" in rels
        assert "sub/drop.log" not in rels
        assert "sub/also.tmp" not in rels

    def test_cap_stops_iteration(self, tmp_path):
        _build_tree(tmp_path, {f"f{i}.py": "x" for i in range(10)})
        out = list(walk_files(tmp_path, gitignore=False, cap=3))
        assert len(out) == 3

    def test_cap_zero_is_unlimited(self, tmp_path):
        _build_tree(tmp_path, {f"f{i}.py": "x" for i in range(5)})
        out = list(walk_files(tmp_path, gitignore=False, cap=0))
        assert len(out) == 5

    def test_permission_error_on_iterdir_skips_subtree(self, tmp_path, monkeypatch):
        _build_tree(tmp_path, {"sub": {"a.py": "x"}, "ok.py": "y"})

        original_iterdir = Path.iterdir

        def _boom(self):
            if self.name == "sub":
                raise PermissionError("denied")
            return original_iterdir(self)

        monkeypatch.setattr(Path, "iterdir", _boom)
        rels = _rel_set(tmp_path, walk_files(tmp_path, gitignore=False))
        # ``sub`` skipped silently, top-level files still yielded.
        assert "ok.py" in rels
        assert "sub/a.py" not in rels

    def test_permission_error_on_is_dir_skips_entry(self, tmp_path, monkeypatch):
        _build_tree(tmp_path, {"weird": {}, "ok.py": "y"})

        original_is_dir = Path.is_dir

        def _boom(self):
            if self.name == "weird":
                raise PermissionError("denied")
            return original_is_dir(self)

        monkeypatch.setattr(Path, "is_dir", _boom)
        rels = _rel_set(tmp_path, walk_files(tmp_path, gitignore=False))
        assert "ok.py" in rels


# ── walk_dirs ────────────────────────────────────────────────────────


class TestWalkDirs:
    def test_yields_root_plus_subdirs(self, tmp_path):
        _build_tree(tmp_path, {"a": {"b": {"c.py": "x"}}, "other": {"d.py": "y"}})
        out = list(walk_dirs(tmp_path, gitignore=False))
        names = {p.name for p in out}
        assert names >= {tmp_path.name, "a", "b", "other"}

    def test_skips_always_skip_dirs(self, tmp_path):
        _build_tree(
            tmp_path,
            {"src": {}, "__pycache__": {"x.pyc": ""}, ".git": {"HEAD": ""}},
        )
        out = list(walk_dirs(tmp_path, gitignore=False))
        names = {p.name for p in out}
        assert "src" in names
        assert "__pycache__" not in names
        assert ".git" not in names

    def test_gitignore_dir_pattern_filters_subdir(self, tmp_path):
        _build_tree(
            tmp_path,
            {
                ".gitignore": "build/\n",
                "build": {"out.o": ""},
                "src": {"a.py": ""},
            },
        )
        out = list(walk_dirs(tmp_path, gitignore=True))
        names = {p.name for p in out}
        assert "src" in names
        assert "build" not in names

    def test_show_hidden_controls_dot_dirs(self, tmp_path):
        _build_tree(tmp_path, {".hidden": {"inner.py": ""}, "visible": {}})
        names_default = {p.name for p in walk_dirs(tmp_path, gitignore=False)}
        names_hidden = {
            p.name for p in walk_dirs(tmp_path, gitignore=False, show_hidden=True)
        }
        assert ".hidden" not in names_default
        assert ".hidden" in names_hidden

    def test_permission_error_on_iterdir_skips_subtree(self, tmp_path, monkeypatch):
        _build_tree(tmp_path, {"sub": {"a.py": ""}})

        original_iterdir = Path.iterdir

        def _boom(self):
            if self.name == "sub":
                raise PermissionError("denied")
            return original_iterdir(self)

        monkeypatch.setattr(Path, "iterdir", _boom)
        # sub is still yielded (the dir itself was found before iterdir
        # failed), but its contents aren't walked.
        names = {p.name for p in walk_dirs(tmp_path, gitignore=False)}
        assert "sub" in names


# ── iter_matching_files ──────────────────────────────────────────────


class TestIterMatchingFiles:
    def test_non_recursive_glob(self, tmp_path):
        _build_tree(tmp_path, {"a.py": "", "b.py": "", "c.md": ""})
        rels = _rel_set(tmp_path, iter_matching_files(tmp_path, "*.py"))
        assert rels == {"a.py", "b.py"}

    def test_non_recursive_glob_with_cap(self, tmp_path):
        _build_tree(tmp_path, {f"f{i}.py": "" for i in range(10)})
        out = list(iter_matching_files(tmp_path, "*.py", cap=3))
        assert len(out) == 3

    def test_recursive_double_star_match(self, tmp_path):
        _build_tree(
            tmp_path,
            {
                "a.py": "",
                "sub": {"b.py": "", "deep": {"c.py": ""}},
                "x.md": "",
            },
        )
        rels = _rel_set(tmp_path, iter_matching_files(tmp_path, "**/*.py"))
        assert rels == {"a.py", "sub/b.py", "sub/deep/c.py"}

    def test_recursive_with_prefix(self, tmp_path):
        _build_tree(
            tmp_path,
            {
                "src": {"x.py": "", "sub": {"y.py": ""}},
                "other": {"z.py": ""},
            },
        )
        rels = _rel_set(tmp_path, iter_matching_files(tmp_path, "src/**/*.py"))
        assert rels == {"src/x.py", "src/sub/y.py"}

    def test_prefix_directory_not_found_returns_empty(self, tmp_path):
        _build_tree(tmp_path, {"src": {"a.py": ""}})
        out = list(iter_matching_files(tmp_path, "missing/**/*.py"))
        assert out == []

    def test_double_star_in_suffix_handled(self, tmp_path):
        # Regression test for B-fw-1 (fixed): iter_matching_files now
        # matches each file's full base-relative path against the WHOLE
        # pattern, so intermediate `**/` segments resolve at any depth.
        _build_tree(
            tmp_path,
            {
                "a": {"b": {"x.py": "", "c": {"y.py": ""}}},
            },
        )
        out = list(iter_matching_files(tmp_path, "**/c/**/*.py"))
        rels = {str(p.relative_to(tmp_path)).replace(os.sep, "/") for p in out}
        assert rels == {"a/b/c/y.py"}

    def test_cap_respected_on_recursive(self, tmp_path):
        _build_tree(
            tmp_path,
            {f"f{i}.py": "" for i in range(10)},
        )
        out = list(iter_matching_files(tmp_path, "**/*.py", cap=4))
        assert len(out) == 4

    def test_respects_gitignore(self, tmp_path):
        # Regression test for B-fw-2 (fixed): iter_matching_files now
        # routes recursive patterns through walk_files, which filters
        # ignored *files* (not just ignored directories) against
        # .gitignore before they are ever matched.
        _build_tree(
            tmp_path,
            {
                ".gitignore": "drop.log\n",
                "sub": {"keep.py": "", "drop.log": ""},
            },
        )
        rels = _rel_set(tmp_path, iter_matching_files(tmp_path, "**/*", gitignore=True))
        assert "sub/keep.py" in rels
        assert "sub/drop.log" not in rels


# ── _glob_to_regex / _glob_match ─────────────────────────────────────


class TestGlobToRegex:
    def test_star_does_not_cross_slashes(self):
        assert _glob_match("a/b.py", "*.py") is False
        assert _glob_match("a.py", "*.py") is True

    def test_double_star_with_slash_matches_zero_or_more_dirs(self):
        # ``**/`` matches zero or more directory segments.
        assert _glob_match("a.py", "**/*.py") is True
        assert _glob_match("sub/a.py", "**/*.py") is True
        assert _glob_match("a/b/c.py", "**/*.py") is True

    def test_double_star_alone_matches_everything(self):
        # ``**`` (without trailing slash) matches arbitrary path.
        assert _glob_match("a/b/c", "**") is True

    def test_question_mark_matches_single_non_slash(self):
        assert _glob_match("ax.py", "a?.py") is True
        assert _glob_match("a/x.py", "a?.py") is False

    def test_regex_metacharacters_escaped(self):
        # ``.`` in source matches a literal dot, not "any char".
        assert _glob_match("a.py", "a.py") is True
        assert _glob_match("axpy", "a.py") is False

    def test_backslashes_normalised(self):
        # The matcher normalises ``\`` to ``/`` internally.
        assert _glob_match("a/b.py", "a\\b.py") is True


def test_glob_to_regex_returns_compiled_pattern():
    pat = _glob_to_regex("*.py")
    assert pat.match("a.py") is not None
    assert pat.match("b.txt") is None
