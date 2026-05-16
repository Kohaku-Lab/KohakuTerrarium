"""Unit tests for :mod:`kohakuterrarium.core.config_merge`."""

from kohakuterrarium.core.config_merge import merge_configs

# ── scalars / dicts ──────────────────────────────────────────────


class TestScalarOverride:
    def test_child_overrides(self):
        out = merge_configs({"x": 1}, {"x": 2})
        assert out["x"] == 2

    def test_child_none_does_not_override(self):
        out = merge_configs({"x": 1}, {"x": None})
        assert out["x"] == 1

    def test_child_adds_new_key(self):
        out = merge_configs({"x": 1}, {"y": 2})
        assert out == {"x": 1, "y": 2}

    def test_meta_fields_not_propagated(self):
        out = merge_configs(
            {"x": 1},
            {"base_config": "p.yaml", "no_inherit": [], "prompt_mode": "concat"},
        )
        assert "base_config" not in out
        assert "no_inherit" not in out
        assert "prompt_mode" not in out


class TestDictShallowMerge:
    def test_keys_merge(self):
        out = merge_configs(
            {"controller": {"model": "a", "temp": 0.5}},
            {"controller": {"model": "b", "max_tokens": 100}},
        )
        assert out["controller"] == {"model": "b", "temp": 0.5, "max_tokens": 100}

    def test_child_only(self):
        out = merge_configs({}, {"controller": {"x": 1}})
        assert out["controller"] == {"x": 1}


# ── identity-keyed lists ─────────────────────────────────────────


class TestIdentityList:
    def test_tools_child_wins_in_place(self):
        out = merge_configs(
            {"tools": [{"name": "a", "v": 1}, {"name": "b", "v": 2}]},
            {"tools": [{"name": "a", "v": 99}]},
        )
        # Same order: a (overridden), then b.
        assert out["tools"] == [
            {"name": "a", "v": 99},
            {"name": "b", "v": 2},
        ]

    def test_child_new_appended(self):
        out = merge_configs(
            {"tools": [{"name": "a"}]},
            {"tools": [{"name": "b"}]},
        )
        assert out["tools"] == [{"name": "a"}, {"name": "b"}]

    def test_items_without_identity_concat(self):
        out = merge_configs(
            {"triggers": [{"name": "x"}]},
            {"triggers": [{}, {}]},  # no name → append
        )
        assert len(out["triggers"]) == 3

    def test_base_missing_identity_passes_through(self):
        out = merge_configs(
            {"triggers": [{}, {"name": "n"}]},
            {"triggers": [{"name": "n", "v": 1}]},
        )
        # ``{}`` stays at index 0; ``n`` overwritten in place.
        assert out["triggers"][0] == {}
        assert out["triggers"][1] == {"name": "n", "v": 1}

    def test_non_dict_in_list_appended(self):
        out = merge_configs(
            {"tools": [{"name": "a"}]},
            {"tools": ["scalar_tool"]},
        )
        assert "scalar_tool" in out["tools"]


# ── no_inherit ────────────────────────────────────────────────────


class TestNoInherit:
    def test_drops_inherited_field(self):
        out = merge_configs(
            {"x": 1, "y": 2},
            {"no_inherit": ["x"]},
        )
        assert "x" not in out
        assert out["y"] == 2

    def test_no_inherit_with_dict_field(self):
        out = merge_configs(
            {"controller": {"model": "a"}},
            {"no_inherit": ["controller"], "controller": {"model": "b"}},
        )
        # Base controller dropped; child's value is the result.
        assert out["controller"] == {"model": "b"}

    def test_no_inherit_with_identity_list(self):
        out = merge_configs(
            {"tools": [{"name": "a"}]},
            {"no_inherit": ["tools"], "tools": [{"name": "b"}]},
        )
        # Base wiped; only child tools remain.
        assert out["tools"] == [{"name": "b"}]


# ── prompt_mode ───────────────────────────────────────────────────


class TestPromptMode:
    def test_default_concat_keeps_chain(self):
        out = merge_configs(
            {"_prompt_chain": ["base.md"], "system_prompt": "base"},
            {"system_prompt": "child"},
        )
        assert out["_prompt_chain"] == ["base.md"]
        assert out["_inline_system_prompt"] == "child"
        assert out["system_prompt"] == "child"

    def test_replace_drops_chain_and_inline(self):
        out = merge_configs(
            {
                "_prompt_chain": ["base.md"],
                "_inline_system_prompt": "base inline",
                "system_prompt_file": "base.md",
                "system_prompt": "base inline",
            },
            {"prompt_mode": "replace", "system_prompt": "child"},
        )
        assert "_prompt_chain" not in out
        # Child's inline replaces base inline.
        assert out["_inline_system_prompt"] == "child"
        # Base's file/inline opt-out via prompt_mode=replace.
        assert "system_prompt_file" not in out

    def test_replace_without_child_prompt_clears_inline(self):
        out = merge_configs(
            {"system_prompt": "base", "_inline_system_prompt": "base"},
            {"prompt_mode": "replace"},
        )
        assert "system_prompt" not in out
        assert "_inline_system_prompt" not in out

    def test_no_inherit_system_prompt_file_drops_chain(self):
        out = merge_configs(
            {"_prompt_chain": ["a.md"], "system_prompt_file": "a.md"},
            {"no_inherit": ["system_prompt_file"], "system_prompt_file": "b.md"},
        )
        # _prompt_chain dropped because system_prompt_file was opted out.
        assert "_prompt_chain" not in out
        assert out["system_prompt_file"] == "b.md"

    def test_no_inherit_system_prompt_drops_inline(self):
        out = merge_configs(
            {"_inline_system_prompt": "base"},
            {"no_inherit": ["system_prompt"]},
        )
        assert "_inline_system_prompt" not in out


# ── purity ────────────────────────────────────────────────────────


class TestPurity:
    def test_inputs_not_mutated(self):
        base = {"tools": [{"name": "a"}]}
        child = {"tools": [{"name": "a", "v": 1}]}
        snapshot_b = {"tools": [{"name": "a"}]}
        snapshot_c = {"tools": [{"name": "a", "v": 1}]}
        merge_configs(base, child)
        assert base == snapshot_b
        assert child == snapshot_c
