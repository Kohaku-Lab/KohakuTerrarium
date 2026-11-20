"""Resource routes use the authenticated user's store, including binary reads."""

import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from examples.packages.live2d_pet.kt_live2d.model_api import router, resource_root
from examples.packages.live2d_pet.kt_live2d.model_store import MAX_UPLOAD
from kohakuterrarium.api.ext import require_user


@pytest.mark.asyncio
async def test_model_routes_reject_invalid_uploads_and_isolate_users(
    monkeypatch, tmp_path, model_zip
):
    monkeypatch.setenv("KT_CONFIG_DIR", str(tmp_path))
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[require_user] = lambda: SimpleNamespace(id=12)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        assert (await client.get("/models")).json() == {
            "dragon": None,
            "deepseek": None,
        }
        imported = await client.post("/models", content=model_zip().read_bytes())
        assert imported.status_code == 200
        first = imported.json()
        assert first["model"] == "deepseek"
        assert (await client.get("/models")).json()["deepseek"] == first
        manifest = await client.get(
            f"/models/deepseek/files/{first['version']}/high.model3.json"
        )
        assert manifest.json()["KohakuTerrarium"] == {"adapter": "deepseek"}
        rejected = await client.post(
            "/models",
            content=model_zip(KohakuTerrarium={"adapter": "unknown"}).read_bytes(),
        )
        assert rejected.status_code == 400
        assert (await client.get("/models")).json()["deepseek"] == first
        assert (await client.post("/models", content=b"bad ZIP")).status_code == 400
        assert (
            await client.post("/models/deepseek", content=b"bad ZIP")
        ).status_code == 400
        assert (
            await client.post("/models/dragon", content=b"bad ZIP")
        ).status_code == 400
        assert (await client.get("/models/deepseek/files/unknown/x")).status_code == 404

        dragon = await client.post(
            "/models", content=model_zip(model="dragon").read_bytes()
        )
        assert dragon.status_code == 200
        dragon_version = dragon.json()["version"]
        dragon_url = f"/models/dragon/files/{dragon_version}/high.model3.json"
        assert (await client.get(dragon_url)).json()["KohakuTerrarium"] == {
            "adapter": "dragon"
        }

        async def oversized():
            for _ in range(MAX_UPLOAD // (1024**2) + 1):
                yield bytes(1024**2)

        assert (await client.post("/models", content=oversized())).status_code == 413
        version = "a" * 32
        folder = resource_root(SimpleNamespace(id=12)) / "deepseek" / version
        folder.mkdir(parents=True)
        payload = b"private model bytes"
        (folder / "model.moc3").write_bytes(payload)
        (folder / "inventory.json").write_text(
            json.dumps({"files": ["model.moc3"], "bytes": len(payload)})
        )
        (folder.parent / "current").write_text(version)
        url = f"/models/deepseek/files/{version}/model.moc3"
        own = await client.get(url)
        assert own.status_code == 200 and own.content == payload
        assert own.headers["cache-control"] == "private, no-store"
        assert (await client.get("/models")).json()["deepseek"]["version"] == version
        assert (
            await client.get(f"/models/deepseek/files/{version}/inventory.json")
        ).status_code == 404
        for user in (SimpleNamespace(id=13), None):
            app.dependency_overrides[require_user] = lambda user=user: user
            assert (await client.get("/models")).json() == {
                "dragon": None,
                "deepseek": None,
            }
            assert (await client.get(url)).status_code == 404
            assert (await client.get(dragon_url)).status_code == 404
    assert resource_root(SimpleNamespace(id=12)) != resource_root(
        SimpleNamespace(id=13)
    )
    assert resource_root(None).is_relative_to(tmp_path)
