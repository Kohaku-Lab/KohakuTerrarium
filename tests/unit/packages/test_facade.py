"""Unit tests for the :mod:`kohakuterrarium.packages` lazy façade (E10).

``docs/reference/python.md`` promises these symbols are importable from
``kohakuterrarium.packages``; the ``__init__`` used to be intentionally
empty so every documented import failed. The façade resolves lazily
(PEP 562) so the config-loader import chain stays light.
"""

from pathlib import Path

import pytest

import kohakuterrarium.packages as pkgs


class TestFacadeExports:
    def test_every_declared_export_resolves(self):
        for name in pkgs.__all__:
            value = getattr(pkgs, name)
            assert value is not None, name

    def test_exports_are_the_real_submodule_objects(self):
        from kohakuterrarium.packages.install import ensure
        from kohakuterrarium.packages.resolve import resolve_package_path

        assert pkgs.ensure is ensure
        assert pkgs.resolve_package_path is resolve_package_path

    def test_errors_reexported(self):
        from kohakuterrarium import errors

        assert pkgs.PackageError is errors.PackageError
        assert pkgs.PackageNotInstalledError is errors.PackageNotInstalledError

    def test_unknown_attribute_raises_attribute_error(self):
        with pytest.raises(AttributeError, match="no attribute 'nonexistent'"):
            pkgs.nonexistent

    def test_dir_lists_facade_names(self):
        listing = dir(pkgs)
        assert "ensure" in listing
        assert "resolve_package_path" in listing
        assert "list_packages" in listing

    def test_headline_docs_symbols_present(self):
        # The exact list the reference doc promises — pinned so a
        # rename in a submodule can't silently break the public name.
        for name in (
            "is_package_ref",
            "resolve_package_path",
            "resolve_any_path",
            "list_packages",
            "install_package",
            "install_package_spec",
            "update_package",
            "uninstall_package",
            "ensure",
            "packages_dir",
            "resolve_package_tool",
            "resolve_package_io",
            "resolve_package_trigger",
            "resolve_package_command",
            "resolve_package_user_command",
            "resolve_package_prompt",
            "resolve_package_skills",
            "find_package_root_for_path",
            "get_package_framework_hints",
        ):
            assert name in pkgs.__all__, name


class TestFacadeLaziness:
    def test_facade_module_itself_imports_nothing_heavy(self):
        # The whole point of PEP 562: executing ``packages/__init__``
        # must not import any submodule — internal importers
        # (``core.config`` → ``packages.resolve``) keep their exact dep
        # edges, and the installer / marketplace stack only loads when a
        # façade name is actually touched. Pin it at the AST level: the
        # only module-level import is ``importlib``.
        import ast

        source = Path(pkgs.__file__).read_text(encoding="utf-8")
        module_level_imports = [
            node
            for node in ast.parse(source).body
            if isinstance(node, (ast.Import, ast.ImportFrom))
        ]
        names = [alias.name for node in module_level_imports for alias in node.names]
        assert names == ["importlib"], names

    def test_access_caches_into_module_globals(self):
        before = pkgs.__dict__.get("list_packages")
        value = pkgs.list_packages
        # Second access comes from globals(), not __getattr__.
        assert pkgs.__dict__["list_packages"] is value
        assert before is None or before is value
