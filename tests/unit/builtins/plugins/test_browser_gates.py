"""Unit tests for ``builtins/plugins/browser/gates.py``: the sandbox link and the host-resources rule."""

from pathlib import Path
from types import SimpleNamespace

from kohakuterrarium.browser.options import BrowserOptions
from kohakuterrarium.builtins.plugins.browser.gates import (
    CONFINEMENT_NETWORK_REFUSAL,
    SandboxLink,
    bundled_only,
    host_resource_refusal,
)
from kohakuterrarium.builtins.plugins.sandbox.plugin import SandboxPlugin
from kohakuterrarium.modules.tool.base import ToolContext
from kohakuterrarium.terrarium.channels.channels import TERRARIUM_ENGINE_KEY


class Policy:
    """A ``network_policy`` service that refuses one host and records every question."""

    def __init__(self, confined: bool = False) -> None:
        self.requires_os_confinement = confined
        self.asked: list[tuple[str, int]] = []

    def check(self, host: str, port: int) -> str | None:
        self.asked.append((host, port))
        return "host refused" if host == "evil.test" else None


class WritePolicy:
    def check(self, path: Path) -> str | None:
        return "write refused" if path.name == "no.txt" else None


class TestSandboxLink:
    def test_without_services_everything_is_allowed(self, tmp_path):
        link = SandboxLink()
        assert link.policy is None and not link.requires_confinement
        assert link.check("shop.test", 443) is None
        assert link.check_write(tmp_path / "no.txt") is None

    def test_bound_services_judge_hosts_and_writes(self, tmp_path):
        policy = Policy()
        link = SandboxLink()
        link.bind({"network_policy": policy, "write_policy": WritePolicy()})
        assert link.check("evil.test", 443) == "host refused"
        assert link.check("shop.test", 443) is None
        assert link.check_write(tmp_path / "no.txt") == "write refused"
        assert link.check_write(tmp_path / "ok.txt") is None
        link.bind(None)
        assert link.check("evil.test", 443) is None

    def test_live_services_win_over_bound_ones(self):
        live = {"network_policy": Policy()}
        link = SandboxLink(lambda: live)
        link.bind({})
        assert link.check("evil.test", 80) == "host refused"
        live.clear()
        assert link.check("evil.test", 80) is None
        absent = SandboxLink(lambda: None)
        absent.bind({"network_policy": Policy()})
        assert absent.check("evil.test", 80) == "host refused"

    def test_required_confinement_refuses_every_connection(self):
        policy = Policy(confined=True)
        link = SandboxLink(lambda: {"network_policy": policy})
        assert link.requires_confinement
        for host in ("shop.test", "evil.test", "127.0.0.1"):
            assert link.check(host, 443) == CONFINEMENT_NETWORK_REFUSAL
        assert policy.asked == [], "the network rules were asked under confinement"
        policy.requires_os_confinement = False
        assert link.check("shop.test", 443) is None

    def test_the_real_sandbox_flips_the_link_live(self):
        sandbox = SandboxPlugin()
        link = SandboxLink(lambda: sandbox.runtime_services(None))
        assert link.check("shop.test", 443) is None
        sandbox.set_options({"os_enforce": "require"})
        reason = link.check("shop.test", 443)
        assert reason == CONFINEMENT_NETWORK_REFUSAL and "os_enforce=require" in reason
        sandbox.set_options({"os_enforce": "auto"})
        assert link.check("shop.test", 443) is None


class TestHostResources:
    def test_an_unrestricted_creature_may_use_host_options(self, tmp_path):
        context = ToolContext(agent_name="a", session=None, working_dir=tmp_path)
        options = BrowserOptions(headless=False, allow_loopback=True)
        assert host_resource_refusal(options, context) is None
        assert not bundled_only(context)

    def test_a_restricted_creature_is_refused_each_host_option(self):
        engine = SimpleNamespace(host_resources=False)
        context = SimpleNamespace(environment={TERRARIUM_ENGINE_KEY: engine})
        assert bundled_only(context)
        assert host_resource_refusal(BrowserOptions(), context) is None
        refusal = host_resource_refusal(
            BrowserOptions(headless=False, allow_private=True), context
        )
        assert refusal is not None
        assert refusal.endswith("refused options: headless: false, allow_private")
