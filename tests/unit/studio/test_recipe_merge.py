"""Unit tests for :mod:`kohakuterrarium.studio.sessions.recipe_merge`."""

from types import SimpleNamespace

import pytest

from kohakuterrarium.errors import InvalidRequestError, NotFoundError
from kohakuterrarium.studio.sessions import recipe_merge
from kohakuterrarium.studio.sessions.registry import meta_for
from kohakuterrarium.terrarium.engine import Terrarium
from kohakuterrarium.terrarium.service import LocalTerrariumService

_RECIPE = """\
terrarium:
  name: merge-team
  channels:
    link: {description: merged link}
  creatures:
    - name: rex
      system_prompt: "You are rex."
"""

_ROOT_RECIPE = """\
terrarium:
  name: rooted
  root:
    system_prompt: "You lead."
  creatures:
    - name: rex
      system_prompt: "You are rex."
"""


def _write(tmp_path, name, text):
    d = tmp_path / name
    d.mkdir()
    (d / "terrarium.yaml").write_text(text, encoding="utf-8")
    return str(d)


class _FakeEngine:
    """The engine surface recipe_merge touches, recording every call."""

    def __init__(self, privileged=False, fail_start=False):
        self.graph = SimpleNamespace(
            graph_id="g1", creature_ids={"c-old"}, channels={"team": object()}
        )
        self.creatures = {
            "c-old": SimpleNamespace(creature_id="c-old", is_privileged=privileged)
        }
        self.fail_start = fail_start
        self.calls = []

    def list_graphs(self):
        return [self.graph]

    def get_creature(self, cid):
        return self.creatures[cid]

    async def apply_recipe(self, recipe, **kw):
        """Mirror the engine: members in, then ``on_applied``; any failure rolls all back."""
        self.calls.append(
            (
                "apply",
                recipe.name,
                kw["pwd"],
                kw["start"],
                kw["session"],
                recipe.max_creatures,
            )
        )
        added = ["c-rex", "c-sam"]
        for cid in added:
            self.creatures[cid] = SimpleNamespace(creature_id=cid, is_privileged=False)
            self.graph.creature_ids.add(cid)
        self.graph.channels["link"] = object()
        try:
            await kw["on_applied"](self.graph)
        except BaseException:
            self.calls.append(("rollback",))
            self.graph.creature_ids -= set(added)
            self.graph.channels.pop("link")
            raise
        kw["created_ids"].extend(added)

    async def start(self, cid):
        if self.fail_start:
            raise RuntimeError("start failed")
        self.calls.append(("start", cid))

    async def remove_channel(self, gid, name):
        self.calls.append(("remove_channel", name))

    async def remove_creature(self, cid):
        self.calls.append(("remove", cid))


@pytest.fixture
def merge(monkeypatch):
    """Run apply_recipe_to_session against a _FakeEngine; returns (call, engine, attached)."""
    attached = []

    async def fake_attach(service, creature, *, config_type):
        attached.append((creature.creature_id, config_type))

    monkeypatch.setattr(recipe_merge, "attach_session_store_for_creature", fake_attach)

    def make(**engine_kw):
        engine = _FakeEngine(**engine_kw)
        svc = SimpleNamespace()
        meta_for(svc)["g1"] = {"pwd": "/work/team"}
        monkeypatch.setattr(recipe_merge, "host_engine_or_none", lambda _s: engine)

        async def call(path):
            return await recipe_merge.apply_recipe_to_session(
                svc, "g1", config_path=path
            )

        return call, engine, attached

    return make


class TestApplyRecipeToSession:
    async def test_unknown_session_is_not_found(self, tmp_path):
        engine = Terrarium()
        svc = LocalTerrariumService(engine)
        try:
            with pytest.raises(NotFoundError, match="not found"):
                await recipe_merge.apply_recipe_to_session(
                    svc, "no-such", config_path=_write(tmp_path, "r", _RECIPE)
                )
        finally:
            await engine.shutdown()

    async def test_worker_hosted_session_is_rejected(self, tmp_path):
        engine = Terrarium()
        svc = LocalTerrariumService(engine)
        meta_for(svc)["remote-sid"] = {"on_node": "worker-1"}
        try:
            with pytest.raises(InvalidRequestError, match="worker-hosted"):
                await recipe_merge.apply_recipe_to_session(
                    svc, "remote-sid", config_path=_write(tmp_path, "r", _RECIPE)
                )
        finally:
            await engine.shutdown()

    async def test_applies_stopped_in_the_session_folder_then_attaches_then_starts(
        self, tmp_path, merge
    ):
        call, engine, attached = merge()
        created = await call(_write(tmp_path, "r", _RECIPE + "  max_creatures: 3\n"))
        assert created == ["c-rex", "c-sam"]
        assert engine.calls == [
            ("apply", "merge-team", "/work/team", False, False, 0),
            ("start", "c-rex"),
            ("start", "c-sam"),
        ]
        assert attached == [("c-rex", "agent"), ("c-sam", "agent")]

    async def test_start_failure_rolls_back_through_the_engine_transaction(
        self, tmp_path, merge
    ):
        call, engine, _ = merge(fail_start=True)
        with pytest.raises(RuntimeError, match="start failed"):
            await call(_write(tmp_path, "r", _RECIPE))
        assert [c[0] for c in engine.calls] == ["apply", "rollback"]
        assert set(engine.graph.channels) == {"team"}
        assert engine.graph.creature_ids == {"c-old"}

    async def test_relative_recipe_path_resolves_in_the_session_folder(
        self, tmp_path, merge, monkeypatch
    ):
        call, engine, _ = merge()
        _write(tmp_path, "r", _RECIPE)
        monkeypatch.setattr(recipe_merge, "session_pwd", lambda _s, _sid: str(tmp_path))
        assert await call("r") == ["c-rex", "c-sam"]
        assert engine.calls[0][2] == str(tmp_path)

    async def test_root_recipe_into_privileged_graph_is_rejected(self, tmp_path, merge):
        call, engine, _ = merge(privileged=True)
        with pytest.raises(InvalidRequestError, match="privileged node"):
            await call(_write(tmp_path, "r", _ROOT_RECIPE))
        assert engine.calls == []

    async def test_empty_or_invalid_recipes_are_rejected(self, tmp_path, merge):
        call, engine, _ = merge()
        creature_dir = tmp_path / "creature"
        creature_dir.mkdir()
        (creature_dir / "config.yaml").write_text("name: solo\n", encoding="utf-8")
        with pytest.raises(FileNotFoundError):
            await call(str(creature_dir))
        with pytest.raises(InvalidRequestError, match="defines no creatures"):
            await call(str(creature_dir / "config.yaml"))
        with pytest.raises(InvalidRequestError, match="not a valid"):
            await call(_write(tmp_path, "bad", "terrarium: [1\n"))
        assert engine.calls == []
