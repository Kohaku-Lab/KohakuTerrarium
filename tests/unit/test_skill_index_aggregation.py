"""Unit tests for the ``## Skills`` section in the aggregated system prompt."""

from kohakuterrarium.prompt.aggregator import aggregate_system_prompt
from kohakuterrarium.skills import Skill, SkillRegistry, build_skill_index


def _skill(name: str, **kw) -> Skill:
    return Skill(
        name=name,
        description=kw.pop("description", f"{name} description"),
        body=kw.pop("body", ""),
        origin=kw.pop("origin", "user"),
        **kw,
    )


def test_skill_index_lists_enabled_skills():
    reg = SkillRegistry()
    reg.add(_skill("a"))
    reg.add(_skill("b"))
    out = build_skill_index(reg)
    assert "## Skills" in out
    assert "`a`" in out
    assert "`b`" in out


def test_skill_index_empty_registry_returns_empty_string():
    assert build_skill_index(SkillRegistry()) == ""


def test_skill_index_skips_disabled_skills():
    reg = SkillRegistry()
    reg.add(_skill("a", enabled=False))
    reg.add(_skill("b", enabled=True))
    out = build_skill_index(reg)
    assert "`a`" not in out
    assert "`b`" in out


def test_skill_index_hides_disable_model_invocation():
    reg = SkillRegistry()
    reg.add(_skill("shown"))
    reg.add(_skill("hidden", disable_model_invocation=True))
    out = build_skill_index(reg)
    assert "`shown`" in out
    assert "`hidden`" not in out


def test_skill_index_budget_truncates_overflow():
    reg = SkillRegistry()
    # ~100 skills with long descriptions; 4KB default budget can't fit them all.
    for i in range(100):
        reg.add(_skill(f"s{i:03d}", description="x" * 80))
    out = build_skill_index(reg, budget_bytes=500)
    # Some skills included, some omitted message.
    assert "omitted" in out
    assert "s000" in out


def test_skill_index_entries_sorted_alphabetically():
    reg = SkillRegistry()
    reg.add(_skill("charlie"))
    reg.add(_skill("alpha"))
    reg.add(_skill("bravo"))
    out = build_skill_index(reg)
    # Find positions of each name in the output.
    i_a = out.index("alpha")
    i_b = out.index("bravo")
    i_c = out.index("charlie")
    assert i_a < i_b < i_c


def test_aggregator_includes_skill_index_section():
    reg = SkillRegistry()
    reg.add(_skill("pdf", description="merge pdfs"))
    prompt = aggregate_system_prompt(
        "You are a helpful agent.",
        registry=None,
        include_tools=False,
        include_hints=False,
        skill_registry=reg,
    )
    assert "## Skills" in prompt
    assert "pdf" in prompt


def test_aggregator_omits_skill_index_when_no_registry():
    prompt = aggregate_system_prompt(
        "You are a helpful agent.",
        registry=None,
        include_tools=False,
        include_hints=False,
        skill_registry=None,
    )
    assert "## Skills" not in prompt


def test_aggregator_omits_skill_index_when_registry_empty():
    prompt = aggregate_system_prompt(
        "You are a helpful agent.",
        registry=None,
        include_tools=False,
        include_hints=False,
        skill_registry=SkillRegistry(),
    )
    assert "## Skills" not in prompt
