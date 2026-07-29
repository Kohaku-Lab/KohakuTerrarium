"""Tests for per-creature command inventory routes."""

from unittest.mock import AsyncMock

import pytest

from kohakuterrarium.api.routes.sessions_v2 import creatures_command


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
