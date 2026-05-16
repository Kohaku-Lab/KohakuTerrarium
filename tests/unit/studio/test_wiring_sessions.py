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


# ── _resolve_creature_by_name ────────────────────────────────


class _Creature:
    def __init__(self, cid, name=None, graph_id="g"):
        self.creature_id = cid
        self.name = name or cid
        self.graph_id = graph_id


class _Engine:
    def __init__(self, creatures=None):
        self._creatures = creatures or {}
        self.wire_output_calls = []
        self.unwire_output_calls = []
        self.list_output_wiring_calls = []

    def get_creature(self, cid):
        if cid not in self._creatures:
            raise KeyError(cid)
        return self._creatures[cid]

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


class TestResolveCreatureByName:
    def test_by_id(self):
        c = _Creature("cid-1")
        eng = _Engine({"cid-1": c})
        out = wiring_mod._resolve_creature_by_name(eng, "cid-1")
        assert out is c

    def test_by_name(self):
        c = _Creature("cid-1", name="alice")
        eng = _Engine({"cid-1": c})
        out = wiring_mod._resolve_creature_by_name(eng, "alice")
        assert out is c

    def test_unknown(self):
        eng = _Engine()
        assert wiring_mod._resolve_creature_by_name(eng, "ghost") is None


# ── _ensure_target_in_same_graph ─────────────────────────────


class TestEnsureSameGraph:
    async def test_unknown_source_noop(self):
        # Unknown source returns without raising.
        eng = _Engine()
        await wiring_mod._ensure_target_in_same_graph(eng, "ghost", "bob")

    async def test_unknown_target_noop(self):
        c = _Creature("c1")
        eng = _Engine({"c1": c})
        await wiring_mod._ensure_target_in_same_graph(eng, "c1", "ghost")

    async def test_same_graph_noop(self):
        # Source and target in same graph → no ensure_same_graph call.
        c1 = _Creature("c1", graph_id="g")
        c2 = _Creature("c2", graph_id="g", name="bob")
        eng = _Engine({"c1": c1, "c2": c2})
        called = []

        async def fake_merge(e, a, b):
            called.append((a, b))

        # Replace ensure_same_graph via the module's _channels import.
        import kohakuterrarium.terrarium.channels as _channels_mod

        orig = _channels_mod.ensure_same_graph
        _channels_mod.ensure_same_graph = fake_merge
        try:
            await wiring_mod._ensure_target_in_same_graph(eng, "c1", "bob")
        finally:
            _channels_mod.ensure_same_graph = orig
        assert called == []  # same graph → skipped

    async def test_different_graph_merges(self):
        c1 = _Creature("c1", graph_id="g1")
        c2 = _Creature("c2", graph_id="g2", name="bob")
        eng = _Engine({"c1": c1, "c2": c2})
        called = []

        async def fake_merge(e, a, b):
            called.append((a, b))

        import kohakuterrarium.terrarium.channels as _channels_mod

        orig = _channels_mod.ensure_same_graph
        _channels_mod.ensure_same_graph = fake_merge
        try:
            await wiring_mod._ensure_target_in_same_graph(eng, "c1", "bob")
        finally:
            _channels_mod.ensure_same_graph = orig
        assert called == [("c1", "c2")]


# ── wire_output / unwire_output / list_output_wiring ─────────


class TestWireOutput:
    async def test_root_target_skips_merge(self, monkeypatch):
        eng = _Engine()
        # Even with ``"root"`` we should NOT call ``_ensure_*``.
        called = []

        async def fake_ensure(e, src, tgt):
            called.append((src, tgt))

        monkeypatch.setattr(wiring_mod, "_ensure_target_in_same_graph", fake_ensure)
        await wiring_mod.wire_output(eng, "c1", "root")
        assert called == []

    async def test_str_target_triggers_ensure(self, monkeypatch):
        eng = _Engine()
        called = []

        async def fake_ensure(e, src, tgt):
            called.append((src, tgt))

        monkeypatch.setattr(wiring_mod, "_ensure_target_in_same_graph", fake_ensure)
        await wiring_mod.wire_output(eng, "c1", "bob")
        assert called == [("c1", "bob")]

    async def test_no_target_name_skips_ensure(self, monkeypatch):
        eng = _Engine()
        called = []

        async def fake_ensure(e, src, tgt):
            called.append((src, tgt))

        monkeypatch.setattr(wiring_mod, "_ensure_target_in_same_graph", fake_ensure)
        # No ``to`` key → no target name → no ensure.
        await wiring_mod.wire_output(eng, "c1", {"x": 1})
        assert called == []


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
