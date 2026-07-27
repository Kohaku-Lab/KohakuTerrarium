"""Unit tests for :mod:`kohakuterrarium.studio.sessions.wiring`."""

from kohakuterrarium.studio.sessions import wiring as wiring_mod

# ── _extract_target_name ──────────────────────────────────────


class TestExtractTargetName:
    def test_str(self):
        assert wiring_mod._extract_target_name("bob") == "bob"

    def test_dict_with_to(self):
        assert wiring_mod._extract_target_name({"to": "bob"}) == "bob"

    def test_dict_without_to(self):
        assert wiring_mod._extract_target_name({"x": 1}) is None

    def test_dict_empty_to(self):
        assert wiring_mod._extract_target_name({"to": ""}) is None

    def test_other_types(self):
        assert wiring_mod._extract_target_name(None) is None
        assert wiring_mod._extract_target_name(42) is None


# ?? graph-local fixtures ????????????????????????????????????????


class _Graph:
    def __init__(self, gid, creature_ids):
        self.graph_id = gid
        self.creature_ids = set(creature_ids)


class _Topology:
    def __init__(self, graphs):
        self.graphs = {graph.graph_id: graph for graph in graphs}
        self.creature_to_graph = {
            cid: graph.graph_id for graph in graphs for cid in graph.creature_ids
        }


class _Creature:
    def __init__(self, cid, name=None, graph_id="g"):
        self.creature_id = cid
        self.name = name or cid
        self.graph_id = graph_id
        config = type("Cfg", (), {"name": self.name})()
        self.agent = type("Agent", (), {"config": config})()


class _Engine:
    def __init__(self, creatures=None):
        self._creatures = creatures or {}
        groups = {}
        for creature in self._creatures.values():
            groups.setdefault(creature.graph_id, set()).add(creature.creature_id)
        self._topology = _Topology(
            [_Graph(graph_id, ids) for graph_id, ids in groups.items()]
        )
        self.wire_output_calls = []
        self.unwire_output_calls = []
        self.list_output_wiring_calls = []

    async def wire_output(self, cid, target):
        self.wire_output_calls.append((cid, target))
        return "edge-1"

    async def unwire_output(self, cid, edge_id):
        self.unwire_output_calls.append((cid, edge_id))
        return True

    def list_output_wiring(self, cid):
        self.list_output_wiring_calls.append(cid)
        return [{"edge_id": "e1"}]

    async def wire_output_sink(self, cid, sink):
        return "sink-1"

    async def unwire_output_sink(self, cid, sink_id):
        return True


# ── wire_output / unwire_output / list_output_wiring ─────────


class TestWireOutput:
    async def test_root_target_is_preserved(self):
        eng = _Engine()
        await wiring_mod.wire_output(eng, "c1", "root")
        assert eng.wire_output_calls == [("c1", "root")]

    async def test_unique_local_name_is_canonical_runtime_id(self):
        source = _Creature("source-id", "source")
        target = _Creature("target-id", "worker")
        eng = _Engine({"source-id": source, "target-id": target})

        await wiring_mod.wire_output(eng, "source-id", "worker")

        assert eng.wire_output_calls == [("source-id", {"to": "target-id"})]

    async def test_cross_graph_target_is_rejected_without_merge(self):
        import pytest
        from kohakuterrarium.terrarium.graph_identity import TargetNotFoundError

        source = _Creature("source-id", "source", graph_id="g1")
        target = _Creature("target-id", "worker", graph_id="g2")
        eng = _Engine({"source-id": source, "target-id": target})

        with pytest.raises(TargetNotFoundError):
            await wiring_mod.wire_output(eng, "source-id", "worker")
        assert eng.wire_output_calls == []
        assert eng._topology.creature_to_graph == {
            "source-id": "g1",
            "target-id": "g2",
        }

    async def test_duplicate_local_name_is_ambiguous(self):
        import pytest
        from kohakuterrarium.terrarium.graph_identity import AmbiguousTargetError

        source = _Creature("source-id", "source")
        first = _Creature("first-id", "worker")
        second = _Creature("second-id", "worker")
        eng = _Engine(
            {
                "source-id": source,
                "first-id": first,
                "second-id": second,
            }
        )

        with pytest.raises(AmbiguousTargetError):
            await wiring_mod.wire_output(eng, "source-id", "worker")
        assert eng.wire_output_calls == []

    async def test_invalid_mapping_without_target_stays_backward_compatible(self):
        eng = _Engine()
        await wiring_mod.wire_output(eng, "c1", {"x": 1})
        assert eng.wire_output_calls == [("c1", {"x": 1})]


class TestUnwireOutput:
    async def test_returns_engine_result(self):
        eng = _Engine()
        out = await wiring_mod.unwire_output(eng, "c1", "e1")
        assert out is True
        assert ("c1", "e1") in eng.unwire_output_calls


class TestListOutputWiring:
    def test_returns_engine_list(self):
        eng = _Engine()
        out = wiring_mod.list_output_wiring(eng, "c1")
        assert out == [{"edge_id": "e1"}]


class TestSinks:
    async def test_wire_sink(self):
        eng = _Engine()
        out = await wiring_mod.wire_output_sink(eng, "c1", object())
        assert out == "sink-1"

    async def test_unwire_sink(self):
        eng = _Engine()
        out = await wiring_mod.unwire_output_sink(eng, "c1", "s1")
        assert out is True
