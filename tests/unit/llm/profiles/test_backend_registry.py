"""Unit tests for ``llm/profiles/backend_registry.py`` — package-declared backend types."""

import pytest

from kohakuterrarium.errors import ConfigError
from kohakuterrarium.llm.profiles.backend_registry import (
    PackageBackendDescriptor,
    PackageBackendError,
    backend_doctor,
    load_backend_class,
    package_backend,
    package_backends,
)
from kohakuterrarium.llm.profiles.types import LLMBackend

from tests.unit._fakes.package_backend import (
    echo_backend,
    install_backend_package,
    kt_home,  # noqa: F401
)


class TestDescriptorFromManifest:
    def test_valid_entry_keeps_every_field(self):
        d = PackageBackendDescriptor.from_manifest(
            "pkg",
            {
                "name": "echo",
                "module": "m",
                "class": "C",
                "display_name": "Echo",
                "description": "d",
                "credentials": "external",
                "provider_native_tools": ["img"],
            },
        )
        assert (d.name, d.module, d.class_name, d.package) == ("echo", "m", "C", "pkg")
        assert d.display_name == "Echo" and d.credentials == "external"
        assert d.provider_native_tools == ("img",)
        assert d.manages_own_credentials is True

    def test_class_name_alias_and_default_credentials(self):
        d = PackageBackendDescriptor.from_manifest(
            "pkg", {"name": "echo", "module": "m", "class_name": "C"}
        )
        assert d.class_name == "C"
        assert d.credentials == "api_key"
        assert d.manages_own_credentials is False

    @pytest.mark.parametrize(
        "entry, needle",
        [
            ({"module": "m", "class": "C"}, "'name'"),
            ({"name": "e", "class": "C"}, "'module'"),
            ({"name": "e", "module": "m"}, "'class'"),
            ({"name": "e", "module": "m", "class": "C", "credentials": "x"}, "x"),
            (
                {
                    "name": "e",
                    "module": "m",
                    "class": "C",
                    "provider_native_tools": "t",
                },
                "provider_native_tools",
            ),
        ],
    )
    def test_bad_entry_raises_config_error_naming_package(self, entry, needle):
        with pytest.raises(ConfigError) as exc:
            PackageBackendDescriptor.from_manifest("bad-pkg", entry)
        assert "bad-pkg" in str(exc.value) and needle in str(exc.value)

    def test_to_backend_is_a_self_named_provider_record(self):
        d = PackageBackendDescriptor.from_manifest(
            "pkg",
            {
                "name": "echo",
                "module": "m",
                "class": "C",
                "provider_native_tools": ["t"],
            },
        )
        assert d.to_backend() == LLMBackend(
            name="echo",
            backend_type="echo",
            provider_name="echo",
            provider_native_tools=["t"],
        )


class TestPackageBackends:
    def test_no_packages_means_no_backends(self, kt_home):
        assert package_backends() == {}
        assert package_backend("echo") is None

    def test_scan_does_not_import_the_module(self, kt_home):
        install_backend_package(
            kt_home, "boom", [echo_backend()], source="raise RuntimeError('imported')\n"
        )
        assert package_backend("echo").package == "boom"
        with pytest.raises(PackageBackendError) as exc:
            load_backend_class("echo")
        assert "boom" in str(exc.value) and "imported" in str(exc.value)

    def test_builtin_type_name_is_skipped(self, kt_home):
        install_backend_package(kt_home, "p", [echo_backend("openai"), echo_backend()])
        assert set(package_backends()) == {"echo"}
        assert package_backend("openai") is None

    def test_malformed_entry_skipped_others_kept(self, kt_home):
        install_backend_package(
            kt_home, "p", [{"name": "broken", "module": "m"}, echo_backend()]
        )
        assert set(package_backends()) == {"echo"}

    def test_collision_blocks_only_the_clashing_name(self, kt_home):
        install_backend_package(kt_home, "a", [echo_backend()])
        install_backend_package(kt_home, "b", [echo_backend(), echo_backend("other")])
        assert set(package_backends()) == {"other"}
        assert package_backend("other").package == "b"
        with pytest.raises(ConfigError) as exc:
            package_backend("echo")
        assert "a" in str(exc.value) and "b" in str(exc.value)


class TestLoadBackendClass:
    def test_returns_the_provider_class(self, kt_home):
        install_backend_package(kt_home, "p", [echo_backend()])
        cls = load_backend_class("echo")
        assert cls.__name__ == "EchoProvider"
        assert callable(cls.from_profile)

    def test_unknown_backend(self, kt_home):
        with pytest.raises(PackageBackendError, match="No installed package"):
            load_backend_class("ghost")

    def test_missing_class(self, kt_home):
        install_backend_package(
            kt_home, "p", [{"name": "x", "module": "{module}", "class": "Nope"}]
        )
        with pytest.raises(PackageBackendError, match="Class not found"):
            load_backend_class("x")

    def test_class_without_from_profile(self, kt_home):
        install_backend_package(
            kt_home, "p", [{"name": "x", "module": "{module}", "class": "NoFactory"}]
        )
        with pytest.raises(PackageBackendError, match="from_profile"):
            load_backend_class("x")


class TestBackendDoctor:
    def test_returns_the_status_line(self, kt_home):
        install_backend_package(kt_home, "p", [echo_backend()])
        assert backend_doctor("echo") == "echo ready"

    def test_class_without_doctor_returns_none(self, kt_home):
        source = (
            "class Plain:\n"
            "    @classmethod\n"
            "    def from_profile(cls, profile):\n"
            "        return cls()\n"
        )
        install_backend_package(
            kt_home,
            "p",
            [{"name": "x", "module": "{module}", "class": "Plain"}],
            source=source,
        )
        assert backend_doctor("x") is None

    def test_failing_check_propagates(self, kt_home):
        source = (
            "class Bad:\n"
            "    @classmethod\n"
            "    def from_profile(cls, profile):\n"
            "        return cls()\n"
            "    @classmethod\n"
            "    def doctor(cls):\n"
            "        raise RuntimeError('not logged in')\n"
        )
        install_backend_package(
            kt_home,
            "p",
            [{"name": "x", "module": "{module}", "class": "Bad"}],
            source=source,
        )
        with pytest.raises(RuntimeError, match="not logged in"):
            backend_doctor("x")
