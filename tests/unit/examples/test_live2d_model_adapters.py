"""Adapter bindings are explicit, validated and compatible with known exports."""

import pytest

from examples.packages.live2d_pet.kt_live2d.model_adapters import (
    IMPORT_ADAPTERS,
    resolve_adapter,
)


def test_resolves_declared_and_legacy_bindings_without_user_selection():
    for manifest in (
        {"KohakuTerrarium": {"adapter": "deepseek"}},
        {"Demo": {"profile": "deepseek"}},
        {"KohakuTerrarium": {"adapter": "deepseek"}, "Demo": {"profile": "deepseek"}},
    ):
        assert resolve_adapter(manifest, set(), None) == "deepseek"
    files = set(IMPORT_ADAPTERS["deepseek"].legacy_files)
    assert resolve_adapter({}, files, None) == "deepseek"
    files.remove("Animation/IDLE.motion3.json")
    with pytest.raises(ValueError, match="无法识别"):
        resolve_adapter({}, files, None)
    with pytest.raises(ValueError, match="不匹配"):
        resolve_adapter({"Demo": {"profile": "deepseek"}}, set(), "different")


@pytest.mark.parametrize(
    "manifest",
    [
        {"KohakuTerrarium": None},
        {"KohakuTerrarium": {}},
        {"KohakuTerrarium": {"adapter": ["deepseek"]}},
        {"KohakuTerrarium": {"adapter": ""}},
        {"KohakuTerrarium": {"adapter": "../deepseek"}},
        {"Demo": {"profile": []}},
        {"KohakuTerrarium": {"adapter": "deepseek"}, "Demo": {"profile": "other"}},
    ],
)
def test_invalid_declarations_never_fall_back_to_file_matching(manifest):
    with pytest.raises(ValueError):
        resolve_adapter(manifest, set(IMPORT_ADAPTERS["deepseek"].legacy_files), None)


def test_dragon_resolves_both_original_and_declared_imports():
    assert (
        resolve_adapter({"KohakuTerrarium": {"adapter": "dragon"}}, set(), None)
        == "dragon"
    )
    files = set(IMPORT_ADAPTERS["dragon"].legacy_files)
    assert resolve_adapter({}, files, None) == "dragon"
    with pytest.raises(ValueError, match="无法识别"):
        resolve_adapter({}, files | set(IMPORT_ADAPTERS["deepseek"].legacy_files), None)
