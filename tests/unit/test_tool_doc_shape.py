"""Structural gate over ``builtin_skills/`` — the tool documentation contract.

Three tiers of progressive disclosure, enforced so they cannot decay back:

- **Tier 1** — frontmatter ``description``. Always loaded, so it is capped and
  must carry a "Not for" clause wherever a confusable sibling exists.
- **Tier 2** — the body above ``## Reference``. Inlined only under
  ``doc_mode: full``, so it is capped and its section vocabulary is closed.
- **Tier 3** — from ``## Reference`` on. Reachable only through ``info``.

The two assertions that review alone cannot make are the drift checks: a class
description that no longer matches its file, and a doc file with no tool (or a
tool with no doc file).
"""

import pytest

from kohakuterrarium.builtin_skills import (
    BUILTIN_SKILLS_DIR,
    list_builtin_subagent_docs,
    list_builtin_tool_docs,
    split_doc_tiers,
)
from kohakuterrarium.builtins.tool_catalog import get_builtin_tool, list_builtin_tools
from kohakuterrarium.llm.tool_schemas import _BUILTIN_SCHEMAS
from kohakuterrarium.skill_docs import load_skill_doc

MAX_DESCRIPTION = 160
MAX_USAGE = 1400
MAX_REFERENCE = 4000

ALLOWED_TOOL_SECTIONS = {"Arguments", "Behavior", "Limits", "Output"}
ALLOWED_SUBAGENT_SECTIONS = {"Task shape", "Returns", "Limits"}

# Tools whose names invite the wrong pick without an explicit exclusion.
CONFUSABLE = {
    "read",
    "write",
    "multi_edit",
    "edit",
    "glob",
    "grep",
    "tree",
    "bash",
    "python",
    "ask_user",
    "search_memory",
    "web_fetch",
    "web_search",
    "info",
    "skill",
    "explore",
    "plan",
    "worker",
    "critic",
}

TOOL_DOCS = sorted(list_builtin_tool_docs())
SUBAGENT_DOCS = sorted(list_builtin_subagent_docs())


def _doc(kind, name):
    return load_skill_doc(BUILTIN_SKILLS_DIR / kind / f"{name}.md")


def _sections(body):
    return [line[3:].strip() for line in body.splitlines() if line.startswith("## ")]


@pytest.mark.parametrize("name", TOOL_DOCS + SUBAGENT_DOCS)
def test_frontmatter_present_and_named_after_the_file(name):
    kind = "tools" if name in TOOL_DOCS else "subagents"
    doc = _doc(kind, name)
    assert doc is not None, f"{kind}/{name}.md failed to parse"
    assert doc.name == name
    assert doc.description.strip()


@pytest.mark.parametrize("name", TOOL_DOCS + SUBAGENT_DOCS)
def test_description_is_capped(name):
    kind = "tools" if name in TOOL_DOCS else "subagents"
    desc = _doc(kind, name).description
    assert len(desc) <= MAX_DESCRIPTION, f"{name}: {len(desc)} chars"


@pytest.mark.parametrize("name", TOOL_DOCS + SUBAGENT_DOCS)
def test_description_carries_no_mode_instructions(name):
    # "Use info(x) first" describes a doc mode, not the tool. It used to be
    # baked into edit / multi_edit / notebook_edit descriptions.
    kind = "tools" if name in TOOL_DOCS else "subagents"
    desc = _doc(kind, name).description
    for banned in ("info(", "##info##", "tool_format", "run_in_background"):
        assert banned not in desc, f"{name} description mentions {banned!r}"


@pytest.mark.parametrize("name", sorted(CONFUSABLE))
def test_confusable_tools_say_what_they_are_not_for(name):
    kind = "tools" if name in TOOL_DOCS else "subagents"
    if name not in TOOL_DOCS + SUBAGENT_DOCS:
        pytest.skip(f"{name} has no packaged doc")
    desc = _doc(kind, name).description
    assert "Not for" in desc, f"{name} needs an exclusion clause"


@pytest.mark.parametrize("name", TOOL_DOCS + SUBAGENT_DOCS)
def test_body_carries_no_fictional_call_syntax(name):
    # 19 of these files documented `tool call: name(...)`, which matches no
    # real format, and 8 printed `@@arg` as an argument *type*.
    kind = "tools" if name in TOOL_DOCS else "subagents"
    body = _doc(kind, name).content
    assert "tool call:" not in body, f"{name} carries the fictional call syntax"
    for line in body.splitlines():
        if line.startswith("|"):
            assert "@@" not in line, f"{name} leaks a parser token as a type"


@pytest.mark.parametrize("name", TOOL_DOCS + SUBAGENT_DOCS)
def test_tiers_are_within_budget(name):
    kind = "tools" if name in TOOL_DOCS else "subagents"
    usage, reference = split_doc_tiers(_doc(kind, name).content)
    assert len(usage) <= MAX_USAGE, f"{name} usage tier is {len(usage)} chars"
    assert len(reference) <= MAX_REFERENCE, f"{name} reference is {len(reference)}"


@pytest.mark.parametrize("name", TOOL_DOCS)
def test_tool_usage_sections_are_from_the_closed_vocabulary(name):
    usage, _ = split_doc_tiers(_doc("tools", name).content)
    unknown = set(_sections(usage)) - ALLOWED_TOOL_SECTIONS
    assert not unknown, f"{name} has non-contract sections: {sorted(unknown)}"


@pytest.mark.parametrize("name", SUBAGENT_DOCS)
def test_subagent_usage_sections_are_from_the_closed_vocabulary(name):
    usage, _ = split_doc_tiers(_doc("subagents", name).content)
    unknown = set(_sections(usage)) - ALLOWED_SUBAGENT_SECTIONS
    assert not unknown, f"{name} has non-contract sections: {sorted(unknown)}"


@pytest.mark.parametrize("name", TOOL_DOCS)
def test_class_description_matches_the_doc_file(name):
    # The drift review cannot catch: the model reads the class description in
    # the schema and the file body through ``info``; they must agree.
    tool = get_builtin_tool(name)
    if tool is None:
        pytest.skip(f"{name} is not a registerable builtin")
    assert tool.description == _doc("tools", name).description


def test_every_schema_has_a_doc_and_every_doc_has_a_tool():
    documented = set(TOOL_DOCS)
    # The catalog fills lazily via deferred loaders, so force the terrarium
    # tools in before listing or the answer depends on test order.
    get_builtin_tool("group_status")
    registered = set(list_builtin_tools())
    schema_backed = set(_BUILTIN_SCHEMAS) & registered

    missing_docs = sorted(schema_backed - documented)
    assert not missing_docs, f"registered tools with no doc file: {missing_docs}"

    orphan_docs = sorted(documented - registered)
    assert not orphan_docs, f"doc files with no registered tool: {orphan_docs}"
