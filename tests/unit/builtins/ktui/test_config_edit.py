"""The terminal catalog entry saves only explicitly selected models."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

from kohakuterrarium.builtins.ktui.config_edit import ConfigEdit, model_rows
from kohakuterrarium.llm.profiles.resolve import load_presets, save_profile


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
