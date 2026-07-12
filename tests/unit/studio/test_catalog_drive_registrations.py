"""Unit tests for :mod:`kohakuterrarium.studio.catalog.drive_registrations`.

Behavior asserts on the catalog aggregator: builtin (``generic`` + ``goal``) +
installed descriptor discovery (no implementation import at scan time),
duplicate-name hard error, duplicate-kind surfaced as a catalog conflict, and
explicit validate resolving the import target with a load status. Installed
package registrations use a neutral ``custom`` name so they never collide with
the builtin ``goal``.
"""

import pytest

from kohakuterrarium.studio.catalog import drive_registrations as dr
from kohakuterrarium.terrarium.drive.errors import DriveValidationError
from kohakuterrarium.terrarium.drive.registration import DriveRegistrationDescriptor


class FakeCustomRegistration:
    """A resolvable package registration used to exercise validate's import path."""

    name = "custom"
    kind = "custom"
    schema_version = 1

    def descriptor(self):
        return DriveRegistrationDescriptor(
            name="custom", kind="custom", schema_version=1
        )


class FakeConfigurableRegistration:
    """A resolvable package registration with an injectable option (R1-17)."""

    name = "cfg"
    kind = "cfg"
    schema_version = 1

    def __init__(self):
        self.mode = "default"

    def descriptor(self):
        return DriveRegistrationDescriptor(
            name="cfg",
            kind="cfg",
            schema_version=1,
            option_defaults={"mode": "default"},
            option_schema={"mode": {"type": "str", "choices": ["default", "fast"]}},
        )

    def configure(self, options):
        self.mode = options["mode"]


def _cfg_pkg():
    return _pkg(
        "kt-biome",
        [
            {
                "name": "cfg",
                "kind": "cfg",
                "module": __name__,
                "class": "FakeConfigurableRegistration",
                "option_defaults": {"mode": "default"},
                "option_schema": {
                    "mode": {"type": "str", "choices": ["default", "fast"]}
                },
            }
        ],
    )


def _patch(monkeypatch, packages):
    monkeypatch.setattr(dr, "list_packages", lambda: packages)


def _pkg(pkg_name, entries):
    return {"name": pkg_name, "drive_registrations": entries}


class TestBuiltinDescriptors:
    def test_generic_and_goal_are_builtin(self):
        descs = dr.builtin_descriptors()
        assert [d.name for d in descs] == ["generic", "goal"]
        assert all(d.source_package is None for d in descs)
        goal = next(d for d in descs if d.name == "goal")
        assert goal.kind == "goal"
        assert goal.verifier_mode == "extension"


class TestInstalledDescriptors:
    def test_scans_with_provenance_no_import(self, monkeypatch):
        _patch(
            monkeypatch,
            [
                _pkg(
                    "kt-biome",
                    [{"name": "custom", "kind": "custom", "module": "x", "class": "C"}],
                )
            ],
        )
        descs = dr.installed_descriptors()
        assert [d.name for d in descs] == ["custom"]
        assert descs[0].source_package == "kt-biome"
        assert descs[0].module == "x"  # recorded, but not imported

    def test_duplicate_name_across_packages_is_hard_error(self, monkeypatch):
        _patch(
            monkeypatch,
            [
                _pkg(
                    "a",
                    [{"name": "custom", "kind": "custom", "module": "m", "class": "C"}],
                ),
                _pkg(
                    "b",
                    [{"name": "custom", "kind": "custom", "module": "m", "class": "C"}],
                ),
            ],
        )
        with pytest.raises(
            DriveValidationError, match="duplicate drive registration name 'custom'"
        ):
            dr.installed_descriptors()

    def test_malformed_entry_skipped(self, monkeypatch):
        _patch(
            monkeypatch,
            [
                _pkg(
                    "p",
                    [
                        {"name": "bad"},
                        {"name": "ok", "kind": "ok", "module": "m", "class": "C"},
                    ],
                )
            ],
        )
        # 'bad' has no module/class -> logged + skipped; 'ok' survives.
        assert [d.name for d in dr.installed_descriptors()] == ["ok"]


class TestListDriveRegistrations:
    def test_builtins_when_no_packages(self, monkeypatch):
        _patch(monkeypatch, [])
        entries = dr.list_drive_registrations()
        assert [e["name"] for e in entries] == ["generic", "goal"]
        assert all(e["source"] == "builtin" for e in entries)
        assert all(e["conflict"] is False for e in entries)

    def test_aggregates_builtin_and_installed_sorted(self, monkeypatch):
        _patch(
            monkeypatch,
            [
                _pkg(
                    "kt-biome",
                    [{"name": "custom", "kind": "custom", "module": "m", "class": "C"}],
                )
            ],
        )
        entries = dr.list_drive_registrations()
        assert [e["name"] for e in entries] == ["custom", "generic", "goal"]
        custom = next(e for e in entries if e["name"] == "custom")
        assert custom["source"] == "package"
        assert custom["package"] == "kt-biome"

    def test_kind_conflict_is_flagged_not_raised(self, monkeypatch):
        # An installed registration claiming kind 'generic' collides with the
        # builtin generic kind -> both surfaced as conflicts, no exception.
        _patch(
            monkeypatch,
            [
                _pkg(
                    "shadow",
                    [{"name": "gen2", "kind": "generic", "module": "m", "class": "C"}],
                )
            ],
        )
        entries = dr.list_drive_registrations()
        by_name = {e["name"]: e for e in entries}
        assert by_name["generic"]["conflict"] is True
        assert by_name["gen2"]["conflict"] is True
        assert "shadow/gen2" in by_name["generic"]["conflict_reason"]

    def test_name_conflict_with_builtin_is_hard_error(self, monkeypatch):
        _patch(
            monkeypatch,
            [
                _pkg(
                    "evil",
                    [
                        {
                            "name": "goal",
                            "kind": "goal",
                            "module": "m",
                            "class": "C",
                        }
                    ],
                )
            ],
        )
        # A package cannot shadow the builtin ``goal`` name (design §8.2).
        with pytest.raises(
            DriveValidationError, match="duplicate drive registration name 'goal'"
        ):
            dr.list_drive_registrations()

    def test_get_by_name(self, monkeypatch):
        _patch(monkeypatch, [])
        assert dr.get_drive_registration("generic")["kind"] == "generic"
        assert dr.get_drive_registration("goal")["kind"] == "goal"
        assert dr.get_drive_registration("nope") is None


class TestValidate:
    def test_builtin_generic_validates_loaded(self, monkeypatch):
        _patch(monkeypatch, [])
        result = dr.validate_drive_registration("generic")
        assert result["loaded"] is True
        assert result["error"] is None

    def test_builtin_goal_validates_loaded(self, monkeypatch):
        _patch(monkeypatch, [])
        result = dr.validate_drive_registration("goal")
        assert result["loaded"] is True
        assert result["error"] is None
        assert result["kind"] == "goal"

    def test_installed_resolves_and_reports_kind(self, monkeypatch):
        _patch(
            monkeypatch,
            [
                _pkg(
                    "kt-biome",
                    [
                        {
                            "name": "custom",
                            "kind": "custom",
                            "module": __name__,
                            "class": "FakeCustomRegistration",
                        }
                    ],
                )
            ],
        )
        result = dr.validate_drive_registration("custom")
        assert result["loaded"] is True
        assert result["kind"] == "custom"

    def test_installed_load_error_reported(self, monkeypatch):
        _patch(
            monkeypatch,
            [
                _pkg(
                    "ghost",
                    [
                        {
                            "name": "custom",
                            "kind": "custom",
                            "module": "ghost.nope",
                            "class": "C",
                        }
                    ],
                )
            ],
        )
        result = dr.validate_drive_registration("custom")
        assert result["loaded"] is False
        assert "ghost" in result["error"]

    def test_unknown_returns_none(self, monkeypatch):
        _patch(monkeypatch, [])
        assert dr.validate_drive_registration("nope") is None


class TestInstantiate:
    def test_generic_instantiates(self, monkeypatch):
        _patch(monkeypatch, [])
        reg = dr.instantiate_registration("generic")
        assert reg.name == "generic" and reg.kind == "generic"

    def test_goal_builtin_instantiates(self, monkeypatch):
        _patch(monkeypatch, [])
        reg = dr.instantiate_registration("goal")
        assert reg.name == "goal" and reg.kind == "goal"

    def test_package_instantiates(self, monkeypatch):
        _patch(
            monkeypatch,
            [
                _pkg(
                    "kt-biome",
                    [
                        {
                            "name": "custom",
                            "kind": "custom",
                            "module": __name__,
                            "class": "FakeCustomRegistration",
                        }
                    ],
                )
            ],
        )
        assert dr.instantiate_registration("custom").kind == "custom"

    def test_unknown_raises(self, monkeypatch):
        from kohakuterrarium.terrarium.drive.errors import (
            DriveRegistrationNotFoundError,
        )

        _patch(monkeypatch, [])
        with pytest.raises(DriveRegistrationNotFoundError):
            dr.instantiate_registration("nope")

    def test_injects_options_into_package_registration(self, monkeypatch):
        # R1-17: a non-default option changes the resolved registration behavior.
        _patch(monkeypatch, [_cfg_pkg()])
        reg = dr.instantiate_registration("cfg", {"mode": "fast"})
        assert reg.mode == "fast"

    def test_unknown_option_is_rejected(self, monkeypatch):
        _patch(monkeypatch, [_cfg_pkg()])
        with pytest.raises(DriveValidationError, match="unknown option"):
            dr.instantiate_registration("cfg", {"bogus": 1})

    def test_builtin_generic_rejects_options(self, monkeypatch):
        # generic declares no options → any option is rejected, not ignored.
        _patch(monkeypatch, [])
        with pytest.raises(DriveValidationError):
            dr.instantiate_registration("generic", {"anything": 1})


class TestStatus:
    def test_enabled_generic_is_loaded(self, monkeypatch):
        _patch(monkeypatch, [])
        entries = dr.list_drive_registrations_status({"generic"})
        generic = next(e for e in entries if e["name"] == "generic")
        assert generic["enabled"] is True
        assert generic["loaded"] is True
        assert generic["error"] is None

    def test_enabled_goal_builtin_is_loaded(self, monkeypatch):
        # The builtin goal registration enables through the Drive-settings seam.
        _patch(monkeypatch, [])
        entries = dr.list_drive_registrations_status({"goal"})
        goal = next(e for e in entries if e["name"] == "goal")
        assert goal["enabled"] is True
        assert goal["loaded"] is True
        assert goal["kind"] == "goal"

    def test_disabled_goal_is_available_but_off(self, monkeypatch):
        # goal ships available-but-disabled: discoverable, enabled False.
        _patch(monkeypatch, [])
        entries = dr.list_drive_registrations_status(set())
        goal = next(e for e in entries if e["name"] == "goal")
        assert goal["enabled"] is False

    def test_disabled_installed_is_not_imported(self, monkeypatch):
        # A disabled registration is discoverable but never imported (§8.2):
        # loaded stays None even though the module path is bogus.
        _patch(
            monkeypatch,
            [
                _pkg(
                    "kt-biome",
                    [
                        {
                            "name": "custom",
                            "kind": "custom",
                            "module": "ghost.nope",
                            "class": "C",
                        }
                    ],
                )
            ],
        )
        entries = dr.list_drive_registrations_status(set())  # nothing enabled
        custom = next(e for e in entries if e["name"] == "custom")
        assert custom["enabled"] is False
        assert custom["loaded"] is None

    def test_enabled_broken_registration_reports_error(self, monkeypatch):
        _patch(
            monkeypatch,
            [
                _pkg(
                    "ghost",
                    [
                        {
                            "name": "custom",
                            "kind": "custom",
                            "module": "ghost.nope",
                            "class": "C",
                        }
                    ],
                )
            ],
        )
        entries = dr.list_drive_registrations_status({"custom"})
        custom = next(e for e in entries if e["name"] == "custom")
        assert custom["enabled"] is True
        assert custom["loaded"] is False
        assert custom["error"]
