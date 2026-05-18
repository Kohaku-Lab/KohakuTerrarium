"""Integration test for :mod:`kohakuterrarium.prompt`.

This file is the comprehensive *usage example* of the ``prompt/`` package
— it drives prompt assembly exactly the way the framework does, never by
poking the aggregator's private helpers.

How the codebase actually uses ``prompt/``:

* ``bootstrap/agent_init._init_controller`` calls
  :func:`prompt.aggregator.aggregate_system_prompt` at *agent
  construction* time to build the controller's system prompt from the
  base ``system.md`` + the auto-generated tool list + framework hints +
  runtime-plugin contributions.
* The controller then loads *on-demand* tool docs when the model emits
  an ``##info <tool>##`` command (``commands/read.InfoCommand`` →
  ``builtin_skills``). Dynamic mode keeps full docs OUT of the system
  prompt; ``info`` pulls them in per request.
* ``prompt.template.render_template`` powers the Jinja-like base-prompt
  rendering and raises :class:`jinja2.TemplateSyntaxError` on bad syntax.

So each test below builds a *real* ``Agent`` from an on-disk config —
``config.yaml`` + ``prompts/system.md`` + declared builtin tools + a
real custom plugin module that contributes a prompt fragment — and then
runs a *real* turn through a ``ScriptedLLM``. The LLM is the only seam.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
from jinja2 import TemplateSyntaxError

from kohakuterrarium.builtins.tools.read import ReadTool
from kohakuterrarium.core.agent import Agent
from kohakuterrarium.core.registry import Registry
from kohakuterrarium.modules.subagent.config import SubAgentConfig
from kohakuterrarium.modules.tool.base import BaseTool, ExecutionMode, ToolResult
from kohakuterrarium.prompt.aggregator import (
    aggregate_system_prompt,
    aggregate_with_plugins,
    build_context_message,
)
from kohakuterrarium.prompt.framework_hints import (
    HINT_OUTPUT_MODEL,
    canonical_keys,
    get_framework_hint,
    merge_overrides,
)
from kohakuterrarium.prompt.tool_contributions import (
    build_tool_guidance_section,
    collect_tool_contributions,
)
from kohakuterrarium.prompt.loader import (
    load_prompt,
    load_prompt_with_fallback,
    load_prompts_folder,
)
from kohakuterrarium.prompt.plugins import (
    EnvInfoPlugin,
    ProjectInstructionsPlugin,
    ToolListPlugin,
    create_plugin,
    get_default_plugins,
    get_swe_plugins,
)
from kohakuterrarium.prompt.template import (
    PromptTemplate,
    render_template,
    render_template_safe,
)
from kohakuterrarium.testing.llm import ScriptedLLM, ScriptEntry
from kohakuterrarium.testing.output import OutputRecorder

pytestmark = pytest.mark.timeout(30)


# ---------------------------------------------------------------------------
# On-disk agent config builder
# ---------------------------------------------------------------------------

# A real plugin module dropped into the agent's ``custom/`` folder. It is
# loaded by ``ModuleLoader`` exactly like a user-shipped plugin and its
# ``get_prompt_content`` fragment must land in the assembled system
# prompt (between tool guidance and framework hints).
_PLUGIN_SOURCE = textwrap.dedent('''
    """A real prompt-contributing plugin for the prompt integration test."""

    from kohakuterrarium.modules.plugin.base import BasePlugin


    class BannerPlugin(BasePlugin):
        name = "banner"
        priority = 50

        def get_prompt_content(self, context):
            return "## Banner\\n\\nSAFETY-BANNER: always confirm destructive ops."
    ''').strip()


_SYSTEM_MD = textwrap.dedent("""
    # {{ agent_name }}

    You are Prompttest, a meticulous file-handling assistant.
    PERSONALITY-MARKER: you always read before you write.
    """).strip()


_CONFIG_YAML = textwrap.dedent("""
    name: prompttest
    system_prompt_file: prompts/system.md

    controller:
      llm: ""
      skill_mode: dynamic

    tools:
      - { name: read, type: builtin }
      - { name: write, type: builtin }
      - { name: bash, type: builtin }

    plugins:
      - name: banner
        type: custom
        module: custom/banner_plugin.py
        class: BannerPlugin
    """).strip()


def _write_agent(root: Path) -> Path:
    """Materialize a complete on-disk agent config under ``root``."""
    agent_dir = root / "prompttest_agent"
    (agent_dir / "prompts").mkdir(parents=True)
    (agent_dir / "custom").mkdir(parents=True)

    (agent_dir / "config.yaml").write_text(_CONFIG_YAML, encoding="utf-8")
    (agent_dir / "prompts" / "system.md").write_text(_SYSTEM_MD, encoding="utf-8")
    (agent_dir / "custom" / "banner_plugin.py").write_text(
        _PLUGIN_SOURCE, encoding="utf-8"
    )
    return agent_dir


def _patch_llm(monkeypatch: pytest.MonkeyPatch, llm: ScriptedLLM) -> None:
    """Replace both ``create_llm_provider`` import sites with the script.

    ``bootstrap.agent_init`` imports ``create_llm_provider`` by name, so
    patching only ``bootstrap.llm`` would miss it. Patch both.
    """
    monkeypatch.setattr(
        "kohakuterrarium.bootstrap.llm.create_llm_provider",
        lambda *a, **k: llm,
    )
    monkeypatch.setattr(
        "kohakuterrarium.bootstrap.agent_init.create_llm_provider",
        lambda *a, **k: llm,
    )


class _SendMessageTool(BaseTool):
    """A tool named ``send_message`` — the aggregator special-cases this
    name for channel-hint generation and for example synthesis."""

    @property
    def tool_name(self) -> str:
        return "send_message"

    @property
    def description(self) -> str:
        return "Send a message to a named channel."

    @property
    def execution_mode(self) -> ExecutionMode:
        return ExecutionMode.DIRECT

    async def _execute(self, args, **kwargs) -> ToolResult:  # pragma: no cover
        return ToolResult(output="sent")


class _GuidanceTool(BaseTool):
    """A tool that contributes per-tool prompt guidance in the ``first``
    bucket — exercises ``prompt/tool_contributions.py``."""

    prompt_contribution_bucket = "first"

    @property
    def tool_name(self) -> str:
        return "guide"

    @property
    def description(self) -> str:
        return "A tool with prompt guidance."

    @property
    def execution_mode(self) -> ExecutionMode:
        return ExecutionMode.DIRECT

    def prompt_contribution(self) -> str:
        return "GUIDE-CONTRIBUTION: always call guide before anything else."

    async def _execute(self, args, **kwargs) -> ToolResult:  # pragma: no cover
        return ToolResult(output="guided")


def _conversation_text(agent: Agent) -> str:
    """Flatten the controller conversation into a single searchable string."""
    chunks: list[str] = []
    for msg in agent.controller.conversation.to_messages():
        content = msg.get("content", "")
        if isinstance(content, str):
            chunks.append(content)
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict):
                    chunks.append(str(part.get("text", "")))
    return "\n".join(chunks)


# ---------------------------------------------------------------------------
# Workflows
# ---------------------------------------------------------------------------


class TestPromptIntegration:
    """End-to-end workflows for the ``prompt/`` package."""

    async def test_agent_construction_assembles_system_prompt_then_info_loads_docs(
        self, tmp_path, monkeypatch
    ):
        """The headline workflow.

        Build a real ``Agent`` from an on-disk config whose ``system.md``
        carries a personality marker, that declares three builtin tools,
        and that loads a real custom plugin contributing a prompt
        fragment. Then:

        1. Assert the *assembled* controller system prompt contains —
           - the base personality from ``system.md`` (template-rendered),
           - the auto-generated tool LIST (name + one-line desc per tool),
           - the framework hints (call syntax + ``info`` command),
           - the custom plugin's contribution.
        2. Assert it does NOT embed full tool docs — dynamic mode keeps
           those on-demand.
        3. Run a real turn where the controller emits ``##info read##``
           and assert the full ``read`` tool documentation is pulled into
           the conversation on demand (and was NOT there before).
        """
        agent_dir = _write_agent(tmp_path)

        llm = ScriptedLLM(
            [
                # Turn 1: the model asks for the read tool's full docs.
                ScriptEntry("Let me check the docs. [/info]read[info/]"),
            ]
        )
        _patch_llm(monkeypatch, llm)

        agent = Agent.from_path(str(agent_dir), output_module=OutputRecorder())
        try:
            await agent.start()

            system_prompt = agent.controller.config.system_prompt

            # 1a. Base personality survived template rendering. The
            # ``{{ agent_name }}`` placeholder resolved to the config name.
            assert "PERSONALITY-MARKER: you always read before you write." in (
                system_prompt
            )
            assert "# prompttest" in system_prompt
            assert "{{ agent_name }}" not in system_prompt

            # 1b. Auto-generated tool list: name + one-line description,
            # exactly one line each, sourced from the tool classes.
            assert "## Available Functions" in system_prompt
            assert (
                "- `read`: Read file contents: text, images, PDFs "
                "(required before write/edit)" in system_prompt
            )
            assert (
                "- `write`: Write content to a file (must read first if "
                "file exists)" in system_prompt
            )
            assert (
                "- `bash`: Execute shell commands (prefer dedicated tools "
                "for file ops)" in system_prompt
            )

            # 1c. Framework hints: call syntax + the on-demand info command.
            assert "## Calling Functions" in system_prompt
            assert "[/function_name" in system_prompt
            assert "[/info]" in system_prompt

            # 1d. The custom plugin's contribution is present, and placed
            # after the tool list (runtime plugin prose sits between tool
            # guidance and framework hints).
            assert "SAFETY-BANNER: always confirm destructive ops." in system_prompt
            assert system_prompt.index("## Available Functions") < system_prompt.index(
                "SAFETY-BANNER"
            )

            # 2. Dynamic mode: the multi-section ``read`` doc body is NOT
            # embedded. The static-mode-only header must be absent, and
            # the SAFETY section heading from read.md must not be inlined.
            assert "## Function Documentation" not in system_prompt
            assert "Read file contents. Supports text files, images" not in (
                system_prompt
            )

            # 3a. Before the turn, the conversation has only the system
            # message — no read docs anywhere.
            assert "Read file contents. Supports text files, images" not in (
                _conversation_text(agent)
            )

            # 3b. Run the real turn. The controller parses ``##info read##``
            # inline and the InfoCommand pulls the builtin skill body.
            await agent.inject_input("How do I read a file?")

            convo = _conversation_text(agent)
            # The full doc body (not just the one-line description) is now
            # in the conversation, delivered on demand.
            assert "Read file contents. Supports text files, images" in convo
            assert "You MUST read files before writing or editing them." in convo
            assert llm.call_count == 1

            # 4. ``build_context_message`` — the helper the controller uses
            # to wrap event content (+ optional running-job status) into a
            # single context turn. Exact formatting is the contract.
            ctx_msg = build_context_message(
                "the event body", job_status="job_x running"
            )
            assert ctx_msg == ("## Running Jobs\njob_x running\n\nthe event body")
            assert build_context_message("just events") == "just events"
        finally:
            await agent.stop()

        # ─── Second agent: NATIVE tool_format + framework_hint_overrides ───
        # The aggregator behaves differently for native mode (no bracket
        # syntax examples, API-driven calling) and honours per-creature
        # ``framework_hint_overrides`` — an empty-string override drops a
        # whole block, a prose override replaces it verbatim.
        native_dir = tmp_path / "native_agent"
        (native_dir / "prompts").mkdir(parents=True)
        (native_dir / "config.yaml").write_text(
            textwrap.dedent("""
                name: nativeagent
                system_prompt_file: prompts/system.md
                controller:
                  llm: ""
                  tool_format: native
                framework_hint_overrides:
                  framework.output_model: ""
                  framework.execution_model.native: "NATIVE-EXEC-OVERRIDE: be terse."
                tools:
                  - { name: read, type: builtin }
                """).strip(),
            encoding="utf-8",
        )
        (native_dir / "prompts" / "system.md").write_text(
            "You are Nativeagent.", encoding="utf-8"
        )

        llm2 = ScriptedLLM([ScriptEntry("ok")])
        _patch_llm(monkeypatch, llm2)
        native_agent = Agent.from_path(str(native_dir), output_module=OutputRecorder())
        try:
            await native_agent.start()
            sp = native_agent.controller.config.system_prompt
            assert "You are Nativeagent." in sp
            # Native mode still emits the tool LIST...
            assert "## Available Functions" in sp
            assert "- `read`:" in sp
            # ...but NOT the bracket-format calling-syntax header.
            assert "## Calling Functions" not in sp
            assert "[/function_name" not in sp
            # The empty-string output_model override dropped that block.
            assert "## Output Format" not in sp
            # The native execution-model override replaced the default prose.
            assert "NATIVE-EXEC-OVERRIDE: be terse." in sp
            assert "Tools are called via the API's native function" not in sp
        finally:
            await native_agent.stop()

    async def test_base_prompt_template_rendering_and_syntax_error(
        self, tmp_path, monkeypatch
    ):
        """Jinja-like template rendering — the success path and the
        documented failure path.

        ``prompt.template`` powers base-prompt rendering. This workflow
        exercises both public entry points end to end:

        * ``render_template`` / ``PromptTemplate`` substitute variables,
          run conditionals and loops, and the rendered text is what an
          agent's ``system.md`` ultimately becomes.
        * A malformed template raises :class:`jinja2.TemplateSyntaxError`
          (the error documented in ``render_template``'s docstring), and
          ``render_template_safe`` swallows it and returns the original.

        Finally it confirms the *agent path* uses the renderer too: a
        ``system.md`` with a ``{% for %}`` loop renders correctly into
        the assembled system prompt.
        """
        # --- success path: variables, conditional, loop ---
        # The shared Jinja env runs with ``trim_blocks=True``, so the
        # newline immediately following a block tag (``{% endif %}``,
        # ``{% endfor %}``) is stripped — the rendered output reflects
        # exactly what the framework's renderer produces.
        rendered = render_template(
            "Hello {{ who }}!{% if vip %} (VIP){% endif %}\n"
            "{% for t in tools %}- {{ t }}\n{% endfor %}",
            who="agent",
            vip=True,
            tools=["read", "write"],
        )
        assert rendered == "Hello agent! (VIP)- read\n- write\n"

        # PromptTemplate compiles once and renders repeatedly.
        compiled = PromptTemplate("{{ greeting }}, {{ name }}.")
        assert compiled.render(greeting="Hi", name="Prompttest") == "Hi, Prompttest."
        assert compiled.source == "{{ greeting }}, {{ name }}."

        # --- failure path: malformed syntax raises TemplateSyntaxError ---
        with pytest.raises(TemplateSyntaxError):
            render_template("{% if missing_endif %}dangling")

        # render_template_safe degrades to the original string instead.
        broken = "{% if missing_endif %}dangling"
        assert render_template_safe(broken) == broken

        # --- {% include %} resolves a raw file path fallback ---
        # PackagePromptLoader first asks the package manifest, then falls
        # back to a literal file path — so an absolute path to a fragment
        # on disk is includable.
        fragment = tmp_path / "fragment.md"
        fragment.write_text("INCLUDED-FRAGMENT: shared safety rules.", "utf-8")
        # trim_blocks=True strips the newline right after the ``%}`` tag.
        included = render_template(
            'Header\n{% include "' + fragment.as_posix() + '" %}\nFooter'
        )
        assert included == ("Header\nINCLUDED-FRAGMENT: shared safety rules.Footer")
        # A missing include degrades through render_template_safe.
        missing_include = '{% include "definitely-not-a-real-fragment" %}'
        assert render_template_safe(missing_include) == missing_include

        # --- prompt loader: load_prompt / folder / fallback ---
        # ``prompt.loader`` is the package's file-loading surface. The
        # config + sub-agent layers read prompt files through it.
        prompts_dir = tmp_path / "loader_prompts"
        prompts_dir.mkdir()
        (prompts_dir / "alpha.md").write_text("ALPHA BODY", "utf-8")
        (prompts_dir / "beta.txt").write_text("BETA BODY", "utf-8")
        (prompts_dir / "ignored.json").write_text("{}", "utf-8")

        assert load_prompt(prompts_dir / "alpha.md") == "ALPHA BODY"
        with pytest.raises(FileNotFoundError):
            load_prompt(prompts_dir / "nope.md")

        folder = load_prompts_folder(prompts_dir)
        # .md and .txt are loaded (keyed by stem); .json is skipped.
        assert folder == {"alpha": "ALPHA BODY", "beta": "BETA BODY"}
        assert load_prompts_folder(tmp_path / "no_such_dir") == {}

        # Fallback: present file wins, missing file degrades to default.
        assert (
            load_prompt_with_fallback(prompts_dir / "alpha.md", "DEFAULT")
            == "ALPHA BODY"
        )
        assert (
            load_prompt_with_fallback(prompts_dir / "gone.md", "DEFAULT") == "DEFAULT"
        )
        assert load_prompt_with_fallback(None, "DEFAULT") == "DEFAULT"

        # --- agent path: a looping system.md renders into the prompt ---
        agent_dir = tmp_path / "loop_agent"
        (agent_dir / "prompts").mkdir(parents=True)
        (agent_dir / "config.yaml").write_text(
            textwrap.dedent("""
                name: loopagent
                system_prompt_file: prompts/system.md
                controller:
                  llm: ""
                tools:
                  - { name: read, type: builtin }
                """).strip(),
            encoding="utf-8",
        )
        # ``tools`` is injected by the aggregator from the registry, so a
        # ``{% for t in tools %}`` loop in system.md resolves against the
        # real registered tool list.
        (agent_dir / "prompts" / "system.md").write_text(
            "Agent {{ agent_name }} has:\n"
            "{% for t in tools %}* {{ t.name }}: {{ t.description }}\n{% endfor %}",
            encoding="utf-8",
        )

        llm = ScriptedLLM([ScriptEntry("Done.")])
        _patch_llm(monkeypatch, llm)

        agent = Agent.from_path(str(agent_dir), output_module=OutputRecorder())
        try:
            await agent.start()
            system_prompt = agent.controller.config.system_prompt
            assert "Agent loopagent has:" in system_prompt
            # The loop iterated over the registry-injected ``tools`` list.
            assert (
                "* read: Read file contents: text, images, PDFs "
                "(required before write/edit)" in system_prompt
            )
            # A base prompt that placed ``{{ tools }}`` itself suppresses
            # the auto-appended list — but here we used ``{{ t.name }}``,
            # not the literal ``{{ tools }}`` token, so the auto list is
            # still appended.
            assert "## Available Functions" in system_prompt
        finally:
            await agent.stop()

    async def test_static_skill_mode_embeds_full_docs_in_prompt(
        self, tmp_path, monkeypatch
    ):
        """The documented exception: ``skill_mode: static``.

        Dynamic mode (the other workflow) keeps full tool docs out of the
        system prompt and reaches them via ``info``. Static mode is the
        documented opposite — ``aggregate_system_prompt`` embeds the full
        ``## Function Documentation`` section up front. This workflow
        builds a real agent with ``skill_mode: static`` and asserts the
        full ``read`` doc body is in the assembled prompt *without* any
        ``info`` call.

        Regression guard for B-prompt-2 (FIXED): ``aggregate_system_prompt``
        supports static mode and ``AgentConfig.skill_mode`` is parsed from
        the config, but ``bootstrap/agent_init._init_controller`` called
        the aggregator WITHOUT a ``skill_mode=`` argument — the config
        value was dropped and the prompt was always dynamic. The fix
        passes ``skill_mode=self.config.skill_mode`` through.
        """
        agent_dir = tmp_path / "static_agent"
        (agent_dir / "prompts").mkdir(parents=True)
        (agent_dir / "config.yaml").write_text(
            textwrap.dedent("""
                name: staticagent
                system_prompt_file: prompts/system.md
                controller:
                  llm: ""
                  skill_mode: static
                tools:
                  - { name: read, type: builtin }
                """).strip(),
            encoding="utf-8",
        )
        (agent_dir / "prompts" / "system.md").write_text(
            "You are Staticagent.", encoding="utf-8"
        )

        llm = ScriptedLLM([ScriptEntry("Acknowledged.")])
        _patch_llm(monkeypatch, llm)

        agent = Agent.from_path(str(agent_dir), output_module=OutputRecorder())
        try:
            await agent.start()
            system_prompt = agent.controller.config.system_prompt

            assert "You are Staticagent." in system_prompt
            # Static mode embeds the full documentation section verbatim.
            assert "## Function Documentation" in system_prompt
            assert "Read file contents. Supports text files, images" in system_prompt
            assert "You MUST read files before writing or editing them." in (
                system_prompt
            )

            # The same registry, run through ``aggregate_system_prompt`` in
            # DYNAMIC mode, keeps full docs OUT — proving the skill_mode
            # switch is what gates the embedded documentation section.
            dyn = aggregate_system_prompt(
                "You are Staticagent.",
                agent.registry,
                skill_mode="dynamic",
            )
            assert "## Function Documentation" not in dyn
            assert "## Available Functions" in dyn
        finally:
            await agent.stop()

        # ─── Plugin-based aggregation (aggregate_with_plugins) ───
        # ``prompt/`` also ships a plugin-composition API: each plugin
        # contributes one prompt section, sorted by priority. This is the
        # public surface ``get_default_plugins`` / ``get_swe_plugins`` /
        # ``create_plugin`` build on. Drive it with a real Registry.
        reg = Registry()
        reg.register_tool(ReadTool())

        # Default plugin set: tool list + framework hints, in priority order.
        default_plugins = get_default_plugins()
        assert [p.name for p in default_plugins] == ["tool_list", "framework_hints"]
        composed = aggregate_with_plugins(
            "BASE-PERSONALITY-LINE.",
            default_plugins,
            registry=reg,
        )
        assert composed.startswith("BASE-PERSONALITY-LINE.")
        # ToolListPlugin emitted the tool list...
        assert "## Available Tools" in composed
        assert "- `read`:" in composed
        # ...and FrameworkHintsPlugin emitted the bracket call syntax.
        assert "## Tool Call Syntax" in composed
        # Section order follows plugin priority (tool_list=50 < hints=60).
        assert composed.index("## Available Tools") < composed.index(
            "## Tool Call Syntax"
        )

        # SWE plugin set additionally injects env info + project
        # instructions ahead of the tool list (lower priority numbers).
        swe_plugins = get_swe_plugins()
        assert [p.name for p in swe_plugins] == [
            "env_info",
            "project_instructions",
            "tool_list",
            "framework_hints",
        ]
        swe_dir = tmp_path / "swe_workdir"
        swe_dir.mkdir()
        (swe_dir / "AGENTS.md").write_text(
            "PROJECT-RULE: write tests first.", encoding="utf-8"
        )
        swe_composed = aggregate_with_plugins(
            "SWE BASE.",
            swe_plugins,
            registry=reg,
            working_dir=swe_dir,
        )
        # EnvInfoPlugin injected the <env> block with the working dir.
        assert "<env>" in swe_composed
        assert str(swe_dir) in swe_composed
        # ProjectInstructionsPlugin picked up the AGENTS.md file.
        assert "## Project Instructions" in swe_composed
        assert "PROJECT-RULE: write tests first." in swe_composed
        # env_info (priority 10) precedes project_instructions (20) precedes
        # the tool list (50).
        assert (
            swe_composed.index("<env>")
            < swe_composed.index("## Project Instructions")
            < swe_composed.index("## Available Tools")
        )

        # ``create_plugin`` resolves a builtin plugin by name; an unknown
        # name returns None rather than raising.
        assert isinstance(create_plugin("tool_list"), ToolListPlugin)
        assert isinstance(create_plugin("env_info"), EnvInfoPlugin)
        assert isinstance(
            create_plugin("project_instructions"), ProjectInstructionsPlugin
        )
        assert create_plugin("no_such_plugin") is None

        # ─── aggregate_system_prompt: the branch matrix ───
        # Build a registry that triggers every conditional in the
        # aggregator's helpers: a ``send_message`` tool (channel hints +
        # example), a guidance-contributing tool, and a registered
        # sub-agent (sub-agent list + example).
        rich_reg = Registry()
        rich_reg.register_tool(ReadTool())
        rich_reg.register_tool(_SendMessageTool())
        rich_reg.register_tool(_GuidanceTool())
        rich_reg.register_subagent(
            "explore",
            SubAgentConfig(name="explore", description="Codebase explorer."),
        )

        # tool_contributions: collect + section assembly.
        triples = collect_tool_contributions(rich_reg)
        assert (
            "first",
            "guide",
            "GUIDE-CONTRIBUTION: always call guide " "before anything else.",
        ) in triples
        guidance_section = build_tool_guidance_section(rich_reg)
        assert "## Tool guidance" in guidance_section
        assert "- **guide**: GUIDE-CONTRIBUTION" in guidance_section
        # An empty registry contributes nothing.
        assert build_tool_guidance_section(Registry()) == ""
        assert collect_tool_contributions(None) == []

        rich_prompt = aggregate_system_prompt(
            "RICH BASE.",
            rich_reg,
            skill_mode="dynamic",
            tool_format="bracket",
            known_outputs={"discord", "tts"},
        )
        # The sub-agent shows up in the function list...
        assert "**Sub-agents:**" in rich_prompt
        assert "- `explore`: Codebase explorer." in rich_prompt
        # ...the per-tool guidance section is spliced in...
        assert "## Tool guidance" in rich_prompt
        assert "GUIDE-CONTRIBUTION" in rich_prompt
        # ...the channel hints fire because ``send_message`` is registered...
        assert "## Internal Channels" in rich_prompt
        assert "`send_message`" in rich_prompt
        # ...and the named-outputs section lists the two known outputs.
        assert "## Output Format" in rich_prompt
        assert "`discord`" in rich_prompt and "`tts`" in rich_prompt

        # XML tool_format routes through the XML example branch.
        xml_prompt = aggregate_system_prompt(
            "XML BASE.", rich_reg, skill_mode="dynamic", tool_format="xml"
        )
        assert "## Calling Functions" in xml_prompt
        assert "<function" in xml_prompt

        # Static mode + the rich registry → full docs section AND the
        # static execution-model hints.
        static_prompt = aggregate_system_prompt(
            "STATIC RICH BASE.", rich_reg, skill_mode="static"
        )
        assert "## Function Documentation" in static_prompt

        # An empty-string ``framework.output_model`` override drops the
        # whole output block; channel hints + tool list still render.
        no_output = aggregate_system_prompt(
            "NO OUTPUT BASE.",
            rich_reg,
            skill_mode="dynamic",
            framework_hint_overrides={"framework.output_model": ""},
        )
        assert "## Output Format" not in no_output
        assert "## Available Functions" in no_output

        # ─── framework_hints public surface ───
        # canonical_keys lists exactly the four recognised override keys.
        keys = canonical_keys()
        assert HINT_OUTPUT_MODEL in keys
        assert len(keys) == 4
        # A non-canonical key resolves to None (caller-side bug signal).
        assert get_framework_hint("framework.not_a_real_key") is None
        # An override for a canonical key wins verbatim, even empty.
        assert (
            get_framework_hint(HINT_OUTPUT_MODEL, {HINT_OUTPUT_MODEL: "CUSTOM-PROSE"})
            == "CUSTOM-PROSE"
        )
        # Unknown keys in the override map are ignored (no crash).
        assert get_framework_hint(
            HINT_OUTPUT_MODEL, {"bogus.key": "x"}
        ) == get_framework_hint(HINT_OUTPUT_MODEL)
        # merge_overrides: creature-level wins over package-level.
        merged = merge_overrides(
            {"framework.output_model": "pkg", "a": "1"},
            {"framework.output_model": "creature"},
        )
        assert merged == {"framework.output_model": "creature", "a": "1"}
        assert merge_overrides(None, None) == {}
