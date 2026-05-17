"""pip-arg resolution for each source kind."""

import pytest

from kohakuterrarium.launcher import settings as _s
from kohakuterrarium.launcher import sources as _src


class TestPyPI:
    def test_default_latest(self):
        args = _src.resolve_pip_args(_s.SourceConfig(kind="pypi"))
        assert args == ["-U", "kohakuterrarium"]

    def test_version_pin(self):
        args = _src.resolve_pip_args(_s.SourceConfig(kind="pypi", spec="==1.5.0"))
        assert args == ["-U", "kohakuterrarium==1.5.0"]

    def test_extras(self):
        args = _src.resolve_pip_args(
            _s.SourceConfig(kind="pypi", extras=["full", "embeddings-heavy"])
        )
        assert args == ["-U", "kohakuterrarium[full,embeddings-heavy]"]

    def test_extras_with_pin(self):
        args = _src.resolve_pip_args(
            _s.SourceConfig(kind="pypi", spec="<2.0", extras=["full"])
        )
        assert args == ["-U", "kohakuterrarium[full]<2.0"]


class TestGit:
    def test_simple_url(self):
        args = _src.resolve_pip_args(
            _s.SourceConfig(kind="git", spec="https://example.com/x.git@main")
        )
        assert args == ["-U", "git+https://example.com/x.git@main"]

    def test_with_extras_uses_egg_fragment(self):
        args = _src.resolve_pip_args(
            _s.SourceConfig(
                kind="git",
                spec="https://example.com/x.git@v1",
                extras=["full"],
            )
        )
        assert args == [
            "-U",
            "git+https://example.com/x.git@v1#egg=kohakuterrarium[full]",
        ]

    def test_already_git_prefixed(self):
        args = _src.resolve_pip_args(
            _s.SourceConfig(kind="git", spec="git+https://example.com/x.git@v1")
        )
        assert args == ["-U", "git+https://example.com/x.git@v1"]

    def test_missing_spec_raises(self):
        with pytest.raises(ValueError, match="source.spec is required"):
            _src.resolve_pip_args(_s.SourceConfig(kind="git"))


class TestLocal:
    def test_editable_install(self):
        args = _src.resolve_pip_args(
            _s.SourceConfig(kind="local", spec="/home/me/checkout")
        )
        assert args == ["-e", "/home/me/checkout"]

    def test_editable_with_extras(self):
        args = _src.resolve_pip_args(
            _s.SourceConfig(kind="local", spec="/x", extras=["dev"])
        )
        assert args == ["-e", "/x[dev]"]

    def test_missing_spec_raises(self):
        with pytest.raises(ValueError, match="source.spec is required"):
            _src.resolve_pip_args(_s.SourceConfig(kind="local"))


class TestBundled:
    def test_no_bundle_raises(self, monkeypatch):
        # Force bundled_wheels_dir() to return None — covers the "dev
        # install, no bundle present" path that the wrapper detects
        # to surface a clear error instead of an opaque pip failure.
        monkeypatch.setattr(_src, "bundled_wheels_dir", lambda: None)
        with pytest.raises(FileNotFoundError):
            _src.resolve_pip_args(_s.SourceConfig(kind="bundled"))


class TestUnknownKind:
    def test_unknown_kind_raises(self):
        s = _s.SourceConfig(kind="from-the-moon")
        with pytest.raises(ValueError, match="unknown source.kind"):
            _src.resolve_pip_args(s)


class TestAutoUpdateAllowed:
    @pytest.mark.parametrize("kind", ["pypi", "git", "bundled"])
    def test_remote_kinds_allow_auto_update(self, kind):
        assert _src.is_auto_update_allowed(_s.SourceConfig(kind=kind)) is True

    def test_local_kind_disables_auto_update(self):
        assert (
            _src.is_auto_update_allowed(_s.SourceConfig(kind="local", spec="/x"))
            is False
        )
