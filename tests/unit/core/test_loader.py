"""Unit tests for :mod:`kohakuterrarium.core.loader`."""

import sys
import textwrap

import pytest

from kohakuterrarium.core.loader import (
    ModuleLoadError,
    ModuleLoader,
    load_custom_module,
)

# ── fixtures ──────────────────────────────────────────────────────


@pytest.fixture
def agent_dir(tmp_path):
    custom = tmp_path / "custom"
    custom.mkdir()
    return tmp_path


def _write_module(agent_dir, rel_path: str, src: str) -> None:
    full = agent_dir / rel_path
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(textwrap.dedent(src), encoding="utf-8")


# ── load_class — custom (file) ────────────────────────────────────


class TestLoadCustomFile:
    def test_loads_class_from_file(self, agent_dir):
        _write_module(
            agent_dir,
            "custom/my_tool.py",
            """
            class MyTool:
                NAME = "my_tool"
            """,
        )
        loader = ModuleLoader(agent_path=agent_dir)
        cls = loader.load_class("custom/my_tool.py", "MyTool", module_type="custom")
        assert cls.__name__ == "MyTool"
        assert cls.NAME == "my_tool"

    def test_class_missing_raises(self, agent_dir):
        _write_module(agent_dir, "custom/x.py", "")
        loader = ModuleLoader(agent_path=agent_dir)
        with pytest.raises(ModuleLoadError, match="'Missing' not found"):
            loader.load_class("custom/x.py", "Missing", module_type="custom")

    def test_module_file_missing_raises(self, agent_dir):
        loader = ModuleLoader(agent_path=agent_dir)
        with pytest.raises(ModuleLoadError, match="Module file not found"):
            loader.load_class("custom/nope.py", "X", module_type="custom")

    def test_non_python_file_rejected(self, agent_dir):
        (agent_dir / "custom" / "thing.txt").write_text("nope")
        loader = ModuleLoader(agent_path=agent_dir)
        with pytest.raises(ModuleLoadError, match="must be a Python file"):
            loader.load_class("custom/thing.txt", "X", module_type="custom")

    def test_custom_without_agent_path_raises(self):
        loader = ModuleLoader(agent_path=None)
        with pytest.raises(ModuleLoadError, match="agent_path required"):
            loader.load_class("custom/x.py", "X", module_type="custom")

    def test_module_exec_failure_wrapped(self, agent_dir):
        _write_module(agent_dir, "custom/bad.py", "raise RuntimeError('boom')")
        loader = ModuleLoader(agent_path=agent_dir)
        with pytest.raises(ModuleLoadError, match="Failed to load"):
            loader.load_class("custom/bad.py", "X", module_type="custom")


# ── load_class — package ──────────────────────────────────────────


class TestLoadFromPackage:
    def test_loads_class(self):
        loader = ModuleLoader()
        cls = loader.load_class("collections", "OrderedDict", module_type="package")
        assert cls.__name__ == "OrderedDict"

    def test_package_missing_raises(self):
        loader = ModuleLoader()
        with pytest.raises(ModuleLoadError, match="Cannot import package"):
            loader.load_class("definitely_no_such_pkg_xyz", "X", module_type="package")

    def test_class_missing_raises(self):
        loader = ModuleLoader()
        with pytest.raises(ModuleLoadError, match="not found in package"):
            loader.load_class("collections", "DoesNotExist", module_type="package")


class TestLoadClassUnknownType:
    def test_unknown_module_type(self):
        loader = ModuleLoader()
        with pytest.raises(ModuleLoadError, match="Unknown module type"):
            loader.load_class("x", "Y", module_type="nope")


# ── load_instance ─────────────────────────────────────────────────


class TestLoadInstance:
    def test_instantiates_with_options(self, agent_dir):
        _write_module(
            agent_dir,
            "custom/widget.py",
            """
            class Widget:
                def __init__(self, *, name, count=1):
                    self.name = name
                    self.count = count
            """,
        )
        loader = ModuleLoader(agent_path=agent_dir)
        inst = loader.load_instance(
            "custom/widget.py",
            "Widget",
            module_type="custom",
            options={"name": "foo", "count": 3},
        )
        assert inst.name == "foo"
        assert inst.count == 3

    def test_options_default_empty(self, agent_dir):
        _write_module(
            agent_dir,
            "custom/widget2.py",
            """
            class W:
                def __init__(self): self.x = 0
            """,
        )
        loader = ModuleLoader(agent_path=agent_dir)
        inst = loader.load_instance("custom/widget2.py", "W", module_type="custom")
        assert inst.x == 0


# ── load_config_object ────────────────────────────────────────────


class TestLoadConfigObject:
    def test_from_custom_file(self, agent_dir):
        _write_module(
            agent_dir,
            "custom/cfg.py",
            """
            CONFIG = {"value": 42}
            """,
        )
        loader = ModuleLoader(agent_path=agent_dir)
        obj = loader.load_config_object("custom/cfg.py", "CONFIG", module_type="custom")
        assert obj == {"value": 42}

    def test_from_package(self):
        loader = ModuleLoader()
        # ``collections.OrderedDict`` is technically a class but
        # ``load_config_object`` just resolves any attr — works.
        obj = loader.load_config_object(
            "collections", "OrderedDict", module_type="package"
        )
        assert obj.__name__ == "OrderedDict"

    def test_attr_missing_raises(self, agent_dir):
        _write_module(agent_dir, "custom/cfg2.py", "x = 1")
        loader = ModuleLoader(agent_path=agent_dir)
        with pytest.raises(ModuleLoadError, match="'NOPE' not found"):
            loader.load_config_object("custom/cfg2.py", "NOPE", module_type="custom")

    def test_unknown_module_type(self):
        loader = ModuleLoader()
        with pytest.raises(ModuleLoadError, match="Unknown module type"):
            loader.load_config_object("x", "Y", module_type="??")

    def test_attr_load_exception_wrapped(self, agent_dir):
        _write_module(agent_dir, "custom/cfg3.py", "raise ValueError('nope')")
        loader = ModuleLoader(agent_path=agent_dir)
        with pytest.raises(ModuleLoadError, match="Failed to load"):
            loader.load_config_object("custom/cfg3.py", "ANY", module_type="custom")


# ── caching ───────────────────────────────────────────────────────


class TestCaching:
    def test_same_file_returns_same_module(self, agent_dir):
        _write_module(
            agent_dir,
            "custom/c.py",
            """
            counter = 0
            class Tool:
                pass
            """,
        )
        loader = ModuleLoader(agent_path=agent_dir)
        a = loader.load_class("custom/c.py", "Tool", module_type="custom")
        b = loader.load_class("custom/c.py", "Tool", module_type="custom")
        # Cached module → same class object.
        assert a is b

    def test_unique_module_name_per_load(self, agent_dir):
        _write_module(agent_dir, "custom/x.py", "class A: pass")
        _write_module(agent_dir, "custom/y.py", "class B: pass")
        loader = ModuleLoader(agent_path=agent_dir)
        loader.load_class("custom/x.py", "A", module_type="custom")
        loader.load_class("custom/y.py", "B", module_type="custom")
        # Two distinct registrations under unique synthetic names.
        synthetic = [k for k in sys.modules if k.startswith("kohaku_custom_")]
        assert any(k.endswith("_x") for k in synthetic)
        assert any(k.endswith("_y") for k in synthetic)

    def test_clear_cache_forces_reload(self, agent_dir):
        _write_module(agent_dir, "custom/c.py", "class T: pass")
        loader = ModuleLoader(agent_path=agent_dir)
        a = loader.load_class("custom/c.py", "T", module_type="custom")
        loader.clear_cache()
        b = loader.load_class("custom/c.py", "T", module_type="custom")
        # Fresh exec_module produces a distinct class object.
        assert a is not b


# ── sys.path management ───────────────────────────────────────────


class TestSysPath:
    def test_relative_import_works_then_path_cleaned(self, agent_dir):
        _write_module(
            agent_dir,
            "custom/sibling.py",
            "VALUE = 99",
        )
        _write_module(
            agent_dir,
            "custom/main.py",
            """
            from sibling import VALUE
            class T:
                v = VALUE
            """,
        )
        before = list(sys.path)
        loader = ModuleLoader(agent_path=agent_dir)
        cls = loader.load_class("custom/main.py", "T", module_type="custom")
        assert cls.v == 99
        # sys.path was modified only transiently — restored on return.
        assert sys.path == before

    def test_existing_path_entry_not_duplicated(self, agent_dir):
        custom_path = str(agent_dir / "custom")
        sys.path.insert(0, custom_path)
        try:
            _write_module(agent_dir, "custom/c.py", "class T: pass")
            loader = ModuleLoader(agent_path=agent_dir)
            loader.load_class("custom/c.py", "T", module_type="custom")
            # No double-removal: still present once.
            assert sys.path.count(custom_path) == 1
        finally:
            sys.path.remove(custom_path)


# ── load_custom_module convenience ────────────────────────────────


class TestLoadCustomModuleFn:
    def test_one_shot_load(self, agent_dir):
        _write_module(
            agent_dir,
            "custom/oneshot.py",
            """
            class T:
                def __init__(self, *, name): self.name = name
            """,
        )
        inst = load_custom_module(
            agent_dir,
            "custom/oneshot.py",
            "T",
            module_type="custom",
            options={"name": "x"},
        )
        assert inst.name == "x"


class TestSpecFailure:
    def test_spec_from_file_location_returns_none(self, agent_dir, monkeypatch):
        """When ``spec_from_file_location`` returns None, the loader
        raises a ModuleLoadError (line 224)."""
        _write_module(agent_dir, "custom/x.py", "class T: pass")
        loader = ModuleLoader(agent_path=agent_dir)
        import importlib.util

        monkeypatch.setattr(
            importlib.util, "spec_from_file_location", lambda *a, **kw: None
        )
        with pytest.raises(ModuleLoadError, match="Cannot create module spec"):
            loader.load_class("custom/x.py", "T", module_type="custom")
