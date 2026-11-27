"""The ``network_policy`` / ``write_policy`` service interfaces."""

from types import SimpleNamespace

from kohakuterrarium.builtins.plugins.sandbox.plugin import SandboxPlugin
from kohakuterrarium.modules.sandbox import NetworkPolicy, WritePolicy


class _Proxy:
    """A third-party network policy, not derived from the sandbox."""

    mode = "allow"
    requires_os_confinement = False

    def check(self, host: str, port: int) -> str | None:
        return None if host == "ok.test" else "not ok"


def test_the_sandbox_services_satisfy_the_interfaces():
    services = SandboxPlugin().runtime_services(None)
    assert isinstance(services["network_policy"], NetworkPolicy)
    assert isinstance(services["write_policy"], WritePolicy)
    assert not isinstance(services["write_policy"], NetworkPolicy)


def test_any_object_with_the_members_satisfies_network_policy():
    assert isinstance(_Proxy(), NetworkPolicy)
    assert not isinstance(SimpleNamespace(check=lambda h, p: None), NetworkPolicy)


def test_write_policy_needs_only_check():
    assert isinstance(SimpleNamespace(check=lambda path: None), WritePolicy)
    assert not isinstance(SimpleNamespace(), WritePolicy)
