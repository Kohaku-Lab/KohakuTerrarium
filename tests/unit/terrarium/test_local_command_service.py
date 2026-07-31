from dataclasses import dataclass

from kohakuterrarium.modules.user_command.base import UserCommandResult
from kohakuterrarium.skills.registry import Skill, SkillRegistry
from kohakuterrarium.terrarium.local_command_service import (
    LocalCommandServiceMixin,
)


class _Command:
    aliases = ["objective"]
    description = "Manage a goal"

    def __init__(self):
        self.args = None
        self.context = None

    async def execute(self, args, context):
        self.args = args
        self.context = context
        return UserCommandResult(output=f"accepted: {args}")


@dataclass
class _Agent:
    command: _Command
    skills: SkillRegistry
    session: object | None = None

    def __post_init__(self):
        self.injected = []

    def list_user_commands(self):
        return {"goal": self.command}

    async def inject_input(self, content, *, source):
        self.injected.append((content, source))


class _Service(LocalCommandServiceMixin):
    def __init__(self):
        self._engine = object()
        skills = SkillRegistry()
        skills.add(
            Skill(
                name="review",
                description="Review changes",
                body="Inspect the proposed change carefully.",
                origin="test",
            )
        )
        self.agent = _Agent(_Command(), skills)
        self.seen_creature_ids = []

    def _agent(self, creature_id):
        self.seen_creature_ids.append(creature_id)
        return self.agent


async def test_command_inventory_resolves_the_local_agent():
    service = _Service()

    result = await service.command_inventory("creature-one")

    assert result["commands"] == [
        {
            "name": "goal",
            "aliases": ["objective"],
            "description": "Manage a goal",
            "source": "runtime",
            "origin": None,
        }
    ]
    assert result["skills"] == [
        {
            "name": "review",
            "description": "Review changes",
            "source": "test",
            "enabled": True,
            "invocation_blocked": False,
        }
    ]
    assert service.seen_creature_ids == ["creature-one"]


async def test_invoke_skill_forwards_arguments_and_source():
    service = _Service()

    result = await service.invoke_skill(
        "creature-one",
        "review",
        {"args": "diff"},
        source="web:test",
    )

    assert result == {
        "skill": "review",
        "accepted": True,
        "source": "web:test",
    }
    content, source = service.agent.injected[0]
    assert "Inspect the proposed change carefully." in content
    assert "diff" in content
    assert source == "web:test"


async def test_execute_command_normalizes_args_and_forwards_trusted_context():
    service = _Service()

    result = await service.execute_command(
        "creature-one",
        "goal",
        {"args": "set X"},
        principal="user:alice",
        is_operator=True,
    )

    assert result == {
        "command": "goal",
        "output": "accepted: set X",
        "error": None,
        "success": True,
    }
    assert service.agent.command.args == "set X"
    assert service.agent.command.context.extra == {
        "principal": "user:alice",
        "is_operator": True,
        "service": service,
        "engine": service._engine,
        "creature_id": "creature-one",
    }
