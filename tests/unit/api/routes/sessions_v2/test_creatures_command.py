"""Tests for per-creature command inventory and skill routes."""

from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from kohakuterrarium.api.routes.sessions_v2 import creatures_command
from kohakuterrarium.api.schemas import SlashCommand
from kohakuterrarium.terrarium.command_inventory import DisabledSkillError


@pytest.fixture
def service(monkeypatch):
    stub = AsyncMock()
    monkeypatch.setattr(
        creatures_command,
        "resolve_creature_id",
        AsyncMock(return_value="creature-1"),
    )
    return stub


@pytest.mark.asyncio
async def test_command_inventory_delegates_to_service(service):
    service.command_inventory.return_value = {"commands": [], "skills": []}

    result = await creatures_command.get_creature_command_inventory(
        "session-1",
        "root",
        service,
    )

    assert result == {"commands": [], "skills": []}
    service.command_inventory.assert_awaited_once_with("creature-1")


@pytest.mark.asyncio
async def test_skill_input_uses_explicit_web_source(service):
    service.invoke_skill.return_value = {
        "skill": "review",
        "accepted": True,
        "source": "web:skill",
    }

    result = await creatures_command.invoke_creature_skill(
        "session-1",
        "root",
        SlashCommand(command="review", args="diff"),
        service,
    )

    assert result["accepted"] is True
    service.invoke_skill.assert_awaited_once_with(
        "creature-1",
        "review",
        "diff",
        source="web:skill",
    )


@pytest.mark.asyncio
async def test_skill_input_rejects_disabled_skill(service):
    service.invoke_skill.side_effect = DisabledSkillError(
        "review",
        "Skill is disabled: /review",
    )

    with pytest.raises(HTTPException) as exc_info:
        await creatures_command.invoke_creature_skill(
            "session-1",
            "root",
            SlashCommand(command="review", args=""),
            service,
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Skill is disabled: /review"
