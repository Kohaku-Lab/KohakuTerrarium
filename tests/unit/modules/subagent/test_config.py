"""Unit tests for :mod:`kohakuterrarium.modules.subagent.config`.

Behavior-first: SubAgentConfig.from_dict enum coercion + unknown-field
capture, load_prompt resolution order, to_dict round-trip, SubAgentInfo
derivation + prompt line formatting.
"""

from kohakuterrarium.modules.subagent.config import (
    ContextUpdateMode,
    OutputTarget,
    SubAgentConfig,
    SubAgentInfo,
)


class TestFromDict:
    def test_minimal_dict_uses_defaults(self):
        cfg = SubAgentConfig.from_dict({"name": "explore"})
        assert cfg.name == "explore"
        assert cfg.stateless is True
        assert cfg.output_to is OutputTarget.CONTROLLER
        assert cfg.context_mode is ContextUpdateMode.INTERRUPT_RESTART

    def test_string_enums_coerced(self):
        cfg = SubAgentConfig.from_dict(
            {
                "name": "writer",
                "output_to": "external",
                "context_mode": "queue_append",
            }
        )
        assert cfg.output_to is OutputTarget.EXTERNAL
        assert cfg.context_mode is ContextUpdateMode.QUEUE_APPEND

    def test_modifying_tools_list_coerced_to_set(self):
        cfg = SubAgentConfig.from_dict(
            {"name": "editor", "modifying_tools": ["write", "edit", "write"]}
        )
        assert cfg.modifying_tools == {"write", "edit"}

    def test_unknown_fields_captured_into_extra(self):
        # Contract: unrecognized keys are not dropped — they land in extra.
        cfg = SubAgentConfig.from_dict({"name": "x", "custom_knob": 7, "another": "v"})
        assert cfg.extra["custom_knob"] == 7
        assert cfg.extra["another"] == "v"

    def test_explicit_extra_merged_with_unknown_fields(self):
        cfg = SubAgentConfig.from_dict({"name": "x", "extra": {"a": 1}, "unknown": 2})
        assert cfg.extra == {"a": 1, "unknown": 2}

    def test_from_dict_does_not_mutate_input(self):
        # Regression guard for B-modules-2 (fixed): from_dict now makes
        # the defensive `data = dict(data)` copy FIRST, before any enum
        # coercion, so the caller's dict is never touched.
        # Contract: from_dict is a constructor, not a mutator — the caller's
        # dict must be untouched.
        data = {"name": "x", "output_to": "external"}
        SubAgentConfig.from_dict(data)
        assert data["output_to"] == "external"


class TestLoadPrompt:
    def test_inline_system_prompt_is_full_override(self, tmp_path):
        cfg = SubAgentConfig(name="x", system_prompt="BASE", extra_prompt="EXTRA")
        # system_prompt set → full override; extra_prompt is NOT appended.
        assert cfg.load_prompt(tmp_path) == "BASE"

    def test_prompt_file_loaded_when_no_inline(self, tmp_path):
        (tmp_path / "sys.md").write_text("FROM FILE", encoding="utf-8")
        cfg = SubAgentConfig(name="x", prompt_file="sys.md")
        assert cfg.load_prompt(tmp_path) == "FROM FILE"

    def test_default_prompt_when_nothing_provided(self):
        cfg = SubAgentConfig(name="critic")
        assert cfg.load_prompt(None) == "You are a critic sub-agent."

    def test_extra_prompt_appended_to_base(self):
        cfg = SubAgentConfig(name="x", extra_prompt="be terse")
        prompt = cfg.load_prompt(None)
        assert prompt.startswith("You are a x sub-agent.")
        assert "## Additional Instructions" in prompt
        assert "be terse" in prompt

    def test_extra_prompt_file_appended(self, tmp_path):
        (tmp_path / "extra.md").write_text("extra from file", encoding="utf-8")
        cfg = SubAgentConfig(name="x", extra_prompt_file="extra.md")
        prompt = cfg.load_prompt(tmp_path)
        assert "extra from file" in prompt

    def test_memory_path_injects_path_context(self, tmp_path):
        cfg = SubAgentConfig(name="x", memory_path="mem")
        prompt = cfg.load_prompt(tmp_path)
        assert "## Path Context" in prompt
        assert str(tmp_path / "mem") in prompt

    def test_missing_prompt_file_falls_back_to_default(self, tmp_path):
        # Regression guard for B-modules-3 (fixed): the prompt_file
        # branch now falls back to the base default prompt when the
        # configured file does not exist, instead of yielding ''.
        # Contract: a missing prompt_file must NOT yield an empty system
        # prompt — it should fall back to the base default prompt.
        cfg = SubAgentConfig(name="x", prompt_file="does_not_exist.md")
        assert cfg.load_prompt(tmp_path) == "You are a x sub-agent."


class TestToDict:
    def test_enums_serialized_to_values(self):
        cfg = SubAgentConfig(
            name="x",
            output_to=OutputTarget.EXTERNAL,
            context_mode=ContextUpdateMode.FLUSH_REPLACE,
        )
        data = cfg.to_dict()
        assert data["output_to"] == "external"
        assert data["context_mode"] == "flush_replace"

    def test_modifying_tools_set_serialized_as_sorted_list(self):
        cfg = SubAgentConfig(name="x", modifying_tools={"write", "edit"})
        assert cfg.to_dict()["modifying_tools"] == ["edit", "write"]

    def test_round_trip_through_from_dict(self):
        original = SubAgentConfig(
            name="explore",
            tools=["read", "grep"],
            max_turns=5,
            output_to=OutputTarget.EXTERNAL,
        )
        clone = SubAgentConfig.from_dict(original.to_dict())
        assert clone.name == "explore"
        assert clone.tools == ["read", "grep"]
        assert clone.max_turns == 5
        assert clone.output_to is OutputTarget.EXTERNAL


class TestSubAgentInfo:
    def test_from_config_copies_identity(self):
        cfg = SubAgentConfig(
            name="explore",
            description="finds code",
            can_modify=True,
            interactive=False,
        )
        info = SubAgentInfo.from_config(cfg)
        assert info.name == "explore"
        assert info.description == "finds code"
        assert info.can_modify is True

    def test_prompt_line_marks_can_modify(self):
        info = SubAgentInfo(name="editor", description="edits", can_modify=True)
        assert info.to_prompt_line() == "- editor: edits [can modify files]"

    def test_prompt_line_marks_interactive(self):
        info = SubAgentInfo(name="chat", description="talks", interactive=True)
        assert info.to_prompt_line() == "- chat: talks [interactive]"

    def test_prompt_line_plain_when_neither(self):
        info = SubAgentInfo(name="plan", description="plans")
        assert info.to_prompt_line() == "- plan: plans"
