"""Unit tests for ``llm/presets/packages.py`` — package preset folding."""

from kohakuterrarium.llm.presets.packages import merge_package_presets


class TestMergePackagePresets:
    def test_keeps_valid_entries_without_name_or_provider_keys(self):
        packages = [
            {
                "name": "p",
                "llm_presets": [
                    {"name": "m", "provider": "x", "model": "real", "max_context": 9},
                    {"name": "no-provider", "model": "y"},
                    {"provider": "x", "model": "no-name"},
                    "junk",
                ],
            }
        ]
        assert merge_package_presets(packages, set()) == {
            ("x", "m"): {"model": "real", "max_context": 9}
        }

    def test_builtins_and_earlier_packages_win(self):
        packages = [
            {
                "name": "a",
                "llm_presets": [{"name": "m", "provider": "x", "model": "a"}],
            },
            {
                "name": "b",
                "llm_presets": [{"name": "m", "provider": "x", "model": "b"}],
            },
            {
                "name": "c",
                "llm_presets": [{"name": "k", "provider": "x", "model": "c"}],
            },
        ]
        merged = merge_package_presets(packages, {("x", "k")})
        assert merged == {("x", "m"): {"model": "a"}}

    def test_package_without_presets_contributes_nothing(self):
        assert merge_package_presets([{"name": "p", "llm_presets": None}], set()) == {}
