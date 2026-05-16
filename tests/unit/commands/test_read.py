"""Unit tests for :mod:`kohakuterrarium.commands.read`.

Four legacy text-format commands. Behaviour contracts:

- ``ReadCommand`` (``read_job``): returns a job's output, optionally
  line-sliced; errors when no job_id / job missing; reports a running
  job's status instead of an error.
- ``InfoCommand``: resolves docs in priority order — agent-folder
  override → builtin skill → registry tool/subagent → procedural skill
  → "Not found" error.
- ``JobsCommand``: lists running jobs, or "No running jobs."
- ``WaitCommand``: returns immediately for an already-complete job,
  errors for a missing one, times out otherwise.
"""

from kohakuterrarium.commands.read import (
    InfoCommand,
    JobsCommand,
    ReadCommand,
    WaitCommand,
    _format_skill_for_info,
    _get_job_result,
    _lookup_skill_registry,
    _render_builtin_skill,
    _render_skill_from_path,
    _render_skill_info,
)
from kohakuterrarium.core.job import (
    JobResult,
    JobState,
    JobStatus,
    JobStore,
    JobType,
)
from kohakuterrarium.modules.tool.base import ToolInfo
from kohakuterrarium.skills import Skill, SkillRegistry


def _status(job_id: str, state: JobState, type_name: str = "tool") -> JobStatus:
    return JobStatus(
        job_id=job_id,
        job_type=JobType.TOOL,
        type_name=type_name,
        state=state,
        context={},
    )


class _JobContext:
    """Context exposing the job-store accessors ReadCommand/WaitCommand need."""

    def __init__(self):
        self.job_store = JobStore()

    def get_job_result(self, job_id: str):
        return self.job_store.get_result(job_id)

    def get_job_status(self, job_id: str):
        return self.job_store.get_status(job_id)


class TestReadCommand:
    async def test_missing_job_id_is_error(self):
        cmd = ReadCommand()
        result = await cmd.execute("", context=_JobContext())
        assert result.success is False
        assert "No job_id" in result.error

    async def test_context_without_job_result_accessor_is_error(self):
        result = await ReadCommand().execute("job_1", context=object())
        assert result.success is False
        assert "does not support job result" in result.error

    async def test_completed_job_output_returned(self):
        ctx = _JobContext()
        ctx.job_store.register(_status("job_1", JobState.DONE))
        ctx.job_store.store_result(JobResult(job_id="job_1", output="line a\nline b"))
        result = await ReadCommand().execute("job_1", context=ctx)
        assert result.success is True
        assert "line a\nline b" in result.content
        assert "## Job job_1 Output" in result.content

    async def test_job_error_rendered_with_error_section(self):
        ctx = _JobContext()
        ctx.job_store.register(_status("job_e", JobState.ERROR))
        ctx.job_store.store_result(
            JobResult(job_id="job_e", output="", error="disk full")
        )
        result = await ReadCommand().execute("job_e", context=ctx)
        assert result.success is True
        assert "(error)" in result.content
        assert "disk full" in result.content

    async def test_exit_code_appended_when_present(self):
        ctx = _JobContext()
        ctx.job_store.register(_status("job_x", JobState.DONE))
        ctx.job_store.store_result(
            JobResult(job_id="job_x", output="done", exit_code=0)
        )
        result = await ReadCommand().execute("job_x", context=ctx)
        assert "Exit code: 0" in result.content

    async def test_line_slicing_with_lines_and_offset(self):
        ctx = _JobContext()
        ctx.job_store.register(_status("job_s", JobState.DONE))
        ctx.job_store.store_result(
            JobResult(job_id="job_s", output="l0\nl1\nl2\nl3\nl4")
        )
        result = await ReadCommand().execute("job_s --offset 1 --lines 2", context=ctx)
        # offset 1 drops l0; lines 2 keeps l1, l2.
        assert "l1\nl2" in result.content
        assert "l0" not in result.content
        assert "l3" not in result.content

    async def test_running_job_reports_status_not_error(self):
        ctx = _JobContext()
        ctx.job_store.register(_status("job_r", JobState.RUNNING))
        result = await ReadCommand().execute("job_r", context=ctx)
        assert result.success is True
        assert "still running" in result.content

    async def test_pending_job_reports_pending(self):
        ctx = _JobContext()
        ctx.job_store.register(_status("job_p", JobState.PENDING))
        result = await ReadCommand().execute("job_p", context=ctx)
        assert result.success is True
        assert "pending" in result.content

    async def test_unknown_job_is_error(self):
        ctx = _JobContext()
        result = await ReadCommand().execute("nope", context=ctx)
        assert result.success is False
        assert "Job not found: nope" in result.error

    async def test_error_result_with_output_includes_output_block(self):
        ctx = _JobContext()
        ctx.job_store.register(_status("job_eo", JobState.ERROR))
        ctx.job_store.store_result(
            JobResult(job_id="job_eo", output="partial out", error="crashed")
        )
        result = await ReadCommand().execute("job_eo", context=ctx)
        assert "(error)" in result.content
        assert "crashed" in result.content
        assert "Output:\n```\npartial out\n```" in result.content


class TestGetJobResultHelper:
    def test_getter_exception_falls_back_to_job_store(self):
        store = JobStore()
        store.store_result(JobResult(job_id="j", output="from store"))

        class _Ctx:
            job_store = store

            def get_job_result(self, job_id):
                raise RuntimeError("getter exploded")

        result = _get_job_result(_Ctx(), "j")
        assert result is not None
        assert result.output == "from store"

    def test_mock_result_is_ignored_and_store_used(self):
        from unittest.mock import MagicMock

        store = JobStore()
        store.store_result(JobResult(job_id="j", output="real result"))

        class _Ctx:
            job_store = store

            def get_job_result(self, job_id):
                return MagicMock()  # comes from unittest.mock -> rejected

        result = _get_job_result(_Ctx(), "j")
        assert result.output == "real result"

    def test_no_getter_no_store_returns_none(self):
        assert _get_job_result(object(), "j") is None

    def test_real_getter_result_returned_directly(self):
        class _Ctx:
            def get_job_result(self, job_id):
                return JobResult(job_id=job_id, output="direct")

        assert _get_job_result(_Ctx(), "j").output == "direct"


class TestJobsCommand:
    async def test_no_job_store_is_error(self):
        result = await JobsCommand().execute("", context=object())
        assert result.success is False
        assert "No job store" in result.error

    async def test_no_running_jobs_message(self):
        ctx = _JobContext()
        result = await JobsCommand().execute("", context=ctx)
        assert result.success is True
        assert result.content == "No running jobs."

    async def test_lists_running_jobs_only(self):
        ctx = _JobContext()
        ctx.job_store.register(_status("run_1", JobState.RUNNING, type_name="bash"))
        ctx.job_store.register(_status("done_1", JobState.DONE))
        result = await JobsCommand().execute("", context=ctx)
        assert "`run_1`: bash (running)" in result.content
        assert "done_1" not in result.content


class TestWaitCommand:
    async def test_missing_job_id_is_error(self):
        result = await WaitCommand().execute("", context=_JobContext())
        assert result.success is False
        assert "No job_id" in result.error

    async def test_no_job_store_is_error(self):
        result = await WaitCommand().execute("job_1", context=object())
        assert result.success is False
        assert "No job store" in result.error

    async def test_missing_job_is_error(self):
        result = await WaitCommand().execute("ghost", context=_JobContext())
        assert result.success is False
        assert "Job not found: ghost" in result.error

    async def test_already_complete_job_returns_done_with_output(self):
        ctx = _JobContext()
        ctx.job_store.register(_status("job_d", JobState.DONE))
        ctx.job_store.store_result(JobResult(job_id="job_d", output="the result"))
        result = await WaitCommand().execute("job_d", context=ctx)
        assert result.success is True
        assert "## job_d - DONE" in result.content
        assert "the result" in result.content

    async def test_already_complete_job_with_error_returns_error_block(self):
        ctx = _JobContext()
        ctx.job_store.register(_status("job_f", JobState.ERROR))
        ctx.job_store.store_result(
            JobResult(job_id="job_f", output="", error="it broke")
        )
        result = await WaitCommand().execute("job_f", context=ctx)
        assert "## job_f - ERROR" in result.content
        assert "it broke" in result.content

    async def test_complete_job_with_no_stored_result(self):
        ctx = _JobContext()
        ctx.job_store.register(_status("job_n", JobState.DONE))
        result = await WaitCommand().execute("job_n", context=ctx)
        assert "## job_n - DONE (no output)" in result.content

    async def test_running_job_times_out_quickly(self):
        ctx = _JobContext()
        ctx.job_store.register(_status("job_w", JobState.RUNNING))
        # timeout below the 0.5s poll interval -> one sleep, then timeout.
        result = await WaitCommand().execute("job_w --timeout 0.4", context=ctx)
        assert result.success is True
        assert "TIMEOUT" in result.content

    async def test_job_completes_during_wait(self):
        import asyncio

        ctx = _JobContext()
        ctx.job_store.register(_status("job_late", JobState.RUNNING))

        async def _finish():
            await asyncio.sleep(0.6)
            ctx.job_store.register(_status("job_late", JobState.DONE))
            ctx.job_store.store_result(
                JobResult(job_id="job_late", output="finished late")
            )

        asyncio.create_task(_finish())
        result = await WaitCommand().execute("job_late --timeout 5", context=ctx)
        assert "## job_late - DONE" in result.content
        assert "finished late" in result.content

    async def test_job_fails_during_wait_returns_error_block(self):
        import asyncio

        ctx = _JobContext()
        ctx.job_store.register(_status("job_le", JobState.RUNNING))

        async def _fail():
            await asyncio.sleep(0.6)
            ctx.job_store.register(_status("job_le", JobState.ERROR))
            ctx.job_store.store_result(
                JobResult(job_id="job_le", output="", error="late failure")
            )

        asyncio.create_task(_fail())
        result = await WaitCommand().execute("job_le --timeout 5", context=ctx)
        assert "## job_le - ERROR" in result.content
        assert "late failure" in result.content

    async def test_job_completes_during_wait_with_no_result(self):
        import asyncio

        ctx = _JobContext()
        ctx.job_store.register(_status("job_lnr", JobState.RUNNING))

        async def _finish():
            await asyncio.sleep(0.6)
            ctx.job_store.register(_status("job_lnr", JobState.DONE))

        asyncio.create_task(_finish())
        result = await WaitCommand().execute("job_lnr --timeout 5", context=ctx)
        assert "## job_lnr - DONE (no output)" in result.content

    async def test_wait_cancellation_returns_cancelled_block(self):
        import asyncio

        ctx = _JobContext()
        ctx.job_store.register(_status("job_c", JobState.RUNNING))
        task = asyncio.ensure_future(
            WaitCommand().execute("job_c --timeout 30", context=ctx)
        )
        await asyncio.sleep(0.6)  # let it enter the poll loop
        task.cancel()
        result = await task
        assert "## job_c - CANCELLED" in result.content


class TestSkillRenderHelpers:
    def test_format_skill_for_info_no_tags_returns_body_unchanged(self):
        from kohakuterrarium.prompt.skill_loader import SkillDoc

        doc = SkillDoc(name="x", description="d", content="body text", tags=[])
        assert _format_skill_for_info(doc, "body text") == "body text"

    def test_format_skill_for_info_prepends_tag_line(self):
        from kohakuterrarium.prompt.skill_loader import SkillDoc

        doc = SkillDoc(name="x", description="d", content="body", tags=["io", "file"])
        out = _format_skill_for_info(doc, "body text")
        assert out == "Tags: io, file\n\nbody text"

    def test_format_skill_for_info_tags_only_when_body_empty(self):
        from kohakuterrarium.prompt.skill_loader import SkillDoc

        doc = SkillDoc(name="x", description="d", content="", tags=["only"])
        assert _format_skill_for_info(doc, "") == "Tags: only"

    def test_render_skill_from_path_loads_doc_body_with_tags(self, tmp_path):
        f = tmp_path / "s.md"
        f.write_text(
            "---\nname: s\ntags: [a]\n---\nthe documentation", encoding="utf-8"
        )
        out = _render_skill_from_path(f)
        assert out == "Tags: a\n\nthe documentation"

    def test_render_builtin_skill_unknown_kind_returns_none(self):
        assert _render_builtin_skill("not_a_kind", "anything") is None

    def test_render_builtin_skill_missing_tool_returns_none(self):
        assert _render_builtin_skill("tools", "no_such_builtin_tool_xyz") is None

    def test_render_builtin_skill_known_tool_resolves(self):
        # 'read' ships a real builtin SKILL.md.
        out = _render_builtin_skill("tools", "read")
        assert out is not None
        assert out  # non-empty body

    def test_render_skill_from_path_missing_file_returns_none(self, tmp_path):
        assert _render_skill_from_path(tmp_path / "absent.md") is None

    def test_render_skill_info_no_registry_returns_none(self):
        # No skills registry reachable on the context -> None (so the
        # InfoCommand falls through to its "Not found" error).
        assert _render_skill_info(object(), "anything") is None

    def test_render_skill_info_missing_skill_returns_none(self):
        skills = SkillRegistry()

        class _Ctx:
            skills_registry = skills

        assert _render_skill_info(_Ctx(), "no_such_skill") is None


class TestLookupSkillRegistry:
    def test_none_context_returns_none(self):
        assert _lookup_skill_registry(None) is None

    def test_direct_skills_registry_attribute(self):
        class _Ctx:
            skills_registry = SkillRegistry()

        ctx = _Ctx()
        assert _lookup_skill_registry(ctx) is ctx.skills_registry

    def test_resolves_via_agent_skills(self):
        reg = SkillRegistry()

        class _Agent:
            skills = reg

        class _Ctx:
            agent = _Agent()

        assert _lookup_skill_registry(_Ctx()) is reg

    def test_resolves_via_controller_skills_registry(self):
        reg = SkillRegistry()

        class _Controller:
            skills_registry = reg

        class _Ctx:
            controller = _Controller()

        assert _lookup_skill_registry(_Ctx()) is reg

    def test_resolves_via_controller_agent_skills(self):
        reg = SkillRegistry()

        class _Agent:
            skills = reg

        class _Controller:
            _agent = _Agent()

        class _Ctx:
            controller = _Controller()

        assert _lookup_skill_registry(_Ctx()) is reg

    def test_resolves_via_session_extra_empty_registry(self):
        # Regression guard for B-commands-1: an *empty* SkillRegistry is
        # falsy (__len__ == 0), so the session.extra lookup must gate on
        # `is not None`, not truthiness — otherwise a freshly-wired
        # registry is dropped before any skill is added.
        reg = SkillRegistry()  # empty -> len 0 -> falsy

        class _Session:
            extra = {"skills_registry": reg}

        class _Ctx:
            session = _Session()

        assert _lookup_skill_registry(_Ctx()) is reg

    def test_resolves_via_session_extra_non_empty_registry(self):
        reg = SkillRegistry()
        reg.add(Skill(name="s", description="d", body="b"))

        class _Session:
            extra = {"skills_registry": reg}

        class _Ctx:
            session = _Session()

        assert _lookup_skill_registry(_Ctx()) is reg

    def test_no_registry_anywhere_returns_none(self):
        class _Ctx:
            pass

        assert _lookup_skill_registry(_Ctx()) is None


class _RegistryContext:
    """Context exposing registry-style accessors for InfoCommand."""

    def __init__(self, tool_infos=None, tools=None, subagent_infos=None):
        self._tool_infos = tool_infos or {}
        self._tools = tools or {}
        self._subagent_infos = subagent_infos or {}

    def get_tool_info(self, name):
        return self._tool_infos.get(name)

    def get_tool(self, name):
        return self._tools.get(name)

    def get_subagent_info(self, name):
        return self._subagent_infos.get(name)


class TestInfoCommand:
    async def test_missing_name_is_error(self):
        result = await InfoCommand().execute("", context=_RegistryContext())
        assert result.success is False
        assert "No name provided" in result.error

    async def test_agent_folder_tool_override_wins(self, tmp_path):
        tools_dir = tmp_path / "prompts" / "tools"
        tools_dir.mkdir(parents=True)
        (tools_dir / "mytool.md").write_text(
            "---\nname: mytool\n---\nOVERRIDE DOC BODY", encoding="utf-8"
        )
        ctx = _RegistryContext()
        ctx.agent_path = str(tmp_path)
        result = await InfoCommand().execute("mytool", context=ctx)
        assert result.success is True
        assert "OVERRIDE DOC BODY" in result.content

    async def test_agent_folder_subagent_override(self, tmp_path):
        sa_dir = tmp_path / "prompts" / "subagents"
        sa_dir.mkdir(parents=True)
        (sa_dir / "planner.md").write_text(
            "---\nname: planner\n---\nPLANNER SUBAGENT DOC", encoding="utf-8"
        )
        ctx = _RegistryContext()
        ctx.agent_path = str(tmp_path)
        result = await InfoCommand().execute("planner", context=ctx)
        assert "PLANNER SUBAGENT DOC" in result.content

    async def test_builtin_tool_skill_resolved(self):
        # 'read' has a real builtin skill doc shipped in the package.
        result = await InfoCommand().execute("read", context=_RegistryContext())
        assert result.success is True
        assert result.content  # the builtin read.md body
        assert "read" in result.content.lower()

    async def test_registry_tool_info_documentation_used(self):
        info = ToolInfo(
            tool_name="custom_xyz",
            description="a custom tool",
            documentation="FULL CUSTOM DOCS",
        )
        ctx = _RegistryContext(tool_infos={"custom_xyz": info})
        result = await InfoCommand().execute("custom_xyz", context=ctx)
        assert result.content == "FULL CUSTOM DOCS"

    async def test_registry_tool_info_falls_back_to_description(self):
        info = ToolInfo(tool_name="bare_xyz", description="bare desc", documentation="")
        ctx = _RegistryContext(tool_infos={"bare_xyz": info})
        result = await InfoCommand().execute("bare_xyz", context=ctx)
        assert "# bare_xyz" in result.content
        assert "bare desc" in result.content

    async def test_tool_instance_full_documentation_preferred(self):
        info = ToolInfo(tool_name="t_xyz", description="d", documentation="INFO DOC")

        class _Tool:
            def get_full_documentation(self):
                return "INSTANCE FULL DOC"

        ctx = _RegistryContext(tool_infos={"t_xyz": info}, tools={"t_xyz": _Tool()})
        result = await InfoCommand().execute("t_xyz", context=ctx)
        assert result.content == "INSTANCE FULL DOC"

    async def test_subagent_info_used_when_no_tool(self):
        ctx = _RegistryContext(subagent_infos={"sa_xyz": "SUBAGENT INFO TEXT"})
        result = await InfoCommand().execute("sa_xyz", context=ctx)
        assert result.content == "SUBAGENT INFO TEXT"

    async def test_procedural_skill_resolved_via_skills_registry(self):
        skills = SkillRegistry()
        skills.add(
            Skill(
                name="deploy_skill",
                description="how to deploy",
                body="run the deploy script",
                origin="user",
            )
        )
        ctx = _RegistryContext()
        ctx.skills_registry = skills
        result = await InfoCommand().execute("deploy_skill", context=ctx)
        assert result.success is True
        assert "--- Skill: deploy_skill ---" in result.content
        assert "Origin: user" in result.content
        assert "Description: how to deploy" in result.content
        assert "run the deploy script" in result.content

    async def test_builtin_subagent_skill_resolved(self):
        # 'explore' ships a real builtin subagent skill doc.
        result = await InfoCommand().execute("explore", context=_RegistryContext())
        assert result.success is True
        assert result.content

    async def test_procedural_skill_includes_paths_when_set(self):
        skills = SkillRegistry()
        skills.add(
            Skill(
                name="path_skill",
                description="",
                body="",
                origin="user",
                paths=["scripts/run.sh", "refs/notes.md"],
            )
        )
        ctx = _RegistryContext()
        ctx.skills_registry = skills
        result = await InfoCommand().execute("path_skill", context=ctx)
        assert "Paths: scripts/run.sh, refs/notes.md" in result.content
        # No description line when description is empty.
        assert "Description:" not in result.content

    async def test_unknown_target_is_not_found_error(self):
        result = await InfoCommand().execute(
            "definitely_not_real_xyz", context=_RegistryContext()
        )
        assert result.success is False
        assert "Not found: definitely_not_real_xyz" in result.error
