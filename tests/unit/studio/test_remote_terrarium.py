from types import SimpleNamespace

from kohakuterrarium.studio.sessions import remote_terrarium
from kohakuterrarium.terrarium.service import CreatureInfo
from kohakuterrarium.terrarium.topology import GraphTopology


class TestStartRemoteTerrarium:
    async def test_duplicate_dangerous_names_keep_persistence_worker_owned(
        self, tmp_path, monkeypatch
    ):
        recipe_dir = tmp_path / "remote-recipe"
        recipe_dir.mkdir()
        recipe_path = recipe_dir / "terrarium.yaml"
        recipe_path.write_text("terrarium:\n  name: safe-recipe\n", encoding="utf-8")

        async def deploy(*args, **kwargs):
            return "C:/worker/recipes/deployed"

        class RemoteRecipeService:
            def __init__(self):
                self.calls = []

            async def apply_recipe(self, recipe, **kwargs):
                self.calls.append({"recipe": recipe, **kwargs})
                index = len(self.calls)
                creature_id = f"worker-{index}"
                graph_id = f"graph-{index}"
                return (
                    GraphTopology(graph_id=graph_id, creature_ids={creature_id}),
                    [
                        CreatureInfo(
                            creature_id=creature_id,
                            name="worker",
                            graph_id=graph_id,
                            is_running=True,
                            is_privileged=True,
                            parent_creature_id=None,
                            listen_channels=(),
                            send_channels=(),
                        )
                    ],
                )

            async def remove_creature(self, creature_id):
                raise AssertionError(f"unexpected compensation for {creature_id}")

        remote = RemoteRecipeService()
        service = SimpleNamespace(
            host=object(),
            _home={},
            connected_nodes=lambda: ("worker-1",),
            service_for=lambda node: remote,
        )
        monkeypatch.setattr(remote_terrarium, "deploy_creature_to_node", deploy)
        monkeypatch.setattr(
            remote_terrarium,
            "load_terrarium_config",
            lambda path: SimpleNamespace(name="safe-recipe"),
        )

        dangerous_name = "../../shared-session"
        first = await remote_terrarium.start_remote_terrarium(
            service,
            config_path=str(recipe_path),
            name=dangerous_name,
            pwd=None,
            llm=None,
            on_node="worker-1",
        )
        second = await remote_terrarium.start_remote_terrarium(
            service,
            config_path=str(recipe_path),
            name=dangerous_name,
            pwd=None,
            llm=None,
            on_node="worker-1",
        )

        assert first.name == dangerous_name
        assert second.name == f"{dangerous_name} #2"
        assert first.session_id != second.session_id
        assert [call["persist"] for call in remote.calls] == [True, True]
        assert all("session_path" not in call for call in remote.calls)
