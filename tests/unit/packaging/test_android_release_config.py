"""Consistency tests for Android release metadata and policy tables."""

import importlib.util
import sys
import tomllib
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class TestAndroidReleaseConfig:
    def test_version_code_matches_project_version(self):
        config = tomllib.loads((_REPO_ROOT / "pyproject.toml").read_text())
        major, minor, patch = map(int, config["project"]["version"].split("."))
        expected = f"{major}{minor:02}{patch:02}000"

        assert (
            config["tool"]["briefcase"]["app"]["kohakuterrarium"]["android"][
                "version_code"
            ]
            == expected
        )

    def test_dependency_policy_tables_stay_aligned(self):
        postcreate = _load_module(
            "android_postcreate",
            _REPO_ROOT / "packaging" / "android" / "postcreate.py",
        )
        ceiling = _load_module(
            "android_check_chaquopy_ceiling",
            _REPO_ROOT / "packaging" / "android" / "check_chaquopy_ceiling.py",
        )

        assert set(postcreate._ANDROID_URL_REFS) == set(ceiling.URL_REF_PACKAGES)
        postcreate_drops = {
            name.replace("-", "_") for name in postcreate._ANDROID_DROP_PACKAGES
        }
        ceiling_drops = {name.replace("-", "_") for name in ceiling.DROPPED_PACKAGES}
        assert postcreate_drops <= ceiling_drops
