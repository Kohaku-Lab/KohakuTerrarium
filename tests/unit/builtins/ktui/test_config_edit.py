"""The terminal catalog entry saves only explicitly selected models."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

from kohakuterrarium.builtins.ktui.config_edit import TRANSPORTS, ConfigEdit, model_rows
from kohakuterrarium.llm.profiles.resolve import load_presets, save_profile
from kohakuterrarium.llm.profiles.types import LLMPreset
from kohakuterrarium.mcp.transports import MCP_TRANSPORT_CHOICES


@pytest.mark.parametrize("confirm", [False, True])
async def test_catalog_action_respects_confirmation_and_preserves_existing(
    monkeypatch, confirm
):
    async def upstream(self, request):
        assert request.url.path == "/inference/go/v1/models"
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "id": "catalog-model",
                        "name": "Pretty name",
                        "limit": {"context": 64000},
                    }
                ]
            },
        )

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", upstream)
    row = next(r for r in model_rows() if r["label"] == "Browse provider models")
    shell = SimpleNamespace(
        panel=AsyncMock(side_effect=["opencode-go", "catalog-model", confirm]),
        app=Mock(),
    )
    edit = ConfigEdit(shell)
    await edit.act(row["value"].removeprefix("cfg:"))
    model_item = shell.panel.await_args_list[1].args[0].items[0]
    assert (
        "catalog-model" in model_item["label"] and "Pretty name" in model_item["label"]
    )
    saved = load_presets().get(("opencode-go", "catalog-model"))
    if not confirm:
        assert saved is None
        return
    assert saved.max_context == 64000
    assert any(
        i["value"] == "cfg:model:opencode-go/catalog-model" for i in model_rows()
    )

    saved.max_context = 1000
    save_profile(saved)
    shell.panel.side_effect = ["opencode-go", "catalog-model", True]
    await edit.act(row["value"].removeprefix("cfg:"))
    assert load_presets()[("opencode-go", "catalog-model")].max_context == 1000


def test_mcp_form_offers_the_shared_transport_choices():
    assert tuple(TRANSPORTS) == MCP_TRANSPORT_CHOICES


async def test_model_edit_keeps_fields_the_form_does_not_show():
    save_profile(
        LLMPreset(
            name="wide",
            model="m",
            provider="openai",
            max_context=300000,
            context_window=400000,
            variation_groups={"reasoning": {"high": {"reasoning_effort": "high"}}},
        )
    )
    shell = SimpleNamespace(
        panel=AsyncMock(return_value="edit"),
        forms=SimpleNamespace(
            ask=AsyncMock(
                return_value={
                    "name": "wide",
                    "provider": "openai",
                    "model": "m",
                    "max_context": "350000",
                    "max_output": "8000",
                    "temperature": "",
                    "reasoning_effort": "",
                }
            )
        ),
        app=Mock(),
    )
    await ConfigEdit(shell).model("openai/wide")
    saved = load_presets()[("openai", "wide")]
    assert (saved.max_context, saved.context_window) == (350000, 400000)
    assert list(saved.variation_groups) == ["reasoning"]
