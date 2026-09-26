"""Sandbox glob policy checks the same roots as the tool."""

import pytest

from kohakuterrarium.builtins.plugins.sandbox.plugin import SandboxPlugin
from kohakuterrarium.builtins.tools.glob import GlobTool
from kohakuterrarium.modules.plugin.base import PluginBlockError
from kohakuterrarium.modules.tool.base import ToolContext


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
