"""Integration test for the ``commands/`` package.

The ``commands/`` package implements the framework's *text-format*
controller commands — ``[/info]name[info/]`` and ``[/read_job]id[read_job/]``
plus ``[/jobs]`` / ``[/wait]``. These are the legacy/custom tool-call
format counterpart of the native ``info`` tool: when the LLM emits a
``##info##``-style block, the stream parser turns it into a
``CommandEvent``, the controller's ``_execute_command_inline`` runs the
registered :class:`~kohakuterrarium.commands.read.InfoCommand` against the
real :class:`ControllerContext`, and the resolved documentation is spliced
straight into the assistant message so the *next* LLM round can read it.

These tests drive that whole path THROUGH a real ``Agent`` turn — the only
seam is the LLM (a :class:`ScriptedLLM`). Each method runs one complete
end-to-end workflow:

* ``test_info_resolves_builtin_tool_skill`` — ``[/info]bash[info/]`` resolves
  the packaged ``builtin_skills/tools/bash.md`` (tags preamble + body) into
  the conversation, and the follow-up turn's prompt contains it.
* ``test_info_resolves_subagent_and_unknown`` — a builtin *subagent* skill
  resolves, and an unknown name produces a clean ``Not found`` command error
  (no crash) — both in a single turn.
* ``test_info_resolution_order`` — for a registered tool with NO packaged
  skill md the resolver falls through to the tool class' documentation;
  for ``bash`` the packaged skill still wins over the tool class.
* ``test_read_job_and_jobs_workflow`` — ``[/read_job]`` reads a real
  completed job out of the shared ``JobStore``, an unknown job id yields a
  clean error, and ``[/jobs]`` lists a running job.

The agent-folder override (priority #1 in ``InfoCommand``'s documented
resolution order) is covered by a strict-xfail — it is unreachable through
the real controller (see ``B-commands-1`` in the report).
"""

import asyncio
from pathlib import Path
from typing import Any

import pytest

from kohakuterrarium.bootstrap import agent_init as bootstrap_agent_init
from kohakuterrarium.bootstrap import llm as bootstrap_llm
from kohakuterrarium.builtin_skills import BUILTIN_SKILLS_DIR
from kohakuterrarium.commands.base import (
    BaseCommand,
    CommandResult,
    parse_command_args,
)
from kohakuterrarium.core.agent import Agent
from kohakuterrarium.core.config_types import (
    AgentConfig,
    InputConfig,
    OutputConfig,
)
from kohakuterrarium.core.events import create_user_input_event
from kohakuterrarium.core.job import JobResult, JobState, JobStatus, JobType
from kohakuterrarium.modules.tool.base import BaseTool, ExecutionMode, ToolResult
from kohakuterrarium.skills.registry import Skill
from kohakuterrarium.testing.llm import ScriptedLLM
from kohakuterrarium.testing.output import OutputRecorder

pytestmark = pytest.mark.timeout(30)


# --------------------------------------------------------------------------
# Real collaborator: a tool with NO packaged builtin skill md. Its only job
# is to be a deterministic registry entry so the InfoCommand tool-class
# fallback (resolution step #3) has something real to resolve.
# --------------------------------------------------------------------------


class _MyEchoTool(BaseTool):
    """A registered tool whose name (`myecho`) has no builtin_skills md."""

    @property
    def tool_name(self) -> str:
        return "myecho"

    @property
    def description(self) -> str:
        return "Echo a message back to the caller verbatim."

    @property
    def execution_mode(self) -> ExecutionMode:
        return ExecutionMode.DIRECT

    async def _execute(self, args: dict[str, Any], **kwargs: Any) -> ToolResult:
        return ToolResult(output=str(args.get("msg", "")))


# --------------------------------------------------------------------------
# Fixtures — build a real, fully-wired Agent with ScriptedLLM as the only
# seam. Mirrors how tests/unit/core/test_agent_real.py constructs an agent,
# patching BOTH bootstrap LLM-factory import sites.
# --------------------------------------------------------------------------


class _ScriptHandle:
    """Lets a test set the LLM script before the Agent is constructed."""

    def __init__(self) -> None:
        self.script: list[str] = ["OK"]

    def set_script(self, script: list[str]) -> None:
        self.script = script


@pytest.fixture
def script_handle(monkeypatch) -> _ScriptHandle:
    handle = _ScriptHandle()

    def _fake_create(config, llm_override=None):
        return ScriptedLLM(handle.script)

    monkeypatch.setattr(bootstrap_llm, "create_llm_provider", _fake_create)
    monkeypatch.setattr(bootstrap_agent_init, "create_llm_provider", _fake_create)
    return handle


@pytest.fixture
def make_agent(script_handle, tmp_path):
    """Build a real Agent in text (bracket) mode with stub I/O."""

    def _build(*, script: list[str], agent_path: Path | None = None) -> Agent:
        script_handle.set_script(script)
        cfg = AgentConfig(
            name="commands_test_agent",
            llm_profile="openai/gpt-4-test",
            model="gpt-4",
            provider="openai",
            api_key_env="",
            system_prompt="You are a test agent.",
            include_tools_in_prompt=True,
            include_hints_in_prompt=False,
            tool_format="bracket",
            agent_path=agent_path or tmp_path,
            input=InputConfig(type="none"),
            output=OutputConfig(type="stdout"),
            tools=[],
            ephemeral=False,
        )
        agent = Agent(cfg)
        recorder = OutputRecorder()
        agent.output_router.default_output = recorder
        agent._recorder = recorder
        return agent

    return _build


async def _run_turn(agent: Agent, text: str) -> None:
    """Start the agent, dispatch one user-input event, drain, stop."""
    await agent.start()
    try:
        await agent._process_event(create_user_input_event(text))
    finally:
        await agent.stop()


def _assistant_text(agent: Agent) -> str:
    """Concatenate every assistant message's text content."""
    chunks: list[str] = []
    for msg in agent.controller.conversation.get_messages():
        if msg.role != "assistant":
            continue
        content = msg.content
        if isinstance(content, str):
            chunks.append(content)
        elif isinstance(content, list):
            for part in content:
                text = getattr(part, "text", None)
                if isinstance(text, str):
                    chunks.append(text)
    return "\n".join(chunks)


def _read_builtin_skill_body(kind: str, name: str) -> str:
    """Read the post-frontmatter body of a packaged builtin skill md."""
    raw = (BUILTIN_SKILLS_DIR / kind / f"{name}.md").read_text(encoding="utf-8")
    # Strip the leading YAML frontmatter block (--- ... ---).
    stripped = raw.lstrip()
    assert stripped.startswith("---"), f"expected frontmatter in {kind}/{name}.md"
    end = stripped.find("---", 3)
    assert end != -1
    return stripped[end + 3 :].strip()


class TestCommandsIntegration:
    """End-to-end workflows over the ``commands/`` package."""

    async def test_info_resolves_builtin_tool_skill(self, make_agent) -> None:
        """``[/info]bash[info/]`` → packaged builtin tool skill resolved.

        Workflow: a real Agent runs a turn whose scripted LLM reply emits the
        text-format ``info`` command for the ``bash`` tool. The stream parser
        produces a ``CommandEvent``; the controller resolves it through the
        real ``InfoCommand`` against ``builtin_skills/tools/bash.md`` and
        splices the FULL documentation into the assistant message. A second
        turn then confirms the resolved doc is visible to the LLM as prior
        context.
        """
        # bash.md ships with `tags: [shell, command, system]`, so InfoCommand
        # renders it with a "Tags: ..." preamble in front of the body.
        body = _read_builtin_skill_body("tools", "bash")
        expected = "Tags: shell, command, system\n\n" + body

        agent = make_agent(
            script=[
                "Let me check the docs.\n[/info]bash[info/]\nGot it.",
                "Second turn done.",
            ]
        )
        await _run_turn(agent, "what does bash do?")

        # The exact resolved documentation landed in the assistant message —
        # not a shape check: the literal packaged content, tags preamble and
        # all, plus distinctive prose from the body.
        asst = _assistant_text(agent)
        assert expected in asst
        assert "## IMPORTANT: Prefer Dedicated Tools" in asst
        assert "Tags: shell, command, system" in asst

        # The command was not silently dropped — it ran exactly once and
        # produced no error activity.
        recorder: OutputRecorder = agent._recorder
        activity_kinds = recorder.activity_types()
        assert "command_done" in activity_kinds
        assert "command_error" not in activity_kinds

        # Next turn: the LLM's prompt history contains the resolved doc, so
        # the model can actually "see" what info() returned.
        await _run_turn(agent, "thanks")
        llm = agent.controller.llm
        assert isinstance(llm, ScriptedLLM)
        last_prompt = llm.call_log[-1]
        joined = "\n".join(
            m.get("content", "")
            for m in last_prompt
            if isinstance(m.get("content"), str)
        )
        assert "## IMPORTANT: Prefer Dedicated Tools" in joined

    async def test_info_resolves_subagent_and_unknown(self, make_agent) -> None:
        """One turn: a builtin *subagent* skill resolves, a runtime
        *procedural skill* resolves through the SkillRegistry fallback,
        and an unknown name produces a clean ``Not found`` command error
        rather than a crash.

        This pins the success branches (builtin subagent skill +
        ``_render_skill_info`` registry fallback) and the failure branch
        of ``InfoCommand`` in a single end-to-end workflow — the negative
        case (unknown target) is the bug we'd most likely introduce, so
        it is asserted explicitly.
        """
        explore_body = _read_builtin_skill_body("subagents", "explore")
        # explore.md ships with `tags: [search, exploration, analysis]`.
        expected_explore = "Tags: search, exploration, analysis\n\n" + explore_body

        agent = make_agent(
            script=[
                "Checking the explore sub-agent, a skill, and a bogus name.\n"
                "[/info]explore[info/]\n"
                "[/info]deploy_routine[info/]\n"
                "[/info]definitely_not_a_real_thing[info/]\n"
                "Done.",
            ]
        )
        # Register a real procedural skill on the agent's SkillRegistry —
        # the controller wired ``skills_registry`` into the command context
        # at construction, so ``InfoCommand`` step #5 (_render_skill_info)
        # resolves it.
        agent.skills.add(
            Skill(
                name="deploy_routine",
                description="How to deploy the service safely.",
                body="STEP 1: run the smoke tests.\nSTEP 2: flip the flag.",
                origin="user",
                paths=["docs/deploy.md"],
            )
        )

        await _run_turn(agent, "explain explore, the skill, and a bogus tool")

        asst = _assistant_text(agent)
        # Builtin subagent skill fully resolved into the conversation.
        assert expected_explore in asst
        assert "Autonomous sub-agent for codebase exploration" in asst
        # The procedural skill resolved via the SkillRegistry fallback —
        # rendered with the ``--- Skill: ---`` preamble + origin + body.
        assert "--- Skill: deploy_routine ---" in asst
        assert "Origin: user" in asst
        assert "Description: How to deploy the service safely." in asst
        assert "Paths: docs/deploy.md" in asst
        assert "STEP 1: run the smoke tests." in asst
        # Unknown target → InfoCommand returns CommandResult(error=...),
        # which the controller splices in as a bracketed command-error note.
        # No exception, no crash, the turn still completed.
        assert "[Command Error: Not found: definitely_not_a_real_thing]" in asst

        recorder: OutputRecorder = agent._recorder
        activity_kinds = recorder.activity_types()
        # Two successes (subagent skill + procedural skill) and one error.
        assert activity_kinds.count("command_done") == 2
        assert activity_kinds.count("command_error") == 1

    async def test_info_resolution_order(self, make_agent) -> None:
        """Resolution order: packaged builtin skill beats the tool class;
        a tool with no packaged skill falls through to its class doc.

        Workflow: register a real ``myecho`` tool (no ``builtin_skills`` md)
        on the agent's registry, then in one turn ``[/info]`` both ``myecho``
        and ``bash``. ``myecho`` must resolve via the tool-class fallback
        (``ToolInfo.documentation`` == ``get_full_documentation()`` default),
        while ``bash`` must still resolve from the packaged skill md — the
        higher-priority source — even though a ``bash`` tool class also
        exists.
        """
        agent = make_agent(
            script=[
                "Reading docs for both.\n"
                "[/info]myecho[info/]\n"
                "[/info]bash[info/]\n"
                "Done.",
            ]
        )
        # Register a real tool the registry/context will actually resolve.
        agent.registry.register_tool(_MyEchoTool())

        await _run_turn(agent, "docs for myecho and bash")
        asst = _assistant_text(agent)

        # myecho has no packaged skill md → InfoCommand falls through to the
        # tool-class documentation. BaseTool.get_full_documentation() returns
        # "# {name}\n\n{description}\n" when no md exists; ToolInfo.from_tool
        # stores exactly that in ToolInfo.documentation.
        assert "# myecho" in asst
        assert "Echo a message back to the caller verbatim." in asst

        # bash resolves from builtin_skills/tools/bash.md (priority over the
        # tool class) — distinctive packaged-skill prose proves which source
        # won the race.
        assert "Tags: shell, command, system" in asst
        assert "## IMPORTANT: Prefer Dedicated Tools" in asst

        recorder: OutputRecorder = agent._recorder
        activity_kinds = recorder.activity_types()
        assert activity_kinds.count("command_done") == 2
        assert "command_error" not in activity_kinds

    async def test_read_job_and_jobs_workflow(self, make_agent) -> None:
        """Full job-command workflow: ``[/read_job]`` reads a real completed
        job (incl. ``--lines`` / ``--offset`` slicing and the error-result
        rendering branch), the still-running and pending branches both
        report status, an unknown id errors cleanly, ``[/jobs]`` lists
        running jobs, and ``[/wait]`` resolves both an already-complete
        job and an unknown id.

        ``read_job`` / ``jobs`` / ``wait`` integrate with the *shared*
        ``JobStore`` that the executor and controller both own. We seed
        that real store with completed / errored / running / pending
        jobs (real ``JobStatus`` / ``JobResult`` objects — no mocks),
        then drive every command through real turns.
        """
        agent = make_agent(
            script=[
                "Inspecting jobs.\n"
                "[/read_job]job_done_1[read_job/]\n"
                "[/read_job]job_done_1 --lines 2 --offset 1[read_job/]\n"
                "[/read_job]job_errored_3[read_job/]\n"
                "[/read_job]job_running_2[read_job/]\n"
                "[/read_job]job_pending_4[read_job/]\n"
                "[/read_job]job_missing_xyz[read_job/]\n"
                "[/read_job][read_job/]\n"
                "[/jobs][jobs/]\n"
                "[/wait]job_done_1[wait/]\n"
                "[/wait]job_missing_xyz[wait/]\n"
                "Done.",
                # Second turn: a JobStore with no running jobs.
                "[/jobs][jobs/]\nNothing running.",
            ]
        )

        # The controller and executor share one JobStore instance.
        store = agent.controller.job_store
        assert store is agent.executor.job_store

        # A completed job with multi-line output the read_job command renders
        # and slices.
        store.register(
            JobStatus(
                job_id="job_done_1",
                job_type=JobType.TOOL,
                type_name="bash",
                state=JobState.DONE,
            )
        )
        store.store_result(
            JobResult(
                job_id="job_done_1",
                output="line-zero\nline-one\nline-two\nline-three",
                exit_code=0,
            )
        )
        # An errored job — read_job renders the error branch.
        store.register(
            JobStatus(
                job_id="job_errored_3",
                job_type=JobType.TOOL,
                type_name="bash",
                state=JobState.ERROR,
            )
        )
        store.store_result(
            JobResult(
                job_id="job_errored_3",
                error="boom: the job blew up",
                exit_code=1,
            )
        )
        # A still-running job so [/jobs] has something to list and
        # read_job hits the "still running" branch.
        store.register(
            JobStatus(
                job_id="job_running_2",
                job_type=JobType.SUBAGENT,
                type_name="explore",
                state=JobState.RUNNING,
            )
        )
        # A pending job — read_job hits the "pending" branch.
        store.register(
            JobStatus(
                job_id="job_pending_4",
                job_type=JobType.TOOL,
                type_name="grep",
                state=JobState.PENDING,
            )
        )

        await _run_turn(agent, "show me the jobs")
        asst = _assistant_text(agent)

        # read_job rendered the completed job's exact output + exit code.
        assert "## Job job_done_1 Output" in asst
        assert "line-zero\nline-one\nline-two\nline-three" in asst
        assert "Exit code: 0" in asst

        # --lines 2 --offset 1 sliced the output to lines 1..2 only.
        assert "line-one\nline-two" in asst
        # The sliced render dropped line-zero and line-three from THAT block.
        # (The unsliced block above still has them — assert the slice block
        # is present as a contiguous 2-line chunk.)
        assert "```\nline-one\nline-two\n```" in asst

        # The errored job rendered the error branch.
        assert "## Job job_errored_3 (error)" in asst
        assert "Error: boom: the job blew up" in asst

        # The running job → "still running" status string, not an error.
        assert "[Job job_running_2 is still running:" in asst
        # The pending job → "pending" status string.
        assert "[Job job_pending_4 is pending]" in asst

        # Unknown job id → clean command error, no crash, turn still finished.
        assert "[Command Error: Job not found: job_missing_xyz]" in asst

        # read_job with NO job_id (empty args) → usage error, not a crash.
        assert "[Command Error: No job_id provided. Usage: ##read_job job_id##]" in (
            asst
        )

        # [/jobs] listed the running job (and not the completed one).
        assert "## Running Jobs" in asst
        assert "`job_running_2`" in asst
        assert "explore" in asst

        # [/wait] on an ALREADY-complete job returns its result immediately.
        assert "## job_done_1 - DONE" in asst
        # [/wait] on an unknown id → clean command error.
        assert "[Command Error: Job not found: job_missing_xyz]" in asst

        recorder: OutputRecorder = agent._recorder
        activity_kinds = recorder.activity_types()
        # Successful commands: 2x read_job-hit (done + slice), 3x read_job
        # status-content (errored / running / pending all return content,
        # not errors), jobs, wait-done = 7 command_done.
        assert activity_kinds.count("command_done") == 7
        # Errors: read_job-missing + read_job-empty + wait-missing = 3.
        assert activity_kinds.count("command_error") == 3

        # Second turn: with no running jobs left, [/jobs] reports the
        # empty state as a successful (content) command. Mark the running
        # job done so get_running_jobs() returns nothing.
        store.update_status("job_running_2", state=JobState.DONE)
        recorder.clear_all()
        await _run_turn(agent, "any jobs now?")
        asst2 = _assistant_text(agent)
        assert "No running jobs." in asst2
        assert recorder.activity_types().count("command_done") == 1
        assert "command_error" not in recorder.activity_types()

        # ─── WaitCommand polling loop: a job that completes mid-wait ───
        # Register a job as RUNNING, then flip it to DONE from a
        # background task while [/wait] is polling. This drives the
        # poll loop (sleep → re-check status → return result) instead
        # of the already-complete fast path.
        wait_agent = make_agent(
            script=[
                "Waiting on the slow job.\n[/wait]slow_job --timeout 5[wait/]\nDone."
            ]
        )
        wstore = wait_agent.controller.job_store
        wstore.register(
            JobStatus(
                job_id="slow_job",
                job_type=JobType.TOOL,
                type_name="bash",
                state=JobState.RUNNING,
            )
        )

        async def _finish_slow_job() -> None:
            await asyncio.sleep(0.6)
            wstore.update_status("slow_job", state=JobState.DONE)
            wstore.store_result(
                JobResult(
                    job_id="slow_job",
                    output="slow job finished at last",
                    exit_code=0,
                )
            )

        await wait_agent.start()
        try:
            finisher = asyncio.create_task(_finish_slow_job())
            await wait_agent._process_event(
                create_user_input_event("wait for the slow job")
            )
            await finisher
        finally:
            await wait_agent.stop()
        wait_asst = _assistant_text(wait_agent)
        # The poll loop saw the job flip to DONE and returned its result.
        assert "## slow_job - DONE" in wait_asst
        assert "slow job finished at last" in wait_asst

        # ─── parse_command_args + BaseCommand error handling ───
        # These are the text-format command primitives every command
        # above is built on. Exercise the arg-parser branches directly
        # and BaseCommand's execute() exception wrapper with real
        # CommandResult objects.
        # Bare positional, no kwargs.
        assert parse_command_args("job_42") == ("job_42", {})
        # ``--key value`` pairs.
        assert parse_command_args("job_42 --lines 5 --offset 2") == (
            "job_42",
            {"lines": "5", "offset": "2"},
        )
        # A trailing ``--flag`` with no value defaults to "true".
        assert parse_command_args("job_42 --verbose") == (
            "job_42",
            {"verbose": "true"},
        )
        # Single-dash short options parse too: ``-n 3`` and a bare ``-x``.
        assert parse_command_args("job_42 -n 3 -x") == (
            "job_42",
            {"n": "3", "x": "true"},
        )
        # Empty input → empty positional, empty kwargs.
        assert parse_command_args("   ") == ("", {})

        # CommandResult.success reflects the presence of an error.
        assert CommandResult(content="ok").success is True
        assert CommandResult(error="bad").success is False

        # BaseCommand.execute() wraps a raising _execute into a clean
        # CommandResult(error=...) instead of propagating the exception.
        class _BoomCommand(BaseCommand):
            @property
            def command_name(self) -> str:
                return "boom"

            @property
            def description(self) -> str:
                return "Always raises."

            async def _execute(self, args, context):
                raise RuntimeError("kaboom in _execute")

        boom_result = await _BoomCommand().execute("anything", context=None)
        assert boom_result.success is False
        assert boom_result.error == "kaboom in _execute"

        # The un-overridden BaseCommand surfaces NotImplementedError for
        # its abstract members — also funnelled through execute()'s wrapper.
        bare = BaseCommand()
        bare_result = await bare.execute("x", context=None)
        assert bare_result.success is False

    async def test_info_agent_folder_override_wins(self, tmp_path, make_agent) -> None:
        """An agent-folder ``prompts/tools/bash.md`` override wins over the
        packaged builtin skill — per ``InfoCommand``'s documented order.

        Regression guard for B-commands-2 (FIXED): ``ControllerContext``
        exposed no ``agent_path``, so ``InfoCommand``'s documented
        priority-#1 override branch (`hasattr(context, "agent_path")`) was
        dead code and the override file was never consulted. The fix adds
        ``agent_path`` to ``ControllerContext`` and ``bootstrap/agent_init``
        wires the creature's config folder into it.
        """
        override_dir = tmp_path / "prompts" / "tools"
        override_dir.mkdir(parents=True)
        sentinel = "OVERRIDE-SENTINEL-bash-doc-from-agent-folder"
        (override_dir / "bash.md").write_text(
            f"---\nname: bash\n---\n\n# bash\n\n{sentinel}\n",
            encoding="utf-8",
        )

        agent = make_agent(
            script=["[/info]bash[info/]\nok."],
            agent_path=tmp_path,
        )
        await _run_turn(agent, "bash docs please")

        # Intended behaviour: the agent-folder override is resolved first.
        assert sentinel in _assistant_text(agent)
