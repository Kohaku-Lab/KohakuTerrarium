"""E2E journey — programmatic Studio usage end to end.

One fat journey per test function: each simulates an entire user
session driving the :class:`Studio` façade across all six namespaces
(``catalog`` / ``editors`` / ``sessions`` / ``persistence`` /
``identity`` / ``attach``) — the way a real user drives Studio
programmatically (the ``api/routes/*`` and ``cli/*`` consumers funnel
through this same surface).

The journey is NOT one operation per test. ``TestProgStudioJourney``
has two journey methods, each a single function doing a whole
workflow in sequence and asserting observable state at every
milestone:

* ``test_workspace_session_persistence_journey`` — the headline arc:
  browse builtin catalogs -> scaffold + edit a workspace creature and
  one of its modules + write its system prompt -> start a session
  from it -> multi-turn chat -> mutate runtime state (scratchpad
  patch, model switch, plugin toggle) -> save -> list saved ->
  open the viewer panes (tree / summary / turns / events) -> resume
  -> fork -> delete.
* ``test_identity_and_attach_journey`` — the config + attach arc:
  LLM profile + key + default-model + MCP registry + UI-prefs CRUD
  round-trips, then start a creature and read its attach policies.

The ONLY seam is the LLM: BOTH ``create_llm_provider`` import sites
(``bootstrap.llm`` and ``bootstrap.agent_init``) are monkeypatched to
a :class:`ScriptedLLM`. Every other collaborator is real — a real
:class:`Studio` over a real :class:`Terrarium` wrapped in a real
:class:`LocalTerrariumService`, real on-disk ``.kohakutr`` files in a
``tmp_path`` dir, real workspace directories, the real identity YAML
stores (redirected into ``tmp_path``).

No shape asserts: every assertion pins an exact value or an
observable side effect.
"""

from pathlib import Path

import pytest
from fastapi import HTTPException

from kohakuterrarium.bootstrap import agent_init as _agent_init_mod
from kohakuterrarium.bootstrap import llm as _bootstrap_llm_mod
from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.studio.attach.policies import Policy
from kohakuterrarium.studio.editors.workspace_fs import LocalWorkspace
from kohakuterrarium.studio.persistence import store as _persistence_store_mod
from kohakuterrarium.studio.studio import Studio
from kohakuterrarium.testing.llm import ScriptedLLM

pytestmark = pytest.mark.timeout(30)


# ---------------------------------------------------------------------------
# Fixtures — the LLM seam + every module-global path redirected to tmp_path.
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
    """Redirect every studio module-global path into ``tmp_path``.

    Studio's ``identity`` namespace and the ``persistence`` saved-list
    read constants resolved at import time off ``Path.home()``. Without
    this redirect the journey would read/write the real user's
    ``~/.kohakuterrarium`` state. ``KT_SESSION_DIR`` is the documented
    override both ``sessions.lifecycle`` and ``persistence.store``
    honour for the on-disk ``.kohakutr`` files.
    """
    session_dir = tmp_path / "sessions"
    session_dir.mkdir()
    monkeypatch.setenv("KT_SESSION_DIR", str(session_dir))
    # Identity stores resolve through ``config_dir()`` / ``KT_CONFIG_DIR``
    # every call — the ``PROFILES_PATH`` / ``KEYS_PATH`` / … constants
    # are display-only.  Override via env so saves land in tmp.
    monkeypatch.setenv("KT_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(
        _persistence_store_mod, "_SESSION_DIR", session_dir, raising=False
    )
    return {"session_dir": session_dir, "tmp_path": tmp_path}


# ---------------------------------------------------------------------------
# Helpers — the documented drives, kept thin so the journey reads top-down.
# ---------------------------------------------------------------------------


async def _drain_chat(
    studio: Studio, session_id: str, creature_id: str, msg: str
) -> str:
    """Consume ``sessions.chat.chat`` to completion — the documented drive."""
    chunks: list[str] = []
    async for chunk in studio.sessions.chat.chat(session_id, creature_id, msg):
        chunks.append(chunk)
    return "".join(chunks)


# ---------------------------------------------------------------------------
# The e2e journey suite.
# ---------------------------------------------------------------------------


class TestProgStudioJourney:
    """Each method is one fat end-to-end Studio journey."""

    async def test_workspace_session_persistence_journey(
        self, scripted_llm, isolated_paths
    ):
        """The headline programmatic-Studio arc, start to finish.

        catalog.builtins browse
            -> editors.creatures.scaffold + save + write_prompt
            -> editors.modules.scaffold + save a workspace tool
            -> catalog.creatures / catalog.modules see the edits
            -> sessions.start_creature mints a 1-creature graph
            -> sessions.chat.chat streams two turns; history persists them
            -> sessions.state.patch_scratchpad mutates runtime state
            -> identity key+profile, then sessions.model.switch flips
               the live model
            -> sessions.plugins.toggle flips a plugin on and back off
            -> sessions.stop flushes the .kohakutr file
            -> persistence.list surfaces it
            -> persistence.viewer.{tree,summary,turns,events} read it back
            -> persistence.resume adopts it into a fresh engine; the
               resumed conversation carries the turns forward
            -> persistence.fork branches it at the user message
            -> persistence.delete removes the original's files
        """
        scripted_llm["script"] = [
            "First reply.",
            "Second reply.",
            "Regenerated reply.",
        ]
        tmp_path: Path = isolated_paths["tmp_path"]
        session_dir: Path = isolated_paths["session_dir"]
        workspace_root = tmp_path / "workspace"
        workspace_root.mkdir()
        creatures_dir = workspace_root / "creatures"

        async with Studio() as studio:
            # --- catalog: browse the builtin extension catalogs ---------
            tool_names = {t["name"] for t in studio.catalog.builtins.list("tools")}
            assert {"bash", "read", "write"} <= tool_names
            subagent_names = {
                s["name"] for s in studio.catalog.builtins.list("subagents")
            }
            assert {"explore", "plan"} <= subagent_names
            bash_info = studio.catalog.builtins.info("bash")
            assert bash_info["name"] == "bash"
            assert bash_info["source"] == "builtin"
            assert studio.catalog.builtins.info("not-a-real-builtin") is None
            # The introspection schema for the tools kind exposes params.
            tool_schema = studio.catalog.introspect.builtin_schema("tools")
            assert "timeout" in {p["name"] for p in tool_schema["params"]}

            # --- editors: scaffold + edit a workspace creature ----------
            creature_dir = studio.editors.creatures.scaffold(
                creatures_dir, "scout", None
            )
            assert creature_dir.is_dir()
            assert (creature_dir / "config.yaml").exists()
            studio.editors.creatures.write_prompt(
                creatures_dir,
                "scout",
                "prompts/system.md",
                "# scout\nYou are the scout test creature. Reply tersely.",
            )
            studio.editors.creatures.save(
                creatures_dir,
                "scout",
                {
                    "config": {
                        "name": "scout",
                        "version": "1.0",
                        "system_prompt_file": "prompts/system.md",
                        "description": "an e2e-journey scout",
                    },
                    "prompts": {},
                },
            )

            # --- editors: scaffold + edit a workspace module (a tool) ---
            ws = LocalWorkspace.open(workspace_root)
            scaffolded = ws.scaffold_module("tools", "ping_tool", None)
            # ``path`` is workspace-relative; the file lands on disk under
            # the workspace root.
            module_path = workspace_root / scaffolded["path"]
            assert module_path.exists()
            edited_source = scaffolded["raw_source"].replace(
                "TODO: describe this tool", "an e2e-journey ping tool"
            )
            ws.save_module(
                "tools",
                "ping_tool",
                {"mode": "raw", "raw_source": edited_source},
            )
            assert "an e2e-journey ping tool" in module_path.read_text(encoding="utf-8")
            # A simple-mode save patches the tool through its codegen
            # ``update_existing`` libcst path.
            ws.save_module(
                "tools",
                "ping_tool",
                {
                    "mode": "simple",
                    "form": {"description": "a re-saved e2e ping tool"},
                    "execute_body": 'return ToolResult(output="pong")',
                },
            )
            assert "pong" in module_path.read_text(encoding="utf-8")

            # --- editors: author a module of EVERY remaining kind -------
            # Each kind dispatches through its own codegen module
            # (subagent / plugin / trigger / io). Scaffold, load it back
            # (parse_back), raw-mode round-trip, then sync into the
            # manifest so the catalog can discover it.
            for mkind in ("subagents", "plugins", "triggers", "inputs", "outputs"):
                mname = f"{mkind[:-1]}_unit"
                scaffolded_k = ws.scaffold_module(mkind, mname, None)
                kpath = workspace_root / scaffolded_k["path"]
                assert kpath.exists()
                assert scaffolded_k["kind"] == mkind
                reloaded_k = ws.load_module(mkind, mname)
                assert reloaded_k["name"] == mname
                ws.save_module(
                    mkind,
                    mname,
                    {
                        "mode": "raw",
                        "raw_source": reloaded_k["raw_source"] + "\n# journeyed\n",
                    },
                )
                assert "# journeyed" in kpath.read_text(encoding="utf-8")
                synced = ws.sync_manifest(mkind, mname)
                assert synced["ok"] is True
            # A raw save with an empty body is rejected outright.
            with pytest.raises(ValueError):
                ws.save_module("tools", "ping_tool", {"mode": "raw", "raw_source": ""})
            # Scaffolding a duplicate name is a hard FileExistsError.
            with pytest.raises(FileExistsError):
                ws.scaffold_module("tools", "ping_tool", None)
            # An unknown module kind is a hard ValueError.
            with pytest.raises(ValueError):
                ws.scaffold_module("bad-kind", "x", None)
            # introspect: builtin schemas + a custom AST parse of the
            # tool source we authored.
            sub_schema = studio.catalog.introspect.builtin_schema("subagents")
            assert "max_turns" in {p["name"] for p in sub_schema["params"]}
            custom = studio.catalog.introspect.custom_schema(
                module_path.read_text(encoding="utf-8"), None
            )
            assert "params" in custom
            broken = studio.catalog.introspect.custom_schema("def x(\n", None)
            assert [w["code"] for w in broken["warnings"]] == ["syntax_error"]

            # --- catalog: the workspace edits are visible back ----------
            listing = studio.catalog.creatures.list(ws)
            assert [c["name"] for c in listing] == ["scout"]
            assert listing[0]["description"] == "an e2e-journey scout"
            loaded = studio.catalog.creatures.get(ws, "scout")
            assert loaded["name"] == "scout"
            prompt_text = studio.catalog.creatures.read_prompt(
                ws, "scout", "prompts/system.md"
            )
            assert prompt_text.startswith("# scout")
            module_listing = studio.catalog.modules.list(ws, "tools")
            assert "ping_tool" in {m["name"] for m in module_listing}
            mod_loaded = studio.catalog.modules.get(ws, "tools", "ping_tool")
            assert mod_loaded["name"] == "ping_tool"
            # The catalog surfaces a module of every authored kind.
            for mkind in ("subagents", "plugins", "triggers", "inputs", "outputs"):
                k_listing = studio.catalog.modules.list(ws, mkind)
                assert [m["name"] for m in k_listing] == [f"{mkind[:-1]}_unit"]
                k_loaded = studio.catalog.modules.get(ws, mkind, f"{mkind[:-1]}_unit")
                assert k_loaded["name"] == f"{mkind[:-1]}_unit"
            # No skill-doc sidecar until one is written.
            mod_doc = studio.catalog.modules.doc(ws, "tools", "ping_tool")
            assert mod_doc["exists"] is False
            assert mod_doc["content"] == ""
            # Write a sidecar through the workspace, then read it back
            # via the catalog — the same drive the module editor uses.
            ws.save_module_doc("tools", "ping_tool", "# ping_tool\nProcedural notes.")
            mod_doc = studio.catalog.modules.doc(ws, "tools", "ping_tool")
            assert mod_doc["exists"] is True
            assert mod_doc["content"] == "# ping_tool\nProcedural notes."

            # --- sessions: start a session from that creature -----------
            session = await studio.sessions.start_creature(str(creature_dir))
            assert session.name == "scout"
            assert len(session.creatures) == 1
            session_id = session.session_id
            creature_id = session.creatures[0]["creature_id"]
            active = studio.sessions.list()
            assert [s.session_id for s in active] == [session_id]
            assert active[0].creatures == 1

            # --- sessions.chat: two turns, both persisted ---------------
            out1 = await _drain_chat(studio, session_id, creature_id, "ping one")
            assert out1 == "First reply."
            out2 = await _drain_chat(studio, session_id, creature_id, "ping two")
            assert out2 == "Second reply."
            history = studio.sessions.chat.history(session_id, creature_id)
            user_msgs = [
                m["content"] for m in history["messages"] if m["role"] == "user"
            ]
            assert user_msgs == ["ping one", "ping two"]
            assert history["messages"][-1]["role"] == "assistant"
            assert "Second reply." in history["messages"][-1]["content"]
            assert any(e["type"] == "user_input" for e in history["events"])
            assert history["is_processing"] is False

            # --- sessions.chat: regenerate replaces the tail reply ------
            await studio.sessions.chat.regenerate(session_id, creature_id)
            regen_history = studio.sessions.chat.history(session_id, creature_id)
            # The tail assistant message is now the regenerated text;
            # the user turns are untouched.
            assert "Regenerated reply." in regen_history["messages"][-1]["content"]
            assert [
                m["content"] for m in regen_history["messages"] if m["role"] == "user"
            ] == ["ping one", "ping two"]
            # Per-turn branch metadata now records two branches on the
            # last turn (the original + the regeneration).
            br = studio.sessions.chat.branches(session_id, creature_id)
            last_turn = br["turns"][-1]
            assert last_turn["branches"] == [1, 2]
            assert last_turn["latest_branch"] == 2

            # --- sessions.state: scratchpad + read-only runtime panes ---
            patched = studio.sessions.state.patch_scratchpad(
                session_id, creature_id, {"phase": "scouting", "owner": "scout"}
            )
            assert patched == {"phase": "scouting", "owner": "scout"}
            after_delete = studio.sessions.state.patch_scratchpad(
                session_id, creature_id, {"owner": None}
            )
            assert after_delete == {"phase": "scouting"}
            assert studio.sessions.state.scratchpad(session_id, creature_id) == {
                "phase": "scouting"
            }
            # Reserved scratchpad keys are rejected — never silently set.
            with pytest.raises(ValueError):
                studio.sessions.state.patch_scratchpad(
                    session_id, creature_id, {"__turn_count__": "9"}
                )
            # The system prompt carries the workspace creature's seeded
            # personality line; triggers list is empty for a scaffold.
            sysprompt = studio.sessions.state.system_prompt(session_id, creature_id)
            assert "scout test creature" in sysprompt["text"]
            assert studio.sessions.state.triggers(session_id, creature_id) == []
            env = studio.sessions.state.env(session_id, creature_id)
            assert env["pwd"]
            assert not any("secret" in k.lower() for k in env["env"])
            assert (
                studio.sessions.state.working_dir(session_id, creature_id) == env["pwd"]
            )
            # set_working_dir relocates the creature's pwd; the env pane
            # + working_dir read both reflect the move.
            new_wd = str((tmp_path / "relocated").resolve())
            (tmp_path / "relocated").mkdir()
            moved_wd = studio.sessions.state.set_working_dir(
                session_id, creature_id, new_wd
            )
            assert moved_wd == new_wd
            assert studio.sessions.state.working_dir(session_id, creature_id) == new_wd
            assert studio.sessions.state.env(session_id, creature_id)["pwd"] == new_wd
            # Native-tool options for a bare scaffolded creature: empty.
            assert (
                studio.sessions.model.native_tool_options(session_id, creature_id) == {}
            )

            # --- sessions.ctl: idle jobs + interrupt are clean no-ops ---
            assert await studio.sessions.ctl.list_jobs(session_id, creature_id) == []
            await studio.sessions.ctl.interrupt(session_id, creature_id)
            assert (
                await studio.sessions.ctl.cancel_job(
                    session_id, creature_id, "missing-job"
                )
                is False
            )

            # --- topology: declare a channel + broadcast a message ------
            ch = await studio.sessions.add_channel(
                session_id, "scouting-net", description="scout coordination"
            )
            assert ch == {
                "name": "scouting-net",
                "type": "broadcast",
                "description": "scout coordination",
            }
            from kohakuterrarium.studio.sessions import topology as _topology

            channels = await _topology.list_channels(studio.service, session_id)
            assert [c["name"] for c in channels] == ["scouting-net"]
            msg_id = await _topology.send_to_channel(
                studio.service, session_id, "scouting-net", "area clear", sender="human"
            )
            assert msg_id
            # Output-wiring starts empty for a freshly-scaffolded creature.
            assert studio.sessions.list_output_wiring(creature_id) == []

            # --- sessions.model: flip the live model --------------------
            studio.identity.keys.set("openai", "sk-journey-test-key")
            studio.identity.llm.save_profile("journey-fast", "gpt-4o-mini", "openai")
            new_model = studio.sessions.model.switch(
                session_id, creature_id, "openai/journey-fast"
            )
            assert new_model == "openai/journey-fast"
            status = studio.sessions.list_creatures(session_id)[0]
            assert status["model"] == "gpt-4o-mini"
            assert status["llm_name"] == "openai/journey-fast"

            # --- sessions.plugins: toggle a plugin on then off ----------
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

            # Stop the session — flushes + closes the .kohakutr file.
            await studio.sessions.stop(session_id)
            assert studio.sessions.list() == []

        # The session file landed on disk under KT_SESSION_DIR.
        saved_files = sorted(p.name for p in session_dir.glob("*.kohakutr"))
        assert len(saved_files) == 1
        saved_stem = saved_files[0].split(".kohakutr")[0]

        async with Studio() as studio:
            # --- persistence.list: the saved session is indexed --------
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
                # Type-filtered events narrow to exactly the kind asked.
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
                # Two original turns + the regeneration re-recorded the
                # second turn's user_input under a new branch.
                assert len(only_inputs["events"]) >= 2
                # Markdown + jsonl exports both render the recorded turns.
                ct_md, md_body = studio.persistence.viewer.export(
                    store, saved_stem, "md", None
                )
                assert ct_md == "text/markdown; charset=utf-8"
                assert "ping one" in md_body
                ct_jsonl, jsonl_body = studio.persistence.viewer.export(
                    store, saved_stem, "jsonl", None
                )
                assert ct_jsonl == "application/jsonl; charset=utf-8"
                assert jsonl_body.strip().count("\n") >= 1
            finally:
                store.close(update_status=False)

            # --- persistence.resume: adopt the saved session -----------
            resumed = await studio.persistence.resume(saved_path)
            assert len(resumed.creatures) == 1
            resumed_cid = resumed.creatures[0]["creature_id"]
            resumed_history = studio.sessions.chat.history(
                resumed.session_id, resumed_cid
            )
            resumed_users = [
                m["content"] for m in resumed_history["messages"] if m["role"] == "user"
            ]
            assert resumed_users == ["ping one", "ping two"]
            # The regeneration replaced the tail reply — the resumed
            # conversation carries the regenerated text forward, not the
            # superseded "Second reply.".
            assert any(
                "Regenerated reply." in (m.get("content") or "")
                for m in resumed_history["messages"]
                if m["role"] == "assistant"
            )
            await studio.sessions.stop(resumed.session_id)

            # --- persistence.fork: branch the saved session ------------
            fork_store = SessionStore(saved_path)
            try:
                events_all = [evt for _k, evt in fork_store.get_all_events()]
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
            assert fork_result["session_id"] != saved_stem

            # --- persistence.delete: remove the original's files -------
            removed = studio.persistence.delete(saved_stem)
            assert all(not p.exists() for p in removed)
            assert not saved_path.exists()
            # The fork survives the original's deletion.
            assert fork_path.exists()
            # The saved-session index reflects the deletion + the fork.
            fork_stem = fork_path.name.split(".kohakutr")[0]
            remaining = studio.persistence.list(max_age=0.0)
            remaining_names = {e["name"] for e in remaining}
            assert saved_stem not in remaining_names
            assert fork_stem in remaining_names

            # --- persistence.history: per-target read-only history -----
            # Runs on the surviving fork — read-only, no further reopen
            # of a to-be-deleted file.
            index = studio.persistence.history_index(fork_path)
            assert index["session_name"] == fork_path.stem
            assert "scout" in index["targets"]
            scout_history = studio.persistence.history(fork_path, "scout")
            assert scout_history["meta"]["agents"] == ["scout"]
            with pytest.raises(HTTPException) as exc_info:
                studio.persistence.history(fork_path, "no-such-target")
            assert exc_info.value.status_code == 404

    async def test_identity_and_attach_journey(self, scripted_llm, isolated_paths):
        """The config + attach arc, end-to-end through the façade.

        identity.keys      : set / get / list / delete
        identity.llm       : save_profile / list_profiles / set_default /
                             get_default / get_profile / delete_profile
        identity.mcp       : upsert / find / list / delete
        identity.ui_prefs  : load default / save / reload
        catalog.builtins   : a triggers-kind read to round out the catalog
            -> then start a real creature and read its attach policies:
        attach.policies_for_creature / policies_for_session
        """
        scripted_llm["script"] = ["Acknowledged.", "Team reply."]
        tmp_path: Path = isolated_paths["tmp_path"]
        workspace_root = tmp_path / "id_workspace"
        workspace_root.mkdir()
        creatures_dir = workspace_root / "creatures"

        async with Studio() as studio:
            # --- identity.keys: set / get / list / delete ---------------
            studio.identity.keys.set("openai", "sk-key-one-2345")
            assert studio.identity.keys.get("openai") == "sk-key-one-2345"
            keys_listing = studio.identity.keys.list()
            openai_entry = next(k for k in keys_listing if k["provider"] == "openai")
            assert openai_entry["has_key"] is True
            studio.identity.keys.delete("openai")
            assert studio.identity.keys.get("openai") == ""

            # --- identity.llm: backend CRUD -----------------------------
            # Six built-in backends ship with every install.
            assert {b["name"] for b in studio.identity.llm.list_backends()} == {
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
            # The combined model list surfaces the registered profile,
            # flagged as the default.
            combined = studio.identity.llm.list_models()
            house = next(
                m
                for m in combined
                if m.get("provider") == "openai" and m.get("name") == "house-model"
            )
            assert house["is_default"] is True
            # Native-tool descriptors are a fixed catalog.
            native_tools = studio.identity.llm.list_native_tools()
            assert native_tools and all("name" in t for t in native_tools)
            assert studio.identity.llm.delete_profile("house-model", "openai") is True
            assert studio.identity.llm.list_profiles() == []
            # The user backend deletes cleanly; built-ins cannot be deleted.
            assert studio.identity.llm.delete_backend("acme") is True
            with pytest.raises(ValueError):
                studio.identity.llm.delete_backend("openai")

            # --- identity.settings: config path map ---------------------
            paths = studio.identity.settings.paths()
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
            studio.identity.mcp.upsert(
                {"name": "fs-server", "command": "mcp-fs-v2", "args": []}
            )
            assert studio.identity.mcp.find("fs-server")["command"] == "mcp-fs-v2"
            assert [s["name"] for s in studio.identity.mcp.list()] == ["fs-server"]
            assert studio.identity.mcp.delete("fs-server") is True
            assert studio.identity.mcp.delete("fs-server") is False
            assert studio.identity.mcp.list() == []

            # --- identity.ui_prefs: default -> save -> reload -----------
            assert studio.identity.ui_prefs.load()["theme"] == "system"
            studio.identity.ui_prefs.save({"theme": "dark", "nav-expanded": False})
            reloaded = studio.identity.ui_prefs.load()
            assert reloaded["theme"] == "dark"
            assert reloaded["nav-expanded"] is False

            # --- catalog.builtins: triggers kind round-trip -------------
            trigger_names = {
                t["name"] for t in studio.catalog.builtins.list("triggers")
            }
            assert "add_timer" in trigger_names
            with pytest.raises(ValueError):
                studio.catalog.builtins.list("nonsense-kind")

            # --- attach: start a real creature, read its policies -------
            creature_dir = studio.editors.creatures.scaffold(
                creatures_dir, "operator", None
            )
            studio.editors.creatures.write_prompt(
                creatures_dir,
                "operator",
                "prompts/system.md",
                "# operator\nYou are the operator test creature. Reply tersely.",
            )
            session = await studio.sessions.start_creature(str(creature_dir))
            session_id = session.session_id
            creature_id = session.creatures[0]["creature_id"]

            out = await _drain_chat(studio, session_id, creature_id, "do the thing")
            assert out == "Acknowledged."

            # A standalone scaffolded creature has no input module and no
            # channels — only the engine-independent baseline.
            creature_policies = studio.attach.policies_for_creature(creature_id)
            assert creature_policies == [Policy.LOG, Policy.TRACE]
            # The session's privileged creature makes IO available; a
            # graph always advertises OBSERVER.
            session_policies = studio.attach.policies_for_session(session_id)
            assert Policy.IO in session_policies
            assert Policy.OBSERVER in session_policies

            await studio.sessions.stop(session_id)
            assert studio.sessions.list() == []

            # --- sessions.start_terrarium: a multi-creature graph -------
            # Author a real recipe on disk and start it through the
            # façade — the same drive ``cli/run.py`` + the terrarium
            # HTTP route use. Two inline NoneInput creatures wired by
            # one broadcast channel.
            recipe = tmp_path / "team.yaml"
            recipe.write_text(
                "terrarium:\n"
                "  name: id-team\n"
                "  channels:\n"
                "    findings:\n"
                "      description: shared findings\n"
                "  creatures:\n"
                "    - name: alpha\n"
                "      system_prompt: You are alpha.\n"
                "      tool_format: bracket\n"
                "      input:\n"
                "        type: none\n"
                "      output:\n"
                "        type: stdout\n"
                "      channels:\n"
                "        listen: [findings]\n"
                "        can_send: [findings]\n"
                "    - name: beta\n"
                "      system_prompt: You are beta.\n"
                "      tool_format: bracket\n"
                "      input:\n"
                "        type: none\n"
                "      output:\n"
                "        type: stdout\n"
                "      channels:\n"
                "        listen: [findings]\n",
                encoding="utf-8",
            )
            team = await studio.sessions.start_terrarium(str(recipe))
            assert team.name == "id-team"
            assert {c["name"] for c in team.creatures} == {"alpha", "beta"}
            team_sid = team.session_id
            # The graph carries the declared ``findings`` channel plus
            # the per-creature implicit direct channels.
            channel_names = {c["name"] for c in team.channels}
            assert channel_names == {"findings", "alpha", "beta"}
            findings = next(c for c in team.channels if c["name"] == "findings")
            assert findings["description"] == "shared findings"
            # The session lists alongside any others as a 2-creature graph.
            team_listing = next(
                s for s in studio.sessions.list() if s.session_id == team_sid
            )
            assert team_listing.creatures == 2
            # Chat reaches a named creature inside the graph. The
            # ScriptedLLM factory mints a fresh provider per creature,
            # so alpha replies with the first scripted entry.
            alpha_id = next(
                c["creature_id"] for c in team.creatures if c["name"] == "alpha"
            )
            team_out = await _drain_chat(studio, team_sid, alpha_id, "team status?")
            assert team_out == "Acknowledged."
            # The turn is reflected in alpha's history.
            alpha_history = studio.sessions.chat.history(team_sid, alpha_id)
            assert [
                m["content"] for m in alpha_history["messages"] if m["role"] == "user"
            ] == ["team status?"]

            # --- hot-plug a third creature into the running graph -------
            from kohakuterrarium.terrarium.config import CreatureConfig

            hp_cfg = CreatureConfig(
                name="gamma",
                config_data={
                    "name": "gamma",
                    "system_prompt": "You are gamma.",
                    "tool_format": "bracket",
                    "input": {"type": "none"},
                    "output": {"type": "stdout"},
                },
                base_dir=tmp_path,
            )
            gamma_id = await studio.sessions.add_creature(team_sid, hp_cfg)
            assert gamma_id
            assert {c["name"] for c in studio.sessions.list_creatures(team_sid)} == {
                "alpha",
                "beta",
                "gamma",
            }
            # Wire gamma's output to alpha, list the edge, unwire by id.
            edge_id = await studio.sessions.wire_output(gamma_id, "alpha")
            gamma_wiring = studio.sessions.list_output_wiring(gamma_id)
            assert [w["to"] for w in gamma_wiring] == ["alpha"]
            assert await studio.sessions.unwire_output(gamma_id, edge_id) is True
            assert studio.sessions.list_output_wiring(gamma_id) == []
            # Remove gamma — the graph shrinks back to the two
            # recipe-declared creatures.
            assert await studio.sessions.remove_creature(team_sid, gamma_id) is True
            assert {c["name"] for c in studio.sessions.list_creatures(team_sid)} == {
                "alpha",
                "beta",
            }
            # Removing it again is a clean False, not a hard error.
            assert await studio.sessions.remove_creature(team_sid, gamma_id) is False

            # --- declare a fresh channel on the running graph -----------
            from kohakuterrarium.studio.sessions import topology as _topology

            extra_ch = await studio.sessions.add_channel(
                team_sid, "side-net", description="side channel"
            )
            assert extra_ch == {
                "name": "side-net",
                "type": "broadcast",
                "description": "side channel",
            }
            extra_channels = await _topology.list_channels(studio.service, team_sid)
            assert "side-net" in {c["name"] for c in extra_channels}

            await studio.sessions.stop(team_sid)
            assert studio.sessions.list() == []

    async def test_same_name_creatures_do_not_share_scratchpad(
        self, scripted_llm, isolated_paths
    ):
        """A re-started creature of the same name must not inherit the
        previous instance's scratchpad.

        Start a creature named ``scout``, patch its scratchpad, stop it.
        Start a *fresh* ``scout`` from a different config directory and
        read its scratchpad — it must be empty.

        Regression guard for B-fat-studio-2 (FIXED): the per-creature
        ``core.Session`` was resolved via ``get_session(config.name)`` —
        the process-global ``core/session.py:_sessions`` cache keyed by
        creature *name* — so two creatures sharing a name silently
        shared a ``Session`` / ``Scratchpad``. The fix: an agent with no
        explicit ``config.session_key`` now gets a fresh private
        ``Session``; the global registry is used only when a
        ``session_key`` is explicitly configured (the opt-in to
        intentional sharing between cooperating agents).
        """
        scripted_llm["script"] = ["A reply.", "B reply."]
        tmp_path: Path = isolated_paths["tmp_path"]
        ws_a = tmp_path / "ws_one"
        ws_a.mkdir()
        ws_b = tmp_path / "ws_two"
        ws_b.mkdir()

        async with Studio() as studio:
            dir_a = studio.editors.creatures.scaffold(ws_a / "creatures", "scout", None)
            studio.editors.creatures.write_prompt(
                ws_a / "creatures",
                "scout",
                "prompts/system.md",
                "# scout\nYou are scout.",
            )
            sess_a = await studio.sessions.start_creature(str(dir_a))
            cid_a = sess_a.creatures[0]["creature_id"]
            patched = studio.sessions.state.patch_scratchpad(
                sess_a.session_id, cid_a, {"first_run": "value-from-run-one"}
            )
            assert patched["first_run"] == "value-from-run-one"
            await studio.sessions.stop(sess_a.session_id)

            # A second creature, same name, different config dir — a
            # brand-new instance whose scratchpad must start empty.
            dir_b = studio.editors.creatures.scaffold(ws_b / "creatures", "scout", None)
            studio.editors.creatures.write_prompt(
                ws_b / "creatures",
                "scout",
                "prompts/system.md",
                "# scout\nYou are scout.",
            )
            sess_b = await studio.sessions.start_creature(str(dir_b))
            cid_b = sess_b.creatures[0]["creature_id"]
            assert studio.sessions.state.scratchpad(sess_b.session_id, cid_b) == {}
            await studio.sessions.stop(sess_b.session_id)
