"""Unit tests for ``llm/preset_aliases.py`` — canonical-name + alias tables.

Behavior-first: assert the invariants the docstring promises — every
``_CANONICAL_NAMES`` value actually strips the disambiguation suffix,
every ALIAS target is a well-formed ``(provider, name)`` pair, and the
aliases resolve against the real preset catalogue (no dangling targets).
"""

from kohakuterrarium.llm.preset_aliases import ALIASES, _CANONICAL_NAMES
from kohakuterrarium.llm.presets import PRESETS, get_all_presets


class TestCanonicalNames:
    def test_every_canonical_value_is_a_nonempty_bare_name(self):
        for key, canonical in _CANONICAL_NAMES.items():
            assert canonical, f"{key} maps to empty canonical name"
            # canonical names drop the legacy provider-disambiguation
            # suffixes (``-api`` / ``-or``). ``-codex`` is part of a real
            # model name (gpt-5.3-codex), not a disambiguation suffix.
            assert not canonical.endswith(
                ("-api", "-or", "-direct")
            ), f"{key} -> {canonical} still has a disambiguation suffix"

    def test_suffixed_keys_map_to_their_unsuffixed_form(self):
        # the table only lists entries whose canonical name differs from key
        for key, canonical in _CANONICAL_NAMES.items():
            assert key != canonical
            # the canonical name is the key with its disambiguation suffix removed
            assert key.startswith(canonical) or canonical in key


class TestAliases:
    def test_every_alias_target_is_provider_name_pair(self):
        for alias, target in ALIASES.items():
            assert (
                isinstance(target, tuple) and len(target) == 2
            ), f"{alias} target malformed: {target}"
            provider, name = target
            assert provider and name, f"{alias} -> {target} has empty component"

    def test_aliases_resolve_to_real_presets(self):
        # get_all_presets() is keyed by (provider, name) tuples
        catalogue = get_all_presets()
        for alias, target in ALIASES.items():
            assert (
                target in catalogue
            ), f"{alias} -> {target} not found in the preset catalogue"

    def test_friendly_short_names_present(self):
        # the docstring promises short friendly names for frequent picks
        assert ALIASES["opus"][0] == "anthropic"
        assert ALIASES["gpt5"] == ("codex", "gpt-5.4")
        assert ALIASES["kimi-code"] == ("kimi-code", "kimi-for-coding")
        assert ALIASES["glm-code"] == ("glm-coding", "glm-5.1")

    def test_canonical_name_keys_appear_in_flat_presets(self):
        # legacy keys in _CANONICAL_NAMES come from the flat PRESETS dict
        for key in _CANONICAL_NAMES:
            assert key in PRESETS, f"_CANONICAL_NAMES key '{key}' not in PRESETS"
