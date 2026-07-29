"""Tests for the live command and skill inventory boundary."""

import json
from dataclasses import dataclass
from types import SimpleNamespace

from kohakuterrarium.terrarium.command_inventory import build_command_inventory


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
