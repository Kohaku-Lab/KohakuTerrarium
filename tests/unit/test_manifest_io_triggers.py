"""Tests for manifest-driven io/trigger wiring (hygiene H.3 / audit #17).

Covers:
- resolve_package_io() / resolve_package_trigger() manifest scanning
- bootstrap/io.py loads a packaged input/output by bare name
- bootstrap/triggers.py loads a packaged trigger by bare name
- Collisions between two packages raise ValueError at lookup time
"""

import sys
import textwrap

import pytest
import yaml

from kohakuterrarium.bootstrap.io import create_input, create_output
from kohakuterrarium.bootstrap.triggers import create_trigger
from kohakuterrarium.core.config import (
    AgentConfig,
    InputConfig,
    OutputConfig,
    TriggerConfig,
)
from kohakuterrarium.core.loader import ModuleLoader
from kohakuterrarium.packages import (
    install_package,
    resolve_package_io,
    resolve_package_trigger,
)

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_packages(tmp_path, monkeypatch):
    """Redirect the package install root to a throwaway directory."""
    import kohakuterrarium.packages as pkg_mod

    monkeypatch.setattr(pkg_mod, "PACKAGES_DIR", tmp_path / "packages")
    (tmp_path / "packages").mkdir()
    return tmp_path / "packages"


def _make_io_package(
    root,
    *,
    pkg_name: str,
    import_name: str,
    input_name: str = "fake_input",
    output_name: str = "fake_output",
):
    """Create a package directory containing a Python module with
    ``FakeInput`` / ``FakeOutput`` classes and an ``io:`` manifest entry.
    """
    pkg = root / pkg_name
    (pkg / import_name).mkdir(parents=True)
    (pkg / import_name / "__init__.py").write_text("")
    (pkg / import_name / "io_mod.py").write_text(textwrap.dedent("""
            class FakeInput:
                def __init__(self, **options):
                    self.options = options

                async def start(self):
                    pass

                async def stop(self):
                    pass

                async def get_input(self):
                    return None

            class FakeOutput:
                def __init__(self, **options):
                    self.options = options

                async def start(self):
                    pass

                async def stop(self):
                    pass

                async def write(self, text, target=None):
                    pass
            """))
    manifest = {
        "name": pkg_name,
        "version": "0.1.0",
        "io": [
            {
                "name": input_name,
                "module": f"{import_name}.io_mod",
                "class": "FakeInput",
                "description": "Packaged input",
            },
            {
                "name": output_name,
                "module": f"{import_name}.io_mod",
                "class": "FakeOutput",
                "description": "Packaged output",
            },
        ],
    }
    (pkg / "kohaku.yaml").write_text(yaml.dump(manifest))
    return pkg


def _make_trigger_package(
    root,
    *,
    pkg_name: str,
    import_name: str,
    trigger_name: str = "fake_trigger",
):
    """Create a package directory with a ``FakeTrigger`` class and
    a ``triggers:`` manifest entry.
    """
    pkg = root / pkg_name
    (pkg / import_name).mkdir(parents=True)
    (pkg / import_name / "__init__.py").write_text("")
    (pkg / import_name / "trig_mod.py").write_text(textwrap.dedent("""
            from kohakuterrarium.modules.trigger.base import BaseTrigger


            class FakeTrigger(BaseTrigger):
                def __init__(self, **options):
                    super().__init__()
                    self.options = options

                async def wait_for_trigger(self):
                    return None
            """))
    manifest = {
        "name": pkg_name,
        "version": "0.1.0",
        "triggers": [
            {
                "name": trigger_name,
                "module": f"{import_name}.trig_mod",
                "class": "FakeTrigger",
                "description": "Packaged trigger",
            },
        ],
    }
    (pkg / "kohaku.yaml").write_text(yaml.dump(manifest))
    return pkg


@pytest.fixture
def io_package(tmp_path):
    return _make_io_package(tmp_path, pkg_name="io-pkg", import_name="kt_test_io_pkg")


@pytest.fixture
def trigger_package(tmp_path):
    return _make_trigger_package(
        tmp_path, pkg_name="trigger-pkg", import_name="kt_test_trigger_pkg"
    )


@pytest.fixture(autouse=True)
def _scrub_sys_modules():
    """Drop the throwaway test packages from sys.modules between tests so
    each install is loaded from the current tmp_path rather than a cached
    earlier copy."""
    before = set(sys.modules)
    yield
    for name in list(sys.modules):
        if name.startswith(("kt_test_io_pkg", "kt_test_trigger_pkg", "kt_test_dup")):
            sys.modules.pop(name, None)
    # Also remove any sys.path entries that point at test tmp dirs
    new = set(sys.modules) - before
    for name in new:
        if name.startswith(("kt_test_io_pkg", "kt_test_trigger_pkg", "kt_test_dup")):
            sys.modules.pop(name, None)


# ---------------------------------------------------------------------------
# resolve_package_io / resolve_package_trigger
# ---------------------------------------------------------------------------


class TestResolveManifest:
    def test_resolve_io_found(self, tmp_packages, io_package):
        install_package(str(io_package))
        assert resolve_package_io("fake_input") == (
            "kt_test_io_pkg.io_mod",
            "FakeInput",
        )
        assert resolve_package_io("fake_output") == (
            "kt_test_io_pkg.io_mod",
            "FakeOutput",
        )

    def test_resolve_io_missing(self, tmp_packages, io_package):
        install_package(str(io_package))
        assert resolve_package_io("no_such_input") is None

    def test_resolve_io_no_packages(self, tmp_packages):
        assert resolve_package_io("anything") is None

    def test_resolve_trigger_found(self, tmp_packages, trigger_package):
        install_package(str(trigger_package))
        assert resolve_package_trigger("fake_trigger") == (
            "kt_test_trigger_pkg.trig_mod",
            "FakeTrigger",
        )

    def test_resolve_trigger_missing(self, tmp_packages, trigger_package):
        install_package(str(trigger_package))
        assert resolve_package_trigger("no_such_trigger") is None

    def test_resolve_trigger_no_packages(self, tmp_packages):
        assert resolve_package_trigger("anything") is None


# ---------------------------------------------------------------------------
# Collision policy — two packages declaring the same name → ValueError
# ---------------------------------------------------------------------------


class TestCollisionPolicy:
    def test_io_collision_raises(self, tmp_path, tmp_packages):
        pkg_a = _make_io_package(
            tmp_path,
            pkg_name="dup-a",
            import_name="kt_test_dup_a",
            input_name="shared_in",
            output_name="unique_out_a",
        )
        pkg_b = _make_io_package(
            tmp_path,
            pkg_name="dup-b",
            import_name="kt_test_dup_b",
            input_name="shared_in",
            output_name="unique_out_b",
        )
        install_package(str(pkg_a))
        install_package(str(pkg_b))

        with pytest.raises(ValueError) as exc:
            resolve_package_io("shared_in")
        msg = str(exc.value)
        assert "dup-a" in msg
        assert "dup-b" in msg
        assert "shared_in" in msg

    def test_trigger_collision_raises(self, tmp_path, tmp_packages):
        pkg_a = _make_trigger_package(
            tmp_path,
            pkg_name="dup-trig-a",
            import_name="kt_test_dup_trig_a",
            trigger_name="shared_trig",
        )
        pkg_b = _make_trigger_package(
            tmp_path,
            pkg_name="dup-trig-b",
            import_name="kt_test_dup_trig_b",
            trigger_name="shared_trig",
        )
        install_package(str(pkg_a))
        install_package(str(pkg_b))

        with pytest.raises(ValueError) as exc:
            resolve_package_trigger("shared_trig")
        msg = str(exc.value)
        assert "dup-trig-a" in msg
        assert "dup-trig-b" in msg
        assert "shared_trig" in msg


# ---------------------------------------------------------------------------
# Bootstrap integration — bare-name loading via manifest lookup
# ---------------------------------------------------------------------------


class TestBootstrapManifestLookup:
    def test_create_input_uses_package_manifest(self, tmp_packages, io_package):
        install_package(str(io_package))
        from kohakuterrarium.packages import ensure_package_importable

        ensure_package_importable("io-pkg")

        config = AgentConfig(
            name="t",
            input=InputConfig(type="fake_input", options={"channel": 42}),
        )
        loader = ModuleLoader()
        result = create_input(config, input_override=None, loader=loader)
        # Should be an instance of the packaged FakeInput, not the CLI fallback.
        assert type(result).__name__ == "FakeInput"
        assert result.options == {"channel": 42}

    def test_create_output_uses_package_manifest(self, tmp_packages, io_package):
        install_package(str(io_package))
        from kohakuterrarium.packages import ensure_package_importable

        ensure_package_importable("io-pkg")

        config = AgentConfig(
            name="t",
            output=OutputConfig(type="fake_output", options={"room": "general"}),
        )
        loader = ModuleLoader()
        default_output, _ = create_output(config, output_override=None, loader=loader)
        assert type(default_output).__name__ == "FakeOutput"
        assert default_output.options == {"room": "general"}

    def test_create_trigger_uses_package_manifest(self, tmp_packages, trigger_package):
        install_package(str(trigger_package))
        from kohakuterrarium.packages import ensure_package_importable

        ensure_package_importable("trigger-pkg")

        tc = TriggerConfig(type="fake_trigger", prompt="hi", options={"x": 1})
        loader = ModuleLoader()
        result = create_trigger(tc, session=None, loader=loader)
        assert result is not None
        assert type(result).__name__ == "FakeTrigger"
        assert result.options == {"prompt": "hi", "x": 1}

    def test_create_trigger_unknown_still_returns_none(self, tmp_packages):
        """Unknown trigger with no matching manifest entry still returns None."""
        tc = TriggerConfig(type="never_declared", prompt=None, options={})
        result = create_trigger(tc, session=None, loader=ModuleLoader())
        assert result is None

    def test_create_input_backward_compat_custom(self, tmp_path):
        """Explicit `custom` configs still bypass manifest lookup and go
        through the legacy module/class path."""
        # No packages installed at all — ensure the classic custom path is
        # untouched: missing module field falls back to CLIInput.
        from kohakuterrarium.builtins.inputs import CLIInput

        config = AgentConfig(
            name="t",
            input=InputConfig(type="custom", module=None, class_name=None),
        )
        result = create_input(config, input_override=None, loader=ModuleLoader())
        assert isinstance(result, CLIInput)
