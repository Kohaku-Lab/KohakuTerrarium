"""Unit tests for ``packaging/android/check_chaquopy_ceiling.py``.

The check itself runs a real ``pip install --dry-run`` against the
project to get the full transitive tree.  These tests instead feed
the analyser hand-crafted install reports that exercise each
code path: a clean tree, a blocker via direct demander, a blocker
via extras-gated transitive, a URL-ref carve-out, a dropped-package
carve-out.  Network-free, fast, deterministic.

The real-world end-to-end behaviour (running pip resolution against
our actual pyproject) is what the script does when invoked from CI;
unit tests prove the analyser handles every shape of input correctly
without paying the ~30-60s pip-resolution cost.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

_CHECK_PATH = (
    Path(__file__).resolve().parents[3]
    / "packaging"
    / "android"
    / "check_chaquopy_ceiling.py"
)


@pytest.fixture(scope="module")
def check():
    spec = importlib.util.spec_from_file_location("check_chaquopy_ceiling", _CHECK_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["check_chaquopy_ceiling"] = module
    spec.loader.exec_module(module)
    return module


def _pkg(name: str, version: str, *, requires: list[str] | None = None) -> dict:
    """Build a minimal install-report package entry matching
    pip's ``--dry-run --report`` JSON shape."""
    return {
        "metadata": {
            "name": name,
            "version": version,
            "requires_dist": list(requires or []),
        }
    }


class TestAnalyse:
    """Behaviour tests for ``check.analyse``."""

    def test_no_blockers_on_empty_install(self, check):
        assert check.analyse({"install": []}) == []

    def test_no_blocker_when_floor_under_ceiling(self, check):
        # pyyaml ceiling is 6.0.3; a demand for >=6.0.0 must NOT
        # block.
        report = {
            "install": [
                _pkg("libcst", "1.4.0", requires=["pyyaml>=6.0.0"]),
                _pkg("pyyaml", "6.0.3"),
            ]
        }
        assert check.analyse(report) == []

    def test_detects_direct_floor_over_ceiling(self, check):
        # This is the actual bug we hit: kohakuvault demanding
        # numpy>=2.0.0 when Chaquopy ships 1.26.2.
        report = {
            "install": [
                _pkg("kohakuvault", "0.8.5", requires=["numpy>=2.0.0"]),
                _pkg("numpy", "2.4.0"),
            ]
        }
        blockers = check.analyse(report)
        assert len(blockers) == 1
        b = blockers[0]
        assert b.dep == "numpy"
        assert str(b.ceiling) == "1.26.2"
        assert str(b.floor) == "2.0.0"
        assert b.demander == "kohakuvault"

    def test_detects_lxml_html_clean_chain(self, check, monkeypatch):
        # lxml_html_clean is the canonical example of the chain bug:
        # demanded by jusText via lxml[html_clean] extra, and
        # ``lxml_html_clean`` itself demands ``lxml>=6.1.1`` which
        # exceeds Chaquopy's 5.3.0 ceiling.  In production we strip
        # it via DROPPED_PACKAGES.  This test verifies the analyser
        # WOULD catch the blocker if the drop weren't in place —
        # which is what protects us when a future-equivalent
        # transitive chain (the same shape, different package
        # names) appears in the tree.
        monkeypatch.setattr(
            check,
            "DROPPED_PACKAGES",
            frozenset(check.DROPPED_PACKAGES - {"lxml_html_clean", "lxml-html-clean"}),
        )
        report = {
            "install": [
                _pkg("lxml_html_clean", "0.4.5", requires=["lxml>=6.1.1"]),
                _pkg("lxml", "6.1.1"),
            ]
        }
        blockers = check.analyse(report)
        assert len(blockers) == 1
        assert blockers[0].dep == "lxml"

    def test_lxml_html_clean_chain_is_neutralised_by_drop(self, check):
        # The mirror of the previous test: with lxml_html_clean in
        # DROPPED_PACKAGES (the production config), the chain MUST
        # not register as a blocker.
        report = {
            "install": [
                _pkg("lxml_html_clean", "0.4.5", requires=["lxml>=6.1.1"]),
                _pkg("lxml", "6.1.1"),
            ]
        }
        assert check.analyse(report) == []

    def test_dropped_target_dep_neutralises_blocker(self, check):
        # bcrypt is in DROPPED_PACKAGES — even when an unconditional
        # demander (KT itself) declares ``bcrypt>=4.0.0`` (ceiling
        # 3.2.2), the analyser must NOT report it as a blocker.
        # bcrypt is stripped from Android requirements.txt entirely,
        # so Chaquopy pip never sees the floor.  This mirrors the
        # demander-side drop test and proves the carve-out is
        # symmetric.
        report = {
            "install": [
                _pkg("KohakuTerrarium", "2.0.0", requires=["bcrypt>=4.0.0"]),
            ]
        }
        assert check.analyse(report) == []

    def test_dropped_package_demands_ignored(self, check):
        # bcrypt is in DROPPED_PACKAGES — its METADATA demands
        # (none here, but the principle holds) and ITS OWN
        # floor (we don't even install it) shouldn't blocker.
        # More importantly: if a package we drop demands an
        # Android-incompatible version, it must NOT register as
        # a blocker because we never install it.
        report = {
            "install": [
                _pkg(
                    "bcrypt",
                    "5.0.0",
                    requires=["cryptography>=999.0.0"],  # impossible floor
                ),
            ]
        }
        # cryptography ceiling is 42.0.8; floor 999.0.0 would be a
        # blocker — but bcrypt is dropped on Android so its demands
        # are silently filtered.
        assert check.analyse(report) == []

    def test_url_ref_packages_their_demands_still_count(self, check):
        # We URL-ref kohakuvault (it IS installed on Android via
        # our wheel), so KV's own demands DO count.  This is the
        # exact path that bit us.
        report = {
            "install": [
                _pkg("kohakuvault", "0.8.5", requires=["numpy>=2.0.0"]),
            ]
        }
        blockers = check.analyse(report)
        assert len(blockers) == 1
        assert blockers[0].dep == "numpy"

    def test_extras_gated_demand_inactive_when_extra_not_used(self, check):
        # safetensors's ``numpy>=1.21.6 ; extra == 'numpy'`` is
        # extras-gated.  We don't pull safetensors[numpy], so this
        # demand is INACTIVE and must not produce a blocker even
        # though the floor (1.21.6) is below the ceiling anyway.
        report = {
            "install": [
                _pkg(
                    "safetensors",
                    "0.7.0",
                    requires=["numpy>=99.0.0 ; extra == 'numpy'"],
                ),
            ]
        }
        # 99.0.0 would obviously blow the ceiling — but the marker
        # gates the demand off.
        assert check.analyse(report) == []

    def test_extras_gated_demand_active_when_extra_is_active(self, check):
        # httpx[brotli,http2,socks] is in ANDROID_ACTIVE_EXTRAS;
        # so httpx's ``Brotli ; extra == 'brotli'`` IS active.
        # If httpx declared an impossibly-high Brotli floor under
        # that extra, that DOES become a blocker.
        report = {
            "install": [
                _pkg(
                    "httpx",
                    "0.28.0",
                    requires=["Brotli>=99.0.0 ; extra == 'brotli'"],
                ),
            ]
        }
        blockers = check.analyse(report)
        assert len(blockers) == 1
        assert blockers[0].dep == "brotli"

    def test_python_version_marker_evaluated_on_android(self, check):
        # libcst's metadata has ``pyyaml>=6.0.3 ; python_version
        # >= '3.14'``.  Chaquopy is cp313, so the marker fails →
        # demand inactive → no blocker even if 6.0.3 floor would
        # equal ceiling.  But the same metadata has a
        # ``pyyaml>=5.2 ; python_version < '3.13'`` clause which
        # ALSO fails on cp313.  So only the unmarked demand (if
        # any) would apply.
        report = {
            "install": [
                _pkg(
                    "libcst",
                    "1.4.0",
                    requires=[
                        'pyyaml>=99.0.0 ; python_version >= "3.14"',
                        'pyyaml>=99.0.0 ; python_version < "3.13"',
                    ],
                ),
            ]
        }
        assert check.analyse(report) == []


class TestAndroidMarkerEnv:
    """Sanity-check the marker env we evaluate against."""

    def test_env_says_cp313_aarch64_linux(self, check):
        env = check._android_marker_env()
        assert env["python_version"] == "3.13"
        assert env["platform_machine"] == "aarch64"
        assert env["sys_platform"] == "linux"


class TestNormalize:
    def test_name_normalisation_handles_underscores_and_dots(self, check):
        assert check._normalize_name("ruamel.yaml.clib") == "ruamel-yaml-clib"
        assert check._normalize_name("lxml_html_clean") == "lxml-html-clean"
        assert check._normalize_name("Pillow") == "pillow"


class TestBlockerDataclass:
    def test_blocker_carries_diagnostic_fields(self, check):
        from packaging.version import Version

        b = check.Blocker(
            dep="numpy",
            ceiling=Version("1.26.2"),
            floor=Version("2.0.0"),
            demander="kohakuvault",
            spec="numpy>=2.0.0",
        )
        assert b.dep == "numpy"
        assert str(b.ceiling) == "1.26.2"
        assert b.demander == "kohakuvault"
