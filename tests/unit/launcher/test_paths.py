"""Path-resolver behaviour for the launcher.

Tests focus on ``bundled_wheels_dir()`` — the canonical entry point
for "is the bundled-wheel cache shipped here, and where is it?" The
resolver is factored into ``_candidate_wheel_dirs()`` (where to look)
and ``_bundle_has_framework_wheel()`` (is it actually a bundle?); we
test both directly and then test the composed behaviour.
"""

from pathlib import Path

from kohakuterrarium.launcher import paths as _p


def _place_wheels_bundle(parent: Path, *, with_framework: bool = True) -> Path:
    """Create ``parent / "wheels-bundle"`` with the right contents."""
    bundle = parent / "wheels-bundle"
    bundle.mkdir(parents=True, exist_ok=True)
    if with_framework:
        (bundle / "kohakuterrarium-1.5.0-py3-none-any.whl").write_bytes(b"")
    # A dep wheel that should never be enough on its own.
    (bundle / "pip-24.0-py3-none-any.whl").write_bytes(b"")
    return bundle


class TestBundleHasFrameworkWheel:
    def test_true_when_kohakuterrarium_wheel_present(self, tmp_path):
        bundle = _place_wheels_bundle(tmp_path)
        assert _p._bundle_has_framework_wheel(bundle) is True

    def test_false_when_directory_missing(self, tmp_path):
        assert _p._bundle_has_framework_wheel(tmp_path / "nope") is False

    def test_false_when_only_dep_wheels_present(self, tmp_path):
        # Leftover scaffolding from another project — must NOT count.
        bundle = _place_wheels_bundle(tmp_path, with_framework=False)
        assert _p._bundle_has_framework_wheel(bundle) is False

    def test_false_when_path_is_a_file(self, tmp_path):
        (tmp_path / "wheels-bundle").write_bytes(b"")
        assert _p._bundle_has_framework_wheel(tmp_path / "wheels-bundle") is False


class TestCandidateWheelDirs:
    def test_returns_four_candidates_with_expected_shape(self, monkeypatch, tmp_path):
        # Pin sys.executable so candidates 2 and 3 are deterministic.
        exec_path = tmp_path / "bin" / "python"
        exec_path.parent.mkdir(parents=True)
        monkeypatch.setattr(_p.sys, "executable", str(exec_path))
        candidates = _p._candidate_wheel_dirs()
        assert len(candidates) == 4
        # Candidates 2 and 3 are derived from sys.executable.
        assert candidates[1] == exec_path.parent / "wheels-bundle"
        assert candidates[2] == exec_path.parent.parent / "wheels-bundle"
        # Candidates 1 and 4 are derived from __file__; we don't pin
        # them but they must end with the expected suffix.
        assert candidates[0].name == "wheels-bundle"
        assert candidates[3].name == "wheels-bundle"


class TestBundledWheelsDir:
    """End-to-end behaviour of the public resolver.

    Strategy: monkey-patch ``_candidate_wheel_dirs`` so the test owns
    every search location, then exercise real ``bundled_wheels_dir``
    logic against tmp_path artifacts.  This keeps the test
    deterministic regardless of whether the developer has a local
    ``wheels-bundle/`` lying around.
    """

    def test_returns_none_when_no_candidate_has_wheels(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            _p,
            "_candidate_wheel_dirs",
            lambda: [tmp_path / "a", tmp_path / "b", tmp_path / "c"],
        )
        assert _p.bundled_wheels_dir() is None

    def test_returns_first_matching_candidate(self, monkeypatch, tmp_path):
        c1 = tmp_path / "c1"
        c2 = tmp_path / "c2"
        c3 = tmp_path / "c3"
        # Only c2 holds a real bundle; c1 + c3 don't exist at all.
        c2.mkdir()
        (c2 / "kohakuterrarium-1.5.0-py3-none-any.whl").write_bytes(b"")
        monkeypatch.setattr(_p, "_candidate_wheel_dirs", lambda: [c1, c2, c3])
        assert _p.bundled_wheels_dir() == c2

    def test_prefers_earlier_candidate_when_multiple_match(self, monkeypatch, tmp_path):
        c1 = tmp_path / "c1"
        c2 = tmp_path / "c2"
        for c in (c1, c2):
            c.mkdir()
            (c / "kohakuterrarium-1.5.0-py3-none-any.whl").write_bytes(b"")
        monkeypatch.setattr(_p, "_candidate_wheel_dirs", lambda: [c1, c2])
        assert _p.bundled_wheels_dir() == c1

    def test_skips_directory_without_framework_wheel(self, monkeypatch, tmp_path):
        # First candidate has scaffolding but no framework wheel;
        # second is the real bundle.  Resolver must skip the first.
        c1 = tmp_path / "c1"
        c1.mkdir()
        (c1 / "some-other-package.whl").write_bytes(b"")
        c2 = tmp_path / "c2"
        c2.mkdir()
        (c2 / "kohakuterrarium-1.5.0-py3-none-any.whl").write_bytes(b"")
        monkeypatch.setattr(_p, "_candidate_wheel_dirs", lambda: [c1, c2])
        assert _p.bundled_wheels_dir() == c2
