"""Tests for the live command and skill inventory boundary."""

import json
from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from kohakuterrarium.terrarium.command_inventory import (
    DisabledSkillError,
    UnknownInvocationError,
    build_command_inventory,
    resolve_explicit_invocation,
)


@dataclass
class _FakeCommand:
    description: str = "Run the command"
    aliases: tuple[str, ...] = ()


@dataclass
class _FakeSkill:
    name: str
    description: str = "Use the skill"
    origin: str = "project"
    enabled: bool = True
    invocation_blocked: bool = False


class _FakeSkillRegistry:
    def __init__(self, *skills: _FakeSkill) -> None:
        self._skills = {skill.name: skill for skill in skills}

    def all(self) -> list[_FakeSkill]:
        return list(self._skills.values())

    def get(self, name: str) -> _FakeSkill | None:
        return self._skills.get(name)


class _FakeAgent:
    def __init__(
        self,
        commands: dict[str, _FakeCommand] | None = None,
        skills: _FakeSkillRegistry | None = None,
    ) -> None:
        self._commands = commands or {}
        self.skills = skills or _FakeSkillRegistry()
        self._user_command_provenance = {
            name: SimpleNamespace(source="plugin", origin="demo")
            for name in self._commands
        }

    def list_user_commands(self) -> dict[str, _FakeCommand]:
        return dict(self._commands)


def test_inventory_is_serializable_and_describes_commands_aliases_and_skills():
    agent = _FakeAgent(
        commands={"status": _FakeCommand(aliases=("info",))},
        skills=_FakeSkillRegistry(_FakeSkill("review")),
    )

    inventory = build_command_inventory(agent).to_dict()

    assert inventory == {
        "commands": [
            {
                "name": "status",
                "aliases": ["info"],
                "description": "Run the command",
                "source": "plugin",
                "origin": "demo",
            }
        ],
        "skills": [
            {
                "name": "review",
                "description": "Use the skill",
                "source": "project",
                "enabled": True,
                "invocation_blocked": False,
            }
        ],
    }
    json.dumps(inventory)


@pytest.mark.parametrize("requested", ["review", "r"])
def test_command_name_or_alias_wins_over_same_named_skill(requested):
    commands = {"review": _FakeCommand(aliases=("r",))}
    skills = _FakeSkillRegistry(_FakeSkill(requested))

    result = resolve_explicit_invocation(_FakeAgent(commands, skills), requested)

    assert result.kind == "command"
    assert result.name == "review"
    assert result.requested_name == requested


def test_disabled_skill_cannot_be_explicitly_invoked():
    agent = _FakeAgent(skills=_FakeSkillRegistry(_FakeSkill("lint", enabled=False)))

    with pytest.raises(DisabledSkillError, match="disabled") as error:
        resolve_explicit_invocation(agent, "lint")

    assert error.value.name == "lint"


def test_invocation_blocked_does_not_block_explicit_user_invocation():
    skill = _FakeSkill("manual", invocation_blocked=True)

    result = resolve_explicit_invocation(
        _FakeAgent(skills=_FakeSkillRegistry(skill)), "manual"
    )

    assert result.kind == "skill"
    assert result.name == "manual"


def test_unknown_name_raises_typed_error():
    with pytest.raises(UnknownInvocationError, match="/missing") as error:
        resolve_explicit_invocation(_FakeAgent(), "missing")

    assert error.value.name == "missing"


def test_mixed_case_skill_name_resolves_from_slash_normalization():
    skill = _FakeSkill("CodeReview")
    agent = _FakeAgent(skills=_FakeSkillRegistry(skill))

    invocation = resolve_explicit_invocation(agent, "codereview")

    assert invocation.kind == "skill"
    assert invocation.name == "CodeReview"
    assert invocation.value is skill


def test_exact_skill_case_wins_when_registry_has_casefold_variants():
    mixed = _FakeSkill("CodeReview")
    lower = _FakeSkill("codereview")
    agent = _FakeAgent(skills=_FakeSkillRegistry(mixed, lower))

    invocation = resolve_explicit_invocation(agent, "CodeReview")

    assert invocation.value is mixed
