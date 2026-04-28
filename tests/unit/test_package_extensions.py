"""Tests for the package extension system (Phase 1).

Covers:
- list_packages() new fields (tools, plugins, llm_presets)
- get_package_modules() lookup
- resolve_package_tool() scanning
- _validate_package() acceptance of extension-only packages
- get_all_presets() merging
- CLI extension commands
- CLI mcp commands
"""

import pytest
import yaml

from kohakuterrarium.packages.install import install_package
from kohakuterrarium.packages.resolve import resolve_package_tool
from kohakuterrarium.packages.walk import get_package_modules, list_packages


@pytest.fixture
def tmp_packages(tmp_path, monkeypatch):
    """Use a temporary directory for packages."""
    import kohakuterrarium.packages.locations as pkg_mod

    monkeypatch.setattr(pkg_mod, "PACKAGES_DIR", tmp_path / "packages")
    (tmp_path / "packages").mkdir()
    return tmp_path / "packages"


@pytest.fixture
def extension_package(tmp_path):
    """Create a package with tools, plugins, and llm_presets but no creatures."""
    pkg = tmp_path / "ext-pack"
    pkg.mkdir()
    (pkg / "kohaku.yaml").write_text(
        yaml.dump(
            {
                "name": "ext-pack",
                "version": "0.1.0",
                "description": "Extension-only package",
                "tools": [
                    {
                        "name": "my_tool",
                        "module": "ext_pack.tools.my_tool",
                        "class_name": "MyTool",
                        "description": "A custom tool",
                    },
                    {
                        "name": "other_tool",
                        "module": "ext_pack.tools.other",
                        "class_name": "OtherTool",
                    },
                ],
                "plugins": [
                    {"name": "my_plugin", "module": "ext_pack.plugins.my_plugin"},
                ],
                "llm_presets": [
                    {
                        "name": "custom-model",
                        "provider": "openai",
                        "model": "custom/model-v1",
                        "base_url": "https://custom.api.com/v1",
                        "api_key_env": "CUSTOM_API_KEY",
                        "max_context": 100000,
                    },
                ],
            }
        )
    )
    return pkg


@pytest.fixture
def mixed_package(tmp_path):
    """Create a package with both creatures and extension modules."""
    pkg = tmp_path / "mixed-pack"
    pkg.mkdir()
    (pkg / "creatures").mkdir()
    (pkg / "creatures" / "agent-a").mkdir()
    (pkg / "creatures" / "agent-a" / "config.yaml").write_text(
        yaml.dump({"name": "agent-a"})
    )
    (pkg / "kohaku.yaml").write_text(
        yaml.dump(
            {
                "name": "mixed-pack",
                "version": "1.0.0",
                "creatures": [{"name": "agent-a", "path": "creatures/agent-a"}],
                "tools": [
                    {
                        "name": "mixed_tool",
                        "module": "mixed_pack.tools",
                        "class_name": "MixedTool",
                    }
                ],
                "llm_presets": [
                    {
                        "name": "mixed-model",
                        "provider": "openai",
                        "model": "mixed/model",
                        "max_context": 50000,
                    }
                ],
            }
        )
    )
    return pkg


# ---------------------------------------------------------------------------
# list_packages() new fields
# ---------------------------------------------------------------------------


class TestListPackagesExtensions:
    def test_extension_fields_present(self, tmp_packages, extension_package):
        install_package(str(extension_package))
        pkgs = list_packages()
        assert len(pkgs) == 1
        pkg = pkgs[0]
        assert "tools" in pkg
        assert "plugins" in pkg
        assert "llm_presets" in pkg
        assert len(pkg["tools"]) == 2
        assert len(pkg["plugins"]) == 1
        assert len(pkg["llm_presets"]) == 1

    def test_extension_fields_default_empty(self, tmp_packages, tmp_path):
        """A package without extension entries should have empty lists."""
        pkg = tmp_path / "bare-pack"
        pkg.mkdir()
        (pkg / "creatures").mkdir()
        (pkg / "kohaku.yaml").write_text(
            yaml.dump({"name": "bare-pack", "version": "1.0"})
        )
        install_package(str(pkg))
        pkgs = list_packages()
        assert len(pkgs) == 1
        assert pkgs[0]["tools"] == []
        assert pkgs[0]["plugins"] == []
        assert pkgs[0]["llm_presets"] == []

    def test_mixed_package_has_all_fields(self, tmp_packages, mixed_package):
        install_package(str(mixed_package))
        pkgs = list_packages()
        assert len(pkgs) == 1
        pkg = pkgs[0]
        assert len(pkg["creatures"]) == 1
        assert len(pkg["tools"]) == 1
        assert len(pkg["llm_presets"]) == 1


# ---------------------------------------------------------------------------
# get_package_modules()
# ---------------------------------------------------------------------------


class TestGetPackageModules:
    def test_get_tools(self, tmp_packages, extension_package):
        install_package(str(extension_package))
        tools = get_package_modules("ext-pack", "tools")
        assert len(tools) == 2
        assert tools[0]["name"] == "my_tool"

    def test_get_plugins(self, tmp_packages, extension_package):
        install_package(str(extension_package))
        plugins = get_package_modules("ext-pack", "plugins")
        assert len(plugins) == 1
        assert plugins[0]["name"] == "my_plugin"

    def test_get_llm_presets(self, tmp_packages, extension_package):
        install_package(str(extension_package))
        presets = get_package_modules("ext-pack", "llm_presets")
        assert len(presets) == 1
        assert presets[0]["name"] == "custom-model"

    def test_get_creatures(self, tmp_packages, mixed_package):
        install_package(str(mixed_package))
        creatures = get_package_modules("mixed-pack", "creatures")
        assert len(creatures) == 1

    def test_nonexistent_package(self, tmp_packages):
        result = get_package_modules("no-such-pkg", "tools")
        assert result == []

    def test_nonexistent_module_type(self, tmp_packages, extension_package):
        install_package(str(extension_package))
        result = get_package_modules("ext-pack", "nonexistent_type")
        assert result == []


# ---------------------------------------------------------------------------
# resolve_package_tool()
# ---------------------------------------------------------------------------


class TestResolvePackageTool:
    def test_resolve_found(self, tmp_packages, extension_package):
        install_package(str(extension_package))
        result = resolve_package_tool("my_tool")
        assert result is not None
        module_path, class_name = result
        assert module_path == "ext_pack.tools.my_tool"
        assert class_name == "MyTool"

    def test_resolve_second_tool(self, tmp_packages, extension_package):
        install_package(str(extension_package))
        result = resolve_package_tool("other_tool")
        assert result is not None
        assert result == ("ext_pack.tools.other", "OtherTool")

    def test_resolve_not_found(self, tmp_packages, extension_package):
        install_package(str(extension_package))
        result = resolve_package_tool("nonexistent_tool")
        assert result is None

    def test_resolve_no_packages(self, tmp_packages):
        result = resolve_package_tool("anything")
        assert result is None


# ---------------------------------------------------------------------------
# _validate_package() accepts extension-only packages
# ---------------------------------------------------------------------------


class TestValidatePackageExtensions:
    def test_extension_only_accepted(self, tmp_packages, extension_package):
        """An extension-only package should install without error."""
        # Should not raise -- extension-only packages are valid
        name = install_package(str(extension_package))
        assert name == "ext-pack"

    def test_validate_checks_manifest(self, tmp_path):
        """_validate_package checks manifest for extension entries."""
        from kohakuterrarium.packages.manifest import _validate_package

        # Package with tools in manifest but no creatures/ dir
        pkg = tmp_path / "tool-only"
        pkg.mkdir()
        (pkg / "kohaku.yaml").write_text(
            yaml.dump({"name": "tool-only", "tools": [{"name": "t1"}]})
        )
        # Should not raise (tools count as valid content)
        _validate_package(pkg, "tool-only")

    def test_validate_empty_package(self, tmp_path):
        """_validate_package still runs for truly empty packages."""
        from kohakuterrarium.packages.manifest import _validate_package

        pkg = tmp_path / "empty"
        pkg.mkdir()
        (pkg / "kohaku.yaml").write_text(yaml.dump({"name": "empty"}))
        # Should not raise (just logs warning), but the function completes
        _validate_package(pkg, "empty")


# ---------------------------------------------------------------------------
# get_all_presets()
# ---------------------------------------------------------------------------


class TestGetAllPresets:
    def test_includes_builtins(self):
        # Reset cache for clean test
        import kohakuterrarium.llm.presets as presets_mod
        from kohakuterrarium.llm.preset_aliases import _CANONICAL_NAMES
        from kohakuterrarium.llm.presets import PRESETS, get_all_presets

        presets_mod._all_presets_cache = None
        presets_mod._package_presets_merged = False

        result = get_all_presets()
        # All builtin presets should be present under their (provider, canonical_name) key.
        for legacy_name, data in PRESETS.items():
            provider = data.get("provider")
            if not provider:
                continue
            canonical = _CANONICAL_NAMES.get(legacy_name, legacy_name)
            assert (provider, canonical) in result

    def test_caching(self):
        import kohakuterrarium.llm.presets as presets_mod
        from kohakuterrarium.llm.presets import get_all_presets

        presets_mod._all_presets_cache = None
        presets_mod._package_presets_merged = False

        result1 = get_all_presets()
        result2 = get_all_presets()
        assert result1 is result2  # Same object, cached

    def test_package_presets_merged(self, tmp_packages, extension_package):
        """Package presets should appear in get_all_presets() under their
        (provider, name) key."""
        import kohakuterrarium.llm.presets as presets_mod

        presets_mod._all_presets_cache = None
        presets_mod._package_presets_merged = False

        install_package(str(extension_package))
        result = presets_mod.get_all_presets()
        assert ("openai", "custom-model") in result
        assert result[("openai", "custom-model")]["model"] == "custom/model-v1"

        # Cleanup
        presets_mod._all_presets_cache = None
        presets_mod._package_presets_merged = False


# ---------------------------------------------------------------------------
# CLI extension commands
# ---------------------------------------------------------------------------


class TestExtensionCLI:
    def test_extension_list_empty(self, tmp_packages, capsys):
        from kohakuterrarium.cli.extension import extension_list_cli

        rc = extension_list_cli()
        assert rc == 0
        out = capsys.readouterr().out
        assert "No packages installed" in out

    def test_extension_list_with_packages(
        self, tmp_packages, extension_package, capsys
    ):
        from kohakuterrarium.cli.extension import extension_list_cli

        install_package(str(extension_package))
        rc = extension_list_cli()
        assert rc == 0
        out = capsys.readouterr().out
        assert "ext-pack" in out
        assert "my_tool" in out

    def test_extension_info_found(self, tmp_packages, extension_package, capsys):
        from kohakuterrarium.cli.extension import extension_info_cli

        install_package(str(extension_package))
        rc = extension_info_cli("ext-pack")
        assert rc == 0
        out = capsys.readouterr().out
        assert "ext-pack" in out
        assert "my_tool" in out

    def test_extension_info_not_found(self, tmp_packages, capsys):
        from kohakuterrarium.cli.extension import extension_info_cli

        rc = extension_info_cli("nonexistent")
        assert rc == 1
        out = capsys.readouterr().out
        assert "not found" in out.lower()


# ---------------------------------------------------------------------------
# CLI mcp commands
# ---------------------------------------------------------------------------


class TestMCPCLI:
    def test_mcp_list_no_servers(self, tmp_path, capsys):
        from kohakuterrarium.cli.identity_mcp import list_for_agent_cli as mcp_list_cli

        agent = tmp_path / "agent"
        agent.mkdir()
        (agent / "config.yaml").write_text(yaml.dump({"name": "test-agent"}))
        rc = mcp_list_cli(str(agent))
        assert rc == 0
        out = capsys.readouterr().out
        assert "No MCP servers" in out

    def test_mcp_list_with_servers(self, tmp_path, capsys):
        from kohakuterrarium.cli.identity_mcp import list_for_agent_cli as mcp_list_cli

        agent = tmp_path / "agent"
        agent.mkdir()
        (agent / "config.yaml").write_text(
            yaml.dump(
                {
                    "name": "test-agent",
                    "mcp_servers": [
                        {
                            "name": "filesystem",
                            "type": "stdio",
                            "command": "npx",
                            "args": ["-y", "@modelcontextprotocol/server-filesystem"],
                        },
                        {
                            "name": "web",
                            "type": "sse",
                            "url": "http://localhost:3000/sse",
                        },
                    ],
                }
            )
        )
        rc = mcp_list_cli(str(agent))
        assert rc == 0
        out = capsys.readouterr().out
        assert "filesystem" in out
        assert "web" in out
        assert "stdio" in out
        assert "sse" in out

    def test_mcp_list_missing_path(self, capsys):
        from kohakuterrarium.cli.identity_mcp import list_for_agent_cli as mcp_list_cli

        rc = mcp_list_cli("/nonexistent/path")
        assert rc == 1
        out = capsys.readouterr().out
        assert "not found" in out.lower()

    def test_mcp_list_no_config(self, tmp_path, capsys):
        from kohakuterrarium.cli.identity_mcp import list_for_agent_cli as mcp_list_cli

        agent = tmp_path / "agent"
        agent.mkdir()
        rc = mcp_list_cli(str(agent))
        assert rc == 1
        out = capsys.readouterr().out
        assert "No config" in out
