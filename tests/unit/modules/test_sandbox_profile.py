"""Unit tests for the sandbox profile lattice + presets + parsing.

Behavior-first: SandboxProfile validates axis values, intersection keeps
the narrower capability per axis (and the *higher* risk), presets carry
the documented capability shape, parse_profile resolves names/dicts/
instances, and ProfileViolation builds a stable error + payload.
"""

import pytest

from kohakuterrarium.modules.sandbox.parse import parse_profile
from kohakuterrarium.modules.sandbox.presets import (
    PURE,
    SHELL,
    WORKSPACE,
    get_profile,
    list_profiles,
)
from kohakuterrarium.modules.sandbox.profile import (
    SandboxProfile,
    narrower_axis,
    profile_intersection,
    risk_max,
    wider_axis,
)
from kohakuterrarium.modules.sandbox.violations import ProfileViolation


class TestSandboxProfileValidation:
    def test_valid_profile_constructs(self):
        p = SandboxProfile(fs_read="broad", network="allow", risk="medium")
        assert p.fs_read == "broad"
        assert p.network == "allow"

    def test_invalid_axis_value_rejected(self):
        with pytest.raises(ValueError, match="fs_read"):
            SandboxProfile(fs_read="nonsense")

    def test_invalid_risk_value_rejected(self):
        with pytest.raises(ValueError, match="risk"):
            SandboxProfile(risk="apocalyptic")

    def test_fs_deny_normalized_to_string_tuple(self):
        from pathlib import PurePosixPath

        # Non-str entries are coerced to str so the profile stays
        # JSON/YAML-friendly regardless of how it was constructed.
        p = SandboxProfile(fs_deny=(PurePosixPath("/secret"), "/other"))
        assert p.fs_deny == ("/secret", "/other")
        assert all(isinstance(x, str) for x in p.fs_deny)

    def test_network_allowlist_lowercased(self):
        p = SandboxProfile(network_allowlist=("API.EXAMPLE.COM", "Cdn.Net"))
        assert p.network_allowlist == ("api.example.com", "cdn.net")


class TestSandboxProfileSerialization:
    def test_to_dict_round_trips_via_from_dict(self):
        original = SandboxProfile(
            name="custom",
            fs_read="broad",
            fs_write="workspace",
            network="allow",
            syscall="shell",
            risk="high",
        )
        clone = SandboxProfile.from_dict(original.to_dict())
        assert clone == original

    def test_from_dict_applies_documented_defaults(self):
        # An empty dict yields the most restrictive profile.
        p = SandboxProfile.from_dict({})
        assert p.fs_read == "deny"
        assert p.fs_write == "deny"
        assert p.network == "deny"
        assert p.risk == "safe"

    def test_with_overrides_replaces_only_named_fields(self):
        base = SandboxProfile(fs_read="broad", network="allow", risk="medium")
        overridden = base.with_overrides(network="deny")
        assert overridden.network == "deny"
        # Untouched fields preserved.
        assert overridden.fs_read == "broad"
        assert overridden.risk == "medium"


class TestLatticeHelpers:
    def test_narrower_axis_picks_more_restrictive(self):
        # fs lattice: deny < workspace < broad
        assert narrower_axis("fs_read", "deny", "broad") == "deny"
        assert narrower_axis("fs_read", "broad", "workspace") == "workspace"

    def test_wider_axis_picks_more_permissive(self):
        assert wider_axis("fs_read", "deny", "broad") == "broad"
        assert wider_axis("network", "deny", "allow") == "allow"

    def test_risk_max_picks_the_more_dangerous(self):
        assert risk_max("safe", "high") == "high"
        assert risk_max("low", "medium") == "medium"


class TestProfileIntersection:
    def test_intersection_keeps_narrower_capability_per_axis(self):
        left = SandboxProfile(fs_read="broad", network="allow", risk="low")
        right = SandboxProfile(fs_read="deny", network="allow", risk="high")
        inter = profile_intersection(left, right)
        # fs_read: deny is narrower than broad.
        assert inter.fs_read == "deny"
        # network: both allow → allow.
        assert inter.network == "allow"
        # risk takes the MAX (more dangerous) so the caller is warned.
        assert inter.risk == "high"

    def test_intersection_unions_fs_deny(self):
        left = SandboxProfile(fs_deny=("/a",))
        right = SandboxProfile(fs_deny=("/b",))
        inter = profile_intersection(left, right)
        assert set(inter.fs_deny) == {"/a", "/b"}

    def test_intersection_network_allowlist_empty_means_unrestricted(self):
        # An empty allowlist means "all hosts" — intersecting with a
        # restricted list yields the restricted list.
        unrestricted = SandboxProfile(network="allow")
        restricted = SandboxProfile(
            network="allow", network_allowlist=("api.example.com",)
        )
        inter = profile_intersection(unrestricted, restricted)
        assert inter.network_allowlist == ("api.example.com",)

    def test_intersection_restricted_left_unrestricted_right(self):
        # Mirror image: left restricted, right empty (unrestricted) →
        # the restricted left allowlist wins.
        restricted = SandboxProfile(
            network="allow", network_allowlist=("api.example.com",)
        )
        unrestricted = SandboxProfile(network="allow")
        inter = profile_intersection(restricted, unrestricted)
        assert inter.network_allowlist == ("api.example.com",)

    def test_intersection_two_allowlists_intersect(self):
        a = SandboxProfile(network="allow", network_allowlist=("x.com", "y.com"))
        b = SandboxProfile(network="allow", network_allowlist=("y.com", "z.com"))
        inter = profile_intersection(a, b)
        assert inter.network_allowlist == ("y.com",)

    def test_intersection_name_defaults_and_overrides(self):
        a = SandboxProfile(name="A")
        b = SandboxProfile(name="B")
        assert profile_intersection(a, b).name == "intersection"
        assert profile_intersection(a, b, name="merged").name == "merged"


class TestPresets:
    def test_pure_is_fully_locked_down(self):
        assert PURE.fs_read == "deny"
        assert PURE.fs_write == "deny"
        assert PURE.network == "deny"
        assert PURE.risk == "safe"

    def test_shell_is_the_most_permissive_builtin(self):
        assert SHELL.syscall == "shell"
        assert SHELL.network == "allow"
        assert SHELL.risk == "high"

    def test_get_profile_resolves_canonical_name(self):
        assert get_profile("WORKSPACE") is WORKSPACE

    def test_get_profile_resolves_aliases(self):
        assert get_profile("workspace") is WORKSPACE
        assert get_profile("read-only").name == "READ_ONLY"
        assert get_profile("readonly").name == "READ_ONLY"

    def test_get_profile_unknown_name_raises(self):
        with pytest.raises(ValueError, match="Unknown sandbox profile"):
            get_profile("teleport")

    def test_get_profile_default_when_empty(self):
        assert get_profile("") is WORKSPACE

    def test_list_profiles_returns_a_copy(self):
        listing = list_profiles()
        listing["INJECTED"] = PURE
        # Mutating the returned dict must not corrupt the registry.
        assert "INJECTED" not in list_profiles()


class TestParseProfile:
    def test_parse_existing_profile_passes_through(self):
        p = SandboxProfile(name="x")
        assert parse_profile(p) is p

    def test_parse_dict_builds_profile(self):
        result = parse_profile({"fs_read": "broad", "name": "from_dict"})
        assert isinstance(result, SandboxProfile)
        assert result.fs_read == "broad"
        assert result.name == "from_dict"

    def test_parse_name_resolves_preset(self):
        assert parse_profile("PURE") is PURE

    def test_parse_none_defaults_to_workspace(self):
        assert parse_profile(None) is WORKSPACE


class TestProfileViolation:
    def test_default_message_is_built_from_fields(self):
        violation = ProfileViolation(
            axis="network",
            operation="connect",
            requested="allow",
            profile=PURE,
        )
        # Message synthesized from axis/operation/requested/profile.
        assert "connect" in violation.message
        assert "network" in violation.message
        assert "PURE" in violation.message
        # It is a real exception carrying that message.
        assert isinstance(violation, Exception)
        assert str(violation) == violation.message

    def test_explicit_message_is_preserved(self):
        violation = ProfileViolation(
            axis="fs_write",
            operation="write",
            requested="workspace",
            profile=PURE,
            message="custom denial",
        )
        assert violation.message == "custom denial"
        assert str(violation) == "custom denial"

    def test_to_dict_serializes_stable_payload(self):
        violation = ProfileViolation(
            axis="syscall",
            operation="exec",
            requested="shell",
            profile=PURE,
            metadata={"pid": 42},
        )
        payload = violation.to_dict()
        assert payload["type"] == "profile_violation"
        assert payload["axis"] == "syscall"
        assert payload["operation"] == "exec"
        assert payload["requested"] == "shell"
        assert payload["metadata"] == {"pid": 42}
        # The profile is nested as its own serialized dict.
        assert payload["profile"]["name"] == "PURE"
