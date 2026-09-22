"""Unit tests for :mod:`kohakuterrarium.utils.file_walk`, the traversal
behind the tree / grep / glob tools.  Every branch exercised on real fixtures."""

import os
from pathlib import Path

import pytest

from kohakuterrarium.utils import file_walk
from kohakuterrarium.utils.file_walk import (
    ALWAYS_SKIP_NAMES,
    _glob_match,
    _glob_to_regex,
    iter_matching_files,
    iter_matching_files_stat,
    should_skip_dir,
    walk_dirs,
    walk_files,
)


@pytest.mark.parametrize("pattern", ["**/*.log", "*.log", "src/**/*.log", "src/*.log"])
def test_search_preserves_ignore_scope_and_negation(tmp_path, pattern):
    _build_tree(
        tmp_path,
        {
            ".gitignore": "*.log\n!keep.log\n",
            "drop.log": "x",
            "keep.log": "x",
            "src": {"drop.log": "x", "keep.log": "x"},
        },
    )
    paths = _rel_set(tmp_path, iter_matching_files(tmp_path, pattern))
    assert paths
    assert all(p.endswith("keep.log") for p in paths)
    unfiltered = _rel_set(
        tmp_path, iter_matching_files(tmp_path, pattern, gitignore=False)
    )
    assert any(p.endswith("drop.log") for p in unfiltered)


def test_search_anchored_rules_and_nested_scope(tmp_path):
    _build_tree(
        tmp_path,
        {
            ".gitignore": "/temp/\nartifacts/out/\n",
            "temp": {"bad.py": "x"},
            "artifacts": {"out": {"bad.py": "x"}},
            "src": {
                ".gitignore": "/cache/\n",
                "cache": {"bad.py": "x"},
                "deep": {"cache": {"good.py": "x"}, "temp": {"good.py": "x"}},
            },
        },
    )
    assert _rel_set(tmp_path, iter_matching_files(tmp_path, "**/*.py")) == {
        "src/deep/cache/good.py",
        "src/deep/temp/good.py",
    }


@pytest.mark.parametrize("pattern", ["blocked/**/*.py", "blocked/*.py"])
def test_narrowed_root_cannot_bypass_excluded_parent(tmp_path, pattern):
    _build_tree(
        tmp_path,
        {
            ".gitignore": "blocked/\n!blocked/keep.py\n",
            "blocked": {".gitignore": "!keep.py\n", "keep.py": "x"},
        },
    )
    assert list(iter_matching_files(tmp_path, pattern)) == []
    assert len(list(iter_matching_files(tmp_path, pattern, gitignore=False))) == 1


def test_walk_from_repository_subdirectory_inherits_ancestors(tmp_path):
    _build_tree(
        tmp_path,
        {
            ".git": {},
            ".gitignore": "*.log\n",
            "src": {"drop.log": "x", "ok.txt": "x"},
        },
    )
    assert _rel_set(tmp_path, walk_files(tmp_path / "src")) == {"src/ok.txt"}


def test_ignored_subtree_is_not_enumerated(tmp_path, monkeypatch):
    _build_tree(
        tmp_path, {".gitignore": "/temp/\n", "temp": {"bad.py": "x"}, "ok.py": "x"}
    )
    real_scandir = file_walk.scandir

    def entries(path):
        assert Path(path) != tmp_path / "temp"
        return real_scandir(path)

    monkeypatch.setattr(file_walk, "scandir", entries)
    assert _rel_set(tmp_path, walk_files(tmp_path)) == {"ok.py"}
    assert _rel_set(tmp_path, walk_dirs(tmp_path)) == {"."}


def test_disabled_gitignore_does_not_read_rules_or_disable_other_filters(
    tmp_path, monkeypatch
):
    _build_tree(
        tmp_path,
        {
            ".gitignore": "*.log\n",
            "drop.log": "x",
            ".secret": "x",
            "node_modules": {"a.log": "x"},
        },
    )

    def forbidden(*args, **kwargs):
        raise AssertionError("gitignore=False must not read ignore files")

    monkeypatch.setattr(Path, "read_text", forbidden)
    assert _rel_set(
        tmp_path, iter_matching_files(tmp_path, "**/*", gitignore=False)
    ) == {"drop.log"}


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

    def test_permission_error_on_scandir_skips_subtree(self, tmp_path, monkeypatch):
        _build_tree(tmp_path, {"sub": {"a.py": "x"}, "ok.py": "y"})

        real_scandir = file_walk.scandir

        def _boom(path):
            if Path(path).name == "sub":
                raise PermissionError("denied")
            return real_scandir(path)

        monkeypatch.setattr(file_walk, "scandir", _boom)
        rels = _rel_set(tmp_path, walk_files(tmp_path, gitignore=False))
        # ``sub`` skipped silently, top-level files still yielded.
        assert "ok.py" in rels
        assert "sub/a.py" not in rels

    def test_permission_error_on_entry_is_dir_skips_entry(self, tmp_path, monkeypatch):
        _build_tree(tmp_path, {"weird": {"inner.py": "x"}, "ok.py": "y"})

        real_scandir = file_walk.scandir

        class _BrokenIsDir:
            def __init__(self, entry):
                self._entry = entry

            name = property(lambda self: self._entry.name)
            path = property(lambda self: self._entry.path)

            def is_dir(self, follow_symlinks=True):
                if self._entry.name == "weird":
                    raise PermissionError("denied")
                return self._entry.is_dir(follow_symlinks=follow_symlinks)

        def _boom(path):
            if Path(path) == tmp_path:
                return iter([_BrokenIsDir(e) for e in real_scandir(path)])
            return real_scandir(path)

        monkeypatch.setattr(file_walk, "scandir", _boom)
        rels = _rel_set(tmp_path, walk_files(tmp_path, gitignore=False))
        # The entry whose type can't be determined is skipped (and not
        # descended into); the sibling file is still yielded.
        assert "ok.py" in rels
        assert "weird/inner.py" not in rels

    def test_scandir_walk_matches_reference_filters(self, tmp_path):
        # End-to-end equivalence of the scandir walk against the pinned
        # filter semantics: gitignore (inherited + nested + dir-only),
        # hidden filtering, and unconditional skip names.
        _build_tree(
            tmp_path,
            {
                ".gitignore": "*.log\nbuild/\n",
                "keep.py": "",
                "drop.log": "",
                "sub": {
                    ".gitignore": "*.tmp\n",
                    "also.tmp": "",
                    "deep": {"x.py": ""},
                },
                "build": {"out.o": ""},
                ".hidden": {"h.py": ""},
                "__pycache__": {"c.pyc": ""},
            },
        )
        rels = _rel_set(tmp_path, walk_files(tmp_path))
        assert rels == {"keep.py", "sub/deep/x.py"}

    def test_stat_carrying_match_reports_mtimes_and_defers_io(
        self, tmp_path, monkeypatch
    ):
        _build_tree(
            tmp_path,
            {
                "a.py": "x",
                "sub": {"b.py": "y"},
                "skipme.log": "z",
                ".gitignore": "*.log\n",
            },
        )
        # The walk hands out raw DirEntries; _iter_matching must stat
        # only entries that survive the glob match (selective searches
        # keep their per-match syscall budget on POSIX).
        statted = []
        real_stat = os.DirEntry.stat

        def observe(entry, follow_symlinks=True):
            statted.append(entry.name)
            return real_stat(entry, follow_symlinks=follow_symlinks)

        monkeypatch.setattr(os.DirEntry, "stat", observe)
        pairs = list(iter_matching_files_stat(tmp_path, "**/*.py"))
        assert {
            str(p.relative_to(tmp_path)).replace(os.sep, "/") for p, _s in pairs
        } == {"a.py", "sub/b.py"}
        for path, stat in pairs:
            assert stat is not None
            assert stat.st_mtime == pytest.approx(path.stat().st_mtime)
        assert sorted(statted) == ["a.py", "b.py"]


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

    def test_permission_error_on_scandir_skips_subtree(self, tmp_path, monkeypatch):
        _build_tree(tmp_path, {"sub": {"a.py": ""}})

        real_scandir = file_walk.scandir

        def _boom(path):
            if Path(path).name == "sub":
                raise PermissionError("denied")
            return real_scandir(path)

        monkeypatch.setattr(file_walk, "scandir", _boom)
        # sub is still yielded (the dir itself was found before scandir
        # failed), but its contents aren't walked.
        names = {p.name for p in walk_dirs(tmp_path, gitignore=False)}
        assert "sub" in names


# ── iter_matching_files ──────────────────────────────────────────────


class TestIterMatchingFiles:
    @pytest.mark.parametrize(
        "pattern", [r"src\**\*.py", r"src/**\*.py", r"src\**/*.py"]
    )
    def test_recursive_backslash_prefix_matches_forward_slashes(
        self, tmp_path, pattern
    ):
        _build_tree(
            tmp_path,
            {"src": {"a.py": "", "nested": {"b.py": ""}}, "other": {"c.py": ""}},
        )
        actual = list(iter_matching_files(tmp_path, pattern))
        expected = list(iter_matching_files(tmp_path, "src/**/*.py"))
        assert actual == expected
        assert _rel_set(tmp_path, actual) == {"src/a.py", "src/nested/b.py"}
        assert list(iter_matching_files(tmp_path, pattern, cap=1)) == expected[:1]

    def test_recursive_matcher_is_reused_for_all_candidates(
        self, tmp_path, monkeypatch
    ):
        _build_tree(tmp_path, {f"item{i}.py": "" for i in range(20)})
        original = file_walk._glob_to_regex
        translations = []

        def observe(pattern):
            translations.append(pattern)
            return original(pattern)

        monkeypatch.setattr(file_walk, "_glob_to_regex", observe)
        assert len(list(iter_matching_files(tmp_path, "**/*.py"))) == 20
        assert translations == ["**/*.py"]

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


# ── iter_matching_files_stat ─────────────────────────────────────────


class TestIterMatchingFilesStat:
    def test_recursive_pairs_match_plain_iterator_and_real_stats(self, tmp_path):
        _build_tree(tmp_path, {"a.py": "", "sub": {"b.py": ""}, "x.md": ""})
        plain = _rel_set(tmp_path, iter_matching_files(tmp_path, "**/*.py"))
        pairs = list(iter_matching_files_stat(tmp_path, "**/*.py"))
        assert {
            str(p.relative_to(tmp_path)).replace(os.sep, "/") for p, _s in pairs
        } == plain
        for p, stat in pairs:
            assert stat is not None
            assert stat.st_mtime == pytest.approx(p.stat().st_mtime)

    def test_non_recursive_pairs_carry_stats(self, tmp_path):
        _build_tree(tmp_path, {"a.py": "", "b.py": "", "c.md": ""})
        pairs = list(iter_matching_files_stat(tmp_path, "*.py"))
        assert {p.name for p, _s in pairs} == {"a.py", "b.py"}
        assert all(s is not None for _p, s in pairs)

    def test_cap_applies_to_pairs(self, tmp_path):
        _build_tree(tmp_path, {f"f{i}.py": "" for i in range(6)})
        out = list(iter_matching_files_stat(tmp_path, "**/*.py", cap=3))
        assert len(out) == 3

    def test_respects_gitignore(self, tmp_path):
        _build_tree(
            tmp_path,
            {".gitignore": "drop.log\n", "keep.py": "", "drop.log": ""},
        )
        rels = _rel_set(
            tmp_path, (p for p, _s in iter_matching_files_stat(tmp_path, "**/*"))
        )
        assert rels == {"keep.py"}


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
