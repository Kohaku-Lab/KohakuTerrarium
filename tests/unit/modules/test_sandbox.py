"""Unit tests for :mod:`kohakuterrarium.modules.sandbox`."""

from kohakuterrarium.modules.sandbox.config import SandboxConfig
from kohakuterrarium.modules.sandbox.profile import SandboxProfile


class TestSandboxConfigDefaults:
    def test_defaults(self):
        c = SandboxConfig()
        assert c.enabled is True
        assert c.audit is False
        assert c.backend == "auto"
        assert isinstance(c.profile, SandboxProfile)
        assert c.fs_read is None
        assert c.fs_deny == ()


class TestEffectiveCap:
    def test_scalar_overrides_applied(self):
        c = SandboxConfig(
            fs_read="deny",
            fs_write="deny",
            network="deny",
        )
        cap = c.effective_cap()
        assert cap.fs_read == "deny"
        assert cap.fs_write == "deny"
        assert cap.network == "deny"

    def test_fs_deny_merged(self):
        c = SandboxConfig(fs_deny=("/etc/secret",))
        cap = c.effective_cap()
        assert "/etc/secret" in cap.fs_deny

    def test_no_override_inherits_profile(self):
        c = SandboxConfig()
        cap = c.effective_cap()
        # Same values as the underlying profile (because no overrides).
        assert cap.fs_read == c.profile.fs_read
