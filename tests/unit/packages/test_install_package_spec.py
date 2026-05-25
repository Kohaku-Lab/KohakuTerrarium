"""Unit tests for :func:`kohakuterrarium.packages.install.install_package_spec`.

The wrapper just needs to:
  * route ``@`` specs through ``marketplace.resolve_sync`` →
    ``install_package(url, name_override=entry.name)``
  * reject ``editable=True`` for marketplace specs
  * pass-through everything else verbatim

So tests only need to stub ``marketplace.resolve_sync`` +
``install.install_package`` and assert the wiring.  Real installs
are covered by the existing test_packages_install.py suite.
"""

import pytest

from kohakuterrarium.packages import install as install_mod
from kohakuterrarium.packages.marketplace_types import (
    MarketplaceEntry,
    MarketplaceVersion,
)


def _make_entry(name: str = "kt-biome") -> tuple[MarketplaceEntry, MarketplaceVersion]:
    version = MarketplaceVersion(tag="main", released="2026-05-24")
    entry = MarketplaceEntry(
        name=name,
        repo="https://github.com/Kohaku-Lab/kt-biome",
        description="",
        tags=("creatures",),
        author="Kohaku-Lab",
        license="LicenseRef-KohakuTerrarium-1.0",
        framework=">=1.5.0,<2.0.0",
        versions=(version,),
        source_alias="default",
    )
    return entry, version


class TestSpecRouting:
    def test_at_name_resolves_via_marketplace(self, monkeypatch):
        entry, version = _make_entry("kt-biome")
        captured: dict[str, object] = {}

        def fake_resolve(spec: str):
            captured["spec"] = spec
            return entry, version

        def fake_install(source, *, editable, name_override, ref=None):
            captured["install"] = (source, editable, name_override, ref)
            return name_override or "fallback"

        monkeypatch.setattr(install_mod.marketplace, "resolve_sync", fake_resolve)
        monkeypatch.setattr(install_mod, "install_package", fake_install)

        out = install_mod.install_package_spec("@kt-biome")
        assert captured["spec"] == "@kt-biome"
        # The wrapper now forwards the resolved version tag as ``ref``
        # so the cloner pins to it — fixes the "kt install @x@v1.0.0
        # silently clones default HEAD" audit finding.
        assert captured["install"] == (
            "https://github.com/Kohaku-Lab/kt-biome",
            False,
            "kt-biome",
            "main",
        )
        assert out == "kt-biome"

    def test_name_override_wins_over_marketplace_name(self, monkeypatch):
        entry, version = _make_entry("kt-biome")
        captured: dict[str, object] = {}
        monkeypatch.setattr(
            install_mod.marketplace, "resolve_sync", lambda s: (entry, version)
        )

        def fake_install(source, *, editable, name_override, ref=None):
            captured["name_override"] = name_override
            captured["ref"] = ref
            return name_override or "fallback"

        monkeypatch.setattr(install_mod, "install_package", fake_install)
        install_mod.install_package_spec("@kt-biome", name_override="my-pin")
        assert captured["name_override"] == "my-pin"
        assert captured["ref"] == "main"

    def test_editable_rejected_for_at_spec(self, monkeypatch):
        # Even with marketplace stubbed, the wrapper must refuse the
        # combo before delegating.
        with pytest.raises(ValueError, match="editable"):
            install_mod.install_package_spec("@kt-biome", editable=True)

    def test_git_url_passes_through(self, monkeypatch):
        captured: dict[str, object] = {}

        def fake_install(source, *, editable, name_override):
            captured["args"] = (source, editable, name_override)
            return "biome"

        monkeypatch.setattr(install_mod, "install_package", fake_install)
        out = install_mod.install_package_spec(
            "https://github.com/x/biome.git", editable=False
        )
        assert captured["args"] == (
            "https://github.com/x/biome.git",
            False,
            None,
        )
        assert out == "biome"

    def test_local_path_passes_through(self, monkeypatch, tmp_path):
        captured: dict[str, object] = {}

        def fake_install(source, *, editable, name_override):
            captured["args"] = (source, editable, name_override)
            return tmp_path.name

        monkeypatch.setattr(install_mod, "install_package", fake_install)
        install_mod.install_package_spec(str(tmp_path), editable=True)
        assert captured["args"] == (str(tmp_path), True, None)

    def test_marketplace_resolve_propagates(self, monkeypatch):
        # MarketplaceNotFoundError must bubble through unchanged so the
        # CLI / API layer can surface its message.
        from kohakuterrarium.packages.marketplace_types import (
            MarketplaceNotFoundError,
        )

        def raise_not_found(_spec: str):
            raise MarketplaceNotFoundError("no such package")

        monkeypatch.setattr(install_mod.marketplace, "resolve_sync", raise_not_found)
        with pytest.raises(MarketplaceNotFoundError):
            install_mod.install_package_spec("@nope")
