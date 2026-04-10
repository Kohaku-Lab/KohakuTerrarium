"""Unit tests for the gitignore-aware file walker and grep/glob optimizations."""

from pathlib import Path

from kohakuterrarium.utils.file_walk import (
    _glob_match,
    is_ignored,
    iter_matching_files,
    parse_gitignore,
    should_skip_dir,
    walk_dirs,
    walk_files,
)

# ── should_skip_dir ──────────────────────────────────────────────────


class TestShouldSkipDir:
    def test_exact_names(self):
        for name in ("node_modules", ".git", "__pycache__", ".venv"):
            assert should_skip_dir(name), name

    def test_egg_info_pattern(self):
        assert should_skip_dir("my_pkg.egg-info")
        assert should_skip_dir("foo.egg-info")

    def test_normal_dirs_pass(self):
        assert not should_skip_dir("src")
        assert not should_skip_dir("tests")
        assert not should_skip_dir("data")


# ── parse_gitignore ──────────────────────────────────────────────────


class TestParseGitignore:
    def test_reads_patterns(self, tmp_path: Path):
        gi = tmp_path / ".gitignore"
        gi.write_text("*.pyc\nbuild/\n# comment\n\n__pycache__\n")
        patterns = parse_gitignore(gi)
        assert patterns == ["*.pyc", "build/", "__pycache__"]

    def test_missing_file(self, tmp_path: Path):
        assert parse_gitignore(tmp_path / "nope") == []


# ── is_ignored ───────────────────────────────────────────────────────


class TestIsIgnored:
    def test_simple_pattern(self):
        assert is_ignored("foo.pyc", False, ["*.pyc"])
        assert not is_ignored("foo.py", False, ["*.pyc"])

    def test_dir_only_pattern(self):
        assert is_ignored("build", True, ["build/"])
        assert not is_ignored("build", False, ["build/"])  # file named build

    def test_negation_skipped(self):
        # Negation patterns are not supported — they are ignored
        assert is_ignored("foo.log", False, ["*.log", "!important.log"])


# ── walk_files ───────────────────────────────────────────────────────


class TestWalkFiles:
    def _make_tree(self, tmp_path: Path):
        """Create a realistic project tree for testing."""
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "main.py").write_text("# main")
        (tmp_path / "src" / "utils.py").write_text("# utils")
        (tmp_path / "node_modules").mkdir()
        (tmp_path / "node_modules" / "pkg").mkdir()
        (tmp_path / "node_modules" / "pkg" / "index.js").write_text("//")
        (tmp_path / "__pycache__").mkdir()
        (tmp_path / "__pycache__" / "main.cpython-312.pyc").write_bytes(b"\x00")
        (tmp_path / "README.md").write_text("# readme")
        (tmp_path / ".git").mkdir()
        (tmp_path / ".git" / "config").write_text("[core]")
        return tmp_path

    def test_skips_always_skip_dirs(self, tmp_path: Path):
        self._make_tree(tmp_path)
        files = {f.name for f in walk_files(tmp_path)}
        assert "main.py" in files
        assert "utils.py" in files
        assert "README.md" in files
        # Must NOT include files from always-skip dirs
        assert "index.js" not in files
        assert "main.cpython-312.pyc" not in files
        assert "config" not in files

    def test_respects_gitignore(self, tmp_path: Path):
        self._make_tree(tmp_path)
        (tmp_path / ".gitignore").write_text("*.md\n")
        files = {f.name for f in walk_files(tmp_path, gitignore=True)}
        assert "README.md" not in files
        assert "main.py" in files

    def test_gitignore_false_keeps_all(self, tmp_path: Path):
        self._make_tree(tmp_path)
        (tmp_path / ".gitignore").write_text("*.md\n")
        files = {f.name for f in walk_files(tmp_path, gitignore=False)}
        assert "README.md" in files

    def test_cap_limits_output(self, tmp_path: Path):
        self._make_tree(tmp_path)
        files = list(walk_files(tmp_path, cap=2))
        assert len(files) == 2

    def test_hidden_files_skipped_by_default(self, tmp_path: Path):
        (tmp_path / ".hidden").write_text("secret")
        (tmp_path / "visible").write_text("hello")
        files = {f.name for f in walk_files(tmp_path)}
        assert "visible" in files
        assert ".hidden" not in files

    def test_show_hidden(self, tmp_path: Path):
        (tmp_path / ".hidden").write_text("secret")
        files = {f.name for f in walk_files(tmp_path, show_hidden=True)}
        assert ".hidden" in files


# ── walk_dirs ────────────────────────────────────────────────────────


class TestWalkDirs:
    def test_skips_node_modules(self, tmp_path: Path):
        (tmp_path / "src").mkdir()
        (tmp_path / "node_modules").mkdir()
        dirs = {d.name for d in walk_dirs(tmp_path)}
        assert tmp_path.name in dirs
        assert "src" in dirs
        assert "node_modules" not in dirs


# ── iter_matching_files ──────────────────────────────────────────────


class TestIterMatchingFiles:
    def _make_tree(self, tmp_path: Path):
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "main.py").write_text("# main")
        (tmp_path / "src" / "style.css").write_text("body {}")
        (tmp_path / "node_modules").mkdir()
        (tmp_path / "node_modules" / "dep.py").write_text("# dep")
        (tmp_path / "README.md").write_text("# readme")
        return tmp_path

    def test_recursive_pattern(self, tmp_path: Path):
        self._make_tree(tmp_path)
        files = list(iter_matching_files(tmp_path, "**/*.py"))
        names = {f.name for f in files}
        assert "main.py" in names
        assert "dep.py" not in names  # node_modules skipped

    def test_non_recursive_pattern(self, tmp_path: Path):
        self._make_tree(tmp_path)
        files = list(iter_matching_files(tmp_path, "*.md"))
        assert len(files) == 1
        assert files[0].name == "README.md"

    def test_prefixed_recursive(self, tmp_path: Path):
        self._make_tree(tmp_path)
        files = list(iter_matching_files(tmp_path, "src/**/*.py"))
        names = {f.name for f in files}
        assert "main.py" in names
        assert "style.css" not in names

    def test_cap_stops_early(self, tmp_path: Path):
        self._make_tree(tmp_path)
        files = list(iter_matching_files(tmp_path, "**/*", cap=1))
        assert len(files) == 1

    def test_gitignore_false(self, tmp_path: Path):
        self._make_tree(tmp_path)
        # With gitignore off, node_modules is still skipped by ALWAYS_SKIP
        files = list(iter_matching_files(tmp_path, "**/*.py", gitignore=False))
        names = {f.name for f in files}
        assert "main.py" in names
        # node_modules is in ALWAYS_SKIP so still excluded
        assert "dep.py" not in names


# ── _glob_match ──────────────────────────────────────────────────────


class TestGlobMatch:
    def test_double_star_slash(self):
        assert _glob_match("src/foo.py", "**/*.py")
        assert _glob_match("foo.py", "**/*.py")
        assert _glob_match("a/b/c/foo.py", "**/*.py")
        assert not _glob_match("foo.txt", "**/*.py")

    def test_prefix_double_star(self):
        assert _glob_match("components/Foo.vue", "components/**/*.vue")
        assert _glob_match("components/sub/Bar.vue", "components/**/*.vue")
        assert not _glob_match("other/Foo.vue", "components/**/*.vue")

    def test_simple_star(self):
        assert _glob_match("foo.py", "*.py")
        assert not _glob_match("dir/foo.py", "*.py")

    def test_question_mark(self):
        assert _glob_match("a.py", "?.py")
        assert not _glob_match("ab.py", "?.py")
