"""Sandbox file policy: glob roots and declared tool path arguments."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from kohakuterrarium.builtins.plugins.sandbox import plugin as plugin_mod
from kohakuterrarium.builtins.plugins.sandbox import roots
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
from kohakuterrarium.builtins.tools.web.fetch import WebFetchTool
from kohakuterrarium.builtins.tools.web.search import WebSearchTool
from kohakuterrarium.core.registry import Registry
from kohakuterrarium.core.agent.workspace import WorkspaceController
from kohakuterrarium.modules.plugin.base import (
    BasePlugin,
    PluginBlockError,
    PluginContext,
    PluginLockedError,
)
from kohakuterrarium.modules.plugin.manager import manager as manager_mod
from kohakuterrarium.modules.plugin.manager.manager import PluginManager
from kohakuterrarium.modules.tool.base import BaseTool, ToolContext, ToolResult
from kohakuterrarium.modules.tool.function import tool as kt_tool


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
        monkeypatch.setattr(roots, "temp_roots", lambda: [])
        with pytest.raises(PluginBlockError, match="outside working directory"):
            await self._write(
                SandboxPlugin(fs_write_allow=[]), outside / "new.txt", context
            )

    async def test_write_under_a_temp_root_is_allowed(self, paths, monkeypatch):
        _, outside, context = paths
        monkeypatch.setattr(roots, "temp_roots", lambda: [str(outside)])
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
        monkeypatch.setattr(roots, "temp_roots", lambda: [])
        sockets = outside / "sockets"
        sandbox = SandboxPlugin(fs_deny=[str(outside)], fs_write_allow=[str(sockets)])
        assert await self._write(sandbox, sockets / "mux", context) is None
        with pytest.raises(PluginBlockError, match="path is denied"):
            await self._write(sandbox, outside / "outside.txt", context)

    async def test_deny_list_overrides_the_cache_roots(self, paths, monkeypatch):
        _, outside, context = paths
        monkeypatch.setattr(roots, "temp_roots", lambda: [])
        monkeypatch.setattr(roots, "CACHE_ROOTS", (str(outside),))
        sandbox = SandboxPlugin(fs_deny=[str(outside / "secret")])
        assert await self._write(sandbox, outside / "pip" / "wheel", context) is None
        with pytest.raises(PluginBlockError, match="path is denied"):
            await self._write(sandbox, outside / "secret" / "token", context)

    async def test_reads_do_not_gain_the_temp_roots(self, paths, monkeypatch):
        _, outside, context = paths
        monkeypatch.setattr(roots, "temp_roots", lambda: [str(outside)])
        with pytest.raises(PluginBlockError, match="outside working directory"):
            await SandboxPlugin(fs_read="workspace").pre_tool_execute(
                {"path": str(outside / "outside.txt")},
                tool_name="read",
                tool=ReadTool(),
                context=context,
            )


class _UrlTool(_UploadTool):
    path_arguments = {}
    url_arguments = ("target",)

    @property
    def tool_name(self):
        return "pkg_url"


class _HostTool(_UploadTool):
    path_arguments = {}
    network_hosts = ("api.example.com",)

    @property
    def tool_name(self):
        return "pkg_host"


class _ElsewhereTool(_UploadTool):
    path_arguments = {}
    network_access = True

    @property
    def tool_name(self):
        return "pkg_elsewhere"


class _NetworkPlugin(BasePlugin):
    name = "pkg_net"

    def contribute_tools(self):
        return [_UrlTool()]


def _gate(sandbox, tool, args, context):
    return sandbox.pre_tool_execute(
        args, tool_name=tool.tool_name, tool=tool, context=context
    )


def _record_warnings(monkeypatch):
    seen: list[str] = []
    monkeypatch.setattr(
        plugin_mod.logger, "warning", lambda msg, *a, **k: seen.append(msg)
    )
    return seen


class TestDeclaredNetworkUse:
    """``url_arguments`` / ``network_hosts`` / ``network_access`` gate any tool."""

    @pytest.mark.parametrize(
        ("tool", "args", "requested"),
        [
            (_UrlTool(), {"target": "https://ok.test/a"}, "https://ok.test/a"),
            (_HostTool(), {}, "https://api.example.com/"),
            (_ElsewhereTool(), {}, "pkg_elsewhere"),
        ],
    )
    async def test_network_deny_blocks_each_declaration(
        self, paths, tool, args, requested
    ):
        _, _, context = paths
        with pytest.raises(PluginBlockError) as err:
            await _gate(SandboxPlugin(network="deny"), tool, args, context)
        assert str(err.value) == (
            f"SandboxViolation[network]: {tool.tool_name}: network access denied "
            f"({requested})"
        )

    @pytest.mark.parametrize(
        ("tool", "args", "host"),
        [
            (_UrlTool(), {"target": "https://evil.test/a"}, "evil.test"),
            (_UrlTool(), {"target": ["https://ok.test", "evil.test/x"]}, "evil.test"),
            (_HostTool(), {}, "api.example.com"),
        ],
    )
    async def test_allowlist_miss_is_blocked(self, paths, tool, args, host):
        _, _, context = paths
        sandbox = SandboxPlugin(network="allow", network_allowlist=["ok.test"])
        with pytest.raises(PluginBlockError, match=f"host '{host}' not allowlisted"):
            await _gate(sandbox, tool, args, context)

    @pytest.mark.parametrize(
        ("tool", "args"),
        [
            (_UrlTool(), {"target": "https://OK.test/a"}),
            (_UrlTool(), {"target": ["ok.test/1", "https://api.example.com/2"]}),
            (_HostTool(), {}),
            (_ElsewhereTool(), {}),
        ],
    )
    async def test_allowlist_hit_passes(self, paths, tool, args):
        _, _, context = paths
        sandbox = SandboxPlugin(
            network="allow", network_allowlist=["ok.test", "api.example.com"]
        )
        assert await _gate(sandbox, tool, args, context) is None

    @pytest.mark.parametrize(
        ("tool", "args"),
        [
            (_UrlTool(), {"target": "https://evil.test/a"}),
            (_HostTool(), {}),
            (_ElsewhereTool(), {}),
        ],
    )
    async def test_audit_mode_logs_and_allows(self, paths, monkeypatch, tool, args):
        _, _, context = paths
        seen = _record_warnings(monkeypatch)
        sandbox = SandboxPlugin(backend="audit", network="deny")
        assert await _gate(sandbox, tool, args, context) is None
        assert any("network access denied" in line for line in seen)

    async def test_truthy_network_access_fails_closed(self, paths):
        _, _, context = paths
        tool = _ElsewhereTool()
        tool.network_access = 1
        with pytest.raises(PluginBlockError, match="network access denied"):
            await _gate(SandboxPlugin(network="deny"), tool, {}, context)

    async def test_bare_string_declarations_are_one_name(self, paths):
        _, _, context = paths
        tool = _UrlTool()
        tool.url_arguments = "target"
        tool.network_hosts = "api.example.com"
        sandbox = SandboxPlugin(network_allowlist=["api.example.com"])
        assert (
            await _gate(sandbox, tool, {"target": "api.example.com"}, context) is None
        )
        with pytest.raises(PluginBlockError, match="host 'evil.test'"):
            await _gate(sandbox, tool, {"target": "evil.test"}, context)

    async def test_empty_url_argument_is_not_checked(self, paths):
        _, _, context = paths
        sandbox = SandboxPlugin(network="deny")
        assert await _gate(sandbox, _UrlTool(), {"target": ""}, context) is None

    async def test_tool_without_declarations_is_not_gated(self, paths):
        _, _, context = paths
        sandbox = SandboxPlugin(network="deny", network_allowlist=["ok.test"])
        result = await _gate(
            sandbox, _UndeclaredTool(), {"target": "https://evil.test"}, context
        )
        assert result is None

    async def test_function_tool_declarations_are_gated(self, paths):
        _, _, context = paths

        @kt_tool(url_arguments=["url"])
        def fetch_page(url: str) -> str:
            return url

        with pytest.raises(PluginBlockError, match="fetch_page: network access"):
            await _gate(
                SandboxPlugin(network="deny"), fetch_page, {"url": "x.test"}, context
            )

    async def test_plugin_contributed_tool_is_gated_through_the_registry(self, paths):
        _, _, context = paths
        registry = Registry()
        for contributed in _NetworkPlugin().contribute_tools():
            registry.register_tool(contributed)
        sandbox = SandboxPlugin(network="allow", network_allowlist=["ok.test"])
        await sandbox.on_load(
            PluginContext(
                agent_name="a",
                working_dir=context.working_dir,
                _host_agent=SimpleNamespace(registry=registry),
            )
        )
        with pytest.raises(PluginBlockError, match="host 'evil.test'"):
            await sandbox.pre_tool_execute(
                {"target": "https://evil.test"}, tool_name="pkg_url", context=context
            )


class _TwoUrlTool(_UrlTool):
    url_arguments = ("first", "second")


def _managed(sandbox, tool):
    """``tool.execute`` behind a real plugin manager holding ``sandbox``."""
    manager = PluginManager()
    manager.register(sandbox)
    return manager.wrap_method(
        "pre_tool_execute",
        "post_tool_execute",
        tool.execute,
        input_kwarg="args",
        extra_kwargs={"tool_name": tool.tool_name, "tool": tool},
    )


class TestUnparseableNetworkDeclarations:
    """A declared URL or host the parser rejects is a denial, not a hook failure."""

    @pytest.mark.parametrize(
        ("tool", "args", "requested"),
        [
            (_UrlTool(), {"target": "http://[::1"}, "http://[::1"),
            (_UrlTool(), {"target": "https://[not-an-ip]/"}, "https://[not-an-ip]/"),
            (
                _UrlTool(),
                {"target": ["https://ok.test/", "https://]/"]},
                "https://]/",
            ),
            (_UrlTool(), {"target": [["x"]]}, "https://['x']"),
            (
                _TwoUrlTool(),
                {"first": "https://ok.test/", "second": "http://[::1"},
                "http://[::1",
            ),
        ],
    )
    async def test_allowlist_denies_the_call_through_the_manager(
        self, paths, tool, args, requested
    ):
        _, _, context = paths
        execute = _managed(SandboxPlugin(network_allowlist=["ok.test"]), tool)
        with pytest.raises(PluginBlockError) as err:
            await execute(args, context=context)
        assert str(err.value) == (
            f"SandboxViolation[network]: {tool.tool_name}: unparseable URL "
            f"({requested})"
        )

    async def test_malformed_declared_host_is_denied(self, paths):
        _, _, context = paths
        tool = _HostTool()
        tool.network_hosts = ("[::1",)
        execute = _managed(SandboxPlugin(network_allowlist=["ok.test"]), tool)
        with pytest.raises(PluginBlockError, match="unparseable URL"):
            await execute({}, context=context)

    async def test_unrestricted_network_runs_the_call_without_a_hook_failure(
        self, paths, monkeypatch
    ):
        _, _, context = paths
        seen: list[str] = []
        monkeypatch.setattr(
            manager_mod.logger, "warning", lambda msg, *a, **k: seen.append(msg)
        )
        execute = _managed(SandboxPlugin(), _UrlTool())
        result = await execute({"target": "http://[::1"}, context=context)
        assert result.output == "sent"
        assert seen == [], "no pre-hook failure was logged"

    async def test_audit_mode_logs_and_allows(self, paths, monkeypatch):
        _, _, context = paths
        seen = _record_warnings(monkeypatch)
        sandbox = SandboxPlugin(backend="audit", network_allowlist=["ok.test"])
        result = await _managed(sandbox, _UrlTool())(
            {"target": "http://[::1"}, context=context
        )
        assert result.output == "sent"
        assert seen == [
            "SandboxViolation[network]: pkg_url: unparseable URL (http://[::1)"
        ]


class TestWebToolsKeepTheirDecisions:
    """``web_fetch`` and ``web_search`` produce the exact pre-declaration messages."""

    def test_web_tools_declare_their_network_use(self):
        assert WebFetchTool.url_arguments == ("url",)
        assert WebSearchTool.network_hosts == ("duckduckgo.com",)
        assert WebFetchTool.network_access is False
        assert WebSearchTool.network_access is False

    @pytest.mark.parametrize(
        ("tool", "args", "options", "message"),
        [
            (
                WebFetchTool(),
                {"url": "https://example.com/page"},
                {"network": "deny"},
                "SandboxViolation[network]: web_fetch: network access denied "
                "(https://example.com/page)",
            ),
            (
                WebFetchTool(),
                {"url": "evil.test/x"},
                {"network_allowlist": ["example.com"]},
                "SandboxViolation[network]: web_fetch: host 'evil.test' not "
                "allowlisted (https://evil.test/x)",
            ),
            (
                WebSearchTool(),
                {"query": "q"},
                {"network": "deny"},
                "SandboxViolation[network]: web_search: network access denied "
                "(https://duckduckgo.com/)",
            ),
            (
                WebSearchTool(),
                {"query": "q"},
                {"network_allowlist": ["example.com"]},
                "SandboxViolation[network]: web_search: host 'duckduckgo.com' not "
                "allowlisted (https://duckduckgo.com/)",
            ),
            (
                WebSearchTool(),
                {"query": "q"},
                {"profile": "PURE"},
                "SandboxViolation[network]: web_search: network access denied "
                "(https://duckduckgo.com/)",
            ),
        ],
    )
    async def test_violation_messages_are_unchanged(
        self, paths, tool, args, options, message
    ):
        _, _, context = paths
        with pytest.raises(PluginBlockError) as err:
            await _gate(SandboxPlugin(**options), tool, args, context)
        assert str(err.value) == message

    @pytest.mark.parametrize(
        ("tool", "args", "options"),
        [
            (WebFetchTool(), {"url": "https://example.com/p"}, {}),
            (
                WebFetchTool(),
                {"url": "example.com/p"},
                {"network_allowlist": ["example.com"]},
            ),
            (WebFetchTool(), {}, {"network": "deny"}),
            (WebSearchTool(), {"query": "q"}, {}),
            (
                WebSearchTool(),
                {"query": "q"},
                {"network_allowlist": ["duckduckgo.com"]},
            ),
        ],
    )
    async def test_allowed_calls_still_pass(self, paths, tool, args, options):
        _, _, context = paths
        assert await _gate(SandboxPlugin(**options), tool, args, context) is None

    async def test_audit_logs_both_web_fetch_violations(self, paths, monkeypatch):
        _, _, context = paths
        seen = _record_warnings(monkeypatch)
        sandbox = SandboxPlugin(
            backend="audit", network="deny", network_allowlist=["example.com"]
        )
        args = {"url": "https://evil.test/x"}
        assert await _gate(sandbox, WebFetchTool(), args, context) is None
        assert seen == [
            "SandboxViolation[network]: web_fetch: network access denied "
            "(https://evil.test/x)",
            "SandboxViolation[network]: web_fetch: host 'evil.test' not "
            "allowlisted (https://evil.test/x)",
        ]

    async def test_audit_logs_both_web_search_violations_once(self, paths, monkeypatch):
        _, _, context = paths
        seen = _record_warnings(monkeypatch)
        sandbox = SandboxPlugin(
            backend="audit", network="deny", network_allowlist=["example.com"]
        )
        assert await _gate(sandbox, WebSearchTool(), {"query": "q"}, context) is None
        assert seen == [
            "SandboxViolation[network]: web_search: network access denied "
            "(https://duckduckgo.com/)",
            "SandboxViolation[network]: web_search: host 'duckduckgo.com' not "
            "allowlisted (https://duckduckgo.com/)",
        ]


class _NamedUndeclaredTool(_UndeclaredTool):
    """A replacement tool that takes a built-in network tool's name."""

    def __init__(self, name):
        super().__init__()
        self._name = name

    @property
    def tool_name(self):
        return self._name


class TestNetworkNameTableFloor:
    """The built-in name tables gate ``web_fetch`` / ``web_search`` regardless."""

    @pytest.mark.parametrize(
        ("name", "args", "requested"),
        [
            ("web_fetch", {"url": "https://example.com/p"}, "https://example.com/p"),
            ("web_search", {"query": "q"}, "https://duckduckgo.com/"),
        ],
    )
    async def test_undeclared_replacement_is_gated(self, paths, name, args, requested):
        _, _, context = paths
        with pytest.raises(PluginBlockError) as err:
            await _gate(
                SandboxPlugin(network="deny"), _NamedUndeclaredTool(name), args, context
            )
        assert str(err.value) == (
            f"SandboxViolation[network]: {name}: network access denied ({requested})"
        )

    @pytest.mark.parametrize(
        ("name", "args"),
        [("web_fetch", {"url": "evil.test/x"}), ("web_search", {"query": "q"})],
    )
    async def test_call_without_tool_or_registry_is_gated(self, paths, name, args):
        _, _, context = paths
        sandbox = SandboxPlugin(network_allowlist=["example.com"])
        with pytest.raises(PluginBlockError, match="not allowlisted"):
            await sandbox.pre_tool_execute(
                args, tool_name=name, tool=None, context=context
            )

    async def test_declared_and_tabled_entries_are_checked_once(
        self, paths, monkeypatch
    ):
        _, _, context = paths
        tool = _NamedUndeclaredTool("web_fetch")
        tool.url_arguments = ("url", "url")
        tool.network_hosts = ("api.example.com", "api.example.com")
        seen = _record_warnings(monkeypatch)
        sandbox = SandboxPlugin(backend="audit", network="deny")
        assert await _gate(sandbox, tool, {"url": "https://a.test/"}, context) is None
        assert seen == [
            "SandboxViolation[network]: web_fetch: network access denied "
            "(https://api.example.com/)",
            "SandboxViolation[network]: web_fetch: network access denied "
            "(https://a.test/)",
        ]


class TestNetworkPolicyService:
    """``runtime_services()['network_policy']`` answers host checks live."""

    def _policy(self, sandbox):
        return sandbox.runtime_services(None)["network_policy"]

    def test_published_beside_the_subprocess_runner(self):
        services = SandboxPlugin().runtime_services(None)
        assert services["subprocess_runner"] is not None
        assert isinstance(services["network_policy"], plugin_mod.SandboxNetworkPolicy)
        assert callable(services["network_policy"].check)

    @pytest.mark.parametrize("options", [{"enabled": False}, {"backend": "off"}])
    def test_disabled_plugin_exposes_no_service(self, options):
        assert "network_policy" not in SandboxPlugin(**options).runtime_services(None)

    @pytest.mark.parametrize(
        ("options", "host", "expected"),
        [
            ({}, "anything.test", None),
            ({"network": "deny"}, "anything.test", "network access denied"),
            ({"profile": "PURE"}, "anything.test", "network access denied"),
            (
                {"network_allowlist": ["ok.test"]},
                "evil.test",
                "host 'evil.test' not allowlisted",
            ),
            ({"network_allowlist": ["ok.test"]}, "ok.test", None),
            ({"network_allowlist": ["ok.test"]}, "OK.Test", None),
            (
                {"network": "deny", "network_allowlist": ["ok.test"]},
                "ok.test",
                "network access denied",
            ),
        ],
    )
    def test_check_applies_mode_and_allowlist(self, options, host, expected):
        assert self._policy(SandboxPlugin(**options)).check(host, 443) == expected

    def test_option_changes_apply_to_a_held_service(self):
        sandbox = SandboxPlugin()
        policy = self._policy(sandbox)
        assert policy.mode == "allow"
        assert policy.check("evil.test", 80) is None
        sandbox.set_options({"network_allowlist": ["ok.test"]})
        assert policy.check("evil.test", 80) == "host 'evil.test' not allowlisted"
        sandbox.set_options({"network": "deny"})
        assert policy.mode == "deny"
        assert policy.check("ok.test", 80) == "network access denied"
        sandbox.set_options({"enabled": False})
        assert policy.check("evil.test", 80) is None
        assert policy.mode == "allow"

    def test_bracketed_ipv6_host_matches_its_allowlist_entry(self):
        policy = self._policy(SandboxPlugin(network_allowlist=["::1"]))
        assert policy.check("[::1]", 443) is None
        assert policy.check("[::2]", 443) == "host '::2' not allowlisted"

    def test_audit_mode_logs_and_allows(self, monkeypatch):
        seen = _record_warnings(monkeypatch)
        policy = self._policy(SandboxPlugin(backend="audit", network="deny"))
        assert policy.check("evil.test", 8080) is None
        assert seen == [
            "SandboxViolation[network]: network_policy: network access denied "
            "(evil.test:8080)"
        ]

    @pytest.mark.parametrize(
        ("options", "expected"),
        [
            ({}, False),
            ({"os_enforce": "auto"}, False),
            ({"os_enforce": "off"}, False),
            ({"os_enforce": "require"}, True),
            ({"os_enforce": "require", "backend": "audit"}, False),
        ],
    )
    def test_requires_os_confinement(self, options, expected):
        policy = self._policy(SandboxPlugin(**options))
        assert policy.requires_os_confinement is expected

    def test_confinement_flag_follows_option_changes(self):
        sandbox = SandboxPlugin()
        policy = self._policy(sandbox)
        sandbox.set_options({"os_enforce": "require"})
        assert policy.requires_os_confinement is True
        sandbox.set_options({"enabled": False})
        assert policy.requires_os_confinement is False


class TestWritePolicyService:
    """``runtime_services()['write_policy']`` answers write checks live."""

    @pytest.fixture
    def write_paths(self, paths, monkeypatch):
        monkeypatch.setattr(roots, "temp_roots", lambda: [])
        work, outside, _ = paths
        return work, outside

    async def _policy(self, work, **options):
        sandbox = SandboxPlugin(**options)
        await sandbox.on_load(PluginContext(working_dir=work))
        return sandbox, sandbox.runtime_services(None)["write_policy"]

    async def test_write_deny_refuses_every_path(self, write_paths):
        work, outside = write_paths
        _, policy = await self._policy(work, fs_write="deny")
        for path in (work / "inside.txt", "inside.txt", outside / "x.txt"):
            assert policy.check(path) == "fs_write=deny"

    async def test_workspace_refuses_outside_and_allows_inside(self, write_paths):
        work, outside = write_paths
        _, policy = await self._policy(work, fs_write="workspace")
        refused = f"path is outside working directory ({work})"
        assert policy.check(outside / "x.txt") == refused
        assert policy.check("../outside/x.txt") == refused
        assert policy.check(work / "sub" / "new.txt") is None
        assert policy.check("sub/new.txt") is None, "relative to the workspace"

    async def test_fs_deny_entry_is_refused_under_broad_write(self, write_paths):
        work, outside = write_paths
        secret = outside / "secret"
        _, policy = await self._policy(work, fs_write="broad", fs_deny=[str(secret)])
        assert policy.check(secret / "key.pem") == "path is denied"
        assert policy.check(outside / "free.txt") is None

    async def test_matches_the_tool_hook_decision(self, write_paths):
        work, outside = write_paths
        sandbox, policy = await self._policy(
            work,
            fs_write="broad",
            fs_deny=[str(outside)],
            fs_write_allow=[str(outside / "drop")],
        )
        context = ToolContext("test", None, work)
        for path, allowed in ((outside / "drop" / "a", True), (outside / "b", False)):
            assert (policy.check(path) is None) is allowed
            if allowed:
                await sandbox.pre_tool_execute(
                    {"path": str(path)}, tool_name="write", context=context
                )
                continue
            with pytest.raises(PluginBlockError, match="path is denied"):
                await sandbox.pre_tool_execute(
                    {"path": str(path)}, tool_name="write", context=context
                )

    async def test_option_change_is_seen_through_a_held_service(self, write_paths):
        work, _ = write_paths
        sandbox, policy = await self._policy(work)
        assert policy.check(work / "a.txt") is None
        sandbox.set_options({"fs_write": "deny"})
        assert policy.check(work / "a.txt") == "fs_write=deny"
        sandbox.set_options({"enabled": False})
        assert policy.check(work / "a.txt") is None

    async def test_follows_the_agents_current_working_directory(self, write_paths):
        work, outside = write_paths
        agent = SimpleNamespace(executor=SimpleNamespace(_working_dir=work))
        agent.workspace = WorkspaceController(agent)
        sandbox = SandboxPlugin(fs_write="workspace")
        await sandbox.on_load(PluginContext(working_dir=work, _host_agent=agent))
        policy = sandbox.runtime_services(None)["write_policy"]
        assert policy.check("x.txt") is None
        assert policy.check(outside / "x.txt") is not None
        agent.executor._working_dir = outside
        assert policy.check(outside / "x.txt") is None
        assert policy.check(work / "x.txt") is not None

    async def test_audit_mode_logs_every_violation_and_allows(
        self, write_paths, monkeypatch
    ):
        work, outside = write_paths
        seen = _record_warnings(monkeypatch)
        sandbox, policy = await self._policy(
            work, backend="audit", fs_write="deny", fs_deny=[str(outside)]
        )
        target = outside / "x.txt"
        assert policy.check(target) is None
        assert seen == [
            f"SandboxViolation[fs_write]: write_policy: path is denied ({target})",
            f"SandboxViolation[fs_write]: write_policy: fs_write=deny ({target})",
        ]
        seen.clear()
        context = ToolContext("test", None, work)
        await sandbox.pre_tool_execute(
            {"path": str(target)}, tool_name="write", context=context
        )
        assert [line.split(": ", 2)[2] for line in seen] == [
            f"path is denied ({target})",
            f"fs_write=deny ({target})",
        ], "the tool hook still logs each violation in audit mode"

    @pytest.mark.parametrize("options", [{"enabled": False}, {"backend": "off"}])
    def test_disabled_plugin_exposes_no_service(self, options):
        assert "write_policy" not in SandboxPlugin(**options).runtime_services(None)


class TestLock:
    def test_locked_sandbox_refuses_runtime_changes(self, monkeypatch):
        monkeypatch.delenv(plugin_mod.UNLOCK_ENV, raising=False)
        sandbox = SandboxPlugin(locked=True)
        with pytest.raises(PluginLockedError, match="KT_SANDBOX_ALLOW_UNLOCK"):
            sandbox.set_options({"backend": "off"})
        with pytest.raises(PluginLockedError):
            sandbox.set_options({"locked": False})
        assert sandbox.refuses_disable() is True
        assert sandbox.options["backend"] == "auto"

    def test_reapplying_current_values_is_not_a_change(self, monkeypatch):
        monkeypatch.delenv(plugin_mod.UNLOCK_ENV, raising=False)
        sandbox = SandboxPlugin(locked=True, fs_write="workspace")
        # Resume re-applies the persisted options onto an already-locked plugin.
        sandbox.set_options({"locked": True, "fs_write": "workspace"})

    def test_server_started_with_the_unlock_flag_may_change_it(self, monkeypatch):
        monkeypatch.setenv(plugin_mod.UNLOCK_ENV, "1")
        sandbox = SandboxPlugin(locked=True)
        assert sandbox.refuses_disable() is False
        assert sandbox.set_options({"locked": False})["locked"] is False

    def test_locked_sandbox_pins_the_working_dir(self, monkeypatch):
        monkeypatch.delenv(plugin_mod.UNLOCK_ENV, raising=False)
        assert SandboxPlugin(locked=True).pins_working_dir() is True
        assert SandboxPlugin().pins_working_dir() is False
        assert SandboxPlugin(locked=True, backend="off").pins_working_dir() is False

    def test_unlocked_sandbox_can_be_locked_at_runtime(self, monkeypatch):
        monkeypatch.delenv(plugin_mod.UNLOCK_ENV, raising=False)
        sandbox = SandboxPlugin()
        assert sandbox.refuses_disable() is False
        sandbox.set_options({"locked": True})
        assert sandbox.refuses_disable() is True
