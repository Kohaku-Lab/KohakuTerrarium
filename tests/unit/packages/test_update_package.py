"""Unit tests for ``update_package`` — the four-way update router.

The bug these pin: every marketplace install resolves to a concrete ref, so
``ref`` alone could not distinguish "give me the newest" from "give me exactly
this". Reading it as a pin made ``kt update`` refuse every marketplace-installed
package, which is the documented primary install route.
"""

import json

import pytest

from kohakuterrarium.errors import PackageError
from kohakuterrarium.packages import install as install_mod
from kohakuterrarium.packages.marketplace_types import (
    MarketplaceEntry,
    MarketplaceVersion,
)


def _pkg(tmp_path, name="demo", **info):
    """Create a fake installed git package carrying the given install info."""
    root = tmp_path / name
    (root / ".git").mkdir(parents=True)
    payload = {"source": "https://example.com/demo", "ref": "abc123", **info}
    (root / ".kt_install_info.json").write_text(json.dumps(payload))
    return root


def _entry(tag="v2.0.0", alias=""):
    version = MarketplaceVersion(tag=tag, released="2026-01-01", commit=f"sha-{tag}")
    entry = MarketplaceEntry(
        name="demo",
        repo="https://example.com/demo",
        description="",
        tags=(),
        author="",
        license="",
        framework="",
        versions=(version,),
        source_alias=alias,
    )
    return entry, version


@pytest.fixture
def routed(tmp_path, monkeypatch):
    """Route package lookups at a temp dir and record what the router did."""
    calls = {"pull": [], "swap": [], "resolve": [], "deps": []}

    # Models the real helper: None when nothing by that name is installed.
    monkeypatch.setattr(
        install_mod,
        "get_package_root",
        lambda n: (tmp_path / n) if (tmp_path / n).exists() else None,
    )
    monkeypatch.setattr(
        install_mod.git_backend, "pull_repo", lambda t: calls["pull"].append(t)
    )
    monkeypatch.setattr(install_mod.git_backend, "is_dirty", lambda t: False)
    monkeypatch.setattr(install_mod.git_backend, "is_detached", lambda t: False)
    monkeypatch.setattr(install_mod.marketplace, "has_entry", lambda n: True)
    monkeypatch.setattr(install_mod, "_validate_package", lambda t, n: None)
    monkeypatch.setattr(
        install_mod, "_install_python_deps", lambda t, deps: calls["deps"].append(t)
    )
    monkeypatch.setattr(
        install_mod,
        "_swap_in_clone",
        lambda url, target, name, *, ref: calls["swap"].append((url, ref)),
    )

    def _resolve(spec):
        calls["resolve"].append(spec)
        return _entry()

    monkeypatch.setattr(install_mod.marketplace, "resolve_sync", _resolve)
    return calls


class TestPinnedRefusal:
    def test_explicit_pin_is_refused_with_the_next_move(self, tmp_path, routed):
        _pkg(tmp_path, pinned=True, version="v1.0.0", spec="@demo@v1.0.0")
        with pytest.raises(PackageError) as exc:
            install_mod.update_package("demo")
        assert "kt install @demo@<newversion>" in str(exc.value)
        assert not routed["pull"] and not routed["swap"]

    def test_resolved_ref_alone_is_not_a_pin(self, tmp_path, routed):
        # The regression: `kt install @demo` records a ref but pins nothing.
        _pkg(tmp_path, pinned=False, spec="@demo", version="v1.0.0")
        assert install_mod.update_package("demo") == "demo"
        assert routed["swap"], "an unpinned marketplace install must update"

    def test_legacy_info_without_pinned_key_updates(self, tmp_path, routed):
        # Installs predating intent tracking have no `pinned`; reading them as
        # pinned would keep them permanently un-updatable.
        _pkg(tmp_path, spec="@demo", version="v1.0.0")
        assert install_mod.update_package("demo") == "demo"
        assert routed["swap"]


class TestMarketplaceRoute:
    def test_reresolves_and_swaps_transactionally(self, tmp_path, routed):
        root = _pkg(tmp_path, pinned=False, spec="@demo", version="v1.0.0")
        install_mod.update_package("demo")
        assert routed["resolve"] == ["@demo"]
        assert routed["swap"] == [("https://example.com/demo", "sha-v2.0.0")]
        # git pull cannot express "newest version" against a detached HEAD.
        assert not routed["pull"]
        written = json.loads((root / ".kt_install_info.json").read_text())
        assert written["version"] == "v2.0.0"
        assert written["pinned"] is False

    def test_source_alias_is_preserved_on_reresolution(self, tmp_path, routed):
        _pkg(tmp_path, pinned=False, spec="@myfork/demo", source_alias="myfork")
        install_mod.update_package("demo")
        assert routed["resolve"] == ["@myfork/demo"]

    def test_already_current_is_a_no_op(self, tmp_path, routed):
        _pkg(tmp_path, pinned=False, spec="@demo", version="v2.0.0")
        assert install_mod.update_package("demo") == "demo"
        assert not routed["swap"]
        assert not routed["deps"]


class TestLegacyInstalls:
    def test_pre_3_0_install_without_a_spec_reresolves(self, tmp_path, routed):
        # Those files record {source, ref, written} only. They were cloned at
        # that ref, so they sit on a detached HEAD where `git pull` exits 0 and
        # changes nothing — a silent no-op that reports success.
        _pkg(tmp_path)  # no spec, no pinned, no version
        install_mod.update_package("demo")
        assert routed["resolve"] == ["@demo"]
        assert routed["swap"]
        assert not routed["pull"]

    def test_unknown_legacy_package_falls_through_to_pull(
        self, tmp_path, routed, monkeypatch
    ):
        # Not in any marketplace: a plain clone that happens to record a ref.
        monkeypatch.setattr(install_mod.marketplace, "has_entry", lambda n: False)
        _pkg(tmp_path)
        install_mod.update_package("demo")
        assert routed["pull"] and not routed["swap"]


class TestPlainGitRoute:
    def test_fast_forwards_a_plain_clone(self, tmp_path, routed):
        _pkg(tmp_path, pinned=False, spec=None, ref=None)
        install_mod.update_package("demo")
        assert routed["pull"] and not routed["swap"]

    def test_detached_head_is_refused_rather_than_silently_no_oping(
        self, tmp_path, routed, monkeypatch
    ):
        # `git pull --ff-only` on a detached HEAD exits 0 and advances nothing.
        # Reporting that as a successful update is worse than refusing.
        monkeypatch.setattr(install_mod.git_backend, "is_detached", lambda t: True)
        monkeypatch.setattr(install_mod.marketplace, "has_entry", lambda n: False)
        _pkg(tmp_path, pinned=False, spec=None, ref=None)
        with pytest.raises(PackageError) as exc:
            install_mod.update_package("demo")
        assert "not a branch" in str(exc.value)
        assert not routed["pull"]

    def test_local_modifications_are_refused_with_guidance(
        self, tmp_path, routed, monkeypatch
    ):
        # Studio's editors write into installed packages, so this is the
        # common case; git's own message is not actionable.
        monkeypatch.setattr(install_mod.git_backend, "is_dirty", lambda t: True)
        _pkg(tmp_path, pinned=False, spec=None, ref=None)
        with pytest.raises(PackageError) as exc:
            install_mod.update_package("demo")
        assert "local modifications" in str(exc.value)
        assert not routed["pull"], "must not touch the tree when it is dirty"


class TestPreconditions:
    def test_missing_package_raises(self, tmp_path, routed):
        with pytest.raises(FileNotFoundError):
            install_mod.update_package("ghost")

    def test_non_git_package_raises(self, tmp_path, routed):
        (tmp_path / "plain").mkdir()
        with pytest.raises(PackageError) as exc:
            install_mod.update_package("plain")
        assert "not a git clone" in str(exc.value)


class TestIntentRecording:
    def test_bare_spec_records_unpinned(self, tmp_path):
        install_mod._write_install_info(
            tmp_path, source="u", ref="r", spec="@demo", pinned=False, version="v1"
        )
        info = install_mod._read_install_info(tmp_path)
        assert info["pinned"] is False
        assert info["version"] == "v1"

    def test_versioned_spec_records_pinned(self, tmp_path):
        install_mod._write_install_info(
            tmp_path, source="u", ref="r", spec="@demo@v1", pinned=True, version="v1"
        )
        assert install_mod._read_install_info(tmp_path)["pinned"] is True
