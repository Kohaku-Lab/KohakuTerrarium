"""Sub-agent codegen tests."""

from kohakuterrarium.studio.editors import codegen_subagent as sa_cg

SAMPLE = '''\
"""Explore sub-agent."""

from kohakuterrarium.modules.subagent.config import SubAgentConfig

EXPLORE_PROMPT = "You are a file search specialist."

EXPLORE_CONFIG = SubAgentConfig(
    name="explore",
    description="Search and explore codebase (read-only)",
    tools=["glob", "grep", "read"],
    system_prompt=EXPLORE_PROMPT,
    can_modify=False,
    stateless=True,
)
'''


def test_render_new_compiles():
    source = sa_cg.render_new(
        {
            "name": "my_sa",
            "description": "a sub-agent",
            "tools": ["read", "write"],
            "system_prompt": "You are a helper.",
            "can_modify": False,
            "stateless": True,
        }
    )
    compile(source, "<rendered>", "exec")
    assert "SubAgentConfig(" in source
    assert '"my_sa"' in source
    assert "stateless=True" in source


def test_parse_back_extracts_fields():
    env = sa_cg.parse_back(SAMPLE)
    assert env["mode"] == "simple"
    form = env["form"]
    assert form["name"] == "explore"
    assert form["description"] == "Search and explore codebase (read-only)"
    assert form["tools"] == ["glob", "grep", "read"]
    # system_prompt resolves from EXPLORE_PROMPT binding
    assert form["system_prompt"] == "You are a file search specialist."
    assert form["can_modify"] is False
    assert form["stateless"] is True


def test_parse_back_no_call():
    env = sa_cg.parse_back("x = 1\n")
    assert env["mode"] == "raw"


def test_update_existing_rewrites_tools():
    new_src = sa_cg.update_existing(
        SAMPLE,
        {
            "name": "explore",
            "description": "Search and explore codebase (read-only)",
            "tools": ["read"],
            "system_prompt": "You are a file search specialist.",
            "can_modify": False,
            "stateless": True,
        },
        "",
    )
    compile(new_src, "<updated>", "exec")
    # Original prompt binding preserved
    assert "EXPLORE_PROMPT" in new_src
    # Tools list updated to a single element
    env = sa_cg.parse_back(new_src)
    assert env["form"]["tools"] == ["read"]


def test_update_existing_rewrites_description():
    new_src = sa_cg.update_existing(
        SAMPLE,
        {
            "name": "explore",
            "description": "NEW DESC",
            "tools": ["glob", "grep", "read"],
            "system_prompt": "You are a file search specialist.",
            "can_modify": False,
            "stateless": True,
        },
        "",
    )
    assert "NEW DESC" in new_src


def test_roundtrip_form_preserves_all_fields():
    form_in = {
        "name": "alpha",
        "description": "desc",
        "tools": ["a", "b"],
        "system_prompt": "you are alpha",
        "can_modify": True,
        "stateless": False,
        "interactive": True,
    }
    source = sa_cg.render_new(form_in)
    env = sa_cg.parse_back(source)
    out = env["form"]
    assert out["name"] == "alpha"
    assert out["description"] == "desc"
    assert out["tools"] == ["a", "b"]
    assert out["system_prompt"] == "you are alpha"
    assert out["can_modify"] is True
    assert out["stateless"] is False
    assert out["interactive"] is True
