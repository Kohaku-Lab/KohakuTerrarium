"""Session display names persist into the session file (``meta["name"]``)."""

from types import SimpleNamespace

from kohakuterrarium.studio.persistence.resume import resume_session
from kohakuterrarium.studio.sessions import lifecycle
from kohakuterrarium.studio.sessions.registry import meta_for
from kohakuterrarium.terrarium import LocalTerrariumService, Terrarium


def _recipe(tmp_path, creature_dir):
    recipe = tmp_path / "pair.yaml"
    rel = creature_dir.resolve().as_posix()
    recipe.write_text(
        "terrarium:\n  name: pair\n  creatures:\n"
        f"    - {{ name: gamma, base_config: '{rel}' }}\n"
        f"    - {{ name: delta, base_config: '{rel}' }}\n",
        encoding="utf-8",
    )
    return recipe


async def test_names_persist_on_create_and_rename_and_drive_resume(
    tmp_path, scripted, creature_dir, monkeypatch
):
    monkeypatch.setenv("KT_SESSION_DIR", str(tmp_path / "sessions"))
    engine = Terrarium(session_dir=str(tmp_path / "sessions"))
    service = LocalTerrariumService(engine)
    try:
        unnamed = await lifecycle.start_creature(service, config_path=str(creature_dir))
        named = await lifecycle.start_creature(
            service, config_path=str(creature_dir), name="scout"
        )
        team = await lifecycle.start_terrarium(
            service, config_path=str(_recipe(tmp_path, creature_dir)), name=" Pair run "
        )
        recipe_named = await lifecycle.start_terrarium(
            service, config_path=str(_recipe(tmp_path, creature_dir))
        )
        stores = engine._session_stores
        assert stores[unnamed.session_id].meta.get("name") is None
        assert stores[named.session_id].meta.get("name") == "scout"
        assert stores[team.session_id].meta.get("name") == "Pair run"
        assert stores[recipe_named.session_id].meta.get("name") is None

        lifecycle.rename_session(service, team.session_id, "Pair night run")
        assert stores[team.session_id].meta.get("name") == "Pair night run"
        lifecycle.rename_creature(
            service, unnamed.creatures[0]["creature_id"], "renamed-probe"
        )
        assert stores[unnamed.session_id].meta.get("name") == "renamed-probe"
        member = team.creatures[0]["creature_id"]
        lifecycle.rename_creature(service, member, "gamma-2")
        assert stores[team.session_id].meta.get("name") == "Pair night run"
        team_path = stores[team.session_id].path
    finally:
        await engine.shutdown()

    engine = Terrarium(session_dir=str(tmp_path / "sessions"))
    service = LocalTerrariumService(engine)
    try:
        resumed = await resume_session(service, team_path)
        assert meta_for(service)[resumed.session_id]["name"] == "Pair night run"
    finally:
        await engine.shutdown()


def test_persist_session_name_tolerates_missing_and_read_only_stores():
    service = SimpleNamespace(engine=SimpleNamespace(_session_stores={}))
    lifecycle.persist_session_name(service, "nope", "x")
    readonly = SimpleNamespace(_readonly=True, meta={})
    service.engine._session_stores["g"] = readonly
    lifecycle.persist_session_name(service, "g", "x")
    assert readonly.meta == {}
