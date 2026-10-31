"""Sandbox file policy: glob roots and declared tool path arguments."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from kohakuterrarium.builtins.plugins.sandbox import plugin as plugin_mod
from kohakuterrarium.builtins.plugins.sandbox.plugin import SandboxPlugin
from kohakuterrarium.builtins.tools.files.edit import EditTool
from kohakuterrarium.builtins.tools.files.glob import GlobTool
from kohakuterrarium.builtins.tools.files.grep import GrepTool
from kohakuterrarium.builtins.tools.files.multi_edit import MultiEditTool
from kohakuterrarium.builtins.tools.notebook.edit import NotebookEditTool
from kohakuterrarium.builtins.tools.notebook.read import NotebookReadTool
from kohakuterrarium.builtins.tools.files.read import ReadTool
from kohakuterrarium.builtins.tools.files.tree import TreeTool
from kohakuterrarium.builtins.tools.files.write import WriteTool
from kohakuterrarium.core.registry import Registry
from kohakuterrarium.modules.plugin.base import PluginBlockError, PluginContext
from kohakuterrarium.modules.tool.base import BaseTool, ToolContext, ToolResult


@pytest.fixture
def paths(tmp_path):
    work = tmp_path / "workspace"
    outside = tmp_path / "outside"
    work.mkdir()
    outside.mkdir()
    (work / "inside.txt").write_text("inside")
    (outside / "outside.txt").write_text("outside")
    return work, outside, ToolContext("test", None, work)


@pytest.mark.parametrize("uri", [False, True])
async def test_external_path_blocked(paths, uri):
    work, outside, context = paths
    path = outside.as_uri() if uri else str(outside)
    with pytest.raises(PluginBlockError, match="outside working directory"):
        await SandboxPlugin(fs_read="workspace").pre_tool_execute(
            {"path": path, "pattern": "*.txt"}, tool_name="glob", context=context
        )


@pytest.mark.parametrize(
    "args", [{"pattern": "*.txt"}, {"path": ".", "pattern": "*.txt"}]
)
async def test_default_path_obeys_read_deny(paths, args):
    _, _, context = paths
    with pytest.raises(PluginBlockError, match="fs_read=deny"):
        await SandboxPlugin(fs_read="deny").pre_tool_execute(
            args, tool_name="glob", context=context
        )


@pytest.mark.parametrize("path_form", ["relative", "absolute", "uri", "literal"])
async def test_allowed_search_uses_actual_base(paths, path_form):
    work, _, context = paths
    directory = work / ("literal[1]%TEMP%" if path_form == "literal" else "nested")
    directory.mkdir()
    (directory / "found.txt").write_text("found")
    path = (
        directory.name
        if path_form in ("relative", "literal")
        else (directory.as_uri() if path_form == "uri" else str(directory))
    )
    args = {"path": path, "pattern": "*.txt", "gitignore": False}
    await SandboxPlugin(fs_read="workspace").pre_tool_execute(
        args, tool_name="glob", context=context
    )
    result = await GlobTool().execute(args, context=context)
    assert result.error is None
    assert "found.txt" in result.output


async def test_prefix_is_relative_to_explicit_base(paths):
    work, _, context = paths
    nested = work / "nested"
    nested.mkdir()
    args = {"path": str(nested), "pattern": "../*.txt", "gitignore": False}
    await SandboxPlugin(fs_read="workspace").pre_tool_execute(
        args, tool_name="glob", context=context
    )
    assert "inside.txt" in (await GlobTool().execute(args, context=context)).output


@pytest.mark.parametrize(
    "pattern",
    [
        "../outside/*.txt",
        "*/../../*.txt",
        "**/../*.txt",
        "/tmp/*.txt",
        "C:/outside/*.txt",
        "C:*.txt",
        "\\\\server/share/*.txt",
    ],
)
async def test_escaping_patterns_blocked(paths, pattern):
    _, _, context = paths
    with pytest.raises(PluginBlockError):
        await SandboxPlugin(fs_read="workspace").pre_tool_execute(
            {"pattern": pattern}, tool_name="glob", context=context
        )


async def test_deny_list_uses_literal_path(paths):
    work, _, context = paths
    directory = work / "denied[1]"
    directory.mkdir()
    plugin = SandboxPlugin(fs_read="broad", fs_deny=[str(directory)])
    with pytest.raises(PluginBlockError, match="path is denied"):
        await plugin.pre_tool_execute(
            {"path": directory.as_uri(), "pattern": "*.txt"},
            tool_name="glob",
            context=context,
        )


@pytest.mark.parametrize("backend", ["audit", "off"])
async def test_non_enforcing_modes_remain_non_blocking(paths, backend):
    _, outside, context = paths
    await SandboxPlugin(fs_read="workspace", backend=backend).pre_tool_execute(
        {"path": str(outside), "pattern": "**/../*.txt"},
        tool_name="glob",
        context=context,
    )


async def test_base_symlink_target_is_checked(paths):
    work, outside, context = paths
    link = work / "link"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Directory symlinks unavailable on this host")
    with pytest.raises(PluginBlockError, match="outside working directory"):
        await SandboxPlugin(fs_read="workspace").pre_tool_execute(
            {"path": str(link), "pattern": "*.txt"}, tool_name="glob", context=context
        )


class _UploadTool(BaseTool):
    """Package-style tool that reads ``media`` and writes ``save_to``."""

    path_arguments = {"media": "read", "save_to": "write"}

    @property
    def tool_name(self):
        return "pkg_upload"

    @property
    def description(self):
        return "upload"

    async def _execute(self, args, **kwargs):
        return ToolResult(output="sent")


class _UndeclaredTool(_UploadTool):
    path_arguments = {}

    @property
    def tool_name(self):
        return "pkg_plain"


class TestDeclaredPathArguments:
    """Declared ``path_arguments`` are checked for any tool, not only built-ins."""

    async def test_denied_config_dir_is_blocked(self, paths):
        _, _, context = paths
        target = Path("~/.kohakuterrarium/credentials.json").expanduser()
        with pytest.raises(PluginBlockError, match="path is denied"):
            await SandboxPlugin(fs_read="broad").pre_tool_execute(
                {"media": str(target)},
                tool_name="pkg_upload",
                tool=_UploadTool(),
                context=context,
            )

    async def test_parent_traversal_leaves_the_workspace(self, paths):
        _, _, context = paths
        with pytest.raises(PluginBlockError, match="outside working directory"):
            await SandboxPlugin(fs_read="workspace").pre_tool_execute(
                {"media": "../outside/outside.txt"},
                tool_name="pkg_upload",
                tool=_UploadTool(),
                context=context,
            )

    async def test_list_values_are_each_checked(self, paths):
        _, outside, context = paths
        with pytest.raises(PluginBlockError, match="outside working directory"):
            await SandboxPlugin(fs_read="workspace").pre_tool_execute(
                {"media": ["inside.txt", str(outside / "outside.txt")]},
                tool_name="pkg_upload",
                tool=_UploadTool(),
                context=context,
            )

    async def test_write_argument_uses_the_write_axis(self, paths):
        _, _, context = paths
        with pytest.raises(PluginBlockError, match="fs_write=deny"):
            await SandboxPlugin(fs_write="deny").pre_tool_execute(
                {"save_to": "out.bin"},
                tool_name="pkg_upload",
                tool=_UploadTool(),
                context=context,
            )

    async def test_relative_deny_entry_resolves_against_the_working_dir(
        self, paths, monkeypatch, tmp_path
    ):
        work, _, context = paths
        (work / "private").mkdir()
        (work / "private" / "x.png").write_bytes(b"x")
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        monkeypatch.chdir(elsewhere)
        with pytest.raises(PluginBlockError, match="path is denied"):
            await SandboxPlugin(
                fs_read="workspace", fs_deny=["private"]
            ).pre_tool_execute(
                {"media": "private/x.png"},
                tool_name="pkg_upload",
                tool=_UploadTool(),
                context=context,
            )

    async def test_allowed_path_passes(self, paths):
        _, _, context = paths
        result = await SandboxPlugin(fs_read="workspace").pre_tool_execute(
            {"media": "inside.txt"},
            tool_name="pkg_upload",
            tool=_UploadTool(),
            context=context,
        )
        assert result is None

    async def test_tool_without_declarations_is_unaffected(self, paths):
        _, outside, context = paths
        result = await SandboxPlugin(fs_read="deny").pre_tool_execute(
            {"media": str(outside / "outside.txt")},
            tool_name="pkg_plain",
            tool=_UndeclaredTool(),
            context=context,
        )
        assert result is None

    async def test_registry_lookup_when_no_tool_is_passed(self, paths):
        _, outside, context = paths
        plugin = SandboxPlugin(fs_read="workspace")
        registry = Registry()
        registry.register_tool(_UploadTool())
        await plugin.on_load(
            PluginContext(
                agent_name="a",
                working_dir=context.working_dir,
                _host_agent=SimpleNamespace(registry=registry),
            )
        )
        with pytest.raises(PluginBlockError, match="outside working directory"):
            await plugin.pre_tool_execute(
                {"media": str(outside / "outside.txt")},
                tool_name="pkg_upload",
                context=context,
            )


@pytest.mark.parametrize(
    "tool_cls",
    [
        ReadTool,
        WriteTool,
        EditTool,
        MultiEditTool,
        GrepTool,
        TreeTool,
        NotebookReadTool,
        NotebookEditTool,
    ],
)
def test_builtin_file_tools_declare_their_path_argument(tool_cls):
    assert "path" in tool_cls.path_arguments


class TestFileToolWritesMatchTheKernelProfile:
    """``fs_write=workspace`` admits the same roots for file tools as for commands."""

    async def _write(self, sandbox, target, context):
        return await sandbox.pre_tool_execute(
            {"path": str(target), "content": "x"},
            tool_name="write",
            tool=WriteTool(),
            context=context,
        )

    async def test_write_outside_every_allowed_root_is_blocked(
        self, paths, monkeypatch
    ):
        _, outside, context = paths
        monkeypatch.setattr(plugin_mod, "temp_roots", lambda: [])
        with pytest.raises(PluginBlockError, match="outside working directory"):
            await self._write(
                SandboxPlugin(fs_write_allow=[]), outside / "new.txt", context
            )

    async def test_write_under_a_temp_root_is_allowed(self, paths, monkeypatch):
        _, outside, context = paths
        monkeypatch.setattr(plugin_mod, "temp_roots", lambda: [str(outside)])
        assert (
            await self._write(
                SandboxPlugin(fs_write_allow=[]), outside / "n.txt", context
            )
            is None
        )

    async def test_explicit_allow_is_an_exception_to_the_deny_list(
        self, paths, monkeypatch
    ):
        _, outside, context = paths
        monkeypatch.setattr(plugin_mod, "temp_roots", lambda: [])
        sockets = outside / "sockets"
        sandbox = SandboxPlugin(fs_deny=[str(outside)], fs_write_allow=[str(sockets)])
        assert await self._write(sandbox, sockets / "mux", context) is None
        with pytest.raises(PluginBlockError, match="path is denied"):
            await self._write(sandbox, outside / "outside.txt", context)

    async def test_deny_list_overrides_the_cache_roots(self, paths, monkeypatch):
        _, outside, context = paths
        monkeypatch.setattr(plugin_mod, "temp_roots", lambda: [])
        monkeypatch.setattr(plugin_mod, "CACHE_ROOTS", (str(outside),))
        sandbox = SandboxPlugin(fs_deny=[str(outside / "secret")])
        assert await self._write(sandbox, outside / "pip" / "wheel", context) is None
        with pytest.raises(PluginBlockError, match="path is denied"):
            await self._write(sandbox, outside / "secret" / "token", context)

    async def test_reads_do_not_gain_the_temp_roots(self, paths, monkeypatch):
        _, outside, context = paths
        monkeypatch.setattr(plugin_mod, "temp_roots", lambda: [str(outside)])
        with pytest.raises(PluginBlockError, match="outside working directory"):
            await SandboxPlugin(fs_read="workspace").pre_tool_execute(
                {"path": str(outside / "outside.txt")},
                tool_name="read",
                tool=ReadTool(),
                context=context,
            )
