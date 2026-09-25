"""Unit tests for :mod:`kohakuterrarium.terrarium.drive.registration`.

Behavior asserts on the registration contract: descriptor validation, the
builtin generic registration's policy, manifest-descriptor construction (no
import), and lazy :func:`resolve_registration` (import only on enable, with
provenance-carrying typed errors).
"""

import pytest

from kohakuterrarium.terrarium.drive.errors import (
    DriveRegistrationIncompatibleError,
    DriveRegistrationNotFoundError,
    DriveValidationError,
)
from kohakuterrarium.terrarium.drive.registration import (
    DEFAULT_SPEC_MAX_BYTES,
    DriveProjection,
    DriveRegistrationDescriptor,
    GenericDriveRegistration,
    Readiness,
    VerificationResult,
    builtin_registrations,
    resolve_registration,
)

_THIS_MODULE = "kohakuterrarium.terrarium.drive.registration"
_TEST_MODULE = __name__


class _CompatImpl:
    """A resolvable registration whose descriptor advertises schema [1,2],
    an ``actor`` verifier, a ``spec``/``verifier`` role split, and an
    ``api=1`` compatibility marker."""

    name = "custom"
    kind = "custom"
    schema_version = 2

    def descriptor(self) -> DriveRegistrationDescriptor:
        return DriveRegistrationDescriptor(
            name="custom",
            kind="custom",
            schema_version=2,
            min_schema_version=1,
            required_roles=frozenset({"spec"}),
            optional_roles=frozenset({"verifier"}),
            verifier_mode="actor",
            compatibility={"api": 1},
        )


class _BoolCompatImpl:
    """A resolvable registration whose descriptor advertises JSON-typed markers:
    a bool ``flag``, a number ``count``, and a nested ``caps.api`` number — used
    to pin JSON-type-aware compatibility equality (R1-18)."""

    name = "boolc"
    kind = "boolc"
    schema_version = 1

    def descriptor(self) -> DriveRegistrationDescriptor:
        return DriveRegistrationDescriptor(
            name="boolc",
            kind="boolc",
            schema_version=1,
            required_roles=frozenset({"spec"}),
            verifier_mode="none",
            compatibility={"flag": True, "count": 1, "caps": {"api": 1}},
        )


class TestDescriptorValidation:
    def test_valid_descriptor_round_trips_to_dict(self):
        d = DriveRegistrationDescriptor(
            name="goal",
            kind="goal",
            schema_version=2,
            min_schema_version=1,
            source_package="kt-biome",
            module="kt_biome.drive.goal",
            class_name="GoalDriveRegistration",
            description="objective pursuit",
            required_roles=frozenset({"spec", "readiness"}),
            optional_roles=frozenset({"prompt"}),
            prompt_contribution="pursue the objective",
            verifier_mode="actor",
        )
        dto = d.to_dict()
        assert dto["name"] == "goal"
        assert dto["min_schema_version"] == 1
        assert dto["required_roles"] == ["readiness", "spec"]  # sorted
        assert dto["verifier_mode"] == "actor"
        assert dto["has_prompt"] is True

    def test_empty_name_rejected(self):
        with pytest.raises(DriveValidationError, match="name must be non-empty"):
            DriveRegistrationDescriptor(name=" ", kind="k", schema_version=1)

    def test_empty_kind_rejected(self):
        with pytest.raises(DriveValidationError, match="kind must be non-empty"):
            DriveRegistrationDescriptor(name="n", kind="", schema_version=1)

    def test_schema_version_must_be_positive_int(self):
        with pytest.raises(DriveValidationError, match="schema_version must be >= 1"):
            DriveRegistrationDescriptor(name="n", kind="k", schema_version=0)

    def test_bool_schema_version_rejected(self):
        with pytest.raises(DriveValidationError, match="schema_version must be an int"):
            DriveRegistrationDescriptor(name="n", kind="k", schema_version=True)

    def test_min_schema_version_out_of_range_rejected(self):
        with pytest.raises(DriveValidationError, match="min_schema_version must be"):
            DriveRegistrationDescriptor(
                name="n", kind="k", schema_version=2, min_schema_version=3
            )

    def test_unknown_role_rejected(self):
        with pytest.raises(DriveValidationError, match="unknown role 'bogus'"):
            DriveRegistrationDescriptor(
                name="n",
                kind="k",
                schema_version=1,
                required_roles=frozenset({"bogus"}),
            )

    def test_module_without_class_rejected(self):
        with pytest.raises(DriveValidationError, match="module and class_name"):
            DriveRegistrationDescriptor(
                name="n", kind="k", schema_version=1, module="m.n"
            )

    def test_bad_verifier_mode_rejected(self):
        with pytest.raises(DriveValidationError, match="verifier_mode must be one of"):
            DriveRegistrationDescriptor(
                name="n", kind="k", schema_version=1, verifier_mode="whenever"
            )

    def test_supports_schema_range(self):
        d = DriveRegistrationDescriptor(
            name="n", kind="k", schema_version=3, min_schema_version=2
        )
        assert d.supports_schema(2) is True
        assert d.supports_schema(3) is True
        assert d.supports_schema(1) is False
        assert d.supports_schema(4) is False

    def test_supports_schema_defaults_to_exact(self):
        d = DriveRegistrationDescriptor(name="n", kind="k", schema_version=2)
        assert d.min_version == 2
        assert d.supports_schema(2) is True
        assert d.supports_schema(1) is False

    def test_normalized_options_merges_defaults(self):
        d = DriveRegistrationDescriptor(
            name="n", kind="k", schema_version=1, option_defaults={"a": 1, "b": 2}
        )
        assert d.normalized_options({"b": 9}) == {"a": 1, "b": 9}
        assert d.normalized_options(None) == {"a": 1, "b": 2}

    def test_normalized_options_rejects_non_json(self):
        d = DriveRegistrationDescriptor(name="n", kind="k", schema_version=1)
        with pytest.raises(DriveValidationError, match="JSON-serializable"):
            d.normalized_options({"bad": object()})

    def test_validate_options_rejects_unknown_key(self):
        d = DriveRegistrationDescriptor(
            name="n", kind="k", schema_version=1, option_defaults={"a": 1}
        )
        d.validate_options({"a": 2})  # known
        with pytest.raises(DriveValidationError, match="unknown option 'b'"):
            d.validate_options({"a": 1, "b": 2})

    def test_validate_options_enforces_type(self):
        d = DriveRegistrationDescriptor(
            name="n",
            kind="k",
            schema_version=1,
            option_schema={"n": {"type": "int"}},
        )
        d.validate_options({"n": 5})
        with pytest.raises(DriveValidationError, match="type 'int'"):
            d.validate_options({"n": "five"})
        # bool is not an int for option typing.
        with pytest.raises(DriveValidationError, match="type 'int'"):
            d.validate_options({"n": True})

    def test_validate_options_enforces_choices(self):
        d = DriveRegistrationDescriptor(
            name="n",
            kind="k",
            schema_version=1,
            option_schema={"mode": {"type": "str", "choices": ["a", "b"]}},
        )
        d.validate_options({"mode": "a"})
        with pytest.raises(DriveValidationError, match="one of"):
            d.validate_options({"mode": "z"})


class _ConfigurableRegistration:
    """A registration whose ``configure`` hook changes its behavior — the
    real-injection contract R1-17 requires."""

    name = "cfg"
    kind = "cfg"
    schema_version = 1

    def __init__(self):
        self.mode = "default"

    def descriptor(self) -> DriveRegistrationDescriptor:
        return DriveRegistrationDescriptor(
            name="cfg",
            kind="cfg",
            schema_version=1,
            option_defaults={"mode": "default"},
            option_schema={"mode": {"type": "str", "choices": ["default", "fast"]}},
        )

    def configure(self, options: dict) -> None:
        self.mode = options["mode"]


class _OptionlessRegistration:
    """Declares an option but exposes NO ``configure`` hook — a misconfiguration
    that must be rejected rather than silently dropping the option (R1-17)."""

    name = "opt"
    kind = "opt"
    schema_version = 1

    def descriptor(self) -> DriveRegistrationDescriptor:
        return DriveRegistrationDescriptor(
            name="opt", kind="opt", schema_version=1, option_defaults={"x": 0}
        )


class TestOptionInjection:
    """R1-17: effective options are validated and injected, not stored + ignored."""

    def test_non_default_option_changes_behavior(self):
        from kohakuterrarium.terrarium.drive.registration import (
            apply_registration_options,
            effective_options,
        )

        reg = _ConfigurableRegistration()
        eff = apply_registration_options(reg, reg.descriptor(), {"mode": "fast"})
        assert reg.mode == "fast"  # the injected option actually changed behavior
        assert eff == {"mode": "fast"}
        assert effective_options(reg) == {"mode": "fast"}

    def test_default_applied_when_no_override(self):
        from kohakuterrarium.terrarium.drive.registration import (
            apply_registration_options,
        )

        reg = _ConfigurableRegistration()
        apply_registration_options(reg, reg.descriptor(), None)
        assert reg.mode == "default"

    def test_invalid_option_rejected(self):
        from kohakuterrarium.terrarium.drive.registration import (
            apply_registration_options,
        )

        reg = _ConfigurableRegistration()
        with pytest.raises(DriveValidationError, match="one of"):
            apply_registration_options(reg, reg.descriptor(), {"mode": "warp"})

    def test_options_for_registration_without_configure_rejected(self):
        from kohakuterrarium.terrarium.drive.registration import (
            apply_registration_options,
        )

        reg = _OptionlessRegistration()
        with pytest.raises(DriveValidationError, match="no configure"):
            apply_registration_options(reg, reg.descriptor(), {"x": 1})

    def test_non_empty_defaults_without_configure_rejected(self):
        from kohakuterrarium.terrarium.drive.registration import (
            apply_registration_options,
        )

        # R1-17: even with NO caller override the effective set is the declared
        # default {"x": 0}; a registration that carries a default it cannot apply
        # (no configure hook) is rejected, not silently stamped-and-ignored.
        reg = _OptionlessRegistration()
        with pytest.raises(DriveValidationError, match="no configure"):
            apply_registration_options(reg, reg.descriptor(), None)


class TestFromManifest:
    def test_builds_shallow_descriptor_without_importing(self):
        d = DriveRegistrationDescriptor.from_manifest(
            "kt-biome",
            {
                "name": "goal",
                "kind": "goal",
                "module": "kt_biome.drive.goal",
                "class": "GoalDriveRegistration",
                "description": "objective pursuit",
                "schema_version": 3,
            },
        )
        assert d.source_package == "kt-biome"
        assert d.module == "kt_biome.drive.goal"
        assert d.class_name == "GoalDriveRegistration"
        assert d.schema_version == 3
        # No prompt/role metadata is fabricated for a shallow manifest scan.
        assert d.required_roles == frozenset()
        assert d.prompt_contribution is None

    def test_class_name_alias_accepted(self):
        d = DriveRegistrationDescriptor.from_manifest(
            "p", {"name": "g", "kind": "g", "module": "m", "class_name": "C"}
        )
        assert d.class_name == "C"

    def test_missing_name_or_kind_rejected(self):
        with pytest.raises(DriveValidationError, match="needs 'name' and 'kind'"):
            DriveRegistrationDescriptor.from_manifest(
                "p", {"kind": "g", "module": "m", "class": "C"}
            )

    def test_missing_module_or_class_rejected(self):
        with pytest.raises(DriveValidationError, match="needs .*module.* and .*class"):
            DriveRegistrationDescriptor.from_manifest("p", {"name": "g", "kind": "g"})

    def test_non_int_schema_version_rejected(self):
        with pytest.raises(DriveValidationError, match="schema_version"):
            DriveRegistrationDescriptor.from_manifest(
                "p",
                {
                    "name": "g",
                    "kind": "g",
                    "module": "m",
                    "class": "C",
                    "schema_version": "x",
                },
            )

    def test_full_descriptor_metadata_round_trips(self):
        # R1-18: every supported descriptor field survives the manifest scan.
        d = DriveRegistrationDescriptor.from_manifest(
            "kt-biome",
            {
                "name": "custom",
                "kind": "custom",
                "module": "kt_biome.custom",
                "class": "CustomRegistration",
                "schema_version": 2,
                "min_schema_version": 1,
                "required_roles": ["spec", "readiness"],
                "optional_roles": ["verifier"],
                "prompt_contribution": "pursue the objective",
                "verifier_mode": "actor",
                "option_defaults": {"mode": "fast"},
                "option_schema": {"mode": {"type": "str"}},
                "compatibility": {"engine": ">=1.0"},
            },
        )
        assert d.min_schema_version == 1
        assert d.required_roles == frozenset({"spec", "readiness"})
        assert d.optional_roles == frozenset({"verifier"})
        assert d.prompt_contribution == "pursue the objective"
        assert d.verifier_mode == "actor"
        assert d.option_defaults == {"mode": "fast"}
        assert d.option_schema == {"mode": {"type": "str"}}
        assert d.compatibility == {"engine": ">=1.0"}

    def test_bad_manifest_role_rejected(self):
        with pytest.raises(DriveValidationError, match="unknown role"):
            DriveRegistrationDescriptor.from_manifest(
                "p",
                {
                    "name": "g",
                    "kind": "g",
                    "module": "m",
                    "class": "C",
                    "required_roles": ["bogus"],
                },
            )

    def test_bad_manifest_verifier_mode_rejected(self):
        with pytest.raises(DriveValidationError, match="verifier_mode"):
            DriveRegistrationDescriptor.from_manifest(
                "p",
                {
                    "name": "g",
                    "kind": "g",
                    "module": "m",
                    "class": "C",
                    "verifier_mode": "whenever",
                },
            )


class TestManifestImplementationCompatibility:
    """R1-18: resolution fails closed when the implementation descriptor does
    not match the manifest-advertised schema / roles / verifier contract."""

    def _manifest(self, **overrides) -> DriveRegistrationDescriptor:
        entry = {
            "name": "custom",
            "kind": "custom",
            "module": _TEST_MODULE,
            "class": "_CompatImpl",
            "schema_version": 2,
            "min_schema_version": 1,
            "required_roles": ["spec"],
            "verifier_mode": "actor",
        }
        entry.update(overrides)
        return DriveRegistrationDescriptor.from_manifest("kt-biome", entry)

    def test_compatible_range_resolves(self):
        reg = resolve_registration(self._manifest())
        assert isinstance(reg, _CompatImpl)

    def test_compatibility_marker_value_mismatch_rejected(self):
        # R1-18: the manifest requires api=2 but the implementation advertises
        # api=1 → fail closed.
        with pytest.raises(DriveRegistrationIncompatibleError, match="compatibility"):
            resolve_registration(self._manifest(compatibility={"api": 2}))

    def test_compatibility_marker_absent_rejected(self):
        # R1-18: a marker the manifest requires that the implementation does not
        # advertise at all fails closed (fail-open would silently run
        # unverified-compatible code).
        with pytest.raises(DriveRegistrationIncompatibleError, match="compatibility"):
            resolve_registration(self._manifest(compatibility={"feature": "x"}))

    def test_compatibility_subset_of_impl_markers_resolves(self):
        # The implementation MAY advertise markers the manifest omitted; only the
        # manifest's declared markers are enforced.
        reg = resolve_registration(self._manifest(compatibility={"api": 1}))
        assert isinstance(reg, _CompatImpl)

    def _bool_manifest(self, compatibility: dict) -> DriveRegistrationDescriptor:
        return DriveRegistrationDescriptor.from_manifest(
            "kt-biome",
            {
                "name": "boolc",
                "kind": "boolc",
                "module": _TEST_MODULE,
                "class": "_BoolCompatImpl",
                "schema_version": 1,
                "required_roles": ["spec"],
                "verifier_mode": "none",
                "compatibility": compatibility,
            },
        )

    def test_matching_bool_marker_resolves(self):
        # A bool marker still matches a bool of the SAME value (control).
        reg = resolve_registration(self._bool_manifest({"flag": True}))
        assert isinstance(reg, _BoolCompatImpl)

    def test_scalar_bool_vs_number_mismatch_rejected(self):
        # R1-18: manifest requires bool True but the implementation advertises the
        # number 1 — Python's True == 1 must NOT pass; distinct JSON types fail
        # closed.
        with pytest.raises(DriveRegistrationIncompatibleError, match="compatibility"):
            resolve_registration(self._bool_manifest({"count": True}))

    def test_scalar_number_vs_bool_mismatch_rejected(self):
        # R1-18 reverse direction: manifest requires number 1 but the impl
        # advertises bool True — also fails closed.
        with pytest.raises(DriveRegistrationIncompatibleError, match="compatibility"):
            resolve_registration(self._bool_manifest({"flag": 1}))

    def test_nested_bool_vs_number_mismatch_rejected(self):
        # R1-18 recursion: a nested marker's bool-vs-number distinction must be
        # honoured too — {"caps": {"api": True}} != impl {"caps": {"api": 1}}.
        with pytest.raises(DriveRegistrationIncompatibleError, match="compatibility"):
            resolve_registration(self._bool_manifest({"caps": {"api": True}}))

    def test_nested_matching_marker_resolves(self):
        # The nested marker matches when the JSON types agree (control).
        reg = resolve_registration(self._bool_manifest({"caps": {"api": 1}}))
        assert isinstance(reg, _BoolCompatImpl)

    def test_current_schema_mismatch_rejected(self):
        # Manifest advertises schema 5 but the implementation only supports [1,2].
        with pytest.raises(DriveRegistrationIncompatibleError, match="schema"):
            resolve_registration(self._manifest(schema_version=5))

    def test_min_schema_mismatch_rejected(self):
        # Manifest advertises min schema 1 with current 2 that the impl covers;
        # but a manifest whose min drops below the impl's support is rejected.
        with pytest.raises(DriveRegistrationIncompatibleError, match="schema"):
            resolve_registration(self._manifest(schema_version=3, min_schema_version=3))

    def test_required_role_mismatch_rejected(self):
        with pytest.raises(DriveValidationError, match="role"):
            resolve_registration(self._manifest(required_roles=["projection"]))

    def test_verifier_mode_mismatch_rejected(self):
        with pytest.raises(DriveValidationError, match="verifier_mode"):
            resolve_registration(self._manifest(verifier_mode="two_party"))


class TestGenericRegistration:
    def test_descriptor_declares_generic_kind_and_roles(self):
        d = GenericDriveRegistration().descriptor()
        assert d.name == "generic"
        assert d.kind == "generic"
        assert d.schema_version == 1
        assert "spec" in d.required_roles
        assert d.verifier_mode == "none"

    def test_validate_spec_accepts_json_safe_dict(self):
        GenericDriveRegistration().validate_spec({"instruction": "watch it"})

    def test_validate_spec_rejects_non_dict(self):
        with pytest.raises(DriveValidationError, match="must be a dict"):
            GenericDriveRegistration().validate_spec(["not", "a", "dict"])

    def test_validate_spec_rejects_non_json_safe(self):
        with pytest.raises(DriveValidationError, match="JSON-serializable"):
            GenericDriveRegistration().validate_spec({"x": object()})

    def test_validate_spec_rejects_oversize(self):
        reg = GenericDriveRegistration(spec_max_bytes=64)
        with pytest.raises(DriveValidationError, match="over the 64-byte cap"):
            reg.validate_spec({"blob": "y" * 500})

    def test_default_spec_cap_allows_reasonable_spec(self):
        GenericDriveRegistration().validate_spec(
            {"blob": "y" * (DEFAULT_SPEC_MAX_BYTES // 2)}
        )

    def test_bad_spec_max_bytes_rejected(self):
        with pytest.raises(DriveValidationError, match="positive int"):
            GenericDriveRegistration(spec_max_bytes=0)

    def test_readiness_trivially_ready(self):
        assert GenericDriveRegistration().readiness(None, None, None) == Readiness(True)

    def test_project_event_is_bounded_drive_ready(self):
        proj = GenericDriveRegistration().project_event(None, None, "ready")
        assert isinstance(proj, DriveProjection)
        assert proj.event_type == "drive_ready"

    def test_verify_terminal_approves_manual_proposal(self):
        assert GenericDriveRegistration().verify_terminal(
            None, None
        ) == VerificationResult(True)

    def test_prompt_contribution_is_non_empty(self):
        assert GenericDriveRegistration().prompt_contribution()

    def test_builtin_registrations_is_just_generic(self):
        regs = builtin_registrations()
        assert [r.name for r in regs] == ["generic"]


class TestResolveRegistration:
    def test_resolves_a_real_class(self):
        d = DriveRegistrationDescriptor(
            name="generic",
            kind="generic",
            schema_version=1,
            module=_THIS_MODULE,
            class_name="GenericDriveRegistration",
        )
        reg = resolve_registration(d)
        assert isinstance(reg, GenericDriveRegistration)

    def test_no_import_target_raises(self):
        d = DriveRegistrationDescriptor(name="g", kind="g", schema_version=1)
        with pytest.raises(DriveRegistrationNotFoundError, match="no import target"):
            resolve_registration(d)

    def test_missing_module_raises_with_provenance(self):
        d = DriveRegistrationDescriptor(
            name="g",
            kind="g",
            schema_version=1,
            source_package="ghostpkg",
            module="ghostpkg.nope",
            class_name="X",
        )
        with pytest.raises(DriveRegistrationNotFoundError, match="ghostpkg"):
            resolve_registration(d)

    def test_missing_class_raises(self):
        d = DriveRegistrationDescriptor(
            name="g",
            kind="g",
            schema_version=1,
            module=_THIS_MODULE,
            class_name="NoSuch",
        )
        with pytest.raises(DriveRegistrationNotFoundError, match="not found"):
            resolve_registration(d)

    def test_non_registration_object_raises(self):
        d = DriveRegistrationDescriptor(
            name="g",
            kind="g",
            schema_version=1,
            module=_THIS_MODULE,
            class_name="ROLE_METHODS",
        )
        with pytest.raises(DriveValidationError, match="not a DriveRegistration"):
            resolve_registration(d)

    def test_instantiation_failure_raises(self):
        # DriveRegistrationDescriptor() needs required args -> TypeError on ().
        d = DriveRegistrationDescriptor(
            name="g",
            kind="g",
            schema_version=1,
            module=_THIS_MODULE,
            class_name="DriveRegistrationDescriptor",
        )
        with pytest.raises(DriveValidationError, match="failed to instantiate"):
            resolve_registration(d)

    def test_identity_mismatch_raises(self):
        d = DriveRegistrationDescriptor(
            name="other",
            kind="other",
            schema_version=1,
            module=_THIS_MODULE,
            class_name="GenericDriveRegistration",
        )
        with pytest.raises(DriveValidationError, match="identity mismatch"):
            resolve_registration(d)
