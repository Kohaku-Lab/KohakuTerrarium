"""Integration suite for ``kohakuterrarium.studio`` — the management tier.

Studio is the management framework above the Terrarium engine. It
exposes six namespaces — ``catalog``, ``identity``, ``sessions``,
``persistence``, ``editors``, ``attach`` — through the :class:`Studio`
façade. The real consumers (``api/routes/*`` and the ``cli/``
subcommands) drive studio through exactly that façade; this suite does
the same.

Each test method runs ONE complete lifecycle end-to-end:

* ``test_workspace_to_session_to_persistence_lifecycle`` — the headline
  flow: scaffold a workspace creature (editors), browse it back
  (catalog), start a session from it, chat, save, list saved, open the
  viewer panes, resume, fork.
* ``test_runtime_session_mutation`` — drive the per-creature runtime
  surface: scratchpad patch, model switch, plugin toggle, slash
  command, attach-policy advertisement.
* ``test_identity_and_catalog_surface`` — the read/write config tier:
  LLM profiles + keys + MCP registry + UI prefs CRUD, plus the
  read-only builtin catalog + introspection.

The ONLY seam is the LLM: BOTH ``create_llm_provider`` import sites
(``bootstrap.llm`` and ``bootstrap.agent_init``) are monkeypatched to a
:class:`ScriptedLLM`. Every other collaborator is real — a real
:class:`Studio` over a real :class:`Terrarium` engine wrapped in a real
:class:`LocalTerrariumService`, real on-disk ``.kohakutr`` session
files in a ``tmp_path`` dir, real workspace directories, the real
identity YAML stores (redirected to ``tmp_path``).

No shape asserts: every assertion pins an exact value or an observable
side effect.
"""

from pathlib import Path

import pytest
from fastapi import HTTPException

from kohakuterrarium.bootstrap import agent_init as _agent_init_mod
from kohakuterrarium.bootstrap import llm as _bootstrap_llm_mod
from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.studio.persistence import store as _persistence_store_mod
from kohakuterrarium.studio.studio import Studio
from kohakuterrarium.testing.llm import ScriptedLLM

pytestmark = pytest.mark.timeout(30)


# ---------------------------------------------------------------------------
# Fixtures — isolate every module-global path + the LLM seam.
# ---------------------------------------------------------------------------


@pytest.fixture
def scripted_llm(monkeypatch):
    """Patch BOTH ``create_llm_provider`` import sites to a ScriptedLLM.

    ``bootstrap.agent_init`` imports the symbol by name and
    ``bootstrap.llm`` defines it — patching only one leaves a real
    provider on the other path. The returned dict lets a test set the
    script before it starts a creature.
    """
    holder: dict[str, list] = {"script": ["OK"]}

    def _fake_create(config, llm_override=None):
        return ScriptedLLM(holder["script"])

    monkeypatch.setattr(_bootstrap_llm_mod, "create_llm_provider", _fake_create)
    monkeypatch.setattr(_agent_init_mod, "create_llm_provider", _fake_create)
    return holder


@pytest.fixture
def isolated_paths(tmp_path, monkeypatch):
    """Redirect every studio config + session path into ``tmp_path``.

    The ``identity`` namespace (API keys / LLM profiles / MCP servers /
    UI prefs / Codex tokens) resolves its files through
    ``utils.config_dir.config_dir`` — ``KT_CONFIG_DIR`` is the single
    isolation seam.  The session dir is the documented
    ``KT_SESSION_DIR`` override.  Without both the suite would
    read/write the real user's ``~/.kohakuterrarium`` — the suite must
    never touch real state.
    """
    session_dir = tmp_path / "sessions"
    session_dir.mkdir()
    monkeypatch.setenv("KT_SESSION_DIR", str(session_dir))
    monkeypatch.setenv("KT_CONFIG_DIR", str(tmp_path / "kt-config"))
    # Force the saved-session index to honour KT_SESSION_DIR rather than
    # the home-dir default baked in at import.
    monkeypatch.setattr(
        _persistence_store_mod, "_SESSION_DIR", session_dir, raising=False
    )
    return {"session_dir": session_dir, "tmp_path": tmp_path}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _drain_chat(
    studio: Studio, session_id: str, creature_id: str, msg: str
) -> str:
    """Consume ``sessions.chat.chat`` to completion — the documented drive."""
    chunks: list[str] = []
    async for chunk in studio.sessions.chat.chat(session_id, creature_id, msg):
        chunks.append(chunk)
    return "".join(chunks)


def _scaffold_creature(studio: Studio, workspace_root: Path, name: str) -> Path:
    """Scaffold a workspace creature and give it a deterministic prompt.

    Returns the creature directory — the path ``sessions.start_creature``
    accepts.
    """
    creatures_dir = workspace_root / "creatures"
    creature_dir = studio.editors.creatures.scaffold(creatures_dir, name, None)
    studio.editors.creatures.write_prompt(
        creatures_dir,
        name,
        "prompts/system.md",
        f"# {name}\nYou are the {name} test creature. Reply tersely.",
    )
    return creature_dir


# ---------------------------------------------------------------------------
# The integration suite.
# ---------------------------------------------------------------------------


class TestStudioIntegration:
    """Each method runs one complete studio-tier lifecycle."""

    async def test_workspace_to_session_to_persistence_lifecycle(
        self, scripted_llm, isolated_paths
    ):
        """The headline flow, end-to-end through the Studio façade:

        editors.creatures.scaffold + write_prompt + save
            -> catalog.creatures.list / get / read_prompt sees it
            -> sessions.start_creature mints a 1-creature graph
            -> sessions.chat.chat streams a turn, chat.history persists it
            -> persistence.list surfaces the saved .kohakutr
            -> persistence.viewer.{tree,summary,turns,events} read it back
            -> persistence.resume adopts it into a fresh engine
            -> persistence.fork branches it at an event

        Mirrors ``api/routes/catalog/*`` + ``api/routes/sessions_v2/*`` +
        ``api/routes/persistence/*`` — every one of those routes funnels
        through these same studio functions.
        """
        scripted_llm["script"] = ["First reply.", "Second reply."]
        workspace_root = isolated_paths["tmp_path"] / "workspace"
        workspace_root.mkdir()
        session_dir = isolated_paths["session_dir"]

        from kohakuterrarium.studio.editors.workspace_fs import LocalWorkspace

        async with Studio() as studio:
            # --- editors: scaffold + edit a workspace creature ----------
            creature_dir = _scaffold_creature(studio, workspace_root, "scout")
            assert creature_dir.is_dir()
            assert (creature_dir / "config.yaml").exists()
            # Edit the config through the editor save path (mirrors the
            # frontend creature-editor PUT).
            studio.editors.creatures.save(
                workspace_root / "creatures",
                "scout",
                {
                    "config": {
                        "name": "scout",
                        "version": "1.0",
                        "system_prompt_file": "prompts/system.md",
                        "description": "an integration-test scout",
                    },
                    "prompts": {},
                },
            )

            # Scaffold + edit a workspace module (a tool) so the module
            # catalog has a real entry to browse back. The workspace
            # helper resolves the on-disk path the editor primitives
            # need — the same drive ``api/routes/catalog/modules.py`` uses.
            ws = LocalWorkspace.open(workspace_root)
            scaffolded_mod = ws.scaffold_module("tools", "ping_tool", None)
            module_path = workspace_root / scaffolded_mod["path"]
            assert module_path.exists()
            edited_source = scaffolded_mod["raw_source"].replace(
                "TODO: describe this tool", "an integration-test ping tool"
            )
            ws.save_module(
                "tools", "ping_tool", {"mode": "raw", "raw_source": edited_source}
            )
            assert "an integration-test ping tool" in module_path.read_text(
                encoding="utf-8"
            )
            # A simple-mode save round-trips the tool through its codegen
            # ``update_existing`` path (libcst patch in place).
            resaved = ws.save_module(
                "tools",
                "ping_tool",
                {
                    "mode": "simple",
                    "form": {"description": "a re-saved ping tool"},
                    "execute_body": 'return ToolResult(output="pong")',
                },
            )
            assert resaved["name"] == "ping_tool"
            assert "pong" in module_path.read_text(encoding="utf-8")
            # The skill-doc sidecar round-trips next to the module file.
            doc_before = ws.load_module_doc("tools", "ping_tool")
            assert doc_before["exists"] is False
            ws.save_module_doc("tools", "ping_tool", "# ping_tool\nUsage notes.")
            doc_after = ws.load_module_doc("tools", "ping_tool")
            assert doc_after["exists"] is True
            assert doc_after["content"] == "# ping_tool\nUsage notes."
            # Sync the tool into the workspace manifest so the catalog
            # can discover it; the second sync is idempotent (added
            # flips False the second time).
            sync_one = ws.sync_manifest("tools", "ping_tool")
            assert sync_one["ok"] is True
            assert sync_one["added"] is True
            sync_two = ws.sync_manifest("tools", "ping_tool")
            assert sync_two["ok"] is True
            assert sync_two["added"] is False

            # Scaffold a module of EVERY remaining kind — each kind
            # dispatches through its own codegen module (subagent /
            # plugin / trigger / io). Scaffold, load it back (parse_back),
            # then a raw-mode round-trip through ``save_module``.
            for mkind in ("subagents", "plugins", "triggers", "inputs", "outputs"):
                mname = f"{mkind[:-1]}_probe"
                scaffolded_k = ws.scaffold_module(mkind, mname, None)
                kpath = workspace_root / scaffolded_k["path"]
                assert kpath.exists()
                assert scaffolded_k["kind"] == mkind
                # parse_back reads the scaffold's form state.
                reloaded_k = ws.load_module(mkind, mname)
                assert reloaded_k["name"] == mname
                # raw-mode round-trip writes the exact bytes back.
                raw_k = reloaded_k["raw_source"] + "\n# probed\n"
                ws.save_module(mkind, mname, {"mode": "raw", "raw_source": raw_k})
                assert "# probed" in kpath.read_text(encoding="utf-8")
            # A raw save with an empty body is rejected — never a
            # silently-empty file write.
            with pytest.raises(ValueError):
                ws.save_module("tools", "ping_tool", {"mode": "raw", "raw_source": ""})
            # An unknown module kind is a hard ValueError on scaffold.
            with pytest.raises(ValueError):
                ws.scaffold_module("nonsense-kind", "x", None)
            # Scaffolding a duplicate name is a hard FileExistsError.
            with pytest.raises(FileExistsError):
                ws.scaffold_module("tools", "ping_tool", None)

            # --- catalog: browse the workspace creature back ------------
            listing = studio.catalog.creatures.list(ws)
            assert [c["name"] for c in listing] == ["scout"]
            assert listing[0]["description"] == "an integration-test scout"
            loaded = studio.catalog.creatures.get(ws, "scout")
            assert loaded["name"] == "scout"
            prompt_text = studio.catalog.creatures.read_prompt(
                ws, "scout", "prompts/system.md"
            )
            assert prompt_text.startswith("# scout")
            # The module catalog surfaces the tool we just scaffolded.
            mod_listing = studio.catalog.modules.list(ws, "tools")
            assert [m["name"] for m in mod_listing] == ["ping_tool"]
            mod_loaded = studio.catalog.modules.get(ws, "tools", "ping_tool")
            assert mod_loaded["name"] == "ping_tool"
            # The catalog surfaces a module of every authored kind.
            for mkind in ("subagents", "plugins", "triggers", "inputs", "outputs"):
                k_listing = studio.catalog.modules.list(ws, mkind)
                assert [m["name"] for m in k_listing] == [f"{mkind[:-1]}_probe"]
                k_loaded = studio.catalog.modules.get(ws, mkind, f"{mkind[:-1]}_probe")
                assert k_loaded["name"] == f"{mkind[:-1]}_probe"
            # The catalog surfaces the skill-doc sidecar we wrote.
            cat_doc = studio.catalog.modules.doc(ws, "tools", "ping_tool")
            assert cat_doc["exists"] is True
            assert cat_doc["content"] == "# ping_tool\nUsage notes."
            # introspect: builtin schemas for each module kind expose
            # the curated param surface the editor's option form renders.
            sub_schema = studio.catalog.introspect.builtin_schema("subagents")
            assert {"max_turns", "interactive", "can_modify"} <= {
                p["name"] for p in sub_schema["params"]
            }
            plug_schema = studio.catalog.introspect.builtin_schema("plugins")
            assert [p["name"] for p in plug_schema["params"]] == ["priority"]
            # introspect: a custom-schema AST-parse of the workspace tool
            # we authored — picks up the BaseTool subclass __init__.
            tool_src = (workspace_root / scaffolded_mod["path"]).read_text(
                encoding="utf-8"
            )
            custom = studio.catalog.introspect.custom_schema(tool_src, None)
            assert "params" in custom and "warnings" in custom
            # A syntactically broken source surfaces a syntax_error
            # warning rather than raising.
            broken = studio.catalog.introspect.custom_schema("def broke(\n", None)
            assert [w["code"] for w in broken["warnings"]] == ["syntax_error"]

            # --- sessions: start a session from that creature -----------
            session = await studio.sessions.start_creature(str(creature_dir))
            assert session.name == "scout"
            assert len(session.creatures) == 1
            creature_id = session.creatures[0]["creature_id"]
            session_id = session.session_id
            # The session lists like any other active session.
            active = studio.sessions.list()
            assert [s.session_id for s in active] == [session_id]
            assert active[0].creatures == 1

            # --- sessions.chat: stream two turns ------------------------
            out = await _drain_chat(studio, session_id, creature_id, "ping one")
            assert out == "First reply."
            out2 = await _drain_chat(studio, session_id, creature_id, "ping two")
            assert out2 == "Second reply."
            history = studio.sessions.chat.history(session_id, creature_id)
            # The user message + assistant reply are both in the
            # conversation snapshot the chat history endpoint returns
            # (index 0 is the system prompt).
            roles = [m["role"] for m in history["messages"]]
            assert "user" in roles
            user_msgs = [
                m["content"] for m in history["messages"] if m["role"] == "user"
            ]
            assert user_msgs == ["ping one", "ping two"]
            assert history["messages"][-1]["role"] == "assistant"
            assert "Second reply." in history["messages"][-1]["content"]
            assert any(e["type"] == "user_input" for e in history["events"])
            assert history["is_processing"] is False
            # Per-turn branch metadata — two linear turns, no branching.
            branches = studio.sessions.chat.branches(session_id, creature_id)
            assert branches["creature_id"] == creature_id
            assert [t["turn_index"] for t in branches["turns"]] == [1, 2]
            assert all(t["branches"] == [1] for t in branches["turns"])
            assert all(t["latest_branch"] == 1 for t in branches["turns"])

            # --- sessions.state: read-only runtime surface --------------
            # The system prompt carries the workspace creature's seeded
            # personality line.
            sysprompt = studio.sessions.state.system_prompt(session_id, creature_id)
            assert "scout test creature" in sysprompt["text"]
            # A scaffolded creature has no triggers declared.
            assert studio.sessions.state.triggers(session_id, creature_id) == []
            # env reports a working dir + a credential-redacted env map.
            env = studio.sessions.state.env(session_id, creature_id)
            assert env["pwd"]
            assert not any("secret" in k.lower() for k in env["env"])
            # working_dir read agrees with the env pwd.
            assert studio.sessions.state.working_dir(session_id, creature_id) == (
                env["pwd"]
            )
            # No provider-native tools on a bare scaffolded creature.
            assert (
                studio.sessions.model.native_tool_options(session_id, creature_id) == {}
            )

            # --- sessions.ctl: jobs + interrupt are idle no-ops ---------
            assert await studio.sessions.ctl.list_jobs(session_id, creature_id) == []
            # Interrupt on an idle creature is a clean no-op.
            await studio.sessions.ctl.interrupt(session_id, creature_id)
            assert (
                await studio.sessions.ctl.cancel_job(
                    session_id, creature_id, "no-such-job"
                )
                is False
            )

            # Stop the session — flushes + closes the .kohakutr file.
            await studio.sessions.stop(session_id)

        # The session file landed on disk under KT_SESSION_DIR.
        saved_files = sorted(p.name for p in session_dir.glob("*.kohakutr"))
        assert len(saved_files) == 1
        saved_stem = saved_files[0].split(".kohakutr")[0]

        async with Studio() as studio:
            # --- persistence.list: the saved session is indexed --------
            # max_age=0.0 forces a rebuild — the index is a module global
            # that may be stale from an earlier test.
            saved = studio.persistence.list(max_age=0.0)
            entry = next(e for e in saved if e["name"] == saved_stem)
            assert entry["config_type"] == "agent"
            assert entry["agents"] == ["scout"]
            assert entry["preview"] == "ping one"

            saved_path = studio.persistence.resolve_path(saved_stem)
            assert saved_path is not None and saved_path.exists()

            # --- persistence.viewer: read the saved session's panes ----
            store = SessionStore(saved_path)
            try:
                tree = studio.persistence.viewer.tree(store, saved_stem)
                # No fork lineage yet — exactly one node, the session
                # itself, and no edges.
                assert [n["id"] for n in tree["nodes"]] == [tree["session_id"]]
                assert tree["edges"] == []

                summary = studio.persistence.viewer.summary(store, saved_stem, None)
                assert summary["agents"] == ["scout"]
                assert summary["config_type"] == "agent"
                assert summary["totals"]["turns"] == 2

                turns = studio.persistence.viewer.turns(
                    store,
                    saved_stem,
                    agent=None,
                    from_turn=None,
                    to_turn=None,
                    limit=50,
                    offset=0,
                )
                assert turns["total"] == 2
                assert turns["agent"] == "scout"
                # A turn-range filter narrows to exactly the first turn.
                turns_first = studio.persistence.viewer.turns(
                    store,
                    saved_stem,
                    agent="scout",
                    from_turn=1,
                    to_turn=1,
                    limit=50,
                    offset=0,
                )
                assert [t["turn_index"] for t in turns_first["turns"]] == [1]
                # An offset past the end yields an empty page, total intact.
                turns_past = studio.persistence.viewer.turns(
                    store,
                    saved_stem,
                    agent=None,
                    from_turn=None,
                    to_turn=None,
                    limit=50,
                    offset=99,
                )
                assert turns_past["turns"] == []
                assert turns_past["total"] == 2

                events = studio.persistence.viewer.events(
                    store,
                    saved_stem,
                    agent=None,
                    turn_index=None,
                    types=None,
                    from_ts=None,
                    to_ts=None,
                    limit=200,
                    cursor=None,
                )
                assert events["agent"] == "scout"
                event_types = {e["type"] for e in events["events"]}
                assert "user_input" in event_types
                assert "text_chunk" in event_types
                # Filtering events by type narrows the result set to
                # exactly the requested kind.
                only_inputs = studio.persistence.viewer.events(
                    store,
                    saved_stem,
                    agent=None,
                    turn_index=None,
                    types="user_input",
                    from_ts=None,
                    to_ts=None,
                    limit=200,
                    cursor=None,
                )
                assert {e["type"] for e in only_inputs["events"]} == {"user_input"}
                assert len(only_inputs["events"]) == 2
                # Filtering by turn_index narrows to a single turn's
                # events — turn 1 carries exactly one user_input.
                turn1_inputs = studio.persistence.viewer.events(
                    store,
                    saved_stem,
                    agent="scout",
                    turn_index=1,
                    types="user_input",
                    from_ts=None,
                    to_ts=None,
                    limit=200,
                    cursor=None,
                )
                assert len(turn1_inputs["events"]) == 1
                assert turn1_inputs["events"][0]["turn_index"] == 1

                # --- persistence.viewer.export: a markdown transcript ---
                content_type, body = studio.persistence.viewer.export(
                    store, saved_stem, "md", None
                )
                assert content_type == "text/markdown; charset=utf-8"
                assert "ping one" in body
                assert "Second reply." in body
                # jsonl export is one event per line.
                ct_jsonl, jsonl_body = studio.persistence.viewer.export(
                    store, saved_stem, "jsonl", None
                )
                assert ct_jsonl == "application/jsonl; charset=utf-8"
                assert jsonl_body.strip().count("\n") >= 1
                # html export wraps the same transcript in a document.
                ct_html, html_body = studio.persistence.viewer.export(
                    store, saved_stem, "html", None
                )
                assert ct_html == "text/html; charset=utf-8"
                assert "ping one" in html_body
                assert "<!doctype html>" in html_body.lower()
                # An agent-scoped export still renders the recorded turns.
                ct_agent, agent_body = studio.persistence.viewer.export(
                    store, saved_stem, "md", "scout"
                )
                assert ct_agent == "text/markdown; charset=utf-8"
                assert "ping one" in agent_body
                # An unsupported export format is a hard 400, never a
                # silently-empty body.
                with pytest.raises(HTTPException) as exc_export:
                    studio.persistence.viewer.export(store, saved_stem, "pdf", None)
                assert exc_export.value.status_code == 400
            finally:
                store.close(update_status=False)

            # --- persistence.resume: adopt the saved session -----------
            resumed = await studio.persistence.resume(saved_path)
            assert len(resumed.creatures) == 1
            resumed_cid = resumed.creatures[0]["creature_id"]
            resumed_history = studio.sessions.chat.history(
                resumed.session_id, resumed_cid
            )
            # The resumed conversation carries the original turn forward.
            assert any(
                "First reply." in (m.get("content") or "")
                for m in resumed_history["messages"]
                if m["role"] == "assistant"
            )
            await studio.sessions.stop(resumed.session_id)

            # --- persistence.fork: branch the saved session ------------
            fork_store = SessionStore(saved_path)
            try:
                events_all = [evt for _k, evt in fork_store.get_all_events()]
                # Fork at the user_message event so a drop_trailing fork
                # is structurally valid.
                user_msg_evt = next(
                    e for e in events_all if e["type"] == "user_message"
                )
                fork_point = user_msg_evt["event_id"]
            finally:
                fork_store.close(update_status=False)

            fork_result = await studio.persistence.fork(
                saved_path,
                at_event_id=fork_point,
                mutate_kind="drop_trailing",
                mutate_args=None,
                name="branch-a",
            )
            assert fork_result["fork_point"] == fork_point
            fork_path = Path(fork_result["path"])
            assert fork_path.exists()
            assert "branch-a" in fork_path.name
            # The fork is a distinct session id from its parent.
            assert fork_result["session_id"] != saved_stem

            # --- persistence.history: per-target read-only history ------
            index = studio.persistence.history_index(saved_path)
            assert index["session_name"] == saved_stem
            assert "scout" in index["targets"]
            scout_history = studio.persistence.history(saved_path, "scout")
            assert scout_history["session_name"] == saved_stem
            assert scout_history["meta"]["agents"] == ["scout"]
            # An unknown target is a hard 404, never a fake-empty payload.
            with pytest.raises(HTTPException) as exc_info:
                studio.persistence.history(saved_path, "no-such-target")
            assert exc_info.value.status_code == 404

            # --- persistence.viewer.diff: parent vs fork ---------------
            # The fork dropped the trailing turn, so the diff is a
            # structured comparison sliced to the one agent on each side.
            diff = studio.persistence.viewer.diff(saved_path, fork_path, agent=None)
            assert diff["a"]["agent"] == "scout"
            assert diff["b"]["agent"] == "scout"
            # Parent has every message the fork has plus more.
            assert diff["a"]["total_messages"] >= diff["b"]["total_messages"]
            # An explicit agent slice produces the same scout-vs-scout
            # comparison as the auto-resolved one.
            diff_scoped = studio.persistence.viewer.diff(
                saved_path, fork_path, agent="scout"
            )
            assert diff_scoped["a"]["agent"] == "scout"
            assert diff_scoped["b"]["agent"] == "scout"
            assert (
                diff_scoped["a"]["total_messages"] >= diff_scoped["b"]["total_messages"]
            )

            # --- persistence.list sees both sessions now ----------------
            fork_stem = fork_path.name.split(".kohakutr")[0]
            both = studio.persistence.list(max_age=0.0)
            assert {saved_stem, fork_stem} <= {e["name"] for e in both}

            # --- sessions.search_memory: FTS over the saved session ----
            # The session is not live in this engine, so the search
            # opens the .kohakutr off disk. ``ping one`` was a recorded
            # user_input — an FTS query for it must hit.
            mem = await studio.sessions.search_memory(
                saved_path, q="ping", mode="fts", k=10, agent=None, engine=studio.engine
            )
            assert mem["query"] == "ping"
            assert mem["mode"] == "fts"
            assert mem["session_name"] == saved_stem
            assert mem["count"] >= 1
            # ``auto`` mode resolves the embedder + falls back to FTS when
            # no vector index exists — still hits the recorded turn.
            mem_auto = await studio.sessions.search_memory(
                saved_path,
                q="ping",
                mode="auto",
                k=5,
                agent=None,
                engine=studio.engine,
            )
            assert mem_auto["mode"] == "auto"
            assert mem_auto["k"] == 5
            assert mem_auto["count"] >= 1
            # An agent-scoped search narrows to the named creature; every
            # hit is attributed to ``scout``.
            mem_scoped = await studio.sessions.search_memory(
                saved_path,
                q="ping",
                mode="fts",
                k=10,
                agent="scout",
                engine=studio.engine,
            )
            assert all(r["agent"] == "scout" for r in mem_scoped["results"])
            # A query with no matches is a clean empty result, not an error.
            mem_miss = await studio.sessions.search_memory(
                saved_path,
                q="zzz-no-such-token-zzz",
                mode="fts",
                k=10,
                agent=None,
                engine=studio.engine,
            )
            assert mem_miss["count"] == 0
            assert mem_miss["results"] == []

    @pytest.mark.timeout(90)
    async def test_runtime_session_mutation(self, scripted_llm, isolated_paths):
        """Drive the per-creature runtime mutation surface end-to-end.

        sessions.start_creature
            -> chat one turn
            -> sessions.state.patch_scratchpad mutates + reads back
            -> identity.keys.set + identity.llm.save_profile, then
               sessions.model.switch flips the creature's live model
            -> sessions.plugins.toggle flips a plugin on and back off
            -> sessions.command.execute runs a slash command
            -> attach.policies_for_creature / _for_session advertise

        Mirrors ``api/routes/sessions_v2/creatures_ctl.py`` +
        ``creatures_chat.py`` + ``api/routes/attach/policies.py``.
        """
        scripted_llm["script"] = ["Acknowledged."]
        workspace_root = isolated_paths["tmp_path"] / "rt_workspace"
        workspace_root.mkdir()

        async with Studio() as studio:
            creature_dir = _scaffold_creature(studio, workspace_root, "operator")
            session = await studio.sessions.start_creature(str(creature_dir))
            session_id = session.session_id
            creature_id = session.creatures[0]["creature_id"]

            out = await _drain_chat(studio, session_id, creature_id, "do the thing")
            assert out == "Acknowledged."

            # --- lifecycle lookups: get / find by id + name -------------
            # ``get_session`` rebuilds a full handle from the graph id.
            handle = studio.sessions.get(session_id)
            assert handle.session_id == session_id
            assert handle.name == "operator"
            assert [c["creature_id"] for c in handle.creatures] == [creature_id]
            # ``find_creature`` resolves a creature by id and by name.
            by_id = studio.sessions.find_creature(session_id, creature_id)
            by_name = studio.sessions.find_creature(session_id, "operator")
            assert by_id.creature_id == creature_id
            assert by_name.creature_id == creature_id
            # ``find_session_for_creature`` maps a creature back to its
            # owning graph. It routes through the service Protocol, so it
            # resolves worker-hosted creatures too — hence ``await``.
            assert (
                await studio.sessions.find_session_for_creature(creature_id)
                == session_id
            )
            # An unknown graph id is a hard KeyError, never a fake handle.
            with pytest.raises(KeyError):
                studio.sessions.get("no-such-session")

            # --- scratchpad: patch then read back -----------------------
            patched = studio.sessions.state.patch_scratchpad(
                session_id, creature_id, {"phase": "running", "owner": "operator"}
            )
            assert patched == {"phase": "running", "owner": "operator"}
            assert studio.sessions.state.scratchpad(session_id, creature_id) == {
                "phase": "running",
                "owner": "operator",
            }
            # Deleting a key (value None) removes it.
            after_delete = studio.sessions.state.patch_scratchpad(
                session_id, creature_id, {"owner": None}
            )
            assert after_delete == {"phase": "running"}

            # --- model switch: needs a key + a profile ------------------
            studio.identity.keys.set("openai", "sk-integration-test-key")
            studio.identity.llm.save_profile("rt-fast", "gpt-4o-mini", "openai")
            new_model = studio.sessions.model.switch(
                session_id, creature_id, "openai/rt-fast"
            )
            assert new_model == "openai/rt-fast"
            # The live creature status reflects the swap.
            status = studio.sessions.list_creatures(session_id)[0]
            assert status["model"] == "gpt-4o-mini"
            assert status["llm_name"] == "openai/rt-fast"

            # --- plugin toggle: flip on, then back off ------------------
            plugins = studio.sessions.plugins.list(session_id, creature_id)
            assert any(p["name"] == "sandbox" for p in plugins)
            assert all(p["enabled"] is False for p in plugins)
            toggled_on = await studio.sessions.plugins.toggle(
                session_id, creature_id, "sandbox"
            )
            assert toggled_on == {"name": "sandbox", "enabled": True}
            toggled_off = await studio.sessions.plugins.toggle(
                session_id, creature_id, "sandbox"
            )
            assert toggled_off == {"name": "sandbox", "enabled": False}
            # An unknown plugin name is a hard KeyError, never a fake OK.
            with pytest.raises(KeyError):
                await studio.sessions.plugins.toggle(
                    session_id, creature_id, "no-such-plugin"
                )

            # --- slash command execution --------------------------------
            cmd_result = await studio.sessions.command.execute(
                session_id, creature_id, "status", ""
            )
            assert cmd_result["command"] == "status"
            assert cmd_result["success"] is True

            # --- chat history mutation: rewind drops the tail -----------
            # Conversation before rewind: [system, user, assistant].
            pre_rewind = studio.sessions.chat.history(session_id, creature_id)
            assert pre_rewind["messages"][-1]["role"] == "assistant"
            # Rewind to msg index 1 (the user message) — drops the
            # assistant reply without re-running.
            await studio.sessions.chat.rewind(session_id, creature_id, 1)
            post_rewind = studio.sessions.chat.history(session_id, creature_id)
            assert all(m["role"] != "assistant" for m in post_rewind["messages"])

            # --- channels: declare + introspect + broadcast -------------
            ch = await studio.sessions.add_channel(
                session_id, "ops", description="ops channel"
            )
            assert ch == {
                "name": "ops",
                "type": "broadcast",
                "description": "ops channel",
            }
            from kohakuterrarium.studio.sessions import topology as _topology

            channels = await _topology.list_channels(studio.service, session_id)
            assert [c["name"] for c in channels] == ["ops"]
            info = await _topology.channel_info(studio.service, session_id, "ops")
            assert info["name"] == "ops"
            assert info["scope"] == "shared"
            # Sending to a non-existent channel is a hard ValueError.
            with pytest.raises(ValueError):
                await _topology.send_to_channel(
                    studio.service, session_id, "ghost-channel", "hi"
                )
            msg_id = await _topology.send_to_channel(
                studio.service, session_id, "ops", "status check", sender="human"
            )
            assert msg_id

            # --- output wiring: list the creature's edges ---------------
            wiring = studio.sessions.list_output_wiring(creature_id)
            assert wiring == []

            # --- attach: policy advertisement ---------------------------
            from kohakuterrarium.studio.attach.policies import Policy

            creature_policies = studio.attach.policies_for_creature(creature_id)
            # The creature has no input module, so no IO — but the
            # ``ops`` channel we declared above puts it in a graph with
            # shared channels, so OBSERVER is now advertised on top of
            # the LOG + TRACE baseline.
            assert creature_policies == [Policy.LOG, Policy.TRACE, Policy.OBSERVER]
            session_policies = studio.attach.policies_for_session(session_id)
            # The session's privileged creature makes IO available; a
            # graph always advertises OBSERVER.
            assert Policy.IO in session_policies
            assert Policy.OBSERVER in session_policies

            await studio.sessions.stop(session_id)
            # After stop, the creature is gone from the engine.
            assert studio.sessions.list() == []

            # --- terrarium: a multi-creature graph + hot-plug + wiring --
            # Author a real recipe on disk and start it through the
            # façade — mirrors ``cli/run.py`` + the terrarium HTTP route.
            recipe = isolated_paths["tmp_path"] / "rt_team.yaml"
            recipe.write_text(
                "terrarium:\n"
                "  name: rt-team\n"
                "  channels:\n"
                "    ops-net:\n"
                "      description: ops coordination\n"
                "  creatures:\n"
                "    - name: lead\n"
                "      system_prompt: You are lead.\n"
                "      tool_format: bracket\n"
                "      input:\n"
                "        type: none\n"
                "      output:\n"
                "        type: stdout\n"
                "      channels:\n"
                "        listen: [ops-net]\n"
                "        can_send: [ops-net]\n"
                "    - name: worker\n"
                "      system_prompt: You are worker.\n"
                "      tool_format: bracket\n"
                "      input:\n"
                "        type: none\n"
                "      output:\n"
                "        type: stdout\n"
                "      channels:\n"
                "        listen: [ops-net]\n",
                encoding="utf-8",
            )
            team = await studio.sessions.start_terrarium(str(recipe))
            assert team.name == "rt-team"
            team_sid = team.session_id
            assert {c["name"] for c in team.creatures} == {"lead", "worker"}
            # The graph carries the declared channel + per-creature
            # implicit direct channels.
            assert {c["name"] for c in team.channels} == {
                "ops-net",
                "lead",
                "worker",
            }
            # Hot-plug a third creature into the running graph.
            from kohakuterrarium.terrarium.config import CreatureConfig

            hp_cfg = CreatureConfig(
                name="scout-hp",
                config_data={
                    "name": "scout-hp",
                    "system_prompt": "You are a hot-plugged scout.",
                    "tool_format": "bracket",
                    "input": {"type": "none"},
                    "output": {"type": "stdout"},
                },
                base_dir=isolated_paths["tmp_path"],
            )
            hp_id = await studio.sessions.add_creature(team_sid, hp_cfg)
            assert hp_id
            after_add = {c["name"] for c in studio.sessions.list_creatures(team_sid)}
            assert after_add == {"lead", "worker", "scout-hp"}
            # Wire the hot-plugged creature's output to the lead creature.
            edge_id = await studio.sessions.wire_output(hp_id, "lead")
            assert edge_id
            hp_wiring = studio.sessions.list_output_wiring(hp_id)
            assert [w["to"] for w in hp_wiring] == ["lead"]
            assert hp_wiring[0]["id"] == edge_id
            # Unwire it by edge id — the edge list goes back to empty.
            assert await studio.sessions.unwire_output(hp_id, edge_id) is True
            assert studio.sessions.list_output_wiring(hp_id) == []
            # Remove the hot-plugged creature — the graph shrinks back.
            assert await studio.sessions.remove_creature(team_sid, hp_id) is True
            assert {c["name"] for c in studio.sessions.list_creatures(team_sid)} == {
                "lead",
                "worker",
            }
            # Removing it twice is a clean False, never a hard error.
            assert await studio.sessions.remove_creature(team_sid, hp_id) is False
            # Broadcast a message onto the declared channel.
            team_msg = await _topology.send_to_channel(
                studio.service, team_sid, "ops-net", "all hands", sender="human"
            )
            assert team_msg
            await studio.sessions.stop(team_sid)
            assert studio.sessions.list() == []

    async def test_identity_and_catalog_surface(self, scripted_llm, isolated_paths):
        """The config + catalog tier — all CRUD round-trips through the façade.

        identity.keys  : set / get / list / delete
        identity.llm   : save_profile / list_profiles / set_default /
                         get_default / delete_profile
        identity.mcp   : upsert / find / list / delete
        identity.ui_prefs : save / load
        catalog.builtins  : list(kind) / info(name)
        catalog.introspect: builtin_schema(kind)

        Mirrors ``api/routes/identity/*`` and ``api/routes/catalog/*``
        plus the ``cli/identity_*`` subcommands — all of which delegate
        straight into these studio functions.
        """
        async with Studio() as studio:
            # --- identity.keys ------------------------------------------
            studio.identity.keys.set("openai", "sk-key-one-2345")
            assert studio.identity.keys.get("openai") == "sk-key-one-2345"
            keys_listing = studio.identity.keys.list()
            openai_entry = next(k for k in keys_listing if k["provider"] == "openai")
            assert openai_entry["has_key"] is True
            studio.identity.keys.delete("openai")
            assert studio.identity.keys.get("openai") == ""

            # --- identity.llm: backend CRUD -----------------------------
            # The six built-in backends ship with every install; no
            # user backends until we add one.
            baseline_backends = {b["name"] for b in studio.identity.llm.list_backends()}
            assert baseline_backends == {
                "codex",
                "openai",
                "openrouter",
                "anthropic",
                "gemini",
                "mimo",
            }
            studio.identity.llm.save_backend(
                "acme",
                "openai",
                base_url="https://acme.example/v1",
                api_key_env="ACME_API_KEY",
            )
            acme = next(
                b for b in studio.identity.llm.list_backends() if b["name"] == "acme"
            )
            assert acme["base_url"] == "https://acme.example/v1"
            assert acme["built_in"] is False
            assert acme["has_token"] is False
            # An unsupported backend type is a hard ValueError.
            with pytest.raises(ValueError):
                studio.identity.llm.save_backend("bad", "not-a-real-type")

            # --- identity.llm: profile CRUD + default model -------------
            assert studio.identity.llm.list_profiles() == []
            saved = studio.identity.llm.save_profile(
                "house-model", "gpt-4o", "openai", max_context=200000
            )
            assert saved.name == "house-model"
            profiles = studio.identity.llm.list_profiles()
            assert [p["name"] for p in profiles] == ["house-model"]
            assert profiles[0]["model"] == "gpt-4o"
            assert profiles[0]["max_context"] == 200000
            studio.identity.llm.set_default("openai/house-model")
            assert studio.identity.llm.get_default() == "openai/house-model"
            resolved = studio.identity.llm.get_profile("openai/house-model")
            assert resolved is not None and resolved.model == "gpt-4o"
            assert studio.identity.llm.delete_profile("house-model", "openai") is True
            assert studio.identity.llm.list_profiles() == []

            # --- identity.llm: combined model list + native tools ------
            # With no user profiles the combined list is built purely
            # from backend presets — it is non-empty and every entry
            # carries a ``name`` selector.
            all_models = studio.identity.llm.list_models()
            assert all_models and all("name" in m for m in all_models)
            # Native-tool descriptors are a fixed catalog.
            native_tools = studio.identity.llm.list_native_tools()
            assert native_tools and all("name" in t for t in native_tools)

            # --- identity.llm: delete the user backend ------------------
            assert studio.identity.llm.delete_backend("acme") is True
            assert "acme" not in {
                b["name"] for b in studio.identity.llm.list_backends()
            }
            # Built-in backends cannot be deleted.
            with pytest.raises(ValueError):
                studio.identity.llm.delete_backend("openai")

            # --- identity.settings: config path map ---------------------
            paths = studio.identity.settings.paths()
            # Every value is a Path; the keys name the on-disk stores.
            assert all(isinstance(p, Path) for p in paths.values())

            # --- identity.mcp: server registry CRUD ---------------------
            assert studio.identity.mcp.list() == []
            studio.identity.mcp.upsert(
                {"name": "fs-server", "command": "mcp-fs", "args": ["--root", "/tmp"]}
            )
            assert studio.identity.mcp.find("fs-server") == {
                "name": "fs-server",
                "command": "mcp-fs",
                "args": ["--root", "/tmp"],
            }
            # Upsert replaces by name.
            studio.identity.mcp.upsert(
                {"name": "fs-server", "command": "mcp-fs-v2", "args": []}
            )
            assert studio.identity.mcp.find("fs-server")["command"] == "mcp-fs-v2"
            assert [s["name"] for s in studio.identity.mcp.list()] == ["fs-server"]
            assert studio.identity.mcp.delete("fs-server") is True
            assert studio.identity.mcp.delete("fs-server") is False
            assert studio.identity.mcp.list() == []

            # --- identity.ui_prefs --------------------------------------
            # Default theme until a save lands.
            assert studio.identity.ui_prefs.load()["theme"] == "system"
            studio.identity.ui_prefs.save({"theme": "dark", "nav-expanded": False})
            reloaded = studio.identity.ui_prefs.load()
            assert reloaded["theme"] == "dark"
            assert reloaded["nav-expanded"] is False

            # --- catalog.builtins: read-only catalog --------------------
            tool_entries = studio.catalog.builtins.list("tools")
            tool_names = {t["name"] for t in tool_entries}
            # bash + read + write are core builtins every install ships.
            assert {"bash", "read", "write"} <= tool_names
            bash_info = studio.catalog.builtins.info("bash")
            assert bash_info is not None
            assert bash_info["name"] == "bash"
            assert bash_info["source"] == "builtin"
            subagent_entries = studio.catalog.builtins.list("subagents")
            assert {"explore", "plan"} <= {s["name"] for s in subagent_entries}
            # Triggers are a first-class builtin kind too.
            trigger_names = {
                t["name"] for t in studio.catalog.builtins.list("triggers")
            }
            assert "add_timer" in trigger_names
            # ``list()`` with no kind returns the union of every kind.
            all_builtins = {b["name"] for b in studio.catalog.builtins.list()}
            assert {"bash", "explore", "add_timer"} <= all_builtins
            # An unknown builtin kind is a hard error, not a silent empty.
            with pytest.raises(ValueError):
                studio.catalog.builtins.list("nonsense-kind")
            assert studio.catalog.builtins.info("definitely-not-a-builtin") is None
            # ``info`` resolves a sub-agent by name just like a tool.
            explore_info = studio.catalog.builtins.info("explore")
            assert explore_info["name"] == "explore"

            # --- catalog.introspect: builtin schema ---------------------
            tool_schema = studio.catalog.introspect.builtin_schema("tools")
            param_names = {p["name"] for p in tool_schema["params"]}
            assert "timeout" in param_names
            # The triggers-kind schema is a distinct shape with its own
            # params surface.
            trigger_schema = studio.catalog.introspect.builtin_schema("triggers")
            assert "params" in trigger_schema

            # --- catalog.packages: the installed-package catalog --------
            # The repo ships the ``kt-biome`` default package installed
            # editable, so both the installed-list and the catalog scan
            # surface it.
            installed = studio.catalog.packages.list()
            assert "kt-biome" in {p["name"] for p in installed}
            biome = next(p for p in installed if p["name"] == "kt-biome")
            assert biome["editable"] is True
            assert "general" in {c["name"] for c in biome["creatures"]}
            # The catalog scan walks every creature/terrarium in the
            # package — ``general`` is the base creature kt-biome ships.
            scanned = studio.catalog.packages.scan()
            assert "general" in {e.name for e in scanned}

    async def test_search_memory_releases_file_handle(
        self, scripted_llm, isolated_paths
    ):
        """A search over a saved session must not leave the .kohakutr
        locked — deleting it right after the search should succeed.

        Regression guard for B-fat-studio-1 (FIXED):
        ``search_session_memory`` constructed a ``SessionMemory`` over
        the saved ``.kohakutr`` but never closed it — ``SessionMemory``
        opens its own ``TextVault`` / ``KVault`` / ``VectorKVault``
        SQLite handles, and only the ``SessionStore`` was closed. On
        Windows the leaked handles kept the file locked, so a subsequent
        ``persistence.delete`` failed with WinError 32. The fix adds
        ``SessionMemory.close()`` and calls it from ``memory_search``.
        """
        scripted_llm["script"] = ["A reply."]
        workspace_root = isolated_paths["tmp_path"] / "mem_workspace"
        workspace_root.mkdir()
        session_dir = isolated_paths["session_dir"]

        async with Studio() as studio:
            creature_dir = _scaffold_creature(studio, workspace_root, "archivist")
            session = await studio.sessions.start_creature(str(creature_dir))
            session_id = session.session_id
            creature_id = session.creatures[0]["creature_id"]
            await _drain_chat(studio, session_id, creature_id, "index this turn")
            await studio.sessions.stop(session_id)

        saved_files = sorted(session_dir.glob("*.kohakutr"))
        assert len(saved_files) == 1
        saved_path = saved_files[0]
        saved_stem = saved_path.name.split(".kohakutr")[0]

        async with Studio() as studio:
            mem = await studio.sessions.search_memory(
                saved_path,
                q="index",
                mode="fts",
                k=10,
                agent=None,
                engine=studio.engine,
            )
            assert mem["count"] >= 1
            # The search is done — the file must be releasable. With the
            # leak this delete raises PermissionError on Windows.
            removed = studio.persistence.delete(saved_stem)
            assert all(not p.exists() for p in removed)
